"""Parse, chunk and embed the law texts, and load the rulebook into Postgres.

    uv run python -m listingaudit.rulebook.build

Downloads any missing law texts first, and on the first run the embedding model
(about 470 MB) into data/models. Every run rebuilds the rulebook from scratch.
"""

import time

from pgvector.psycopg import register_vector
from psycopg import Connection, sql

from listingaudit.config import PROJECT_ROOT, settings
from listingaudit.db import connect
from listingaudit.rulebook import download
from listingaudit.rulebook.chunk import Chunk, chunk_law
from listingaudit.rulebook.embed import get_embedder
from listingaudit.rulebook.parse import (
    Article,
    parse_eurlex_act,
    parse_eurlex_guidance,
    parse_eurlex_judgment,
    parse_gii_xml,
    read_gii_zip,
)
from listingaudit.rulebook.sources import SOURCES, LawSource

RULEBOOK_SCHEMA_FILE = PROJECT_ROOT / "db" / "rulebook.sql"


def load_articles(source: LawSource) -> list[Article]:
    data = download.raw_file(source).read_bytes()
    if source.format == "gii_xml":
        return parse_gii_xml(read_gii_zip(data))
    html = data.decode("utf-8")
    if source.kind == "guidance":
        return parse_eurlex_guidance(html)
    if source.kind == "judgment":
        return parse_eurlex_judgment(html)
    return parse_eurlex_act(html, skip_annexes=source.skip_annexes)


def build_chunks() -> list[Chunk]:
    return [chunk for source in SOURCES for chunk in chunk_law(source, load_articles(source))]


def grant_agent_access(conn: Connection) -> bool:
    """Grant the agent's role read access; False if market.build hasn't created it yet."""
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", [settings.agent_db_user]
    ).fetchone()
    if not exists:
        return False
    agent = sql.Identifier(settings.agent_db_user)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA rulebook TO {}").format(agent))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA rulebook TO {}").format(agent))
    return True


def save(conn: Connection, chunks: list[Chunk], embeddings) -> None:
    conn.execute("DROP SCHEMA IF EXISTS rulebook CASCADE")
    conn.execute(RULEBOOK_SCHEMA_FILE.read_text())
    # Register the vector type with psycopg. This needs the extension to exist, and
    # only cursors opened afterwards pick it up.
    register_vector(conn)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO rulebook.laws (law_id, name, title, kind, language, url, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(s.law_id, s.name, s.title, s.kind, s.language, s.url, s.version) for s in SOURCES],
        )
        cur.executemany(
            "INSERT INTO rulebook.chunks (chunk_id, law_id, language, position, article, "
            "paragraph, heading, citation, text, url, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    c.chunk_id,
                    c.law_id,
                    c.language,
                    c.position,
                    c.article,
                    c.paragraph,
                    c.heading,
                    c.citation,
                    c.text,
                    c.url,
                    embedding,
                )
                for c, embedding in zip(chunks, embeddings, strict=True)
            ],
        )


def main() -> None:
    download.main()
    chunks = build_chunks()
    print(f"Cut {len(SOURCES)} law texts into {len(chunks)} chunks. Embedding them...")
    started = time.perf_counter()
    embeddings = get_embedder().passages([c.search_text for c in chunks], progress=True)
    print(f"Embedded in {time.perf_counter() - started:.0f} s.")
    # One transaction: if anything fails, the database keeps its old rulebook
    with connect() as conn:
        save(conn, chunks, embeddings)
        granted = grant_agent_access(conn)
    for source in SOURCES:
        count = sum(c.law_id == source.law_id for c in chunks)
        print(f"  {source.law_id:14} {count:4} chunks  {source.name}")
    if granted:
        print(f"The agent's login '{settings.agent_db_user}' can read the rulebook.")
    else:
        print("No agent login yet: run python -m listingaudit.market.build, then this again.")


if __name__ == "__main__":
    main()
