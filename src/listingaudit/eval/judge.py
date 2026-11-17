"""Are the reports clear and fair? A model grades them, and a person checks the model.

    uv run python -m listingaudit.eval.judge --grade
    uv run --with "deepeval==4.3.4" python -m listingaudit.eval.judge

--grade shows 30 of the agent's reports with a possible problem, picked at random, and
asks for a grade from 1 to 5 for clarity and for fairness. The grades go to
results/<split>/human_grades.csv, and grading can stop and carry on at any time.
Without it, DeepEval's G-Eval grades every report with a possible problem, using the
judge model (JUDGE_MODEL, by default the agent's model), into results/<split>/judge.jsonl.
The grader compares the two on the reports both graded.

DeepEval is not one of the project's dependencies: it needs click below 8.4, which would
hold huggingface-hub and transformers back for the whole app. uv's --with puts it in a
layer of its own on top of the project's environment, for this command only.
"""

import argparse
import asyncio
import csv
import os
import random
import sys
from importlib.util import find_spec
from typing import Any

from langchain_core.language_models import BaseChatModel

from listingaudit.agent.brain import chat_model, structured
from listingaudit.agent.report import render
from listingaudit.config import settings
from listingaudit.eval.records import (
    Record,
    folder,
    read_json_lines,
    read_records,
    records_path,
    write_json_lines,
)

# DeepEval reports how it is used to its makers unless told not to
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

DEEPEVAL = "deepeval==4.3.4"
HUMAN_SAMPLE = 30
SAMPLE_SEED = 7
GRADES = {
    1: "unusable: a reviewer can't rely on it",
    2: "poor: hard to follow, or unfair in places",
    3: "acceptable, with clear flaws",
    4: "good, with small flaws",
    5: "excellent: nothing to improve",
}
CRITERIA = {
    "clarity": (
        "Clarity: a compliance reviewer can understand the report quickly and act on it.",
        [
            "Read the check results the report is based on, then the report.",
            "Check that each possible problem says in plain words what is wrong, with the "
            "evidence that shows it.",
            "Check that each suggested fix tells the seller concretely what to change.",
            "Check that the summary gives the number of possible problems and the most "
            "serious one.",
            "Mark down jargon, vague or repeated text, and anything a reviewer has to work "
            "out alone.",
        ],
    ),
    "fairness": (
        "Fairness: the report is fair to the seller.",
        [
            "Read the check results the report is based on, then the report.",
            "Check that it calls each finding a possible problem, never a violation or illegal.",
            "Check that each finding is backed by the check results, without exaggeration.",
            "Check that the tone is neutral and the suggested fixes are reasonable for a seller.",
            "Mark down blame, threats, and certainty the evidence doesn't support.",
        ],
    ),
}
# G-Eval scores from 0 to 10; each grade covers part of that range
RUBRIC = [((0, 2), 1), ((3, 4), 2), ((5, 6), 3), ((7, 8), 4), ((9, 10), 5)]


def to_grade(score: float) -> int:
    """A G-Eval score (0 to 1) as a grade from 1 to 5."""
    points = score * 10
    return next(grade for (_, high), grade in RUBRIC if points <= high + 0.5)


def evidence(record: Record) -> str:
    """The check results a report rests on, as the judge and a person read them."""
    lines = [f"Listing {record.offer_id}: {record.report.title}", "Check results:"]
    lines += [f"- {r['rule']} ({r['status']}): {r['summary']}" for r in record.results]
    return "\n".join(lines)


def reports_to_grade(split: str, system: str) -> list[Record]:
    records = read_records(records_path(split, system))
    return [r for r in records if r.repeat == 0 and not r.error and r.report.problems]


# --- The model as judge -----------------------------------------------------------


def judge_model(chat: BaseChatModel, name: str) -> Any:
    """The chat model in DeepEval's interface for judges."""
    from deepeval.models import DeepEvalBaseLLM

    class Judge(DeepEvalBaseLLM):
        def __init__(self) -> None:
            super().__init__(name)

        def load_model(self) -> BaseChatModel:
            return chat

        def generate(self, prompt: str, schema: Any = None) -> Any:
            if schema is None:
                return chat.invoke(prompt).content
            return structured(chat, schema).invoke(prompt)

        async def a_generate(self, prompt: str, schema: Any = None) -> Any:
            if schema is None:
                return (await chat.ainvoke(prompt)).content
            return await structured(chat, schema).ainvoke(prompt)

        def get_model_name(self) -> str:
            return name

        def supports_log_probs(self) -> bool:
            return False

    return Judge()


