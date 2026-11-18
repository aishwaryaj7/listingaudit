"""Grade the systems' audits against the answer key, and check the gates.

    uv run python -m listingaudit.eval.grade
    uv run python -m listingaudit.eval.grade --check

Reads what the evaluation saved under results/<split>/ and needs neither the database
nor a language model, so CI runs it on every push. --check fails when a gate fails.
"""

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from listingaudit.agent.report import CHECKLIST, Report
from listingaudit.agent.selfcheck import NUMBER, VERDICT, amounts, known_numbers
from listingaudit.checks.results import CheckResult
from listingaudit.eval.baselines import SHOWN_FIELDS
from listingaudit.eval.records import (
    SYSTEMS,
    AnswerKey,
    Listing,
    Record,
    article_key,
    folder,
    read_answer_key,
    read_json_lines,
    read_listings,
    read_records,
    records_path,
)
from listingaudit.market.rules import Rule

GROUPS = {
    "prices": {"pumped_price", "sneaky_percentage", "bad_unit_price"},
    "labels and missing information": {
        "missing_manufacturer_info",
        "missing_eu_responsible_person",
        "missing_safety_warnings",
        "missing_energy_label",
        "bad_fibre_composition",
    },
    "photos": {"photo_mismatch"},
}
# The photo model's cost per photo, at gpt-5-mini's list price, from the average tokens
# per photo in the photo check's evaluation (423 in, 208 out)
PHOTO_MODEL_COST = (423 * 0.25 + 208 * 2.00) / 1e6

Ratio = tuple[int, int]  # how many of how many


@dataclass
class Scores:
    """What the grader measured for one system, on the main run of each listing."""

    system: str
    audited: int
    failed: int
    caught: dict[str, Ratio] = field(default_factory=dict)  # by group of problems
    by_problem: dict[str, Ratio] = field(default_factory=dict)  # "pumped_price, hard"
    clean_false_alarms: Ratio = (0, 0)  # clean listings with a false alarm
    tricky_false_alarms: Ratio = (0, 0)
    by_trap: dict[str, Ratio] = field(default_factory=dict)  # trap rule flagged
    precision: Ratio = (0, 0)  # real possible problems of all flagged
    right_rules: Ratio | None = None
    right_law: Ratio | None = None
    real_numbers: Ratio = (0, 0)  # reports with only real numbers
    made_up: list[str] = field(default_factory=list)  # "E0012: 12.34"
    verdicts: list[str] = field(default_factory=list)  # "E0012: illegal"
    fixes: Ratio | None = None
    judge: dict[str, float] | None = None  # mean grade, 1 to 5, by criterion
    agreement: Ratio | None = None  # judge within one point of a person
    sneaky: Ratio = (0, 0)  # sneaky-note listings with every problem reported
    consistent: Ratio | None = None  # listings with the same findings in every run
    usd_per_1000: float | None = None
    minutes_per_1000: float | None = None


def flagged(record: Record) -> set[Rule]:
    return {problem.rule for problem in record.report.problems}


def model_text(report: Report) -> list[str]:
    """The text a system wrote itself: the summary, and each explanation and fix."""
    return [report.summary] + [f"{p.explanation} {p.suggested_fix}" for p in report.problems]


def known(record: Record, listing: Listing) -> set[Decimal]:
    """The numbers a report may state: the checks' and the listing's own."""
    offer, history = listing
    numbers = known_numbers(*(CheckResult.model_validate(r) for r in record.results))
    texts = [offer.model_dump_json(include=SHOWN_FIELDS)]
    if record.system == "one_prompt":  # it was given the price history too
        texts += [str(point.price) for point in history]
    for raw in NUMBER.findall(" ".join(texts)):
        value = Decimal(raw)
        numbers.update({value, *(round(value, places) for places in (0, 1, 2))})
    return numbers


def law_of(rule: Rule) -> set[tuple[str, str]]:
    return {(ref.law, article_key(ref.article)) for ref in CHECKLIST.rules[rule].law}


def right_law(record: Record, rule: Rule) -> bool:
    """A finding cites the rule's own law and article, and nothing else."""
    cited = {tuple(source) for source in record.cited.get(rule, [])}
    return bool(cited) and cited <= law_of(rule)


def ratio(found: list[bool]) -> Ratio:
    return sum(found), len(found)


