"""Colour families, and what a listing's colour field says in terms of them.

Shop colours are names like "Polished Nickel", "Navy Blazer" or "Soft Cream". To
compare them with a photo, each name is mapped to one of a few broad families.
"""

import re

FAMILIES = [
    "black", "white", "grey", "beige", "brown", "blue", "green",
    "red", "pink", "orange", "yellow", "gold", "purple",
]  # fmt: skip

WORDS = {
    "black": "black noir onyx ebony jet caviar",
    "white": "white ivory cream pearl snow alabaster eggshell coconut parchment daylight",
    "grey": "grey gray lightgrey lightgray charcoal slate ash silver nickel chrome steel pewter "
    "graphite stone dove smoke fog haze sterling gunmetal",
    # Not "linen": in "Grey Linen" it names the fabric, not the colour
    "beige": "beige tan khaki camel sand sandstone taupe oatmeal oat natural nude latte fawn "
    "ecru chino",
    "brown": "brown walnut chocolate coffee espresso mocha cognac saddle chestnut mahogany "
    "oak wood wooden bronze cherry teak kona pine beech",
    "blue": "blue navy denim indigo ink turquoise aqua sky cobalt azure sapphire midnight "
    "glacier lagoon teal wedgewood",
    "green": "green olive sage mint emerald forest lime seafoam jade juniper",
    "red": "red burgundy maroon wine crimson scarlet ruby brick henna bordeaux",
    "pink": "pink blush rose fuchsia fuschia fushia magenta coral salmon",
    "orange": "orange tangerine rust peach apricot terracotta amber",
    "yellow": "yellow mustard lemon canary",
    "gold": "gold golden brass champagne",
    "purple": "purple violet lavender lilac plum mauve",
}
FAMILY_OF = {word: family for family, words in WORDS.items() for word in words.split()}

# Families a photo can be mistaken between: lighting, shade and small photos blur them
NEAR = {
    frozenset(pair)
    for pair in [
        ("white", "beige"), ("white", "grey"), ("beige", "grey"), ("beige", "brown"),
        ("grey", "black"), ("brown", "black"), ("grey", "blue"), ("blue", "green"),
        ("blue", "purple"), ("red", "pink"), ("red", "orange"), ("red", "purple"),
        ("pink", "purple"), ("orange", "yellow"), ("orange", "brown"), ("yellow", "gold"),
        ("gold", "brown"), ("gold", "beige"), ("pink", "beige"), ("pink", "orange"),
    ]
}  # fmt: skip

MULTI = {"multi", "multicolor", "multicolour", "multicolored", "multicoloured", "assorted"}
# Parts of a colour name that name different colours ("Black/White", "Brass and White")
PARTS = re.compile(r"[/&,+]|\band\b|\bwith\b")


def is_near(a: str, b: str) -> bool:
    return frozenset((a, b)) in NEAR


def family_of(colour: str | None) -> str | None:
    """The one colour family a colour name stands for, or None.

    The last colour word decides ("Pearl Gray" is grey, "Slate Blue" is blue).
    Names of several colours ("Walnut/White") and unknown names give None.
    """
    if not colour:
        return None
    families = set()
    for part in PARTS.split(colour.lower()):
        words = re.findall(r"[a-z]+", part)
        if MULTI & set(words):
            return None
        known = [FAMILY_OF[word] for word in words if word in FAMILY_OF]
        if known:
            families.add(known[-1])
    return families.pop() if len(families) == 1 else None


NOUNS = {
    "CANDLE": "candle",
    "CHAIR": "chair",
    "CURTAIN": "curtain",
    "FLAT_SHEET": "bed sheet",
    "HOME_BED_AND_BATH": "bedding",
    "HOME_FURNITURE_AND_DECOR": "piece of home decor",
    "HOME_LIGHTING_AND_LAMPS": "lamp",
    "LAMP": "lamp",
    "LIGHT_BULB": "light bulb",
    "LIGHT_FIXTURE": "light fixture",
    "PILLOW": "pillow",
    "RUG": "rug",
    "SHOES": "pair of shoes",
    "SOFA": "sofa",
    "TABLE": "table",
}


def noun(product_type: str) -> str:
    """What to call the product in a sentence about the photo."""
    return NOUNS.get(product_type, "product")
