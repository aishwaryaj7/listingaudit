"""Product information an online offer must show.

- the manufacturer, with a postal and an electronic address (GPSR Art. 19(a))
- for a manufacturer outside the EU, a responsible person in the EU (GPSR
  Art. 19(b) and Art. 16)
- the product's warnings and safety information (GPSR Art. 19(d))
- for a light source sold on its own, the energy label and the product
  information sheet (Regulation (EU) 2017/1369 Art. 5; Delegated Regulation
  (EU) 2019/2015 Art. 4)

These checks find missing information. They don't judge its content: whether
the warnings shown are the right ones for the product is left to the reviewer.
"""

from listingaudit.checks.results import (
    CheckResult,
    Finding,
    cannot_check,
    is_missing,
    not_applicable,
    ok,
    problem,
)
from listingaudit.market.models import Offer
from listingaudit.market.rules import Rule
from listingaudit.rulebook.checklist import load_checklist

EU_COUNTRIES = {
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "czech republic",
    "denmark", "estonia", "finland", "france", "germany", "greece", "hungary", "ireland",
    "italy", "latvia", "lithuania", "luxembourg", "malta", "netherlands", "the netherlands",
    "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
}  # fmt: skip


def in_eu(country: str) -> bool:
    return country.strip().lower() in EU_COUNTRIES


def named(country: str) -> str:
    """'United Kingdom' -> 'the United Kingdom', for use in a sentence."""
    if country.startswith(("United ", "Czech ", "Netherlands")):
        return f"the {country}"
    return country


def address_country(address: str) -> str:
    """The last part of an address, where the country goes.

    'aleja Głowackiego 235, 14-095 Kołobrzeg, Poland' -> 'Poland'
    """
    return address.rsplit(",", 1)[-1].strip()


def is_food(offer: Offer) -> bool:
    return offer.product_type in load_checklist().food_product_types


FOOD = "Food is outside the General Product Safety Regulation."


def check_manufacturer_info(offer: Offer) -> CheckResult:
    rule = Rule.MANUFACTURER_INFO
    if is_food(offer):
        return not_applicable(rule, FOOD)
    facts = {
        "manufacturer_name": offer.manufacturer_name,
        "manufacturer_address": offer.manufacturer_address,
        "manufacturer_email": offer.manufacturer_email,
        "manufacturer_website": offer.manufacturer_website,
    }
    findings = []
    if is_missing(offer.manufacturer_name):
        findings.append(
            Finding(code="manufacturer_name_missing", message="The manufacturer isn't named.")
        )
    if is_missing(offer.manufacturer_address):
        findings.append(
            Finding(
                code="manufacturer_address_missing",
                message="No postal address is given for the manufacturer.",
            )
        )
    if is_missing(offer.manufacturer_email) and is_missing(offer.manufacturer_website):
        findings.append(
            Finding(
                code="manufacturer_contact_missing",
                message="No email address or website is given for the manufacturer.",
            )
        )
    if findings:
        return problem(rule, findings, **facts)
    return ok(
        rule,
        "The manufacturer is named, with a postal address and an email address or website.",
        **facts,
    )


def check_eu_responsible_person(offer: Offer) -> CheckResult:
    rule = Rule.EU_RESPONSIBLE_PERSON
    if is_food(offer):
        return not_applicable(rule, FOOD)
    country = offer.manufacturer_country
    if is_missing(country) and not is_missing(offer.manufacturer_address):
        country = address_country(offer.manufacturer_address)
    if is_missing(country):
        return cannot_check(rule, "The listing doesn't say where the manufacturer is based.")
    if in_eu(country):
        return not_applicable(
            rule,
            f"The manufacturer is based in {named(country)}, an EU country, so no separate "
            "responsible person is needed.",
            manufacturer_country=country,
        )

    facts = {
        "manufacturer_country": country,
        "eu_rp_name": offer.eu_rp_name,
        "eu_rp_address": offer.eu_rp_address,
        "eu_rp_email": offer.eu_rp_email,
    }
    if all(is_missing(v) for v in (offer.eu_rp_name, offer.eu_rp_address, offer.eu_rp_email)):
        finding = Finding(
            code="responsible_person_missing",
            message=(
                f"The manufacturer is based in {named(country)}, outside the EU, but no "
                "responsible person in the EU is named."
            ),
        )
        return problem(rule, [finding], **facts)

    findings = []
    if is_missing(offer.eu_rp_name):
        findings.append(
            Finding(
                code="responsible_person_name_missing",
                message="The responsible person isn't named.",
            )
        )
    rp_country = None
    if is_missing(offer.eu_rp_address):
        findings.append(
            Finding(
                code="responsible_person_address_missing",
                message="No postal address is given for the responsible person.",
            )
        )
    elif not in_eu(rp_country := address_country(offer.eu_rp_address)):
        findings.append(
            Finding(
                code="responsible_person_outside_eu",
                message=(
                    f"The responsible person's address is in {named(rp_country)}, outside the EU."
                ),
                facts={"responsible_person_country": rp_country},
            )
        )
    if is_missing(offer.eu_rp_email):
        findings.append(
            Finding(
                code="responsible_person_email_missing",
                message="No email address is given for the responsible person.",
            )
        )
    if findings:
        return problem(rule, findings, **facts)
    return ok(
        rule,
        f"The manufacturer is based in {named(country)}, outside the EU, and the "
        f"responsible person named is in {named(rp_country)}.",
        **facts,
    )


def check_safety_warnings(offer: Offer) -> CheckResult:
    rule = Rule.SAFETY_WARNINGS
    if is_missing(offer.safety_warnings):
        finding = Finding(
            code="safety_warnings_missing",
            message="The listing shows no warnings or safety information.",
        )
        return problem(rule, [finding])
    return ok(
        rule,
        "The listing shows warnings or safety information. Whether they are the right ones "
        "for the product is for the reviewer to judge.",
        safety_warnings=offer.safety_warnings,
    )


def check_energy_label(offer: Offer) -> CheckResult:
    rule = Rule.ENERGY_LABEL
    facts = {
        "energy_class": offer.energy_class,
        "energy_label_url": offer.energy_label_url,
        "product_sheet_url": offer.product_sheet_url,
    }
    findings = []
    if is_missing(offer.energy_class):
        findings.append(
            Finding(code="energy_class_missing", message="The energy class isn't shown.")
        )
    if is_missing(offer.energy_label_url):
        findings.append(
            Finding(
                code="energy_label_missing",
                message="The energy label isn't shown or linked.",
            )
        )
    if is_missing(offer.product_sheet_url):
        findings.append(
            Finding(
                code="product_sheet_missing",
                message="The product information sheet isn't linked.",
            )
        )
    if findings:
        return problem(rule, findings, **facts)
    return ok(
        rule,
        f"Energy class {offer.energy_class} is shown, with the energy label and the product "
        "information sheet.",
        **facts,
    )