def grade(
    system: str,
    records: list[Record],
    key: AnswerKey,
    listings: dict[str, Listing],
    extras: dict[str, list[dict]],
) -> Scores:
    main = {r.offer_id: r for r in records if r.repeat == 0 and not r.error}
    failed = sum(1 for r in records if r.repeat == 0 and r.error)
    scores = Scores(system, audited=len(main), failed=failed)
    cases = {case.offer_id: case for case in key.cases if case.offer_id in main}
    planted = defaultdict(dict)
    for problem in key.problems:
        planted[problem.offer_id][problem.rule] = problem

    caught = defaultdict(list)
    for offer_id, problems in planted.items():
        if offer_id not in main:
            continue
        for rule, problem in problems.items():
            hit = rule in flagged(main[offer_id])
            group = next(name for name, members in GROUPS.items() if problem.problem in members)
            caught[group].append(hit)
            caught[f"{problem.problem}, {problem.difficulty}"].append(hit)
    scores.caught = {group: ratio(caught[group]) for group in GROUPS}
    scores.by_problem = {name: ratio(hits) for name, hits in caught.items() if "," in name}

    false_alarms = {
        offer_id: flagged(record) - set(planted[offer_id]) for offer_id, record in main.items()
    }
    for kind in ("clean", "tricky"):
        found = [bool(false_alarms[o]) for o, case in cases.items() if case.kind == kind]
        setattr(scores, f"{kind}_false_alarms", ratio(found))
    traps = defaultdict(list)
    for trap in key.traps:
        if trap.offer_id in main:
            traps[trap.trap].append(trap.rule in flagged(main[trap.offer_id]))
    scores.by_trap = {name: ratio(hits) for name, hits in sorted(traps.items())}
    flags = [rule in planted[o] for o, record in main.items() for rule in flagged(record)]
    scores.precision = ratio(flags)

    if system != "one_prompt":
        scores.right_rules = ratio(
            [set(record.rules) == set(cases[o].applicable_rules) for o, record in main.items()]
        )
    if system != "rules_only":
        scores.right_law = ratio(
            [right_law(record, rule) for record in main.values() for rule in flagged(record)]
        )

    clean_reports = []
    for offer_id, record in main.items():
        allowed = known(record, listings[offer_id])
        stated = set().union(*(amounts(text) for text in model_text(record.report)))
        extra = sorted(stated - allowed)
        scores.made_up += [f"{offer_id}: {number}" for number in extra]
        clean_reports.append(not extra)
        words = {m.group() for t in model_text(record.report) for m in VERDICT.finditer(t)}
        scores.verdicts += [f"{offer_id}: {word}" for word in sorted(words)]
    scores.real_numbers = ratio(clean_reports)

    sneaky = [o for o, case in cases.items() if case.sneaky_note]
    scores.sneaky = ratio([set(planted[o]) <= flagged(main[o]) for o in sneaky])

    fixes = [row for row in extras["fixes"] if row["system"] == system]
    if fixes:
        scores.fixes = ratio(
            [row["outcome"] == "pass" for row in fixes if row["outcome"] != "cannot_check"]
        )
    judged = [row for row in extras["judge"] if row["system"] == system]
    if judged:
        scores.judge = {
            criterion: round(sum(row[criterion] for row in judged) / len(judged), 2)
            for criterion in ("clarity", "fairness")
        }
        people = {row["offer_id"]: row for row in extras["human"]}
        pairs = [
            abs(row[criterion] - int(people[row["offer_id"]][criterion])) <= 1
            for row in judged
            if row["offer_id"] in people
            for criterion in ("clarity", "fairness")
        ]
        scores.agreement = ratio(pairs) if pairs else None

    runs = defaultdict(list)
    for record in records:
        if not record.error:
            runs[record.offer_id].append(flagged(record))
    repeated = [found for found in runs.values() if len(found) > 1]
    if repeated:
        scores.consistent = ratio([all(f == found[0] for f in found) for found in repeated])

    if main:
        costs = [record.cost for record in main.values()]
        if None not in costs:
            photo_calls = sum(
                1
                for record in main.values()
                for result in record.results
                if result.get("facts", {}).get("decided_by") == "vision"
            )
            total = sum(costs) + photo_calls * PHOTO_MODEL_COST
            scores.usd_per_1000 = round(total / len(main) * 1000, 2)
        seconds = sum(record.seconds for record in main.values())
        scores.minutes_per_1000 = round(seconds / len(main) * 1000 / 60, 1)
    return scores


# --- Tables ----------------------------------------------------------------------


def percent(value: Ratio | None) -> str:
    if value is None:
        return "n/a"
    hits, total = value
    return f"{hits / total:.0%} ({hits}/{total})" if total else "none"


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows])


def met(value: Ratio | None, target: float, below: bool = False) -> str:
    if value is None or not value[1]:
        return ""
    share = value[0] / value[1]
    return "yes" if (share < target if below else share >= target) else "no"


