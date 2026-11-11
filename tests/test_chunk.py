"""Chunking keeps every chunk small and correctly cited."""

from listingaudit.rulebook.chunk import chunk_law, citation, split_long_text, words
from listingaudit.rulebook.parse import Article, Paragraph
from listingaudit.rulebook.sources import SOURCES_BY_ID

GPSR = SOURCES_BY_ID["gpsr"]
PANGV = SOURCES_BY_ID["pangv"]


def sentence(n: int, length: int = 20) -> str:
    return "Word " + " ".join(["word"] * (length - 2)) + f" end{n}."


def test_citations():
    assert citation(GPSR, "19") == "GPSR Art. 19"
    assert citation(GPSR, "9", "5") == "GPSR Art. 9(5)"
    assert citation(PANGV, "4", "1") == "PAngV § 4(1)"
    assert citation(SOURCES_BY_ID["light_sources"], "Annex VIII") == (
        "Regulation (EU) 2019/2015 Annex VIII"
    )
    assert citation(SOURCES_BY_ID["ucpd"], "Annex I", "4c") == "UCPD Annex I, point 4c"
    assert citation(SOURCES_BY_ID["ucpd"], "Annex I", "1-3") == "UCPD Annex I, points 1–3"
    assert citation(GPSR, "5", "1-2") == "GPSR Art. 5(1)–(2)"
    guidance = SOURCES_BY_ID["pid_guidance"]
    assert citation(guidance, "2.1") == "Commission guidance on Article 6a, section 2.1"
    assert citation(guidance, "Introduction") == "Commission guidance on Article 6a, Introduction"
    judgment = SOURCES_BY_ID["aldi_sued"]
    assert citation(judgment, "27") == "Case C-330/23 (Aldi Süd), para. 27"
    assert citation(judgment, "operative part") == "Case C-330/23 (Aldi Süd), operative part"


def test_a_short_article_is_one_chunk():
    article = Article("19", "Information requirements", [Paragraph(None, sentence(1))], "art_19")
    [chunk] = chunk_law(GPSR, [article])
    assert chunk.chunk_id == "gpsr/19"
    assert chunk.citation == "GPSR Art. 19"
    assert chunk.paragraph is None
    assert chunk.url.endswith("#art_19")
    assert chunk.search_text.startswith("GPSR Art. 19 – Information requirements\n")


def test_a_long_article_is_cut_into_its_paragraphs():
    paragraphs = [Paragraph(str(n), f"{n}. " + sentence(n, 90)) for n in range(1, 4)]
    chunks = chunk_law(GPSR, [Article("9", "Obligations", paragraphs)], max_words=100)
    assert [c.chunk_id for c in chunks] == ["gpsr/9/1", "gpsr/9/2", "gpsr/9/3"]
    assert [c.citation for c in chunks] == ["GPSR Art. 9(1)", "GPSR Art. 9(2)", "GPSR Art. 9(3)"]
    assert all(c.heading == "Obligations" for c in chunks)


def test_a_long_paragraph_is_cut_at_sentence_ends_and_keeps_its_citation():
    long_paragraph = " ".join(sentence(n) for n in range(10))  # 10 sentences, 200 words
    chunks = chunk_law(PANGV, [Article("4", "Grundpreis", [Paragraph("1", long_paragraph)])], 50)
    assert len(chunks) == 5  # two sentences (40 words) per chunk
    assert all(c.citation == "PAngV § 4(1)" for c in chunks)
    assert [c.chunk_id for c in chunks] == ["pangv/4/1"] + [f"pangv/4/1#{n}" for n in range(2, 6)]
    assert all(words(c.text) <= 50 and c.text.endswith(".") for c in chunks)
    # Nothing lost, nothing added
    assert " ".join(c.text for c in chunks) == long_paragraph


def test_short_paragraphs_are_packed_together():
    paragraphs = [Paragraph(str(n), f"{n}. " + sentence(n, 10)) for n in range(1, 4)]
    paragraphs.append(Paragraph("4", "4. " + sentence(4, 90)))
    chunks = chunk_law(GPSR, [Article("5", None, paragraphs)], max_words=100)
    assert [c.chunk_id for c in chunks] == ["gpsr/5/1-3", "gpsr/5/4"]
    assert [c.citation for c in chunks] == ["GPSR Art. 5(1)–(3)", "GPSR Art. 5(4)"]
    assert chunks[0].text.split("\n")[2].startswith("3. Word")


def test_text_without_a_number_stays_with_the_next_paragraph():
    paragraphs = [
        Paragraph(None, "Introductory words:"),
        Paragraph("1", "1. " + sentence(1, 60)),
        Paragraph("2", "2. " + sentence(2, 60)),
    ]
    chunks = chunk_law(GPSR, [Article("3", None, paragraphs)], max_words=100)
    assert [c.paragraph for c in chunks] == ["1", "2"]
    assert chunks[0].text.startswith("Introductory words:\n1. ")


def test_split_long_text_makes_parts_of_about_the_same_size():
    text = " ".join(sentence(n, 10) for n in range(21))  # 210 words
    assert [words(part) for part in split_long_text(text, 200)] == [110, 100]


def test_split_long_text_prefers_line_ends():
    text = "\n".join(sentence(n, 30) for n in range(4))
    assert split_long_text(text, 60) == [
        "\n".join([sentence(0, 30), sentence(1, 30)]),
        "\n".join([sentence(2, 30), sentence(3, 30)]),
    ]


def test_positions_count_up_within_a_law():
    articles = [Article(str(n), None, [Paragraph(None, sentence(n))]) for n in range(1, 4)]
    assert [c.position for c in chunk_law(GPSR, articles)] == [1, 2, 3]
