"""Law texts in the rulebook and where to download them.

EU law comes from EUR-Lex as English HTML. Where a later amendment matters, the
consolidated version is used: Article 6a of Directive 98/6/EC (the 30-day rule),
for example, only exists in the consolidated text.

The German Price Indication Ordinance (PAngV) comes from gesetze-im-internet.de
as XML, in German. It has no official English version.
"""

from dataclasses import dataclass
from typing import Literal

EURLEX = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{}"


@dataclass(frozen=True)
class LawSource:
    law_id: str  # short id, used in checklist.yaml
    name: str  # how citations name it, e.g. "GPSR"
    title: str
    kind: Literal["act", "guidance", "judgment"]
    format: Literal["eurlex_html", "gii_xml"]
    language: Literal["en", "de"]
    url: str
    version: str  # which version of the text is used
    # Annexes that say nothing about what a listing must show. Correlation tables
    # (old and new article numbers) are always left out.
    skip_annexes: tuple[str, ...] = ()

    @property
    def file_name(self) -> str:
        """Where the download is kept, inside data/laws/raw."""
        return f"{self.law_id}.zip" if self.format == "gii_xml" else f"{self.law_id}.html"


SOURCES: list[LawSource] = [
    LawSource(
        "pid",
        "Directive 98/6/EC",
        "Directive 98/6/EC on consumer protection in the indication of the prices of "
        "products offered to consumers",
        "act",
        "eurlex_html",
        "en",
        EURLEX.format("01998L0006-20220528"),
        "consolidated version of 28 May 2022, with Article 6a added by Directive (EU) 2019/2161",
    ),
    LawSource(
        "ucpd",
        "UCPD",
        "Directive 2005/29/EC concerning unfair business-to-consumer commercial practices "
        "(Unfair Commercial Practices Directive)",
        "act",
        "eurlex_html",
        "en",
        EURLEX.format("02005L0029-20260927"),
        "consolidated version of 27 September 2026",
    ),
    LawSource(
        "gpsr",
        "GPSR",
        "Regulation (EU) 2023/988 on general product safety (General Product Safety Regulation)",
        "act",
        "eurlex_html",
        "en",
        EURLEX.format("32023R0988"),
        "as published in the Official Journal, L 135, 23 May 2023",
    ),
    LawSource(
        "textile",
        "Regulation (EU) No 1007/2011",
        "Regulation (EU) No 1007/2011 on textile fibre names and related labelling and "
        "marking of the fibre composition of textile products",
        "act",
        "eurlex_html",
        "en",
        EURLEX.format("32011R1007"),
        "as published in the Official Journal, L 272, 18 October 2011",
        # Laboratory methods for measuring fibre content: a quarter of all the law
        # text, and none of it about what a listing must say.
        skip_annexes=("Annex VIII",),
    ),
    LawSource(
        "energy_label",
        "Regulation (EU) 2017/1369",
        "Regulation (EU) 2017/1369 setting a framework for energy labelling",
        "act",
        "eurlex_html",
        "en",
        EURLEX.format("32017R1369"),
        "as published in the Official Journal, L 198, 28 July 2017",
    ),
    LawSource(
        "light_sources",
        "Regulation (EU) 2019/2015",
        "Commission Delegated Regulation (EU) 2019/2015 with regard to energy labelling of "
        "light sources",
        "act",
        "eurlex_html",
        "en",
        EURLEX.format("32019R2015"),
        "as published in the Official Journal, L 315, 5 December 2019",
    ),
    LawSource(
        "pid_guidance",
        "Commission guidance on Article 6a",
        "Commission Notice: Guidance on the interpretation and application of Article 6a of "
        "Directive 98/6/EC (2021/C 526/02)",
        "guidance",
        "eurlex_html",
        "en",
        EURLEX.format("52021XC1229(06)"),
        "as published in the Official Journal, C 526, 29 December 2021",
    ),
    LawSource(
        "aldi_sued",
        "Case C-330/23 (Aldi Süd)",
        "Judgment of the Court of Justice of 26 September 2024, Verbraucherzentrale "
        "Baden-Württemberg v Aldi Süd, C-330/23",
        "judgment",
        "eurlex_html",
        "en",
        EURLEX.format("62023CJ0330"),
        "judgment of 26 September 2024",
    ),
    LawSource(
        "pangv",
        "PAngV",
        "Preisangabenverordnung (German Price Indication Ordinance) of 12 November 2021",
        "act",
        "gii_xml",
        "de",
        "https://www.gesetze-im-internet.de/pangv_2022/xml.zip",
        "current version from gesetze-im-internet.de",
    ),
]

SOURCES_BY_ID = {source.law_id: source for source in SOURCES}
