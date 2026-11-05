-- Tables for step 1: the raw product catalogue from Amazon Berkeley Objects (ABO).
-- Every statement uses IF NOT EXISTS, so running this file twice is harmless.

-- One row per photo in the ABO catalogue (from images.csv.gz)
CREATE TABLE IF NOT EXISTS images (
    image_id    TEXT PRIMARY KEY,
    height      INTEGER NOT NULL,
    width       INTEGER NOT NULL,
    path           TEXT NOT NULL,   -- where the photo lives on the ABO server
    used_by_items  INTEGER,         -- how many different products use it as main photo
    used_by_types  INTEGER,         -- ... and across how many product types
    local_file     TEXT             -- filled in once we have downloaded it
);

-- One row per product listing. The same product (item_id) can be listed on
-- several Amazon sites, so a listing is identified by item_id + domain_name.
CREATE TABLE IF NOT EXISTS listings (
    id               BIGSERIAL PRIMARY KEY,
    item_id          TEXT NOT NULL,
    domain_name      TEXT NOT NULL,
    marketplace      TEXT,
    country          TEXT,
    product_type     TEXT,
    text_language    TEXT,          -- which language the text columns below are in
    item_name        TEXT,
    brand            TEXT,
    color            TEXT,
    material         TEXT,
    fabric_type      TEXT,
    bullet_points    TEXT[],
    description      TEXT,
    weight_grams     DOUBLE PRECISION,
    main_image_id    TEXT REFERENCES images (image_id),
    other_image_ids  TEXT[],
    category_path    TEXT,
    raw              JSONB NOT NULL,  -- the original record, so nothing is lost
    UNIQUE (item_id, domain_name)
);

CREATE INDEX IF NOT EXISTS listings_product_type_idx ON listings (product_type);
