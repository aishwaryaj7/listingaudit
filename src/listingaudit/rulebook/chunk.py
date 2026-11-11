"""Split articles into searchable chunks, each with its citation.

A short article is one chunk. A long one is split into its numbered paragraphs,
so a hit points to "Art. 9(5)" rather than all of Article 9. Very short
paragraphs are packed with their neighbours ("Art. 5(1)–(2)"), since a few words
make a poor embedding. A paragraph that is still too long is split at line or
sentence ends, and every part keeps the paragraph's citation. A chunk never
splits a sentence or mixes two articles.
"""

import re
from dataclasses import dataclass

from listingaudit.rulebook.parse import Article
from listingaudit.rulebook.sources import LawSource

# The embedding model truncates at 512 tokens. Legal English averages about 1.4
# tokens per word and German about 1.7, so 200 words plus the heading stay well
# below that (the longest chunk is 382 tokens).
MAX_WORDS = 200
# A paragraph shorter than this is packed together with its neighbours
MIN_WORDS = 50

SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(„\"])")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    law_id: str
    language: str
    position: int
    article: str
    paragraph: str | None
    heading: str | None
    citation: str
    text: str
    url: str

    @property
    def search_text(self) -> str:
        """What the embedding model reads: the citation and heading give context."""
        heading = f" – {self.heading}" if self.heading else ""
        return f"{self.citation}{heading}\n{self.text}"


def words(text: str) -> int:
    return len(text.split())


def citation(source: LawSource, article: str, paragraph: str | None = None) -> str:
    """How a report cites a piece of law, e.g. 'GPSR Art. 19' or 'PAngV § 4(1)'."""
    numbered = article[:1].isdigit()
    if source.kind == "guidance":  # "section 2.1", or "Introduction"
        return f"{source.name}, " + (f"section {article}" if numbered else article)
    if source.kind == "judgment":  # "para. 27", or "operative part"
        return f"{source.name}, " + (f"para. {article}" if numbered else article)
    first, _, last = (paragraph or "").partition("-")  # "1-3": paragraphs 1 to 3
    if article.startswith("Annex"):
        points = f", points {first}–{last}" if last else f", point {first}"
        return f"{source.name} {article}" + (points if paragraph else "")
    where = f"§ {article}" if source.format == "gii_xml" else f"Art. {article}"
    paragraphs = f"({first})–({last})" if last else f"({first})"
    return f"{source.name} {where}" + (paragraphs if paragraph else "")


def article_url(source: LawSource, article: Article) -> str:
    """A link that opens the law at this article."""
    if source.format == "gii_xml":
        # gesetze-im-internet.de has one page per §, e.g. .../pangv_2022/__4.html
        return source.url.removesuffix("xml.zip") + f"__{article.number}.html"
    return f"{source.url}#{article.anchor}" if article.anchor else source.url


def split_long_text(text: str, max_words: int = MAX_WORDS) -> list[str]:
    """Cut a text into parts of at most `max_words`, at line ends or sentence ends.

    The last two parts are evened out, so that 210 words become parts of 110
    and 100 words, not 200 and 10. A single sentence longer than `max_words`
    stays whole: a cut in the middle of a sentence could change what the law says.
    """
    pieces: list[tuple[int, str]] = []  # (line number, line or sentence)
    for n, line in enumerate(text.split("\n")):
        sentences = [line] if words(line) <= max_words else SENTENCE_END.split(line)
        pieces.extend((n, sentence) for sentence in sentences)
    parts: list[list[tuple[int, str]]] = [[]]
    for piece in pieces:
        if parts[-1] and _size(parts[-1]) + words(piece[1]) > max_words:
            parts.append([])
        parts[-1].append(piece)
    if len(parts) > 1:
        before, last = parts[-2], parts[-1]
        while len(before) > 1 and _size(last) + words(before[-1][1]) < _size(before):
            last.insert(0, before.pop())
    return [_join(part) for part in parts]


def _size(pieces: list[tuple[int, str]]) -> int:
    return sum(words(text) for _, text in pieces)


def _join(pieces: list[tuple[int, str]]) -> str:
    """Sentences of the same line are joined with a space, lines with a line break."""
    text = pieces[0][1]
    for (previous, _), (line, piece) in zip(pieces, pieces[1:], strict=False):
        text += (" " if line == previous else "\n") + piece
    return text


def _groups(article: Article, max_words: int) -> list[tuple[str | None, str]]:
    """(paragraph label, text) for each chunk of one article."""
    if words(article.text) <= max_words:
        return [(None, article.text)]
    # The numbered paragraphs. Text without a number joins the next numbered one.
    paragraphs: list[tuple[str | None, str]] = []
    pending: list[str] = []
    for paragraph in article.paragraphs:
        pending.append(paragraph.text)
        if paragraph.label is not None:
            paragraphs.append((paragraph.label, "\n".join(pending)))
            pending = []
    if pending:  # text after the last number, or an article without numbers
        paragraphs.append((None, "\n".join(pending)))
    # Pack short paragraphs together, and cut long ones into parts
    groups: list[tuple[str | None, str]] = []
    packed: list[tuple[str | None, str]] = []
    for label, text in paragraphs:
        size, packed_size = words(text), sum(words(t) for _, t in packed)
        if packed and packed_size + size <= max_words and min(size, packed_size) < MIN_WORDS:
            packed.append((label, text))
            continue
        if packed:
            groups.append(_pack(packed))
        packed = []
        if size > max_words:
            groups.extend((label, part) for part in split_long_text(text, max_words))
        else:
            packed = [(label, text)]
    if packed:
        groups.append(_pack(packed))
    return groups


def _pack(paragraphs: list[tuple[str | None, str]]) -> tuple[str | None, str]:
    """One chunk of several paragraphs, labelled with the range: "1-3"."""
    labels = [label for label, _ in paragraphs if label is not None]
    if not labels:
        label = None
    else:
        label = labels[0] if len(labels) == 1 else f"{labels[0]}-{labels[-1]}"
    return label, "\n".join(text for _, text in paragraphs)


def chunk_law(
    source: LawSource, articles: list[Article], max_words: int = MAX_WORDS
) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: dict[str, int] = {}
    for article in articles:
        for label, text in _groups(article, max_words):
            key = "/".join(
                [source.law_id, article.number.lower().replace(" ", "_")] + [label or ""]
            )
            key = key.rstrip("/")
            seen[key] = seen.get(key, 0) + 1
            chunk_id = key if seen[key] == 1 else f"{key}#{seen[key]}"  # parts of a long paragraph
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    law_id=source.law_id,
                    language=source.language,
                    position=len(chunks) + 1,
                    article=article.number,
                    paragraph=label,
                    heading=article.heading,
                    citation=citation(source, article.number, label),
                    text=text,
                    url=article_url(source, article),
                )
            )
    return chunks
