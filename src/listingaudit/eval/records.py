"""What an evaluation keeps: a record of each audit, and the answer key it is graded on.

Both are saved under results/<split>/ and committed with the code, so the grader
(python -m listingaudit.eval.grade) runs anywhere, CI included, without the database
or a language model.
"""

import gzip
import json
import re
from pathlib import Path
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field

from listingaudit.agent.report import Report
from listingaudit.checks.evaluate import load_answer_key
from listingaudit.checks.run import load_listings
from listingaudit.config import PROJECT_ROOT
from listingaudit.market.models import Case, Offer, PricePoint, Problem, Trap
from listingaudit.market.rules import Rule

RESULTS_DIR = PROJECT_ROOT / "results"

System = Literal["agent", "rules_only", "one_prompt"]
Listing = tuple[Offer, list[PricePoint]]
SYSTEMS: list[System] = ["agent", "rules_only", "one_prompt"]


class Record(BaseModel):
    """One audit of one listing by one system."""

    system: System
    offer_id: str
    repeat: int = 0  # 0 for the main run; 1 and up for the runs that test consistency
    kind: str | None = None  # the kind of product, as the system judged it
    rules: list[Rule] = Field(default_factory=list)  # the rules it checked
    results: list[dict[str, Any]] = Field(default_factory=list)  # the code checks' results
    # The (law, article) of each passage quoted for a possible problem, by rule
    cited: dict[Rule, list[tuple[str, str]]] = Field(default_factory=dict)
    report: Report | None = None
    seconds: float = 0.0
    tokens: dict[str, dict[str, int]] = Field(default_factory=dict)  # by model
    cost: float | None = None  # US dollars at list price; None for an unknown model
    trace_id: str | None = None
    error: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return self.offer_id, self.repeat


class AnswerKey(BaseModel):
    split: str
    cases: list[Case]
    problems: list[Problem]
    traps: list[Trap]


ARTICLE_PREFIX = re.compile(r"^(article|art\.?|section|para\.?|§)\s*", re.IGNORECASE)


def bare_article(article: str) -> str:
    """An article without its prefix: 'Art. 19(a)' -> '19(a)', 'Annex VIII' stays."""
    return ARTICLE_PREFIX.sub("", article.strip())


def article_key(article: str) -> str:
    """An article as chunk ids write it: 'Art. 19(a)' -> '19', 'Annex VIII' -> 'annex_viii'."""
    return "_".join(re.split(r"[(,]", bare_article(article).lower())[0].split())


def source_of(chunk_id: str) -> tuple[str, str]:
    """The law and article of a rulebook passage: 'pangv/11/1#2' -> ('pangv', '11')."""
    law, _, rest = chunk_id.partition("/")
    return law, rest.split("#")[0].split("/")[0]


def folder(split: str) -> Path:
    return RESULTS_DIR / split


def records_path(split: str, system: System) -> Path:
    return folder(split) / f"{system}.jsonl.gz"


def read_records(path: Path) -> list[Record]:
    """The records of a file; a missing file has none."""
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        return [Record.model_validate_json(line) for line in lines if line.strip()]


def write_records(path: Path, records: list[Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as out:
        for record in records:
            out.write(record.model_dump_json() + "\n")


def save_split(conn: psycopg.Connection, split: str) -> list[Listing]:
    """Copy a split's listings and answer key from the database to results/<split>/."""
    listings = load_listings(conn, split=split)
    cases, problems, traps = load_answer_key(conn, split)
    key = AnswerKey(split=split, cases=cases, problems=problems, traps=traps)
    folder(split).mkdir(parents=True, exist_ok=True)
    (folder(split) / "answer_key.json").write_text(
        key.model_dump_json(indent=1) + "\n", encoding="utf-8"
    )
    with gzip.open(folder(split) / "listings.jsonl.gz", "wt", encoding="utf-8") as out:
        for offer, history in listings:
            prices = [(str(point.day), str(point.price)) for point in history]
            out.write(json.dumps({"listing": offer.model_dump(mode="json"), "history": prices}))
            out.write("\n")
    return listings


def read_listings(split: str) -> dict[str, Listing]:
    """A split's listings with their price histories, by offer id."""
    listings = {}
    with gzip.open(folder(split) / "listings.jsonl.gz", "rt", encoding="utf-8") as lines:
        for line in lines:
            row = json.loads(line)
            offer = Offer.model_validate(row["listing"])
            history = [
                PricePoint(offer_id=offer.offer_id, day=day, price=price)
                for day, price in row["history"]
            ]
            listings[offer.offer_id] = (offer, history)
    return listings


def read_answer_key(split: str) -> AnswerKey:
    path = folder(split) / "answer_key.json"
    return AnswerKey.model_validate_json(path.read_text(encoding="utf-8"))


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
