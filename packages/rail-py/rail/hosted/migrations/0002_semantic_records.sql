BEGIN;

CREATE TABLE IF NOT EXISTS krail_semantic_record (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    record_kind text NOT NULL CHECK (record_kind IN (
        'type','entity','fact','alias','conflict','entity_merge',
        'semantic_pack','pack_evaluation','ontology_package',
        'ontology_package_version','ontology_change_set'
    )),
    record_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 1),
    record jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, record_kind, record_id)
);

CREATE INDEX IF NOT EXISTS krail_semantic_record_project_kind
    ON krail_semantic_record (tenant_id, project_id, record_kind, record_id);

COMMIT;
