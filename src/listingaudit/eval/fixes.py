"""Do the suggested fixes work? Apply each one to its listing and run the check again.

    uv run python -m listingaudit.eval.fixes

A language model reads each suggested fix and turns it into changes to the listing's
fields, taking every new value from the fix's own words. Information only the seller
has, such as an address, gets a sample value; a number the fix doesn't state fails
the fix. The rule's check then runs on the changed listing, and the fix works when it
finds no problem. Only real possible problems from the code checks are tried, as a new
photo can't be checked this way. Results go to results/<split>/fixes.jsonl.
"""

import argparse
import asyncio
import re
import sys
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from listingaudit.agent.brain import as_json, chat_model, structured
from listingaudit.agent.report import Problem
from listingaudit.agent.selfcheck import THOUSANDS, amounts
from listingaudit.checks.run import CODE_RULES, run_checks
from listingaudit.config import settings
from listingaudit.eval.records import (
    Listing,
    folder,
    read_answer_key,
    read_json_lines,
    read_listings,
    read_records,
    records_path,
    write_json_lines,
)
from listingaudit.market.models import Offer

FIX_FIELDS = [
    "price",
    "was_price",
    "was_price_kind",
    "discount_percent",
    "net_quantity",
    "unit_price",
    "unit_price_per",
    "manufacturer_name",
    "manufacturer_address",
    "manufacturer_country",
    "manufacturer_email",
    "manufacturer_website",
    "eu_rp_name",
    "eu_rp_address",
    "eu_rp_email",
    "safety_warnings",
    "fibre_composition",
    "energy_class",
    "energy_label_url",
    "product_sheet_url",
]
NUMBERS = {"price", "was_price", "discount_percent", "unit_price"}
SELLER = "SELLER"
# What a seller would fill in for information only they have
SAMPLES = {
    "manufacturer_name": "Example Manufacturing Ltd",
    "manufacturer_email": "contact@example.com",
    "manufacturer_website": "https://www.example.com",
    "eu_rp_name": "Example EU Representative GmbH",
    "eu_rp_address": "Musterstrasse 1, 10115 Berlin, Germany",
    "eu_rp_email": "eu-rep@example.com",
    "safety_warnings": "Keep out of reach of children. Read the instructions before use.",
    "fibre_composition": "100% cotton",
    "energy_class": "F",
    "energy_label_url": "https://www.example.com/energy-label.pdf",
    "product_sheet_url": "https://www.example.com/product-sheet.pdf",
}

INSTRUCTIONS = f"""\
You apply a suggested fix to a marketplace listing, as the seller would.

You get the listing's fields as JSON, a possible problem with the listing, and the fix
suggested for it. Answer with the changes to the fields that the fix asks for, and
nothing else:
- Take each new value from the fix's own words. Never work out a number yourself.
- When the fix asks for information only the seller has, such as an address, an email
  address or the product's warnings, set the value to {SELLER}.
- When the fix asks to remove something, set the value to null.
- When the fix asks for nothing that changes a field, answer with no changes."""


class Change(BaseModel):
    field: Literal[*FIX_FIELDS]
    value: str | None = Field(
        description=f"The new value as the fix gives it; {SELLER} for information only the "
        "seller has; null to remove the field"
    )


class Changes(BaseModel):
    changes: list[Change]


Outcome = Literal["pass", "fail", "vague", "invalid", "cannot_check"]


def sample(field: str, offer: Offer) -> str | None:
    """What a seller would fill in; None for a number, which the fix itself has to give."""
    if field == "manufacturer_address":
        return f"1 Example Street, Example City, {offer.manufacturer_country or 'Germany'}"
    return SAMPLES.get(field)


def as_number(value: str) -> Decimal:
    """A number as a fix writes it: '€1,310.99' -> 1310.99, '40,00' -> 40.00, '-20%' -> 20."""
    digits = THOUSANDS.sub("", re.sub(r"[^\d.,]", "", value))
    return Decimal(digits.replace(",", "."))


