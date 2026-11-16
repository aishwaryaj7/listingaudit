"""The self-check: plain code that tests the brain's draft against the tool results.

A draft passes when
- every check that found a problem has exactly one finding, and no other check has one,
  so seller text can't talk the brain out of a finding;
- every quote is a passage the rulebook search really returned;
- every price and percentage in its text is one the check worked out, since code does
  all the maths;
- it never calls a listing illegal: a report names possible problems, and only a court
  or an authority can decide more.

Each failure becomes a line of feedback for the brain's next try.
"""

import json
import re
from decimal import Decimal

from listingaudit.agent.report import ReportDraft
from listingaudit.checks.results import CheckResult

# A price or a percentage: "€19.99", "19,99 €", "42%", "25.0 %", or an amount with cents
AMOUNT = re.compile(
    r"€\s?(\d+(?:[.,]\d+)?)"
    r"|(\d+(?:[.,]\d+)?)\s?(?:%|€|EUR\b|euros?\b|per ?cent\b)"
    r"|\b(\d+[.,]\d{2})\b",
    re.IGNORECASE,
)
# The comma of "€1,310.99", left out before reading an amount
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
VERDICT = re.compile(r"\b(?:illegal|unlawful|violat|infring)\w*", re.IGNORECASE)


def amounts(text: str) -> set[Decimal]:
    """The prices and percentages a sentence states."""
    found = set()
    for match in AMOUNT.finditer(THOUSANDS.sub("", text)):
        raw = next(group for group in match.groups() if group)
        found.add(Decimal(raw.replace(",", ".")))
    return found


def known_numbers(*results: CheckResult) -> set[Decimal]:
    """Every number in the check results, also rounded the way a sentence might round it."""
    numbers = set()
    for result in results:
        text = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        for raw in NUMBER.findall(text):
            value = Decimal(raw)
            numbers.update(round(value, places) for places in (0, 1, 2))
            numbers.add(value)
    return numbers


def check_draft(
    draft: ReportDraft, results: list[CheckResult], passages: dict[str, dict]
) -> list[str]:
    """What is wrong with a draft, as feedback for the brain; empty when it passes."""
    feedback = []
    by_rule = {result.rule: result for result in results}
    problems = [result.rule for result in results if result.status == "problem"]
    drafted = [finding.rule for finding in draft.findings]

    for rule in problems:
        if rule not in drafted:
            feedback.append(
                f"The {rule} check found a problem, but the draft has no finding for it: "
                f"{by_rule[rule].summary}"
            )
    for rule in dict.fromkeys(drafted):
        if rule not in problems:
            feedback.append(
                f"The draft has a finding for {rule}, but its check found no problem. Leave it out."
            )
        elif drafted.count(rule) > 1:
            feedback.append(f"The draft has {drafted.count(rule)} findings for {rule}; write one.")

    for finding in draft.findings:
        if not finding.explanation.strip() or not finding.suggested_fix.strip():
            feedback.append(f"The finding for {finding.rule} needs an explanation and a fix.")
        for chunk_id in finding.quotes:
            if chunk_id not in passages:
                feedback.append(
                    f"The finding for {finding.rule} quotes {chunk_id!r}, which the rulebook "
                    "search didn't return. Quote only passages from the search results."
                )
        if finding.rule in problems:
            text = f"{finding.explanation} {finding.suggested_fix}"
            for amount in sorted(amounts(text) - known_numbers(by_rule[finding.rule])):
                feedback.append(
                    f"The finding for {finding.rule} states {amount}, a number its check "
                    "didn't give. Use only the check's own prices and percentages."
                )

    for amount in sorted(amounts(draft.summary) - known_numbers(*results)):
        feedback.append(f"The summary states {amount}, a number no check gave.")

    texts = [draft.summary] + [f"{f.explanation} {f.suggested_fix}" for f in draft.findings]
    for word in dict.fromkeys(m.group() for text in texts for m in VERDICT.finditer(text)):
        feedback.append(
            f'The draft says "{word}". Write "possible problem" instead: only a court or an '
            "authority can decide that a listing breaks the law."
        )
    return feedback
