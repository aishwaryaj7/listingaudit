"""Choose the real ABO products our pretend marketplace sells.

The generator needs a few facts that are hidden in the product text:

- what kind of product it is, because that decides which rules apply
- the pack size of groceries and cosmetics, for the price per kilo or litre
- the colour family, so we can swap photos between different colours
- the fibre composition of textiles

Products where we can't read these facts with confidence are left out. A wrong
guess here would put a rule break into the data that is not in the answer key.
"""

import re
from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.rows import namedtuple_row

Kind = Literal[
    "textile",  # needs a fibre composition
    "not_textile",  # sold in a textile category, but not a textile (curtain rods, hooks)
    "light_bulb",  # a light source sold on its own: needs an energy label
    "lamp",  # a lamp or light fixture: no energy label of its own
    "unit_price",  # sold by weight or volume: needs a price per kg or litre
    "mixed_set",  # a set of different products: no price per kg or litre needed
    "candle",  # needs fire safety warnings
    "general",  # furniture, shoes, decor
]

UNIT_PRICE_TYPES = {
    "GROCERY",
    "COFFEE",
    "TEA",
    "SNACK_FOOD_BAR",
    "SKIN_CLEANING_AGENT",
    "SKIN_MOISTURIZER",
    "SHAMPOO",
    "CLEANING_AGENT",
}
TEXTILE_TYPES = {"RUG", "PILLOW", "FLAT_SHEET", "HOME_BED_AND_BATH", "CURTAIN"}
LAMP_TYPES = {"LAMP", "LIGHT_FIXTURE", "HOME_LIGHTING_AND_LAMPS"}
GENERAL_TYPES = {"CHAIR", "SOFA", "TABLE", "HOME_FURNITURE_AND_DECOR", "SHOES"}


@dataclass(frozen=True)
class PackSize:
    count: int  # how many items in the pack
    each: float  # size of one item
    unit: Literal["g", "ml"]

    @property
    def total(self) -> float:
        return self.count * self.each

    def __str__(self) -> str:
        each = f"{self.each:g} {self.unit}"
        return f"{self.count} x {each}" if self.count > 1 else each


@dataclass(frozen=True)
class Product:
    """One real ABO product, plus the facts the generator reads from its text."""

    item_id: str
    product_type: str
    kind: Kind
    title: str
    brand: str | None
    color: str | None
    bullet_points: list[str]
    description: str | None
    image_id: str
    image_file: str  # the downloaded photo, relative to the data folder
    colour_family: str | None = None
    pack: PackSize | None = None
    fibres: str | None = None  # textile fibre composition, EU fibre names


# --- Pack sizes -------------------------------------------------------------

# US units are converted to metric, because EU unit prices use kg and litres.
TO_METRIC = {
    "g": ("g", 1.0),
    "kg": ("g", 1000.0),
    "oz": ("g", 28.349523125),
    "lb": ("g", 453.59237),
    "ml": ("ml", 1.0),
    "cl": ("ml", 10.0),
    "l": ("ml", 1000.0),
    "fl oz": ("ml", 29.5735295625),
    "gal": ("ml", 3785.411784),
}
UNIT_NAMES = {
    "fl oz": r"fl\.?\s*oz|fluid\s*ounces?|fz",
    "oz": r"oz|ounces?",
    "lb": r"lbs?|pounds?",
    "gal": r"gal(?:lons?)?",
    "kg": r"kg|kilograms?",
    "g": r"g|grams?|gm",
    "ml": r"ml|millilit(?:er|re)s?",
    "cl": r"cl",
    "l": r"l|lit(?:er|re)s?",
}
SIZE = re.compile(
    r"(?<![\w.-])(\d+(?:\.\d+)?)\s*-?\s*("
    + "|".join(f"(?P<{u.replace(' ', '_')}>{p})" for u, p in UNIT_NAMES.items())
    + r")(?![a-z])",
    re.IGNORECASE,
)
PACK_OF = re.compile(
    r"pack of (\d+)|(\d+)[- ]?pack\b|(\d+)\s*[x×]\s*(?=\d)|[x×]\s*(\d+)\s*(?:bottles|cans|jars)",
    re.IGNORECASE,
)
COUNT_WORDS = re.compile(
    r"\b\d+\s*(?:count|ct|capsules|pods|bags|bars|pieces|refills?)\b|with \d+ refills",
    re.IGNORECASE,
)
# In US titles, plain "ounces" on a liquid usually means fluid ounces. We decide by
# the product's main noun: "Jojoba Oil, 4 oz" is a liquid, "Cream with Eucalyptus Oil"
# is not. (Not "sauce": a "24 oz" jar of pasta sauce is sold by weight.)
LIQUID_NOUNS = {
    "shampoo", "conditioner", "wash", "gel", "lotion", "oil", "juice", "syrup", "water",
    "detergent", "dressing", "cleanser", "cleaner", "vinegar", "extract", "restorer",
}  # fmt: skip