def apply(offer: Offer, changes: list[Change], fix: str) -> tuple[Offer | None, Outcome, str]:
    """The listing with the changes made, or why they can't be made."""
    data = offer.model_dump()
    stated = amounts(fix)
    for change in changes:
        value: Any = change.value
        if value == SELLER:
            value = sample(change.field, offer)
            if value is None:
                return None, "vague", f"The fix leaves {change.field} to the seller."
        elif value is not None and change.field in NUMBERS:
            try:
                number = as_number(value)
            except InvalidOperation:
                return None, "vague", f"{change.field} = {value!r} is not a number."
            if number not in stated:
                return None, "vague", f"The fix doesn't state {change.field} = {number}."
            value = int(number) if change.field == "discount_percent" else number
        data[change.field] = value
    # A crossed-out price and its badge go together, as do a unit price and its unit
    if data["was_price"] is None:
        data |= {"was_price_kind": None, "discount_percent": None}
    if data["unit_price"] is None:
        data["unit_price_per"] = None
    try:
        return Offer.model_validate(data), "pass", ""
    except ValidationError as error:
        return None, "invalid", str(error.errors()[0]["msg"])


def try_fix(listing: Listing, problem: Problem, changes: list[Change]) -> tuple[Outcome, str]:
    offer, history = listing
    fixed, outcome, detail = apply(offer, changes, problem.suggested_fix)
    if fixed is None:
        return outcome, detail
    result = run_checks(fixed, history, [problem.rule])[0]
    if result.status in ("ok", "not_applicable"):
        return "pass", result.summary
    return ("cannot_check" if result.status == "cannot_check" else "fail"), result.summary


async def changes_for(model: BaseChatModel, offer: Offer, problem: Problem) -> list[Change]:
    fields = offer.model_dump(mode="json", include=set(FIX_FIELDS))
    request = (
        f"The listing:\n{as_json(fields)}\n\nThe possible problem ({problem.name}):\n"
        f"{problem.explanation}\n\nThe suggested fix:\n{problem.suggested_fix}"
    )
    messages = [SystemMessage(INSTRUCTIONS), HumanMessage(request)]
    return (await structured(model, Changes).ainvoke(messages)).changes


async def main_async(split: str, concurrency: int) -> None:
    listings = read_listings(split)
    planted = {(p.offer_id, p.rule) for p in read_answer_key(split).problems}
    path = folder(split) / "fixes.jsonl"
    rows = [row for row in read_json_lines(path) if row["outcome"] != "error"]
    done = {(row["system"], row["offer_id"], row["rule"]) for row in rows}
    todo = [
        (system, record.offer_id, problem)
        for system in ("agent", "one_prompt")
        for record in read_records(records_path(split, system))
        if record.repeat == 0 and not record.error
        for problem in record.report.problems
        if problem.rule in CODE_RULES
        and (record.offer_id, problem.rule) in planted
        and (system, record.offer_id, problem.rule.value) not in done
    ]
    print(f"{len(todo)} fixes to try", file=sys.stderr)
    model, gate = chat_model(settings.agent_model), asyncio.Semaphore(concurrency)

    async def one(system: str, offer_id: str, problem: Problem) -> None:
        async with gate:
            try:
                changes = await changes_for(model, listings[offer_id][0], problem)
                outcome, detail = try_fix(listings[offer_id], problem, changes)
            except Exception as error:  # tried again on the next run
                changes, outcome, detail = [], "error", f"{type(error).__name__}: {error}"
        rows.append(
            {
                "system": system,
                "offer_id": offer_id,
                "rule": problem.rule.value,
                "fix": problem.suggested_fix,
                "changes": [change.model_dump() for change in changes],
                "outcome": outcome,
                "detail": detail[:300],
            }
        )
        write_json_lines(path, sorted(rows, key=lambda r: (r["system"], r["offer_id"], r["rule"])))
        print(f"{system} {offer_id} {problem.rule}: {outcome}", file=sys.stderr)

    await asyncio.gather(*(one(*job) for job in todo))
    print(f"Saved to {path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the suggested fixes and check again.")
    parser.add_argument("--split", default="exam", choices=["exam", "practice"])
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    if settings.vision_api_key is None:
        parser.error("No language model is set up: add VISION_API_KEY to .env (see README).")
    asyncio.run(main_async(args.split, args.concurrency))


if __name__ == "__main__":
    main()
