"""What the agent's brain answers with, and the report a person reviews.

The brain fills in only what needs language: the kind of product, and for each
possible problem an explanation, a suggested fix and the passages of law to quote.
Code fills in the rest from the tool results: the rules, their severity, the
evidence and the articles. A draft that never passes the self-check is left out.
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