def _is_liquid(title: str) -> bool:
    if re.search(r"\bliquid\b", title, re.IGNORECASE):
        return True
    first_size = SIZE.search(title)
    before_size = title[: first_size.start()] if first_size else title
    for segment in re.split(r",| - |\(", before_size):
        name = re.split(r"\b(?:with|in|for)\b", segment, flags=re.IGNORECASE)[0]
        words = re.findall(r"[a-z]+", name.lower())
        if words and words[-1] in LIQUID_NOUNS:
            return True
    return False


def _sizes(title: str) -> list[tuple[float, str, bool]]:
    """Every size in the title in metric: [(amount, "g" | "ml", given_in_metric), ...]."""
    sizes = []
    for match in SIZE.finditer(title):
        unit = next(u.replace("_", " ") for u, v in match.groupdict().items() if v)
        if unit == "oz" and _is_liquid(title):
            unit = "fl oz"
        metric, factor = TO_METRIC[unit]
        given_in_metric = unit in ("g", "kg", "ml", "cl", "l")
        sizes.append((float(match.group(1)) * factor, metric, given_in_metric))
    return sizes


def _round_amount(amount: float) -> float:
    return round(amount) if amount >= 10 else round(amount, 1)


def pack_size(title: str) -> PackSize | None:
    """Read the pack size from a product title, or None if it isn't clear.

    "Hand Wash, 250 ml (Pack of 2)" -> 2 x 250 ml
    "Organic Vanilla Coffee, 12 oz" -> 340 g
    "Tea Pods, 24 Ct." -> None (no size, only a count)
    """
    sizes = _sizes(title)
    if not sizes:
        return None
    # The same size is often given twice, e.g. "1.7 Fluid Ounce (50 mL)". Then we
    # take the metric one, because that is what the label says in the EU.
    amount, unit, _ = max(sizes, key=lambda s: s[2])
    for other_amount, other_unit, _ in sizes:
        if other_unit != unit or abs(other_amount - amount) > 0.05 * amount:
            return None  # two different sizes: probably a bundle, too unclear
    if COUNT_WORDS.search(title):
        return None  # "12 Fl Oz, 12 Count": is the size per can or for all of them?

    counts = [int(next(g for g in m.groups() if g)) for m in PACK_OF.finditer(title)]
    if len(set(counts)) > 1:
        return None
    count = counts[0] if counts else 1
    return PackSize(count=count, each=_round_amount(amount), unit=unit)


# --- Colours ------------------------------------------------------------------

COLOUR_WORDS = {
    "black": "black noir coal ink onyx ebony",
    "white": "white ivory cream pearl ecru parchment snow alabaster coconut",
    "grey": "grey gray charcoal slate ash pewter graphite stone dove storm smoke grijs "
    "haze sterling steel silver nickel chrome",
    "beige": "beige tan camel sand oatmeal khaki taupe linen flax natural latte fawn buff "
    "nude oat hemp chino biege",
    "brown": "brown walnut chocolate cognac saddle espresso oak wood cherry java kona pine "
    "mocha bronze cocoa",
    "blue": "blue navy denim indigo aqua turquoise ocean marine glacier wathet midnight "
    "lagoon seaside sky teal",
    "green": "green olive sage lime emerald forest hunter mint seafoam juniper sod",
    "red": "red burgundy bordeaux henna maroon",
    "pink": "pink blush fuschia fushia fuchsia rose coral salmon",
    "orange": "orange tangerine rust peach",
    "yellow": "yellow mustard canary",
    "gold": "gold brass copper",
}
WORD_TO_FAMILY = {w: family for family, words in COLOUR_WORDS.items() for w in words.split()}

# Colour families that are easy to confuse in a small photo
NEAR_COLOURS = {
    frozenset(pair)
    for pair in [
        ("beige", "white"),
        ("beige", "grey"),
        ("beige", "brown"),
        ("grey", "white"),
        ("grey", "black"),
        ("brown", "black"),
        ("blue", "grey"),
        ("red", "pink"),
        ("red", "orange"),
        ("orange", "yellow"),
        ("yellow", "gold"),
        ("brown", "gold"),
        ("blue", "green"),
    ]
}


