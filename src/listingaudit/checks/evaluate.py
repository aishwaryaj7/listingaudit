"""Score the code checks against the answer key.

    uv run python -m listingaudit.checks.evaluate

Each check runs on the practice listings its rule applies to, according to the
answer key, and the planted problems it catches or misses are counted. The checks
that work out for themselves whether their rule applies (the price check and the
two manufacturer checks) also run on every other listing; there, a "problem" is
a false alarm. The exam split is kept for grading the agent.
"""

import pandas as pd
import psycopg
from psycopg.rows import dict_row

from listingaudit.checks.run import CODE_RULES, load_listings, run_checks
from listingaudit.db import connect
from listingaudit.market.models import Case, Offer, PricePoint, Problem, Trap
from listingaudit.rulebook.checklist import load_checklist

# The findings that count as catching each planted problem
CAUGHT_BY = {
    "pumped_price": {"was_price_too_high"},
    "sneaky_percentage": {"badge_too_high"},
    "missing_manufacturer_info": {
        "manufacturer_name_missing",
        "manufacturer_address_missing",
        "manufacturer_contact_missing",
    },
    "missing_eu_responsible_person": {
        "responsible_person_missing",
        "responsible_person_name_missing",
        "responsible_person_address_missing",
        "responsible_person_outside_eu",
        "responsible_person_email_missing",
    },
    "missing_safety_warnings": {"safety_warnings_missing"},
    "bad_unit_price": {"unit_price_missing", "unit_price_wrong_unit", "unit_price_wrong"},
    "bad_fibre_composition": {
        "fibre_composition_missing",
        "not_an_eu_fibre_name",
        "percentage_missing",
        "percentages_dont_add_up",
        "not_largest_first",
    },
    "missing_energy_label": {
        "energy_class_missing",
        "energy_label_missing",
        "product_sheet_missing",
    },
}
# Rules that depend on the listing, not on the kind of product (checklist.yaml)
SELF_DECIDING = [rule for rule in CODE_RULES if rule in load_checklist().every_listing]
OUTCOMES = ["caught", "missed", "false alarm", "cannot check", "correct"]


def outcomes(
    listings: list[tuple[Offer, list[PricePoint]]], cases: list[Case], problems: list[Problem]
) -> pd.DataFrame:
    """One row per check run: what the check said, and what was planted."""
    applies = {case.offer_id: set(case.applicable_rules) for case in cases}
    planted = {(p.offer_id, p.rule): p for p in problems}
    rows = []
    for offer, history in listings:
        rules = [r for r in CODE_RULES if r in applies[offer.offer_id] or r in SELF_DECIDING]
        for result in run_checks(offer, history, rules):
            codes = {finding.code for finding in result.findings}
            planted_problem = planted.get((offer.offer_id, result.rule))
            if planted_problem:
                caught = codes & CAUGHT_BY[planted_problem.problem]
                outcome = "caught" if caught else "missed"
            elif result.status == "problem":
                outcome = "false alarm"
            elif result.status == "cannot_check":
                outcome = "cannot check"
            else:
                outcome = "correct"
            rows.append(
                {
                    "offer_id": offer.offer_id,
                    "rule": str(result.rule),
                    "rule_applies": result.rule in applies[offer.offer_id],
                    "status": result.status,
                    "findings": ", ".join(sorted(codes)),
                    "problem": planted_problem.problem if planted_problem else None,
                    "difficulty": planted_problem.difficulty if planted_problem else None,
                    "outcome": outcome,
                }
            )
    return pd.DataFrame(rows)


def by_rule(results: pd.DataFrame) -> pd.DataFrame:
    table = pd.crosstab(results["rule"], results["outcome"]).reindex(
        index=[str(rule) for rule in CODE_RULES], columns=OUTCOMES, fill_value=0
    )
    table.insert(0, "planted", table["caught"] + table["missed"])
    table.insert(0, "checks run", table[OUTCOMES].sum(axis=1))
    return table


def by_problem(results: pd.DataFrame) -> pd.DataFrame:
    """Planted problems caught, by problem and difficulty."""
    planted = results[results["problem"].notna()]
    return (
        planted.assign(caught=planted["outcome"].eq("caught"))
        .groupby(["problem", "difficulty"])["caught"]
        .agg(planted="count", caught="sum")
        .reset_index()
    )


def by_trap(results: pd.DataFrame, traps: list[Trap]) -> pd.DataFrame:
    """For each trap, how often a code check ran on its rule and raised a false alarm.

    Traps about which rules apply (a lamp needs no energy label) never reach a check:
    the rule is chosen before the check runs.
    """
    on_traps = pd.DataFrame(
        [{"offer_id": t.offer_id, "rule": str(t.rule), "trap": t.trap} for t in traps]
    ).merge(results, on=["offer_id", "rule"], how="left")
    table = on_traps.groupby("trap").agg(
        listings=("offer_id", "count"),
        checked=("outcome", "count"),  # count() skips listings where no check ran
        false_alarms=("outcome", lambda outcome: int(outcome.eq("false alarm").sum())),
    )
    return table.rename(columns={"checked": "checked by code", "false_alarms": "false alarms"})


def load_answer_key(
    conn: psycopg.Connection, split: str
) -> tuple[list[Case], list[Problem], list[Trap]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cases = cur.execute("SELECT * FROM answer_key.cases WHERE split = %s", (split,))
        cases = [Case(**row) for row in cases]
        ids = [case.offer_id for case in cases]
        problems = cur.execute("SELECT * FROM answer_key.problems WHERE offer_id = ANY(%s)", (ids,))
        problems = [Problem(**row) for row in problems]
        traps = cur.execute("SELECT * FROM answer_key.traps WHERE offer_id = ANY(%s)", (ids,))
        traps = [Trap(**row) for row in traps]
    return cases, problems, traps


def main() -> None:
    with connect() as conn:
        listings = load_listings(conn, split="practice")
        cases, problems, traps = load_answer_key(conn, "practice")
    results = outcomes(listings, cases, problems)

    print(f"{len(listings)} practice listings, {len(results)} checks run\n")
    print(by_rule(results).to_string(), end="\n\n")
    caught = by_problem(results)
    caught["caught"] = caught["caught"].astype(str) + " of " + caught["planted"].astype(str)
    print("Planted problems caught:")
    print(caught.pivot(index="problem", columns="difficulty", values="caught").to_string())
    print()
    print(by_trap(results, traps).to_string())


if __name__ == "__main__":
    main()
