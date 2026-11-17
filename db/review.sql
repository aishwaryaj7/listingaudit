-- The audit log of the review service. It lives in the review schema (REVIEW_SCHEMA),
-- next to the tables of LangGraph's checkpointer, which keep the audits waiting for a
-- person. Every statement can run twice without harm.

-- Every report the agent writes, and every decision a person makes on one.
CREATE TABLE IF NOT EXISTS audit_log (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    audit_id  UUID NOT NULL,
    offer_id  TEXT NOT NULL,
    event     TEXT NOT NULL CHECK (event IN ('reported', 'reviewed')),
    data      JSONB NOT NULL,
    UNIQUE (audit_id, event)
);

-- Rows are only ever added: the log refuses changes, deletions and truncation.
CREATE OR REPLACE FUNCTION refuse_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The audit log only takes new rows';
END $$;

CREATE OR REPLACE TRIGGER audit_log_only_grows
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION refuse_change();

CREATE OR REPLACE TRIGGER audit_log_never_empties
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_change();
