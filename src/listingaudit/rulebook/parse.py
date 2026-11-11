"""Parse downloaded law pages into articles and numbered paragraphs.

Menus, footnotes and layout tables are dropped. Each article keeps its number,
its title and its numbered paragraphs, as plain text.
"""

import io
import re
import warnings
import zipfile
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning


@dataclass(frozen=True)
class Paragraph:
    label: str | None  # "1" for paragraph 1, None for text without a number
    text: str


@dataclass(frozen=True)
class Article:
    """One article of a law (or one § of a German law, or one section of a guidance)."""

    number: str  # "19", "6a", "Annex I"; for German law the § number, e.g. "4"
    heading: str | None
    paragraphs: list[Paragraph]
    anchor: str | None = None  # the id of the article on the web page, for links

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


def clean_text(text: str) -> str:
    """One line of text, with non-breaking and repeated spaces made into single spaces."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


# --- German law from gesetze-im-internet.de (XML) ---------------------------

PARAGRAPH_NUMBER = re.compile(r"^\((\d+[a-z]?)\)\s*")


def read_gii_zip(data: bytes) -> str:
    """gesetze-im-internet.de serves each law as a zip file with one XML file inside."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        [name] = [n for n in archive.namelist() if n.endswith(".xml")]
        return archive.read(name).decode("utf-8")


def _gii_block_text(block: Tag) -> str:
    """The text of one <P> block. Numbered lists (<DL>) become one line per item."""
    for item in block.find_all("DT"):
        item.insert_before("\n")
        item.append(" ")
    return "\n".join(clean_text(line) for line in block.get_text().split("\n") if line.strip())


def parse_gii_xml(xml: str) -> list[Article]:
    """Each <norm> with a "§" number is one article; its <P> blocks are the paragraphs."""
    soup = BeautifulSoup(xml, "xml")
    articles = []
    for norm in soup.find_all("norm"):
        number = norm.find("enbez")
        if number is None or not number.get_text().startswith("§"):
            continue  # the law's title page, section headings and the like
        content = norm.find("Content")
        if content is None:
            continue
        title = norm.find("titel")
        heading = clean_text(title.get_text()) if title else None
        if heading == "(weggefallen)":
            continue  # a repealed §
        paragraphs = []
        for block in content.find_all("P", recursive=False):
            text = _gii_block_text(block)
            match = PARAGRAPH_NUMBER.match(text)
            paragraphs.append(Paragraph(match.group(1) if match else None, text))
        articles.append(
            Article(
                number=clean_text(number.get_text()).removeprefix("§").strip(),
                heading=heading,
                paragraphs=paragraphs,
            )
        )
    return articles


# --- EU law from EUR-Lex (HTML) ---------------------------------------------
#
# EUR-Lex shows laws in two layouts. Laws as published in the Official Journal
# use classes that start with "oj-". Consolidated versions use other classes
# ("norm", "grid-list") and add markers showing which amendment changed what.
# Both put each article in <div class="eli-subdivision" id="art_19">, and each
# annex in <div id="anx_VIII">. Points like (a), (b) are laid out as tables
# (Official Journal) or as grids (consolidated): a label next to its text.

# A label in front of a point or item: "(a)", "(iv)", "2.", "2a.", "—"
POINT_LABEL = re.compile(r"^[‘'“\"]?(\(?\d{1,3}[a-z]?[.)]|\([a-z]{1,5}\)|[a-z]\)|[—–•-])$")
# A numbered paragraph or item: "1. The dealer shall ..." -> "1"
NUMBERED = re.compile(r"^(\d{1,3}[a-z]?)\.\s")
# Tags that start a new line; all others (span, a, em, ...) are part of a line
BLOCK_TAGS = set("p div table tbody thead tr td th ul ol li dl dt dd blockquote h1 h2 h3".split())


def _soup(html: str) -> BeautifulSoup:
    with warnings.catch_warnings():
        # EUR-Lex pages are XHTML, so Beautiful Soup warns that they look like
        # XML. The HTML parser reads them correctly.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    _remove_clutter(soup)
    return soup


def _remove_clutter(soup: BeautifulSoup) -> None:
    """Remove footnote marks and amendment markers, which are not part of the law."""
    # "( 1 )" in the Official Journal, "(1)" in consolidated texts
    for mark in soup.select('a[id^="ntc"], a[id^="src.E"]'):
        _remove_note_mark(mark)
    for marker in soup.select("p.modref, p.arrow"):  # "▼M1": changed by amendment 1
        marker.decompose()


