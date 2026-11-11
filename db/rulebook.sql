-- Rulebook: the law texts the agent searches and cites.
--
-- listingaudit.rulebook.build drops the schema and reruns this file, so there is
-- no IF NOT EXISTS (except for the extension, which lives outside the schema).
-- The agent's role gets read-only access.

-- pgvector: the vector type and distance operators such as <=>
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA rulebook;

-- One row per law text
CREATE TABLE rulebook.laws (
    law_id    TEXT PRIMARY KEY,   -- short id, e.g. 'gpsr'
    name      TEXT NOT NULL,      -- how citations name it, e.g. 'GPSR'
    title     TEXT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('act', 'guidance', 'judgment')),
    language  TEXT NOT NULL CHECK (language IN ('en', 'de')),
    url       TEXT NOT NULL,
    version   TEXT NOT NULL       -- which version of the text is used
);

-- One row per searchable chunk: a whole article, or one or more paragraphs of a
-- long one. Article and paragraph numbers are kept so every hit can be cited.
CREATE TABLE rulebook.chunks (
    chunk_id   TEXT PRIMARY KEY,  -- e.g. 'gpsr/19' or 'pangv/4/1'
    law_id     TEXT NOT NULL REFERENCES rulebook.laws,
    language   TEXT NOT NULL CHECK (language IN ('en', 'de')),
    position   INTEGER NOT NULL,  -- order within the law
    article    TEXT NOT NULL,     -- '19', '6a', 'Annex I'; for PAngV the § number
    paragraph  TEXT,              -- '1', '1-3' for several short ones, NULL for none
    heading    TEXT,              -- the article's title
    citation   TEXT NOT NULL,     -- e.g. 'GPSR Art. 19', 'PAngV § 4(1)'
    text       TEXT NOT NULL,     -- the law text, as quoted in a report
    url        TEXT NOT NULL,     -- the law on the official website

    -- Keyword search: stemmed words of the citation, heading and text, using the
    -- English or German configuration to match the law's language
    search_words TSVECTOR GENERATED ALWAYS AS (
        to_tsvector(
            CASE language WHEN 'de' THEN 'german'::regconfig ELSE 'english'::regconfig END,
            citation || ' ' || coalesce(heading, '') || ' ' || text
        )
    ) STORED,

    -- Meaning search (dimension must match embed.DIMENSIONS)
    embedding  VECTOR(384) NOT NULL,

    UNIQUE (law_id, position)
);

-- No indexes: with about 540 chunks a full scan takes a few milliseconds. BM25
-- needs every chunk's words anyway, and an exact vector scan beats an approximate
-- HNSW index, which only pays off on much larger tables and can miss true
-- nearest neighbours.
