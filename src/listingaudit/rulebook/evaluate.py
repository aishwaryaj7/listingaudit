"""Compare the search methods on the test questions in questions.yaml.

    uv run python -m listingaudit.rulebook.evaluate

Each question lists the articles that answer it. Metrics per method:

- hit@1: the first result is a right article
- hit@5: a right article is in the top five (the agent reads five)
- MRR: mean of 1 / rank of the first right article, 0 if none is in the top ten
"""

from dataclasses import dataclass
from importlib.resources import files

import pandas as pd
import psycopg
import yaml

from listingaudit.db import connect
from listingaudit.rulebook.embed import Embedder, get_embedder
from listingaudit.rulebook.search import DEFAULT_METHOD, Hit, Method, search

METHODS: list[Method] = ["keyword", "meaning", "hybrid"]


@dataclass(frozen=True)
class Question:
    question: str
    answers: list[str]  # "law/article", or just "law" when any part of that law answers it


def load_questions() -> list[Question]:
    text = files("listingaudit.rulebook").joinpath("questions.yaml").read_text(encoding="utf-8")
    return [Question(**item) for item in yaml.safe_load(text)]


def answers_question(chunk_id: str, answers: list[str]) -> bool:
    """Is this chunk part of one of the right articles? ('gpsr/19/1' is part of 'gpsr/19')"""
    law, article = (chunk_id.split("#")[0].split("/") + [""])[:2]
    return any(answer in (law, f"{law}/{article}") for answer in answers)


def first_right_rank(hits: list[Hit], answers: list[str]) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if answers_question(hit.chunk_id, answers):
            return rank
    return None


def run(conn: psycopg.Connection, embedder: Embedder, k: int = 10) -> pd.DataFrame:
    """One row per question and method, with the rank of the first right article."""
    rows = []
    for question in load_questions():
        for method in METHODS:
            hits = search(conn, question.question, method, k=k, embedder=embedder)
            rows.append(
                {
                    "question": question.question,
                    "method": method,
                    "rank": first_right_rank(hits, question.answers),
                    "top_result": hits[0].citation if hits else None,
                }
            )
    return pd.DataFrame(rows)


def scores(results: pd.DataFrame) -> pd.DataFrame:
    rank = results["rank"]
    table = results.assign(
        hit_at_1=rank.eq(1),
        hit_at_5=rank.le(5),
        reciprocal_rank=(1 / rank).fillna(0.0),
    )
    summary = table.groupby("method", sort=False)[
        ["hit_at_1", "hit_at_5", "reciprocal_rank"]
    ].mean()
    return summary.rename(columns={"reciprocal_rank": "mrr"}).round(2)


def main() -> None:
    with connect() as conn:
        results = run(conn, get_embedder())
    print(f"{results['question'].nunique()} questions\n")
    print(scores(results).to_string())
    missed = results[(results["method"] == DEFAULT_METHOD) & results["rank"].isna()]
    if not missed.empty:
        print(f"\nNot in the first ten results of {DEFAULT_METHOD} search (the default):")
        for question in missed["question"]:
            print(f"  - {question}")


if __name__ == "__main__":
    main()
