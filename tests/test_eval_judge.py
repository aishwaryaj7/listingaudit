"""The model as judge, through DeepEval with a scripted model, and a person's grades.

DeepEval runs in a layer of its own (see listingaudit.eval.judge), so the tests that
need it run with

    uv run --with "deepeval==4.3.4" pytest tests/test_eval_judge.py

and are skipped in a plain test run.
"""

import csv
from importlib.util import find_spec

import pytest

from listingaudit.agent.report import CHECKLIST, Problem, Report
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.eval import judge, records
from listingaudit.eval.records import Record, read_json_lines, write_records
from listingaudit.market.rules import Rule

needs_deepeval = pytest.mark.skipif(
    find_spec("deepeval") is None, reason=f'needs uv run --with "{judge.DEEPEVAL}"'
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def audit(offer_id: str, problems: int = 1, system: str = "agent", **fields) -> Record:
    spec = CHECKLIST.rules[Rule.PRIOR_PRICE]
    found = Problem(
        rule=Rule.PRIOR_PRICE,
        name=spec.name,
        severity=spec.severity,
        explanation="The badge says -42%, but the price is only 25.0% below €19.99.",
        evidence=[],
        facts={},
        law=["Directive 98/6/EC Art. 6a(1)"],
        quotes=[],
        suggested_fix="Show -25% on the badge, or leave the badge out.",
    )
    report = Report(
        offer_id=offer_id,
        title="Test Pillow",
        kind="textile",
        summary="1 possible problem: the -42% badge." if problems else "No possible problems.",
        problems=[found] * problems,
        to_check=[],
        passed=[],
        unresolved=[],
    )
    results = [
        {
            "rule": "prior_price",
            "status": "problem",
            "summary": "The badge says -42%, but the price is only 25.0% below €19.99.",
        }
    ]
    return Record(system=system, offer_id=offer_id, results=results, report=report, **fields)


@pytest.fixture
def exam(tmp_path, monkeypatch):
    """Saved audits: four agent reports with a possible problem, one without, one failed."""
    monkeypatch.setattr(records, "RESULTS_DIR", tmp_path)
    agent = [audit(f"E000{n}") for n in range(1, 5)]
    agent += [audit("E0005", problems=0), audit("E0001", repeat=1)]
    agent += [Record(system="agent", offer_id="E0006", error="TimeoutError")]
    write_records(tmp_path / "exam" / "agent.jsonl.gz", agent)
    write_records(tmp_path / "exam" / "one_prompt.jsonl.gz", [audit("E0001", system="one_prompt")])
    return tmp_path / "exam"


@pytest.mark.parametrize(
    ("score", "grade"),
    [(0.0, 1), (0.2, 1), (0.3, 2), (0.45, 2), (0.5, 3), (0.8, 4), (0.85, 4), (0.9, 5), (1.0, 5)],
)
def test_a_score_becomes_the_grade_of_its_rubric_range(score, grade):
    assert judge.to_grade(score) == grade


def test_only_main_runs_with_a_possible_problem_are_graded(exam):
    graded = judge.reports_to_grade("exam", "agent")
    assert [record.offer_id for record in graded] == ["E0001", "E0002", "E0003", "E0004"]


def test_the_evidence_lists_each_check_result():
    assert judge.evidence(audit("E0001")).splitlines() == [
        "Listing E0001: Test Pillow",
        "Check results:",
        "- prior_price (problem): The badge says -42%, but the price is only 25.0% below €19.99.",
    ]


def test_a_person_grades_a_sample_and_can_stop_and_carry_on(exam, monkeypatch, capsys):
    monkeypatch.setattr(judge, "HUMAN_SAMPLE", 3)
    answers = iter(["4", "6", "5", "Clear, but long.", "3", "4", ""])

    def type_in(question: str) -> str:
        answer = next(answers, None)
        if answer is None:
            raise KeyboardInterrupt
        return answer

    monkeypatch.setattr("builtins.input", type_in)
    with pytest.raises(KeyboardInterrupt):
        judge.grade_by_hand("exam")
    assert "Type a whole number from 1 to 5." in capsys.readouterr().out

    answers = iter(["2", "3", "Hard to follow."])
    judge.grade_by_hand("exam")
    out = capsys.readouterr().out
    assert "--- Report 3 of 3" in out
    assert "All 3 reports are graded" in out

    rows = list(csv.DictReader((exam / "human_grades.csv").open(encoding="utf-8")))
    assert [(row["clarity"], row["fairness"], row["note"]) for row in rows] == [
        ("4", "5", "Clear, but long."),
        ("3", "4", ""),
        ("2", "3", "Hard to follow."),
    ]
    assert len({row["offer_id"] for row in rows}) == 3


# --- With DeepEval ---------------------------------------------------------------


def scored(score: float, reason: str):
    """A scripted answer to G-Eval's request for a score."""
    from deepeval.metrics.g_eval.schema import ReasonScore

    return lambda prompt: ReasonScore(reason=reason, score=score)


@needs_deepeval
@pytest.mark.anyio
async def test_the_judge_grades_clarity_and_fairness_with_g_eval():
    model = ScriptedModel(script=[scored(8, "Clear and specific."), scored(10, "Neutral.")])
    row = await judge.judge_report(judge.judge_model(model, "scripted"), "agent", audit("E0001"))

    assert row == {
        "system": "agent",
        "offer_id": "E0001",
        "clarity": 4,
        "clarity_score": 0.8,
        "clarity_reason": "Clear and specific.",
        "fairness": 5,
        "fairness_score": 1.0,
        "fairness_reason": "Neutral.",
    }
    clarity, fairness = model.requests
    assert "Check that each suggested fix tells the seller concretely" in clarity
    assert "never a violation or illegal" in fairness
    for prompt in (clarity, fairness):
        assert "Grade 5: excellent: nothing to improve" in prompt
        assert "- prior_price (problem): The badge says -42%" in prompt
        assert "Suggested fix: Show -25% on the badge" in prompt


@needs_deepeval
@pytest.mark.anyio
async def test_judging_carries_on_where_it_stopped(exam):
    def busy(prompt):
        raise RuntimeError("The model is busy.")

    model = ScriptedModel(script=[busy] + [scored(6, "Fine.")] * 8)
    await judge.judge_all("exam", ["agent", "one_prompt"], 1, model, "scripted")
    rows = read_json_lines(exam / "judge.jsonl")
    assert [(row["system"], row["offer_id"]) for row in rows] == [
        ("agent", "E0002"),
        ("agent", "E0003"),
        ("agent", "E0004"),
        ("one_prompt", "E0001"),
    ]

    model.script = [scored(4, "Some jargon.")] * 2
    await judge.judge_all("exam", ["agent", "one_prompt"], 1, model, "scripted")
    rows = read_json_lines(exam / "judge.jsonl")
    assert len(rows) == 5
    assert rows[0] == {
        "system": "agent",
        "offer_id": "E0001",
        "clarity": 2,
        "clarity_score": 0.4,
        "clarity_reason": "Some jargon.",
        "fairness": 2,
        "fairness_score": 0.4,
        "fairness_reason": "Some jargon.",
    }
    assert model.script == []