def colour_family(color: str | None) -> str | None:
    """Map a free-text colour ("Navy Blazer", "Ash Gray") to one family, if clear.

    Returns None for unknown colours and for mixes of several families.
    """
    if not color:
        return None
    words = re.findall(r"[a-z]+", color.lower())
    families = {WORD_TO_FAMILY[w] for w in words if w in WORD_TO_FAMILY}
    if "multi" in words or "multicolor" in words or "multicolour" in words:
        return None
    return families.pop() if len(families) == 1 else None


# --- Textile fibres -------------------------------------------------------------

# EU textile fibre names (Annex I of Regulation (EU) 1007/2011) and the other names
# sellers use for them. A composition must use the EU names.
EU_FIBRE_NAMES = {
    "cotton": "cotton",
    "linen": "linen",
    "flax": "linen",
    "wool": "wool",
    "silk": "silk",
    "jute": "jute",
    "polyester": "polyester",
    "microfiber": "polyester",
    "microfibre": "polyester",
    "polypropylene": "polypropylene",
    "propylene": "polypropylene",
    "olefin": "polypropylene",
    "polyamide": "polyamide",
    "nylon": "polyamide",
    "acrylic": "acrylic",
    "viscose": "viscose",
    "rayon": "viscose",
    "modal": "modal",
    "lyocell": "lyocell",
    "elastane": "elastane",
    "spandex": "elastane",
    "other fibers": "other fibres",
    "other fibres": "other fibres",
}
FIBRE = "|".join(sorted(EU_FIBRE_NAMES, key=len, reverse=True))
PERCENT_FIBRE = re.compile(rf"(\d{{1,3}})\s*%\s*(?:high uv |spun )?({FIBRE})\b", re.IGNORECASE)
ANY_PERCENT_FIBRE = re.compile(r"\d{1,3}\s*%\s*[a-z]", re.IGNORECASE)
COMPONENT = re.compile(r"^\s*(?:([a-z &]+?)\s*:\s*)?(.+?)\s*$", re.IGNORECASE)


def _normalise_part(text: str) -> str | None:
    """'80% Wool, 20% Cotton' -> '80% wool, 20% cotton' (None if it doesn't add up)."""
    pairs = PERCENT_FIBRE.findall(text)
    leftover = PERCENT_FIBRE.sub("", text)
    if not pairs or re.search(r"[a-z]{3,}", leftover, re.IGNORECASE):
        return None  # empty, or words we don't understand (e.g. "Cotton Duck")
    if sum(int(p) for p, _ in pairs) != 100:
        return None
    return ", ".join(f"{p}% {EU_FIBRE_NAMES[f.lower()]}" for p, f in pairs)


def normalise_composition(fabric_type: str | None) -> str | None:
    """Rewrite an ABO fabric composition with EU fibre names, or None if unclear.

    'Cover: 63% Linen, 37% Rayon; Insert: 100% Cotton'
        -> 'cover: 63% linen, 37% viscose; insert: 100% cotton'
    """
    if not fabric_type or not ANY_PERCENT_FIBRE.search(fabric_type):
        return None
    parts = [p for p in re.split(r";|\.\s+|\.$", fabric_type) if p.strip()]
    normalised = []
    for part in parts:
        label, body = COMPONENT.match(part).groups()
        fibres = _normalise_part(body)
        if fibres is None:
            return None
        normalised.append(f"{label.strip().lower()}: {fibres}" if label else fibres)
    return "; ".join(normalised)


def mentioned_fibres(text: str) -> list[str]:
    """EU names of the fibres a text mentions, in order of first mention."""
    found = []
    for match in re.finditer(rf"\b({FIBRE})\b", text, re.IGNORECASE):
        name = EU_FIBRE_NAMES[match.group(1).lower()]
        if name not in found and name != "other fibres":
            found.append(name)
    return found


# --- Kinds ----------------------------------------------------------------------

# Words that name a product (not a scent or an ingredient), to spot sets of
# different products like "Shampoo, 28 fl oz & Conditioner, 28 fl oz"
PRODUCT_NOUNS = {
    "shampoo", "conditioner", "wash", "lotion", "cream", "serum", "cleanser", "mask",
    "sunscreen", "moisturizer", "soap", "bags", "cocoa", "tea", "coffee", "scrub", "butter",
}  # fmt: skip
HAS_AMOUNT = re.compile(rf"{SIZE.pattern}|\d+\s*ct\b|pack of \d+", re.IGNORECASE)


