"""Load and validate the rules checklist (checklist.yaml).

The checklist tells the agent which rules apply to a listing, what to look for
and which articles to cite. Validation is strict, so a typo in a rule id fails
loudly instead of silently skipping a rule.
"""

from functools import cache
from importlib.resources import files
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from listingaudit.market.rules import Rule

Condition = Literal[
    "always",
    "announces_price_reduction",
    "not_food",
    "not_food_and_maker_outside_eu",
]


class Strict(BaseModel):
    # Unknown keys are errors: "chekcs:" should not be silently ignored
    model_config = ConfigDict(extra="forbid", frozen=True)


class LawRef(Strict):
    """A pointer into the rulebook, such as GPSR Art. 19(b)."""

    law: str  # a source id from rulebook/sources.py
    article: str  # "19", "6a", or "11" for PAngV § 11
    paragraph: str | None = None
    point: str | None = None


class RuleSpec(Strict):
    name: str
    summary: str
    checks: list[str]
    not_a_problem: list[str]
    fields: list[str]  # the listing fields that hold the evidence
    checked_by: Literal["code", "image_model"]
    severity: Literal["high", "medium"]
    law: list[LawRef]


class KindSpec(Strict):
    description: str
    rules: list[Rule]


class Checklist(Strict):
    rules: dict[Rule, RuleSpec]
    product_kinds: dict[str, KindSpec]
    product_types: dict[str, list[Rule]]
    every_listing: dict[Rule, Condition]
    food_product_types: list[str]

    @model_validator(mode="after")
    def every_rule_is_described(self) -> "Checklist":
        missing = set(Rule) - set(self.rules)
        if missing:
            raise ValueError(f"rules without a description: {sorted(missing)}")
        return self

    def rules_for(
        self, kind: str, product_type: str, announces_reduction: bool, maker_in_eu: bool
    ) -> list[Rule]:
        """The rules a listing has to follow, in the order of `Rule`."""
        if kind not in self.product_kinds:
            raise ValueError(f"unknown product kind: {kind!r}")
        food = product_type in self.food_product_types
        holds = {
            "always": True,
            "announces_price_reduction": announces_reduction,
            "not_food": not food,
            "not_food_and_maker_outside_eu": not food and not maker_in_eu,
        }
        applies = set(self.product_kinds[kind].rules)
        applies |= set(self.product_types.get(product_type, []))
        applies |= {rule for rule, when in self.every_listing.items() if holds[when]}
        return [rule for rule in Rule if rule in applies]


@cache
def load_checklist() -> Checklist:
    text = files("listingaudit.rulebook").joinpath("checklist.yaml").read_text(encoding="utf-8")
    return Checklist.model_validate(yaml.safe_load(text))
