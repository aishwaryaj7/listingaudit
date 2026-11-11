"""Search the rulebook by keyword, by meaning, or both (hybrid).

- keyword: Postgres full-text search ranked with BM25. Good at exact and rare
  terms such as "Grundpreis" or "cashgora".
- meaning: pgvector nearest neighbours of the question's embedding. Works when
  the question uses other words, or another language, than the law.
- hybrid: both rankings merged with Reciprocal Rank Fusion (RRF).

Meaning search is the default: it scored best on the test questions
(python -m listingaudit.rulebook.evaluate), which use everyday words that the
laws rarely share. Every hit carries its citation and a link.
"""

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from listingaudit.rulebook.embed import Embedder, get_embedder

Method = Literal["keyword", "meaning", "hybrid"]
DEFAULT_METHOD: Method = "meaning"


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    citation: str
    heading: str | None
    text: str
    url: str
    score: float


# BM25 instead of Postgres' ts_rank, which ignores how rare a word is, so common
# words like "price" outweigh rare ones like "rayon". k1 caps the gain from
# repeated words; b sets how much long chunks are penalised.
BM25_K1 = 1.2
BM25_B = 0.75

KEYWORD_SQL = """
    WITH question AS (  -- question words, stemmed for both languages ("reductions" -> "reduct")
        SELECT 'en' AS language,
               unnest(tsvector_to_array(to_tsvector('english', %(query)s))) AS word
        UNION
        SELECT 'de', unnest(tsvector_to_array(to_tsvector('german', %(query)s)))
    ), chunk_words AS (  -- term frequency of each word in each chunk
        SELECT c.chunk_id, c.language, w.lexeme AS word, array_length(w.positions, 1) AS count
        FROM rulebook.chunks c, unnest(c.search_words) AS w
        WHERE %(laws)s::text[] IS NULL OR c.law_id = ANY(%(laws)s)
    ), lengths AS (
        SELECT chunk_id, sum(count) AS length FROM chunk_words GROUP BY chunk_id
    ), corpus AS (
        SELECT count(*) AS chunks, avg(length) AS average_length FROM lengths
    ), rarity AS (  -- idf: high for words that only a few chunks contain
        SELECT word, language,
               ln(1 + (corpus.chunks - count(*) + 0.5) / (count(*) + 0.5)) AS idf
        FROM chunk_words JOIN question USING (word, language), corpus
        GROUP BY word, language, corpus.chunks
    )
    SELECT c.chunk_id, c.citation, c.heading, c.text, c.url,
           sum(r.idf * w.count * (%(k1)s + 1)
               / (w.count + %(k1)s * (1 - %(b)s + %(b)s * l.length / corpus.average_length)))
               AS score
    FROM chunk_words w
    JOIN rarity r USING (word, language)
    JOIN lengths l USING (chunk_id)
    JOIN rulebook.chunks c USING (chunk_id)
    CROSS JOIN corpus
    GROUP BY c.chunk_id
    ORDER BY score DESC, c.chunk_id
    LIMIT %(k)s
"""

MEANING_SQL = """
    -- <=> is cosine distance: 0 for the same direction, 2 for opposite
    SELECT chunk_id, citation, heading, text, url, 1 - (embedding <=> %(vector)s) AS score
    FROM rulebook.chunks
    WHERE %(laws)s::text[] IS NULL OR law_id = ANY(%(laws)s)
    ORDER BY embedding <=> %(vector)s, chunk_id
    LIMIT %(k)s
"""


def _hits(conn: psycopg.Connection, query: str, params: dict) -> list[Hit]:
    return [Hit(*row) for row in conn.execute(query, params).fetchall()]


def keyword_search(
    conn: psycopg.Connection, query: str, k: int = 5, laws: list[str] | None = None
) -> list[Hit]:
    params = {"query": query, "k": k, "laws": laws, "k1": BM25_K1, "b": BM25_B}
    return _hits(conn, KEYWORD_SQL, params)


def meaning_search(
    conn: psycopg.Connection, vector: np.ndarray, k: int = 5, laws: list[str] | None = None
) -> list[Hit]:
    register_vector(conn)  # lets psycopg send numpy arrays as pgvector values
    return _hits(conn, MEANING_SQL, {"vector": vector, "k": k, "laws": laws})


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Merge rankings (lists of ids, best first) into one, best first.

    k = 60 comes from the paper that introduced RRF (Cormack et al., 2009). It
    keeps one list's first place from outweighing good places in both lists.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def hybrid_search(
    conn: psycopg.Connection,
    query: str,
    k: int = 5,
    laws: list[str] | None = None,
    embedder: Embedder | None = None,
    candidates: int = 30,
) -> list[Hit]:
    embedder = embedder or get_embedder()
    by_keyword = keyword_search(conn, query, candidates, laws)
    by_meaning = meaning_search(conn, embedder.query(query), candidates, laws)
    fused = reciprocal_rank_fusion(
        [[hit.chunk_id for hit in by_keyword], [hit.chunk_id for hit in by_meaning]]
    )
    hits = {hit.chunk_id: hit for hit in by_keyword + by_meaning}
    return [replace(hits[chunk_id], score=score) for chunk_id, score in fused[:k]]


def search(
    conn: psycopg.Connection,
    query: str,
    method: Method = DEFAULT_METHOD,
    k: int = 5,
    laws: list[str] | None = None,
    embedder: Embedder | None = None,
) -> list[Hit]:
    if method == "keyword":
        return keyword_search(conn, query, k, laws)
    if method == "meaning":
        return meaning_search(conn, (embedder or get_embedder()).query(query), k, laws)
    return hybrid_search(conn, query, k, laws, embedder)