def _remove_note_mark(mark: Tag) -> None:
    """Remove a footnote mark and its brackets.

    The space before it goes too when a full stop or comma follows:
    "choices (2)." becomes "choices.".
    """
    before, after = mark.previous_sibling, mark.next_sibling
    mark.decompose()
    left = str(before) if isinstance(before, NavigableString) else None
    right = str(after) if isinstance(after, NavigableString) else None
    if left is not None and right is not None:
        if left.rstrip().endswith("(") and right.lstrip().startswith(")"):
            left, right = left.rstrip()[:-1], right.lstrip()[1:]  # "(<a>1</a>)"
        if right[:1] in (".", ",", ";", ":", ")"):
            left = left.rstrip()
    elif left is not None and after is None:
        left = left.rstrip()  # the mark ended the paragraph
    if left is not None:
        before.replace_with(left)
    if right is not None:
        after.replace_with(right)


def _classes(element: Tag) -> list[str]:
    return element.get("class") or []


def _is_grid_list(element: Tag) -> bool:
    return "grid-list" in _classes(element) and "grid-container" in _classes(element)


def _with_label(label: str, lines: list[str]) -> list[str]:
    """Put a label like "(a)" on the same line as the start of its text."""
    if not label:
        return lines
    if not lines:
        return [label]
    return [f"{label} {lines[0]}", *lines[1:]]


def _lines(element: Tag) -> list[str]:
    """The text of an element as lines: one per paragraph, point or table row."""
    if element.name == "table":
        return _table_lines(element)
    if _is_grid_list(element):
        column_1 = element.find(class_="grid-list-column-1", recursive=False)
        column_2 = element.find(class_="grid-list-column-2", recursive=False)
        label = clean_text(column_1.get_text()) if column_1 else ""
        return _with_label(label, _lines(column_2) if column_2 else [])
    lines: list[str] = []
    words: list[str] = []  # the line being built from text and inline tags

    def end_line() -> None:
        text = clean_text("".join(words))
        if text:
            lines.append(text)
        words.clear()

    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            words.append(str(child))
        elif child.name in ("script", "style"):
            continue
        elif child.name == "br":
            end_line()
        elif child.name in BLOCK_TAGS:
            end_line()
            lines.extend(_lines(child))
        else:
            words.append(child.get_text())
    end_line()
    # A label on its own line, like "1." before the text of item 1
    if len(lines) > 1 and POINT_LABEL.match(lines[0]):
        lines = _with_label(lines[0], lines[1:])
    return lines


def _table_lines(table: Tag) -> list[str]:
    """Lines of a table: its points, data rows or nested layout.

    A point is a two-cell row: "(a)" and its text. A row of a data table becomes
    one line, with " | " between the cells.
    """
    lines = []
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    for row in rows:
        cells = [_lines(cell) for cell in row.find_all(["td", "th"], recursive=False)]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if len(cells) == 2 and len(cells[0]) == 1 and POINT_LABEL.match(cells[0][0]):
            lines.extend(_with_label(cells[0][0], cells[1]))
        elif all(len(cell) == 1 for cell in cells):  # a row of a data table
            lines.append(" | ".join(cell[0] for cell in cells))
        else:  # a layout table: its cells hold paragraphs or other tables
            lines.extend(line for cell in cells for line in cell)
    return lines


def _paragraph_lines(block: Tag) -> list[str]:
    """Lines of one block of an article.

    A numbered paragraph of a consolidated text keeps its number in front:
    "1. The dealer shall ...".
    """
    number = block.find("span", class_="no-parag", recursive=False)
    if number is not None:
        body = block.find(class_="inline-element", recursive=False)
        return _with_label(clean_text(number.get_text()), _lines(body) if body else [])
    return _lines(block)


def _is_heading(lines: list[str]) -> bool:
    """A short line without a full stop, like "Aggressive commercial practices"."""
    return len(lines) == 1 and len(lines[0].split()) <= 8 and lines[0][-1] not in ".;:,"


def _paragraphs(blocks: list[Tag]) -> list[Paragraph]:
    """Group blocks into paragraphs.

    A block that starts with a number ("2. ...") starts a new paragraph. Text
    without a number belongs to the paragraph before it. Headings, and text
    before the first number, stand alone (the chunker joins them to the next
    numbered paragraph).
    """
    paragraphs: list[Paragraph] = []
    for block in blocks:
        lines = _paragraph_lines(block)
        if not lines:
            continue
        match = NUMBERED.match(lines[0])
        if match:
            paragraphs.append(Paragraph(match.group(1), "\n".join(lines)))
        elif paragraphs and paragraphs[-1].label is not None and not _is_heading(lines):
            last = paragraphs.pop()
            paragraphs.append(Paragraph(last.label, last.text + "\n" + "\n".join(lines)))
        else:
            paragraphs.append(Paragraph(None, "\n".join(lines)))
    return paragraphs


