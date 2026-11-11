"""Law page parsing: every layout yields numbered articles with clean text.

The pages in tests/fixtures/laws are small extracts of the real downloads.
"""

from pathlib import Path

import pytest

from listingaudit.rulebook.parse import (
    parse_eurlex_act,
    parse_eurlex_guidance,
    parse_eurlex_judgment,
    parse_gii_xml,
)

FIXTURES = Path(__file__).parent / "fixtures" / "laws"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def by_number(articles):
    return {article.number: article for article in articles}


@pytest.fixture(scope="module")
def official_journal():
    return by_number(parse_eurlex_act(read("official_journal.html")))


@pytest.fixture(scope="module")
def consolidated():
    return by_number(parse_eurlex_act(read("consolidated.html")))


def test_articles_keep_their_numbers_and_headings(official_journal):
    article = official_journal["16"]
    assert article.heading == "Responsible person for products placed on the Union market"
    assert [p.label for p in article.paragraphs] == ["1", "2", "3"]
    assert article.paragraphs[0].text.startswith("1. A product covered by this Regulation")
    assert article.anchor == "art_16"


def test_points_are_on_one_line_with_their_letter(official_journal):
    lines = official_journal["19"].text.split("\n")
    assert lines[0].startswith("Where economic operators make products available")
    assert lines[1].startswith("(a) name, registered trade name or registered trade mark")
    assert lines[4].startswith("(d) any warning or safety information")


def test_text_without_a_number_stays_with_its_paragraph(official_journal):
    second = official_journal["17"].paragraphs[1]
    assert second.label == "2"
    assert second.text.endswith("on how to fulfil the obligations laid down in this Regulation.")


def test_footnote_marks_are_removed(official_journal, consolidated):
    # "( 18 )" in the Official Journal, "(4)" in the consolidated text
    assert "of the Council shall apply." in official_journal["17"].text
    assert "associated guarantees, or the risks he may face." in consolidated["6"].text


def test_annexes_are_cut_into_their_numbered_points(official_journal):
    annex = official_journal["Annex VIII"]
    assert annex.heading == (
        "Information to be provided in the case of distance selling on the internet"
    )
    assert [p.label for p in annex.paragraphs] == ["1", "2", "3", "4"]
    assert annex.anchor == "anx_VIII"


def test_correlation_tables_are_left_out(official_journal):
    assert list(official_journal) == ["16", "17", "19", "4", "Annex VIII"]


def test_annexes_can_be_left_out():
    articles = parse_eurlex_act(read("official_journal.html"), skip_annexes=("Annex VIII",))
    assert "Annex VIII" not in by_number(articles)


def test_consolidated_texts_lose_their_amendment_markers(consolidated):
    article = consolidated["6a"]
    assert [p.label for p in article.paragraphs] == ["1", "2", "3", "4", "5"]
    assert article.paragraphs[1].text == (
        "2. The prior price means the lowest price applied by the trader during a period of "
        "time not shorter than 30 days prior to the application of the price reduction."
    )
    everything = "\n".join(a.text for a in consolidated.values())
    assert "▼" not in everything and "►" not in everything


def test_consolidated_points_and_annex_items(consolidated):
    assert "(a) the existence or nature of the product;" in consolidated["6"].text.split("\n")
    annex = consolidated["Annex I"]
    items = {p.label: p.text for p in annex.paragraphs}
    assert items["4c"].startswith("4c. Claiming, based on the offsetting of greenhouse gas")
    # A heading between the items stands alone, so the chunker puts it with the next item
    assert "Aggressive commercial practices" in [p.text for p in annex.paragraphs]


def test_guidance_sections():
    sections = by_number(parse_eurlex_guidance(read("guidance.html")))
    assert list(sections) == ["Introduction", "4.3"]
    section = sections["4.3"]
    assert section.heading == "Progressive price reductions"
    # The box quoting Article 6a is left out: the article itself is in the rulebook
    assert not section.text.startswith("Article 6a")
    assert "5. Member States may provide" not in section.text
    assert section.text.startswith("The regulatory choice provided in paragraph 5 of Article 6a")
    # Footnotes are left out, and so is the space their marks leave behind
    intro = sections["Introduction"].text
    assert "OJ L 80" not in intro and " ." not in intro
    assert intro.split("\n")[0].endswith("make better informed choices.")


def test_judgment_paragraphs_and_ruling():
    paragraphs = by_number(parse_eurlex_judgment(read("judgment.html")))
    assert list(paragraphs) == ["1", "3", "29", "operative part"]
    assert paragraphs["3"].heading == "Legal context – Directive 98/6"
    assert paragraphs["3"].anchor == "point3"
    assert paragraphs["29"].text.startswith("In the light of the foregoing, the answer")
    ruling = paragraphs["operative part"].text
    assert ruling.startswith("On those grounds, the Court (Eighth Chamber) hereby rules:")
    assert "must be determined on the basis of the ‘prior price’" in ruling
    assert "Signatures" not in ruling and "Language of the case" not in ruling


def test_german_law():
    articles = by_number(parse_gii_xml(read("pangv.xml")))
    assert list(articles) == ["4", "11"]  # the head of the law has no § number
    unit_price = articles["4"]
    assert unit_price.heading == "Pflicht zur Angabe des Grundpreises"
    assert [p.label for p in unit_price.paragraphs] == ["1", "2", "3"]
    assert unit_price.paragraphs[0].text.startswith("(1) Wer als Unternehmer Verbrauchern")
    # A numbered list: one line per item
    lines = unit_price.paragraphs[2].text.split("\n")
    assert lines[0] == "(3) Absatz 1 ist nicht anzuwenden auf"
    assert lines[2].startswith("2. Waren, die verschiedenartige Erzeugnisse enthalten")
