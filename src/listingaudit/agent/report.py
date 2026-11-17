"""What the agent's brain answers with, the report a person reviews, and their review.

The brain fills in only what needs language: the kind of product, and for each
possible problem an explanation, a suggested fix and the passages of law to quote.
Code fills in the rest from the tool results: the rules, their severity, the
evidence and the articles. A draft that never passes the self-check is left out.

A person then approves or rejects each possible problem. The approved ones, with their
fixes, make a draft message to the seller, which is saved for a person to send.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from listingaudit.market.rules import Rule
from listingaudit.rulebook.checklist import load_checklist

CHECKLIST = load_checklist()
Kind = Literal[*CHECKLIST.product_kinds]


class KindChoice(BaseModel):
    """The kind of product a listing is, as the brain judges it."""

    kind: Kind
    reason: str = Field(description="One short sentence on what in the listing shows the kind")


class FindingDraft(BaseModel):
    rule: Rule = Field(description="The rule whose check found the problem")
    explanation: str = Field(
        description="What is wrong, in one to three plain sentences for a reviewer, with the "
        "check's own prices and percentages"
    )
    suggested_fix: str = Field(description="What the seller could change, in one or two sentences")
    quotes: list[str] = Field(
        description="The chunk_id of each rulebook passage that backs the finding"
    )


class ReportDraft(BaseModel):
    """The brain's draft of a report, before the self-check."""

    summary: str = Field(
        description="One or two sentences for the reviewer: how many possible problems, and "
        "the most serious one"
    )
    findings: list[FindingDraft] = Field(
        description="One finding for each check that found a problem"
    )


class Quote(BaseModel):
    chunk_id: str
    citation: str
    text: str
    url: str


class Problem(BaseModel):
    rule: Rule
    name: str
    severity: Literal["high", "medium"]
    explanation: str
    evidence: list[str]  # what the check found, in its own words
    facts: dict[str, Any]
    law: list[str]
    quotes: list[Quote]
    suggested_fix: str


class Note(BaseModel):
    """A rule that passed, doesn't apply, or needs a person to look."""

    rule: Rule
    name: str
    status: str
    summary: str


class Report(BaseModel):
    offer_id: str
    title: str
    kind: str
    summary: str
    problems: list[Problem]
    to_check: list[Note]
    passed: list[Note]
    # What the self-check found wrong with the last draft, which is then left out
    unresolved: list[str]
    status: Literal["awaiting_review"] = "awaiting_review"


def counted(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def short(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render(report: Report) -> str:
    """The report as Markdown, in the layout of the toolbox's audit prompt."""
    lines = [f"# Audit of {report.offer_id}: {report.title}", "", report.summary, ""]
    lines += [f"Kind of product, as the agent judged it: {report.kind}", ""]
    if report.unresolved:
        lines += [
            "**The model's draft failed the self-check, so the findings below are in the "
            "checks' own words:**",
            "",
        ]
        lines += [f"- {issue}" for issue in report.unresolved] + [""]

    lines += ["## Possible problems", ""]
    if not report.problems:
        lines += ["None found.", ""]
    for problem in report.problems:
        facts = "; ".join(f"{key}: {value}" for key, value in problem.facts.items() if value)
        lines += [
            f"### {problem.name} ({problem.severity})",
            "",
            f"- What is wrong: {problem.explanation}",
            f"- Evidence: {' '.join(problem.evidence)} ({facts})",
            f"- Law: possible problem under {'; '.join(problem.law)}",
        ]
        lines += [f'- Quote: "{short(q.text)}" ({q.citation}, {q.url})' for q in problem.quotes]
        if problem.suggested_fix:
            lines.append(f"- Suggested fix: {problem.suggested_fix}")
        lines.append("")

    lines += ["## For a person to check", ""]
    lines += [f"- {note.name}: {note.summary}" for note in report.to_check] or ["Nothing."]
    lines += ["", "## Passed", ""]
    lines += [f"- {note.name}: {note.summary}" for note in report.passed] or ["Nothing."]
    return "\n".join(lines) + "\n"


# --- A person's review ----------------------------------------------------------


class Decision(BaseModel):
    """A person's decision on one possible problem."""

    rule: Rule
    approved: bool
    reason: str = Field(default="", description="Why the finding is rejected")
    fix: str = Field(
        default="",
        description="The fix to send the seller, when the reviewer rewords the suggested one",
    )


class Review(BaseModel):
    """A person's decision on a report: one decision for each possible problem."""

    reviewer: str = Field(min_length=1, description="Who decided")
    decisions: list[Decision] = Field(default_factory=list)
    note: str = Field(default="", description="Anything else the reviewer noticed")


def review_errors(report: Report, review: Review) -> list[str]:
    """What a review still needs before it can be saved; empty when it is complete."""
    problems = {problem.rule: problem for problem in report.problems}
    decided = [decision.rule for decision in review.decisions]
    errors = [
        f"Approve or reject the finding on {problem.name}."
        for rule, problem in problems.items()
        if rule not in decided
    ]
    for rule in dict.fromkeys(decided):
        if rule not in problems:
            errors.append(f"The report has no finding on {rule}.")
        elif decided.count(rule) > 1:
            errors.append(f"Decide only once on {problems[rule].name}.")
    for decision in review.decisions:
        problem = problems.get(decision.rule)
        if problem is None:
            continue
        if not decision.approved and not decision.reason.strip():
            errors.append(f"Say why the finding on {problem.name} is rejected.")
        if decision.approved and not (decision.fix.strip() or problem.suggested_fix):
            errors.append(f"Write the fix for the seller on {problem.name}: the report has none.")
    return list(dict.fromkeys(errors))


class SellerMessage(BaseModel):
    """A draft message to the seller, saved for a person to send. Nothing sends it."""

    offer_id: str
    seller_id: int
    subject: str
    body: str


def seller_message(report: Report, review: Review, seller_id: int) -> SellerMessage | None:
    """The message to the seller about the approved findings; None when none was approved.

    It is made only of checked text: each explanation passed the self-check or is the
    check's own, and a person approved it and its fix.
    """
    decisions = {decision.rule: decision for decision in review.decisions}
    approved = [
        problem
        for problem in report.problems
        if problem.rule in decisions and decisions[problem.rule].approved
    ]
    if not approved:
        return None
    found = counted(len(approved), "possible problem")
    lines = [
        "Hello,",
        "",
        f'We have checked your listing {report.offer_id}, "{report.title}", and found {found} '
        "with the EU rules for selling to consumers. Please look at "
        f"{'it' if len(approved) == 1 else 'them'} and change the listing where needed.",
        "",
    ]
    for number, problem in enumerate(approved, 1):
        fix = decisions[problem.rule].fix.strip() or problem.suggested_fix
        lines += [
            f"{number}. {problem.name}",
            problem.explanation,
            f"What to change: {fix}",
            f"Where the rule comes from: {'; '.join(problem.law)}",
            "",
        ]
    lines += [
        "If you think we got something wrong, reply to this message and tell us why.",
        "",
        "Kind regards,",
        review.reviewer,
    ]
    return SellerMessage(
        offer_id=report.offer_id,
        seller_id=seller_id,
        subject=f"Your listing {report.offer_id}: {found} to look at",
        body="\n".join(lines),
    )
