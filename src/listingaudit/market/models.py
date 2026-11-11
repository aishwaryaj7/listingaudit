"""Pydantic models for the generated data.

They check that the data is well formed, not that it follows the rules. A fake
discount is fine here (those are planted on purpose); a 'was' price below the
current price, or a discount of 140%, is a generator bug and stops the build.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from listingaudit.market.rules import Rule

Split = Literal["practice", "exam"]
Money = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]

ProblemType = Literal[
    "pumped_price",
    "sneaky_percentage",
    "missing_manufacturer_info",
    "missing_eu_responsible_person",
    "missing_safety_warnings",
    "bad_unit_price",
    "bad_fibre_composition",
    "missing_energy_label",
    "photo_mismatch",
]
TrapType = Literal[
    "long_price_rise",
    "sale_started_earlier",
    "rrp_comparison",
    "unannounced_price_drop",
    "eu_maker_no_rp_needed",
    "exact_kilo_or_litre",
    "mixed_set",
    "sold_by_piece",
    "lamp_no_energy_label",
    "not_a_textile",
]


def money(cents: int | None) -> Decimal | None:
    """Whole cents -> Decimal euros with exactly 2 places.

    3999 -> Decimal('39.99'), 70 -> Decimal('0.70') (dividing by 100 would give 0.7).
    """
    return None if cents is None else Decimal(cents).scaleb(-2)


class Offer(BaseModel):
    """One marketplace listing: everything the agent may see."""

    offer_id: str = Field(pattern=r"^[PE]\d{4}$")
    split: Split
    seller_id: int
    product_type: str
    title: str
    brand: str | None
    color: str | None
    bullet_points: list[str]
    description: str | None
    image_id: str
    image_file: str  # the photo, relative to the data folder

    # Price box
    price: Money
    was_price: Money | None = None  # the crossed-out price
    was_price_kind: Literal["previous_price", "rrp"] | None = None
    discount_percent: int | None = Field(default=None, ge=1, le=99)
    net_quantity: str | None = None
    unit_price: Money | None = None
    unit_price_per: Literal["kg", "l", "100 g", "100 ml"] | None = None

    # Product safety information (GPSR)
    manufacturer_name: str | None = None
    manufacturer_address: str | None = None
    manufacturer_country: str | None = None
    manufacturer_email: str | None = None
    manufacturer_website: str | None = None
    eu_rp_name: str | None = None
    eu_rp_address: str | None = None
    eu_rp_email: str | None = None
    safety_warnings: str | None = None

    # Textiles and lighting
    fibre_composition: str | None = None
    energy_class: Literal["A", "B", "C", "D", "E", "F", "G"] | None = None
    energy_label_url: str | None = None
    product_sheet_url: str | None = None

    @model_validator(mode="after")
    def well_formed(self) -> "Offer":
        if self.was_price is not None and self.was_price <= self.price:
            raise ValueError("a crossed-out price must be higher than the price")
        if (self.was_price is None) != (self.was_price_kind is None):
            raise ValueError("a crossed-out price needs a kind, and only then")
        if self.discount_percent is not None and self.was_price_kind != "previous_price":
            raise ValueError("a discount badge needs a previous price")
        if (self.unit_price is None) != (self.unit_price_per is None):
            raise ValueError("a unit price needs its unit, and only then")
        return self


class PricePoint(BaseModel):
    offer_id: str
    day: date
    price: Money


class Seller(BaseModel):
    seller_id: int
    name: str
    country: str
    email: str


# --- The answer key: only the grader may read these ---------------------------


class Case(BaseModel):
    offer_id: str
    split: Split
    kind: Literal["clean", "problem", "tricky"]
    source_item_id: str  # the real ABO product the listing is built on
    applicable_rules: list[Rule]
    sneaky_note: str | None = None  # text that tries to talk the agent out of its job


class Problem(BaseModel):
    offer_id: str
    problem: ProblemType
    rule: Rule
    difficulty: Literal["easy", "hard"]
    evidence: dict[str, Any]  # the facts that prove it, e.g. prices and dates


class Trap(BaseModel):
    offer_id: str
    trap: TrapType
    rule: Rule
    why_legal: str
