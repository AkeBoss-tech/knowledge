BEGIN;

CREATE TABLE IF NOT EXISTS krail_hosted_record (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    record_kind text NOT NULL CHECK (record_kind IN ('capture','capture_revision','projection','idempotency','tombstone')),
    record_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 1),
    record jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, record_kind, record_id)
);

CREATE INDEX IF NOT EXISTS krail_hosted_record_project_kind
    ON krail_hosted_record (tenant_id, project_id, record_kind, record_id);

COMMIT;