def headline(scores: dict[str, Scores]) -> str:
    """The results table: one row for each question the evaluation answers."""
    names = [s for s in SYSTEMS if s in scores]
    agent = scores.get("agent")

    def row(question: str, value, target: str, check: str = "") -> list[str]:
        cells = [value(scores[name]) for name in names]
        return [question, *cells, target, check]

    def judged(s: Scores) -> str:
        if not s.judge:
            return "n/a" if s.system == "rules_only" else "not run"
        return f"{s.judge['clarity']} / {s.judge['fairness']}"

    def money(s: Scores) -> str:
        return "n/a" if s.usd_per_1000 is None else f"${s.usd_per_1000:.2f}"

    def minutes(s: Scores) -> str:
        return "n/a" if s.minutes_per_1000 is None else f"{s.minutes_per_1000:.0f}"

    def caught(group: str):
        return lambda s: percent(s.caught.get(group))

    a = agent or Scores("agent", 0, 0)
    rows = [
        row("Catches price problems", caught("prices"), "90%", met(a.caught.get("prices"), 0.9)),
        row(
            "Catches missing information and label problems",
            caught("labels and missing information"),
            "90%",
            met(a.caught.get("labels and missing information"), 0.9),
        ),
        row("Catches photo swaps", caught("photos"), "70%", met(a.caught.get("photos"), 0.7)),
        row(
            "Clean listings with a false alarm",
            lambda s: percent(s.clean_false_alarms),
            "under 5%",
            met(a.clean_false_alarms, 0.05, below=True),
        ),
        row(
            "Tricky but legal listings with a false alarm",
            lambda s: percent(s.tricky_false_alarms),
            "",
            "",
        ),
        row("Possible problems that are real (precision)", lambda s: percent(s.precision), "", ""),
        row(
            "Listings checked against exactly the right rules",
            lambda s: percent(s.right_rules),
            "90%",
            met(a.right_rules, 0.9),
        ),
        row(
            "Findings quoting the rule's own law and article",
            lambda s: percent(s.right_law),
            "95%",
            met(a.right_law, 0.95),
        ),
        row(
            "Reports with only real numbers",
            lambda s: percent(s.real_numbers),
            "100%",
            met(a.real_numbers, 1.0),
        ),
        row(
            "Fixes that pass the check once applied",
            lambda s: percent(s.fixes),
            "95%",
            met(a.fixes, 0.95),
        ),
        row("Clarity / fairness, judged by a model (1 to 5)", judged, "", ""),
        row(
            "Judge within one point of a person's grade",
            lambda s: percent(s.agreement),
            "most",
            met(a.agreement, 0.5),
        ),
        row(
            "Sneaky-note listings with every problem still found",
            lambda s: percent(s.sneaky),
            "100%",
            met(a.sneaky, 1.0),
        ),
        row(
            "Listings with the same findings in every run",
            lambda s: percent(s.consistent),
            "most",
            met(a.consistent, 0.5),
        ),
        row("Cost per 1,000 listings (USD, list price)", money, "", ""),
        row("Minutes per 1,000 listings, one at a time", minutes, "", ""),
    ]
    header = ["Question", *(n.replace("_", " ") for n in names), "Target", "Agent meets it"]
    return table(header, rows)


def details(scores: dict[str, Scores]) -> str:
    names = [s for s in SYSTEMS if s in scores]
    problems = sorted({p for s in scores.values() for p in s.by_problem})
    traps = sorted({t for s in scores.values() for t in s.by_trap})
    caught = table(
        ["Planted problem, difficulty", *names],
        [[p, *(percent(scores[n].by_problem.get(p)) for n in names)] for p in problems],
    )
    tricked = table(
        ["Trap (legal), flagged as a problem", *names],
        [[t, *(percent(scores[n].by_trap.get(t)) for n in names)] for t in traps],
    )
    return f"Planted problems caught\n\n{caught}\n\nFalse alarms on tricky listings\n\n{tricked}"


def gates(scores: dict[str, Scores], listings: int) -> list[str]:
    """What must hold for the build to pass: integrity, not quality targets."""
    agent = scores.get("agent")
    if agent is None:
        return ["No agent results: run python -m listingaudit.eval.run agent"]
    failed = []
    if agent.audited < listings:
        failed.append(f"The agent audited {agent.audited} of {listings} listings.")
    failed += [f"Made-up number in a report: {item}" for item in agent.made_up]
    failed += [f"A report calls a listing {item}" for item in agent.verdicts]
    return failed


def load(split: str) -> tuple[dict[str, Scores], int]:
    key = read_answer_key(split)
    listings = read_listings(split)
    human = folder(split) / "human_grades.csv"
    extras = {
        "fixes": read_json_lines(folder(split) / "fixes.jsonl"),
        "judge": read_json_lines(folder(split) / "judge.jsonl"),
        "human": list(csv.DictReader(human.open(encoding="utf-8"))) if human.exists() else [],
    }
    scores = {}
    for system in SYSTEMS:
        records = read_records(records_path(split, system))
        if records:
            scores[system] = grade(system, records, key, listings, extras)
    return scores, len(key.cases)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade the audits against the answer key.")
    parser.add_argument("--split", default="exam", choices=["exam", "practice"])
    parser.add_argument("--check", action="store_true", help="fail when a gate fails")
    args = parser.parse_args()
    if not (folder(args.split) / "answer_key.json").exists():
        parser.error(
            f"results/{args.split} holds no runs yet: run python -m listingaudit.eval.run first."
        )

    scores, listings = load(args.split)
    print(headline(scores), end="\n\n")
    print(details(scores))

    if args.check:
        failed = gates(scores, listings)
        for line in failed:
            print(f"Gate failed: {line}", file=sys.stderr)
        if failed:
            sys.exit(1)
        print("\nAll gates pass.", file=sys.stderr)


if __name__ == "__main__":
    main()