def metrics(judge: Any) -> dict[str, Any]:
    from deepeval.metrics import GEval
    from deepeval.metrics.g_eval import Rubric
    from deepeval.test_case import SingleTurnParams

    rubric = [
        Rubric(score_range=points, expected_outcome=f"Grade {grade}: {GRADES[grade]}")
        for points, grade in RUBRIC
    ]
    return {
        name: GEval(
            name=name,
            criteria=criteria,
            evaluation_steps=steps,
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
            rubric=rubric,
            model=judge,
            async_mode=True,
        )
        for name, (criteria, steps) in CRITERIA.items()
    }


async def judge_report(judge: Any, system: str, record: Record) -> dict[str, Any]:
    from deepeval.test_case import LLMTestCase

    case = LLMTestCase(input=evidence(record), actual_output=render(record.report))
    row: dict[str, Any] = {"system": system, "offer_id": record.offer_id}
    for name, metric in metrics(judge).items():
        score = await metric.a_measure(case, _show_indicator=False)
        row |= {
            name: to_grade(score),
            f"{name}_score": round(score, 3),
            f"{name}_reason": metric.reason,
        }
    return row


async def judge_all(
    split: str, systems: list[str], concurrency: int, chat: BaseChatModel, name: str
) -> None:
    path = folder(split) / "judge.jsonl"
    rows = read_json_lines(path)
    done = {(row["system"], row["offer_id"]) for row in rows}
    todo = [
        (system, record)
        for system in systems
        for record in reports_to_grade(split, system)
        if (system, record.offer_id) not in done
    ]
    print(f"{len(todo)} reports to judge with {name}", file=sys.stderr)
    judge, gate = judge_model(chat, name), asyncio.Semaphore(concurrency)

    async def one(system: str, record: Record) -> None:
        async with gate:
            try:
                row = await judge_report(judge, system, record)
            except Exception as error:  # judged again on the next run
                print(
                    f"{system} {record.offer_id}: {type(error).__name__}: {error}", file=sys.stderr
                )
                return
        rows.append(row)
        write_json_lines(path, sorted(rows, key=lambda r: (r["system"], r["offer_id"])))
        print(f"{system} {record.offer_id}: {row['clarity']} / {row['fairness']}", file=sys.stderr)

    await asyncio.gather(*(one(system, record) for system, record in todo))
    print(f"Saved to {path}", file=sys.stderr)


# --- A person's grades ------------------------------------------------------------


def ask_grade(question: str) -> int:
    while True:
        answer = input(f"{question} (1 to 5): ").strip()
        if answer in {"1", "2", "3", "4", "5"}:
            return int(answer)
        print("Type a whole number from 1 to 5.")


def grade_by_hand(split: str) -> None:
    path = folder(split) / "human_grades.csv"
    graded = set()
    if path.exists():
        graded = {row["offer_id"] for row in csv.DictReader(path.open(encoding="utf-8"))}
    reports = reports_to_grade(split, "agent")
    sample = random.Random(SAMPLE_SEED).sample(reports, min(HUMAN_SAMPLE, len(reports)))
    todo = [
        record
        for record in sorted(sample, key=lambda r: r.offer_id)
        if record.offer_id not in graded
    ]
    print("Grade each report from 1 to 5 for clarity and for fairness:")
    for grade, meaning in GRADES.items():
        print(f"  {grade}: {meaning}")
    for question, _ in CRITERIA.values():
        print(f"  {question}")
    with path.open("a", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, ["offer_id", "clarity", "fairness", "note"])
        if not graded:
            writer.writeheader()
        for number, record in enumerate(todo, len(graded) + 1):
            print(f"\n--- Report {number} of {len(sample)} " + "-" * 40)
            print(evidence(record), end="\n\n")
            print(render(record.report))
            row = {
                "offer_id": record.offer_id,
                "clarity": ask_grade("Clarity"),
                "fairness": ask_grade("Fairness"),
                "note": input("A note, if any (Enter to skip): ").strip(),
            }
            writer.writerow(row)
            out.flush()
    print(f"\nAll {len(sample)} reports are graded, in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade the reports' clarity and fairness.")
    parser.add_argument("--split", default="exam", choices=["exam", "practice"])
    parser.add_argument("--grade", action="store_true", help="grade 30 reports by hand")
    parser.add_argument("--systems", nargs="+", default=["agent", "one_prompt"])
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    if args.grade:
        grade_by_hand(args.split)
        return
    if settings.vision_api_key is None:
        parser.error("No language model is set up: add VISION_API_KEY to .env (see README).")
    if find_spec("deepeval") is None:
        command = f'uv run --with "{DEEPEVAL}" python -m listingaudit.eval.judge'
        parser.error(f"DeepEval is missing; run it in a layer of its own: {command}")
    name = settings.judge_model or settings.agent_model
    asyncio.run(judge_all(args.split, args.systems, args.concurrency, chat_model(name), name))


if __name__ == "__main__":
    main()
