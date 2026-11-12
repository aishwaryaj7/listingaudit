"""What a listing check returns, and small helpers the checks share.

Every check answers with one of four statuses. A "problem" always comes with at
least one finding, and every finding carries the facts that show it, so a report
can't state a problem without its evidence.
"""

from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from listingaudit.market.rules import Rule
from listingaudit.rulebook.checklist import load_checklist
from listingaudit.rulebook.chunk import citation
from listingaudit.rulebook.sources import SOURCES_BY_ID

Status = Literal["ok", "problem", "not_applicable", "cannot_check"]

CENT = Decimal("0.01")
# What sellers put in a field instead of leaving it empty
BLANK = {"", "-", "n/a", "na", "none", "unknown", "not available"}


class Finding(BaseModel):
    """One possible problem, in plain words, with the facts behind it."""

    code: str  # stable id for the kind of finding, e.g. "was_price_too_high"
    message: str
    facts: dict[str, Any] = Field(default_factory=dict)


class CheckResult(BaseModel):
    rule: Rule
    status: Status
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)  # what the check read and worked out
    law: list[str] = Field(default_factory=list)  # e.g. ["GPSR Art. 19(a)"]

    @model_validator(mode="after")
    def problems_have_findings(self) -> "CheckResult":
        if (self.status == "problem") != bool(self.findings):
            raise ValueError("a problem needs findings, and only a problem has them")
        return self


@cache
def citations(rule: Rule) -> tuple[str, ...]:
    """The articles the checklist gives for a rule, written as a report cites them."""
    return tuple(
        citation(SOURCES_BY_ID[ref.law], ref.article, ref.paragraph)
        + (f"({ref.point})" if ref.point else "")
        for ref in load_checklist().rules[rule].law
    )


def _result(
    rule: Rule, status: Status, summary: str, findings: list[Finding], facts: dict[str, Any]
) -> CheckResult:
    return CheckResult(
        rule=rule,
        status=status,
        summary=summary,
        findings=findings,
        facts=facts,
        law=list(citations(rule)),
    )


def ok(rule: Rule, summary: str, **facts: Any) -> CheckResult:
    return _result(rule, "ok", summary, [], facts)


def problem(rule: Rule, findings: list[Finding], **facts: Any) -> CheckResult:
    return _result(rule, "problem", " ".join(f.message for f in findings), findings, facts)


def not_applicable(rule: Rule, reason: str, **facts: Any) -> CheckResult:
    return _result(rule, "not_applicable", reason, [], facts)


def cannot_check(rule: Rule, reason: str, **facts: Any) -> CheckResult:
    return _result(rule, "cannot_check", reason, [], facts)


def is_missing(value: str | None) -> bool:
    return value is None or value.strip().lower() in BLANK


def euro(amount: Decimal) -> str:
    """Decimal('39.989') -> '€39.99' (rounded half up, as shops round)."""
    return f"€{amount.quantize(CENT, ROUND_HALF_UP)}"
