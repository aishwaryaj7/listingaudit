-- Synthetic marketplace and its answer key.
--
-- Two schemas keep them apart:
--   market      what the agent may see: the listings, as a shopper would see them
--   answer_key  what only the grader may see: which listings break which rule
-- The agent logs in with its own role that can read "market" and nothing else.

CREATE SCHEMA market;
CREATE SCHEMA answer_key;

-- The marketplace's "today": every price history ends on this day
CREATE TABLE market.snapshot (
    snapshot_date  DATE NOT NULL
);

CREATE TABLE market.sellers (
    seller_id  INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    country    TEXT NOT NULL,
    email      TEXT NOT NULL
);

-- One row per listing, with everything its product page shows
CREATE TABLE market.offers (
    offer_id       TEXT PRIMARY KEY,    -- P0001 to P0400 (practice), E0001 to E0400 (exam)
    split          TEXT NOT NULL CHECK (split IN ('practice', 'exam')),
    seller_id      INTEGER NOT NULL REFERENCES market.sellers,
    product_type   TEXT NOT NULL,
    title          TEXT NOT NULL,
    brand          TEXT,
    color          TEXT,
    bullet_points  TEXT[] NOT NULL,
    description    TEXT,
    image_id       TEXT NOT NULL,
    image_file     TEXT NOT NULL,       -- relative to the data folder

    -- Price box (NUMERIC, never float, so amounts are exact)
    price             NUMERIC(10, 2) NOT NULL CHECK (price > 0),
    was_price         NUMERIC(10, 2),   -- the crossed-out price
    was_price_kind    TEXT CHECK (was_price_kind IN ('previous_price', 'rrp')),
    discount_percent  INTEGER CHECK (discount_percent BETWEEN 1 AND 99),
    net_quantity      TEXT,
    unit_price        NUMERIC(10, 2),
    unit_price_per    TEXT CHECK (unit_price_per IN ('kg', 'l', '100 g', '100 ml')),

    -- Product safety information (GPSR)
    manufacturer_name     TEXT,
    manufacturer_address  TEXT,
    manufacturer_country  TEXT,
    manufacturer_email    TEXT,
    manufacturer_website  TEXT,
    eu_rp_name            TEXT,         -- the EU responsible person
    eu_rp_address         TEXT,
    eu_rp_email           TEXT,
    safety_warnings       TEXT,

    -- Textiles and lighting
    fibre_composition  TEXT,
    energy_class       TEXT CHECK (energy_class IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')),
    energy_label_url   TEXT,
    product_sheet_url  TEXT,

    CHECK (was_price > price)
);

-- The price of every listing on each of the 90 days up to the snapshot date
CREATE TABLE market.price_history (
    offer_id  TEXT NOT NULL REFERENCES market.offers,
    day       DATE NOT NULL,
    price     NUMERIC(10, 2) NOT NULL CHECK (price > 0),
    PRIMARY KEY (offer_id, day)
);

-- One row per listing: is it clean, does it break a rule, or is it a trap?
CREATE TABLE answer_key.cases (
    offer_id          TEXT PRIMARY KEY REFERENCES market.offers,
    split             TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('clean', 'problem', 'tricky')),
    source_item_id    TEXT NOT NULL,    -- ABO product the listing is built on
    applicable_rules  TEXT[] NOT NULL,  -- rules the listing must follow
    sneaky_note       TEXT              -- text planted to talk the agent out of its job
);

-- Every rule break planted, with the facts that prove it
CREATE TABLE answer_key.problems (
    offer_id    TEXT NOT NULL REFERENCES market.offers,
    problem     TEXT NOT NULL,
    rule        TEXT NOT NULL,
    difficulty  TEXT NOT NULL CHECK (difficulty IN ('easy', 'hard')),
    evidence    JSONB NOT NULL,
    PRIMARY KEY (offer_id, problem)
);

-- Listings that look wrong but are legal, and why
CREATE TABLE answer_key.traps (
    offer_id   TEXT PRIMARY KEY REFERENCES market.offers,
    trap       TEXT NOT NULL,
    rule       TEXT NOT NULL,
    why_legal  TEXT NOT NULL
);