def is_mixed_set(title: str) -> bool:
    """A pack of different products, e.g. a shampoo and a conditioner sold together.

    German law (PAngV § 4(3) no. 2) doesn't require a unit price for these.
    """
    if re.search(r"\b(duo|gift set)\b", title, re.IGNORECASE):
        return True
    parts = title.split(" & ")
    if len(parts) < 2 or not all(HAS_AMOUNT.search(p) for p in parts):
        return False
    nouns = [set(re.findall(r"[a-z]+", p.lower())) & PRODUCT_NOUNS for p in parts]
    return all(nouns) and not set.intersection(*nouns)


NOT_TEXTILE = re.compile(
    r"\b(rods?|finials?|caddy|hooks|ceramic|mugs?|cups?|pvc|accessories set)\b", re.IGNORECASE
)
UNCLEAR_TEXTILE = re.compile(
    r"memory[- ]foam|\bfoam\b|mattress|renewed|leather|sheepskin|\bfur\b|feather"
    r"|\bdown\b(?![- ]alternative)",
    re.IGNORECASE,
)
INTEGRATED_LED = re.compile(r"\bled\b", re.IGNORECASE)


def classify(product_type: str, text: str, title: str) -> Kind | None:
    """Decide the kind of product, or None to leave it out of the marketplace."""
    if product_type in TEXTILE_TYPES:
        if NOT_TEXTILE.search(title):
            return "not_textile"
        return None if UNCLEAR_TEXTILE.search(text) else "textile"
    if product_type == "LIGHT_BULB":
        return "light_bulb"
    if product_type in LAMP_TYPES:
        # A lamp with built-in LEDs that can't be taken out counts as a light source
        # itself, and then needs a label. We can't tell from the text, so we skip
        # LED lamps unless they take a separate bulb.
        if INTEGRATED_LED.search(text) and "bulb" not in text.lower():
            return None
        return "lamp"
    if product_type in UNIT_PRICE_TYPES:
        if is_mixed_set(title):
            return "mixed_set"
        pack = pack_size(title)
        # Packs under 10 g or 10 ml don't need a unit price, so we leave them out
        return "unit_price" if pack and pack.each >= 10 else None
    if product_type == "CANDLE":
        # Only the candles themselves: a candle holder is not a fire risk on its own
        if re.search(r"holder|lantern|snuffer", title, re.IGNORECASE):
            return None
        return "candle" if re.search(r"candle|tealight|votive", title, re.IGNORECASE) else None
    if product_type in GENERAL_TYPES:
        return "general"
    return None


# --- Loading ----------------------------------------------------------------------

CANDIDATES = """
SELECT DISTINCT ON (l.item_id)
       l.item_id, l.product_type, l.item_name, l.brand, l.color, l.fabric_type,
       l.bullet_points, l.description, l.main_image_id, i.local_file
FROM listings l
JOIN images i ON i.image_id = l.main_image_id
WHERE i.local_file IS NOT NULL
  AND l.text_language LIKE 'en%%'
  AND l.item_name IS NOT NULL
ORDER BY l.item_id, l.domain_name
"""


def make_product(row) -> Product | None:
    """Turn one row of CANDIDATES into a Product, or None if we can't use it."""
    title, bullets = row.item_name, row.bullet_points or []
    text = " ".join([title, *bullets, row.description or ""])
    kind = classify(row.product_type, text, title)
    if kind is None:
        return None

    fibres = None
    if kind == "textile":
        fibres = normalise_composition(row.fabric_type)
        if fibres is None and ANY_PERCENT_FIBRE.search(f"{row.fabric_type or ''} {text}"):
            return None  # it states a composition we can't read: too risky to use

    return Product(
        item_id=row.item_id,
        product_type=row.product_type,
        kind=kind,
        title=title,
        brand=row.brand,
        color=row.color,
        bullet_points=bullets,
        description=row.description,
        image_id=row.main_image_id,
        image_file=row.local_file,
        colour_family=colour_family(row.color),
        pack=pack_size(title) if kind == "unit_price" else None,
        fibres=fibres,
    )


@dataclass(frozen=True)
class Photo:
    """A downloaded product photo with a known colour, for swapping between listings."""

    item_id: str
    product_type: str
    image_id: str
    image_file: str
    color: str
    colour_family: str


def load_catalogue(conn: psycopg.Connection) -> tuple[list[Product], list[Photo]]:
    """All products we can sell, and all photos we can swap, in a fixed order.

    Any downloaded photo with a clear colour can be swapped in, including photos of
    products we don't sell.
    """
    with conn.cursor(row_factory=namedtuple_row) as cur:
        rows = cur.execute(CANDIDATES).fetchall()
    products = [p for p in map(make_product, rows) if p is not None]
    photos = [
        Photo(r.item_id, r.product_type, r.main_image_id, r.local_file, r.color, family)
        for r in rows
        if (family := colour_family(r.color))
    ]
    return products, photos