def _article(div: Tag) -> Article:
    title = div.find(class_="eli-title", recursive=False)
    heading = clean_text(title.get_text()) if title else ""
    blocks = [
        child
        for child in div.children
        if isinstance(child, Tag)
        and child is not title
        and not {"oj-ti-art", "title-article-norm"} & set(_classes(child))
    ]
    return Article(
        number=div["id"].removeprefix("art_"),
        heading=heading or None,
        paragraphs=_paragraphs(blocks),
        anchor=div["id"],
    )


ANNEX_TITLE = re.compile(r"^ANNEX\s*([IVXLC]*)$", re.IGNORECASE)
ANNEX_HEADING_CLASSES = {"oj-doc-ti", "title-annex-2", "oj-ti-grseq-1", "oj-ti-tbl"}


def _annex(div: Tag) -> Article | None:
    """An annex is cut like an article: its numbered items are its paragraphs."""
    blocks = [child for child in div.children if isinstance(child, Tag)]
    titles = [ANNEX_TITLE.match(clean_text(block.get_text())) for block in blocks]
    i = next((i for i, title in enumerate(titles) if title), None)
    if i is None:
        return None
    number = f"Annex {titles[i].group(1).upper()}".strip()  # a law with one annex: "Annex"
    rest = blocks[i + 1 :]
    heading = None
    if rest and ANNEX_HEADING_CLASSES & set(_classes(rest[0])):
        heading = clean_text(rest[0].get_text())
        rest = rest[1:]
    return Article(number, heading, _paragraphs(rest), anchor=div["id"])


def parse_eurlex_act(html: str, skip_annexes: tuple[str, ...] = ()) -> list[Article]:
    """The articles and annexes of a regulation or directive.

    Correlation tables, which only list old and new article numbers, are left
    out, and so are the annexes named in `skip_annexes`.
    """
    soup = _soup(html)
    articles = [
        _article(div)
        for div in soup.find_all("div", class_="eli-subdivision", id=re.compile(r"^art_\w+$"))
    ]
    for div in soup.find_all("div", id=re.compile(r"^anx_[^.]+$")):
        annex = _annex(div)
        if annex is None or annex.number in skip_annexes:
            continue
        if annex.heading and "correlation table" in annex.heading.lower():
            continue
        articles.append(annex)
    return [article for article in articles if article.paragraphs]


SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def parse_eurlex_guidance(html: str) -> list[Article]:
    """A Commission notice: each numbered section ("2.1. General rules") is one article.

    The notice quotes the law it explains in boxes. The boxes are left out,
    because the law itself is in the rulebook.
    """
    soup = _soup(html)
    first = soup.find("p", class_="oj-ti-grseq-1")
    sections: list[Article] = []
    number, heading, anchor, blocks = None, None, None, []

    def end_section() -> None:
        if number is not None:
            sections.append(Article(number, heading, _paragraphs(blocks), anchor))

    for block in [first, *first.find_next_siblings()]:
        if not isinstance(block, Tag) or "borderOj" in _classes(block):
            continue
        if "oj-note" in _classes(block):
            break  # the footnotes at the end
        if "oj-ti-grseq-1" in _classes(block):
            end_section()
            title = clean_text(block.get_text())
            match = SECTION_NUMBER.match(title)
            number, heading = match.groups() if match else (title.capitalize(), None)
            anchor, blocks = block.get("id"), []
        else:
            blocks.append(block)
    end_section()
    return [section for section in sections if section.paragraphs]


def parse_eurlex_judgment(html: str) -> list[Article]:
    """A judgment of the Court of Justice.

    Each numbered paragraph is one article, and so is the ruling at the end (the
    "operative part").
    """
    soup = _soup(html)
    articles: list[Article] = []
    section, subsection = None, None
    ruling: list[str] | None = None
    for block in soup.body.find_all(["p", "table"], recursive=False):
        classes = _classes(block)
        if "coj-sum-title-1" in classes:
            section, subsection = clean_text(block.get_text()), None
        elif "coj-title-grseq-2" in classes:
            subsection = clean_text(block.get_text())
        elif block.name == "table" and ruling is None:
            count = block.find("p", class_="coj-count", id=re.compile(r"^point\d+$"))
            if count is not None:
                cells = count.find_parent("tr").find_all("td", recursive=False)
                text = "\n".join(_lines(cells[-1]))  # the cell after the number
                number = count["id"].removeprefix("point")
                heading = " – ".join(part for part in (section, subsection) if part) or None
                articles.append(Article(number, heading, [Paragraph(None, text)], count["id"]))
            elif "hereby rules" in block.get_text():
                ruling = _lines(block)
        elif block.name == "table" and ruling is not None:
            if block.find(class_="coj-signaturecase"):
                break
            ruling.extend(_lines(block))
    if ruling:
        articles.append(Article("operative part", "Ruling", [Paragraph(None, "\n".join(ruling))]))
    return articles
