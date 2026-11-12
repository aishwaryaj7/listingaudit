"""Fibre composition of textile products (Regulation (EU) No 1007/2011).

The composition must be visible before the purchase, also online (Art. 16(1)).
It uses only the fibre names of Annex I (Art. 5), and gives each fibre's share by
weight, largest first (Art. 9(1)). A product made of parts, such as a cushion's
cover and filling, gives a composition for each part.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from listingaudit.checks.results import CheckResult, Finding, is_missing, ok, problem
from listingaudit.market.models import Offer
from listingaudit.market.rules import Rule

RULE = Rule.FIBRE_COMPOSITION

# Annex I as published in 2011, the version in the rulebook. "flax (or linen)" and
# "polyamide or nylon" allow both names.
EU_FIBRE_NAMES = {
    "wool", "silk", "cotton", "kapok", "flax", "linen", "true hemp", "jute", "abaca",
    "alfa", "coir", "broom", "ramie", "sisal", "sunn", "henequen", "maguey", "acetate",
    "alginate", "cupro", "modal", "protein", "triacetate", "viscose", "acrylic",
    "chlorofibre", "fluorofibre", "modacrylic", "polyamide", "nylon", "aramid", "polyimide",
    "lyocell", "polylactide", "polyester", "polyethylene", "polypropylene", "polycarbamide",
    "polyurethane", "vinylal", "trivinyl", "elastodiene", "elastane", "glass fibre",
    "elastomultiester", "elastolefin", "melamine",
}  # fmt: skip
# Annex I no. 2: these hairs, "followed or not by the word 'wool' or 'hair'"
FINE_HAIRS = {
    "alpaca", "llama", "camel", "cashmere", "mohair", "angora", "vicuna", "yak", "guanaco",
    "cashgora", "beaver", "otter",
}  # fmt: skip
# Annex I no. 48: fibres named after their material, "followed or not by the word
# 'yarn' or 'fibre'"
MATERIALS = {"metal", "metallic", "metallised", "asbestos", "paper"}
# Art. 9(2) and (5): small or unlisted fibres, with their total percentage
OTHER_FIBRES = "other fibres"
# Art. 9(4): allowed when the composition is hard to state at manufacture
UNSPECIFIED = {"mixed fibres", "unspecified textile composition"}
# Names sellers use instead of the EU ones
EU_NAME_FOR = {
    "rayon": "viscose",
    "spandex": "elastane",
    "lycra": "elastane",
    "olefin": "polypropylene",
    "hemp": "true hemp",
}

PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
# "front cover: 52% polyester, ..." -> a part of the product and its composition
PART_LABEL = re.compile(r"^\s*([a-z][a-z ]*?)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)
# Fibres are separated by commas (not decimal commas), slashes, "+" or "and"
SEPARATOR = re.compile(r"(?<!\d),|,(?!\d)|/|\+|\band\b", re.IGNORECASE)


@dataclass(frozen=True)
class Fibre:
    name: str
    percent: Decimal | None


def is_eu_fibre_name(name: str) -> bool:
    words = name.split()
    if name in EU_FIBRE_NAMES or name in FINE_HAIRS or name == "horsehair":
        return True
    if len(words) == 2 and words[0] in FINE_HAIRS and words[1] in ("wool", "hair"):
        return True
    if len(words) >= 2 and words[-1] == "hair":  # no. 3: "animal hair", "cattle hair"
        return True
    return bool(words) and words[0] in MATERIALS and words[1:] in ([], ["yarn"], ["fibre"])


def clean_name(text: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", text.lower()).split())


def parse_composition(text: str) -> list[tuple[str | None, list[Fibre]]]:
    """Split a composition into its parts and their fibres.

    'cover: 60% cotton, 40% viscose; filling: 100% polyester'
        -> [('cover', [cotton 60, viscose 40]), ('filling', [polyester 100])]
    """
    parts = []
    for section in text.split(";"):
        if not section.strip():
            continue
        label, body = None, section
        match = PART_LABEL.match(section)
        if match and re.search(r"[a-z]", match.group(2), re.IGNORECASE):  # not "Cotton: 60%"
            label, body = match.group(1).lower(), match.group(2)
        fibres = []
        for piece in SEPARATOR.split(body):
            if not piece.strip():
                continue
            percent = PERCENT.search(piece)
            fibres.append(
                Fibre(
                    name=clean_name(PERCENT.sub(" ", piece)),
                    percent=Decimal(percent.group(1).replace(",", ".")) if percent else None,
                )
            )
        parts.append((label, fibres))
    return parts


def quoted(names: list[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def check_fibre_composition(offer: Offer) -> CheckResult:
    text = offer.fibre_composition
    if is_missing(text):
        finding = Finding(
            code="fibre_composition_missing",
            message="The listing doesn't show the fibre composition.",
        )
        return problem(RULE, [finding])
    if clean_name(text) in UNSPECIFIED:
        return ok(
            RULE,
            f"'{text}' may be used when the composition is hard to state at manufacture.",
            fibre_composition=text,
        )

    unknown, no_percent, findings = [], [], []
    for label, fibres in parse_composition(text):
        where = f" of the {label}" if label else ""
        for fibre in fibres:
            if fibre.name != OTHER_FIBRES and not is_eu_fibre_name(fibre.name):
                unknown.append(fibre.name)
            if fibre.percent is None:
                no_percent.append(fibre.name)
        if any(fibre.percent is None for fibre in fibres):
            continue
        total = sum(fibre.percent for fibre in fibres)
        if total != 100:
            findings.append(
                Finding(
                    code="percentages_dont_add_up",
                    message=f"The percentages{where} add up to {total.normalize():f}%, not 100%.",
                )
            )
        shares = [fibre.percent for fibre in fibres if fibre.name != OTHER_FIBRES]
        if shares != sorted(shares, reverse=True):
            findings.append(
                Finding(
                    code="not_largest_first",
                    message=f"The fibres{where} are not listed from the largest share down.",
                )
            )

    if no_percent:
        findings.insert(
            0,
            Finding(
                code="percentage_missing",
                message=f"No percentage is given for {quoted(no_percent)}.",
                facts={"names": no_percent},
            ),
        )
    if unknown:
        message = f"{quoted(unknown)} {'is' if len(unknown) == 1 else 'are'} not on the EU "
        message += "list of fibre names."
        better = [f"'{EU_NAME_FOR[n]}' for '{n}'" for n in unknown if n in EU_NAME_FOR]
        if better:
            message += f" The EU name to use is {', '.join(better)}."
        findings.insert(
            0, Finding(code="not_an_eu_fibre_name", message=message, facts={"names": unknown})
        )
    if findings:
        return problem(RULE, findings, fibre_composition=text)
    return ok(
        RULE,
        "The composition uses EU fibre names, each with its percentage, largest first, "
        "adding up to 100%.",
        fibre_composition=text,
    )
