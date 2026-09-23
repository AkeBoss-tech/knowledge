"""Offline, source-pinned company ownership story. Requires current source checkout."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from krail.provider.v1 import ResourceRef
from rail.authorized_context import HostedAccessContextAuthorizer
from rail.company_knowledge import (
    COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
    COMPANY_POLICY_QUERY_OPERATOR_ID,
    OWNERSHIP_SCHEMA,
    POLICY_SCHEMA,
    SERVICE_SCHEMA,
    TEAM_SCHEMA,
    CompanyKnowledgeService,
    EffectivePolicyQuery,
    ServiceOwnershipQuery,
    company_knowledge_extension,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority
from rail.procedure_projection import TemporalProjectionService
from rail.temporal_records import create_temporal_record


HERE = Path(__file__).resolve().parent
MONDAY = datetime(2026, 9, 7, 9, tzinfo=UTC)
TUESDAY = MONDAY + timedelta(days=1)
THURSDAY = MONDAY + timedelta(days=3)
FRIDAY = MONDAY + timedelta(days=4)
CATALOG = "catalog://fictional-company"


class FixtureWriter:
    def authorize(self, record, *, at):
        assert at.tzinfo is not None and record.authority == CATALOG


class FixtureInvalidation:
    def __init__(self, target):
        self.target = target.exact_key

    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
        assert event_id and changed_ref.exact_key == self.target
        assert event_digest.startswith("sha256:") and at.tzinfo is not None


def fixture(name: str):
    path = HERE / "fixtures" / name
    data = path.read_bytes()
    ref = ResourceRef(
        authority="fixture://company-brain",
        resource_type="document",
        resource_id=f"fixtures/{name}",
        version="1",
        digest="sha256:" + sha256(data).hexdigest(),
    )
    return json.loads(data), ref


def record(schema, entity, revision, payload, source, *, valid_at=MONDAY,
           recorded_at=MONDAY, supersedes=None):
    return create_temporal_record(
        record_id=f"company:{schema}:{entity}:{revision}",
        entity_id=entity,
        entity_authority="company://fictional",
        payload_schema=schema,
        payload_schema_version="1.0.0",
        kind="approved_state",
        authority=CATALOG,
        writer_family="reviewed-fixture-import",
        valid_from=valid_at,
        recorded_at=recorded_at,
        ingested_at=recorded_at,
        source_refs=(source,),
        revision=revision,
        payload=payload,
        supersedes_digest=supersedes,
    )


def reader_for(service, records, operator_id):
    refs = tuple(dict.fromkeys(
        ref for item in records for ref in
        (service.projection.record_ref(item), *item.source_refs)
    ))
    operator = next(item for item in company_knowledge_extension().operators
                    if item.operator_id == operator_id)
    authority = AccessContextAuthority(
        {"fixture": b"company-example-only"}, issuer="https://fixture.example.test"
    )
    claims = AccessClaims(
        issuer="https://fixture.example.test",
        tenant_id="fictional", project_id="knowledge",
        subject="reader/example", delegator="user/example",
        delegation_id=f"delegation/{operator_id}",
        capability_id=operator.operator_id,
        capability_version=operator.version,
        capability_digest=operator.descriptor_digest,
        actions=("context.read",),
        source_ids=tuple(dict.fromkeys(ref.resource_id for ref in refs)),
        classifications=("internal",),
        policy_digest="sha256:" + "d" * 64,
        issued_at=MONDAY, not_before=MONDAY,
        expires_at=FRIDAY + timedelta(days=1),
        nonce=f"company-example/{operator_id}",
    )
    return HostedAccessContextAuthorizer(
        authority, authority.issue(claims, key_id="fixture"),
        exact_refs=refs, clock=lambda: FRIDAY,
    )


def answer(service, reader, valid_at, known_at):
    result, _lineage = service.owner(
        ServiceOwnershipQuery(
            service_id="billing-api", valid_at=valid_at, known_at=known_at,
        ), reader=reader,
    )
    return result


def main():
    repo, repo_ref = fixture("service-repository.json")
    ticket, ticket_ref = fixture("ownership-change-ticket.json")
    policy, policy_ref = fixture("policy-document-export.json")
    assert policy["review_state"] == "approved fixture" and policy_ref.digest
    with tempfile.TemporaryDirectory(prefix="krail-company-brain-") as temp:
        path = str(Path(temp) / "semantic.json")
        projection = TemporalProjectionService(
            path, tenant_id="fictional", project_id="knowledge", clock=lambda: FRIDAY
        )
        team_records = [record(
            TEAM_SCHEMA, team, "1",
            {"team_id": team, "display_name": team,
             "source_authority": CATALOG}, repo_ref,
        ) for team in repo["teams"]]
        service_record = record(
            SERVICE_SCHEMA, repo["service_id"], "1",
            {"service_id": repo["service_id"],
             "display_name": repo["service_name"],
             "source_authority": CATALOG}, repo_ref,
        )
        old = record(
            OWNERSHIP_SCHEMA, repo["service_id"], "1",
            {"service_id": repo["service_id"],
             "owner_team_id": repo["initial_owner"],
             "assertion_status": "approved", "source_authority": CATALOG},
            repo_ref,
        )
        changed = record(
            OWNERSHIP_SCHEMA, ticket["service_id"], "2",
            {"service_id": ticket["service_id"],
             "owner_team_id": ticket["new_owner"],
             "assertion_status": "approved", "source_authority": CATALOG},
            ticket_ref, valid_at=TUESDAY, recorded_at=THURSDAY,
            supersedes=old.record_digest,
        )
        policy_record = record(
            POLICY_SCHEMA, policy["policy_id"], "1",
            {"policy_id": policy["policy_id"], "scope": policy["scope"],
             "rule": policy["rule"], "assertion_status": "approved",
             "source_authority": CATALOG}, policy_ref,
        )
        for item in (*team_records, service_record, old, policy_record):
            projection.ingest(item, at=FRIDAY, writer=FixtureWriter())
        records = (*team_records, service_record, old, changed, policy_record)
        service = CompanyKnowledgeService(projection)
        reader = reader_for(service, records, COMPANY_OWNERSHIP_QUERY_OPERATOR_ID)
        policy_reader = reader_for(service, records, COMPANY_POLICY_QUERY_OPERATOR_ID)

        release_policy, _ = service.effective_policy(
            EffectivePolicyQuery(
                policy_id=policy["policy_id"], scope=policy["scope"],
                valid_at=FRIDAY, known_at=FRIDAY,
            ), reader=policy_reader,
        )
        assert release_policy.status == "effective" and release_policy.rule == policy["rule"]
        assert release_policy.evidence_refs == (policy_ref,)

        before = answer(service, reader, MONDAY, MONDAY)
        assert (before.status, before.owner_team_id) == ("owned", "team-ledger")
        print("Monday owner:", before.owner_team_id, "source:", before.evidence_refs[0].resource_id)

        # The approved fixture ticket is introduced only after the first query.
        projection.ingest(changed, at=FRIDAY, writer=FixtureWriter())
        then_known = answer(service, reader, TUESDAY, TUESDAY)
        revised_history = answer(service, reader, TUESDAY, THURSDAY)
        current = answer(service, reader, FRIDAY, FRIDAY)
        assert (before.status, before.owner_team_id) == ("owned", "team-ledger")
        assert (then_known.status, then_known.owner_team_id) == ("owned", "team-ledger")
        assert (revised_history.status, revised_history.owner_team_id) == ("owned", "team-platform")
        assert (current.status, current.owner_team_id) == ("owned", "team-platform")
        assert current.evidence_refs == (ticket_ref,)
        print("Tuesday as known Tuesday:", then_known.owner_team_id)
        print("Tuesday as known Thursday:", revised_history.owner_team_id)
        print("Current owner:", current.owner_team_id, "source:", current.evidence_refs[0].resource_id)
        print("Current release policy:", release_policy.rule)

        reopened = CompanyKnowledgeService(TemporalProjectionService(
            path, tenant_id="fictional", project_id="knowledge", clock=lambda: FRIDAY
        ))
        assert answer(reopened, reader, FRIDAY, FRIDAY) == current
        reopened.projection.tombstone(
            ticket_ref, event_id="ticket-withdrawn",
            reason="ownership ticket source withdrawn",
            effective_at=FRIDAY, recorded_at=FRIDAY,
            authorizer=FixtureInvalidation(ticket_ref),
        )
        stale = answer(reopened, reader, FRIDAY, FRIDAY)
        assert stale.status == "stale" and stale.owner_team_id is None
        assert answer(reopened, reader, MONDAY, MONDAY) == before
        unaffected_policy, _ = reopened.effective_policy(
            EffectivePolicyQuery(
                policy_id=policy["policy_id"], scope=policy["scope"],
                valid_at=FRIDAY, known_at=FRIDAY,
            ), reader=policy_reader,
        )
        assert unaffected_policy == release_policy
        print("After ticket invalidation:", stale.status, stale.reason)
        print("Earlier answer retained:", before.owner_team_id)


if __name__ == "__main__":
    main()
