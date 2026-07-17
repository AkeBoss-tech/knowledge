from __future__ import annotations

from pathlib import Path
from typing import Any

from rail.integrity import ResearchIntegrityRepo, build_artifact_trust_summary, build_claim_state, build_source_state


DETAIL_COMMANDS = {
    "sources": "krail --local integrity sources",
    "sourceCandidates": "krail --local integrity source-candidates",
    "claims": "krail --local integrity claims",
    "claimCandidates": "krail --local integrity claim-candidates",
    "artifacts": "krail --local integrity artifacts",
    "staleGraph": "krail --local integrity stale-graph",
    "verificationRuns": "krail --local integrity verification-runs",
}


def _reference_key(value: str) -> str:
    return value.split("#", 1)[-1].strip()


def _item(kind: str, key: str, label: str, state: str, reason: str, command: str, repair: str | None = None) -> dict[str, Any]:
    result = {"entityType": kind, "key": key, "label": label, "state": state, "reason": reason, "detailCommand": command}
    if repair:
        result["repairCommand"] = repair
    return result


def _verification_status(path: str, artifact: Any, runs: list[Any]) -> str:
    if artifact.promotion_state == "stale":
        return "stale"
    referenced = {_reference_key(value) for value in artifact.verification_runs}
    statuses = [run.status for run in runs if run.run_id in referenced or path in run.artifact_paths]
    for status in ("failed", "blocked", "pending"):
        if status in statuses:
            return status
    return "passed" if statuses and all(status == "passed" for status in statuses) else "unverified"


def _headline(status: str, trusted: dict[str, list[dict[str, Any]]], counts: dict[str, int]) -> str:
    if status == "empty":
        return "No integrity records have been promoted into trusted state yet."
    if status == "ready":
        return f"Trusted now: {len(trusted['sources'])} source(s), {len(trusted['claims'])} claim(s), and {len(trusted['artifacts'])} artifact(s)."
    if status == "conflict":
        return f"{counts['conflict']} open conflict(s) need resolution before promotion or release."
    if status == "blocked":
        return f"{counts['blocked']} blocked integrity item(s) need repair before promotion or release."
    if status == "stale":
        return f"{counts['stale']} stale integrity item(s) need refresh before promotion or release."
    return f"{counts['missing']} item(s) still lack the evidence needed for trusted promotion."


def build_local_integrity_status(project_root: str | Path) -> dict[str, Any]:
    repo = ResearchIntegrityRepo(project_root)
    repo.ensure_files_exist()
    indexes = repo.load_all()
    trusted: dict[str, list[dict[str, Any]]] = {"sources": [], "claims": [], "artifacts": []}
    attention: dict[str, list[dict[str, Any]]] = {"conflicts": [], "blocked": [], "stale": [], "missingEvidence": []}
    source_by_key = {source.source_key: source for source in indexes.sources}

    freshness_counts: dict[str, int] = {}
    admissibility_counts: dict[str, int] = {}
    for source in indexes.sources:
        state = build_source_state(source)
        freshness_counts[source.freshness_status] = freshness_counts.get(source.freshness_status, 0) + 1
        admissibility = str(state.get("admissibilityStatus") or "unknown")
        admissibility_counts[admissibility] = admissibility_counts.get(admissibility, 0) + 1
        entry = _item("source", source.source_key, source.title, source.freshness_status, "Refresh this source and rerun dependent analyses.", f"krail --local integrity source {source.source_key}")
        if state.get("isBlocked"):
            attention["blocked"].append(entry)
        elif state.get("needsRefresh") or state.get("isStale"):
            attention["stale"].append(entry)
        elif state.get("isFresh"):
            trusted["sources"].append(entry)

    open_conflicts = [conflict for conflict in indexes.conflicts if conflict.status != "resolved"]
    conflicted_claims: set[str] = set()
    for conflict in open_conflicts:
        left, right = _reference_key(conflict.left_ref), _reference_key(conflict.right_ref)
        conflicted_claims.update((left, right))
        attention["conflicts"].append(_item(
            "conflict", conflict.conflict_key, f"{left} vs {right}", conflict.status,
            conflict.recommended_resolution or "Resolve contradictory claims before promotion.",
            DETAIL_COMMANDS["claims"],
            f"krail --local integrity resolve-conflict {conflict.conflict_key} --status resolved",
        ))

    for claim in indexes.claims:
        linked_sources = [source_by_key[key] for key in claim.source_keys if key in source_by_key]
        chunks = [chunk for chunk in indexes.evidence_chunks if claim.claim_key in chunk.claim_keys]
        state = build_claim_state(claim, contradictory_claim_count=int(claim.claim_key in conflicted_claims), source_count=len(linked_sources), chunk_count=len(chunks))
        entry = _item("claim", claim.claim_key, claim.claim_text, claim.status, "Attach explicit evidence before relying on this claim.", f"krail --local integrity claim {claim.claim_key}")
        if claim.claim_key in conflicted_claims or claim.status == "conflicted":
            continue
        if claim.status == "stale" or any(source.freshness_status in {"stale", "needs_refresh"} for source in linked_sources):
            attention["stale"].append(entry)
        elif not state.get("evidenceComplete") or claim.status in {"draft", "unsupported", "needs_evidence"}:
            attention["missingEvidence"].append(entry)
        else:
            trusted["claims"].append(entry)

    promotion_counts: dict[str, int] = {}
    verification_counts: dict[str, int] = {}
    artifact_payloads: list[dict[str, Any]] = []
    for artifact in indexes.artifact_lineage:
        promotion_counts[artifact.promotion_state] = promotion_counts.get(artifact.promotion_state, 0) + 1
        verification = _verification_status(artifact.artifact_path, artifact, indexes.verification_runs)
        verification_counts[verification] = verification_counts.get(verification, 0) + 1
        trust = build_artifact_trust_summary(artifact, verification_status=verification, artifact_blocked=artifact.promotion_state in {"blocked", "needs_evidence"})
        entry = _item("artifact", artifact.artifact_path, artifact.title, artifact.promotion_state, str(trust.get("recommendedNextAction") or "Review artifact trust."), f"krail --local integrity artifact {artifact.artifact_path}")
        if artifact.promotion_state == "needs_evidence" and verification not in {"failed", "blocked"}:
            attention["missingEvidence"].append(entry)
        elif trust.get("isBlocked") or verification in {"failed", "blocked"}:
            attention["blocked"].append(entry)
        elif trust.get("isStale"):
            attention["stale"].append(entry)
        elif artifact.promotion_state in {"draft", "exploratory", "needs_evidence"} or not trust.get("hasEvidence"):
            attention["missingEvidence"].append(entry)
        elif trust.get("isTrusted"):
            trusted["artifacts"].append(entry)
        payload = artifact.model_dump(mode="json")
        payload.update({"verificationStatus": verification, "trustState": trust})
        artifact_payloads.append(payload)

    gaps: list[dict[str, Any]] = []
    for candidate in indexes.source_candidates:
        if candidate.status != "promoted":
            gaps.append({"kind": "source_candidate", "status": "candidate", "candidateKey": candidate.candidate_key, "paths": list(candidate.discovered_in_paths), "message": "Source candidate still needs review or promotion before it can support trusted knowledge."})
    for candidate in indexes.claim_candidates:
        if candidate.status != "promoted":
            external = [path for path in candidate.evidence_paths if path not in set(candidate.discovered_in_paths)]
            state = "needs_evidence" if candidate.source_candidate_keys or external else "unsupported"
            gaps.append({"kind": "claim_candidate", "status": state, "candidateKey": candidate.candidate_key, "paths": list(candidate.discovered_in_paths), "message": "Claim candidate is unsupported and remains a gap until explicit evidence is captured or linked."})

    if gaps and not attention["conflicts"] and not attention["blocked"] and not attention["stale"]:
        first = gaps[0]
        kind = "sourceCandidates" if first["kind"] == "source_candidate" else "claimCandidates"
        attention["missingEvidence"].append(_item(first["kind"], first["candidateKey"], first["candidateKey"], first["status"], first["message"], DETAIL_COMMANDS[kind]))

    has_records = bool(indexes.sources or indexes.claims or indexes.artifact_lineage or indexes.verification_runs)
    status = "conflict" if attention["conflicts"] else "blocked" if attention["blocked"] else "stale" if attention["stale"] else "missing_evidence" if attention["missingEvidence"] else "ready" if has_records else "empty"
    ready_for_promotion = status == "ready" and bool(trusted["claims"] or trusted["artifacts"])
    ready_for_release = ready_for_promotion and promotion_counts.get("verified", 0) > 0 and verification_counts.get("passed", 0) >= promotion_counts.get("verified", 0)
    counts = {"conflict": len(attention["conflicts"]), "blocked": len(attention["blocked"]), "stale": len(attention["stale"]), "missing": len(attention["missingEvidence"])}
    next_command = None
    for bucket in ("conflicts", "blocked", "stale", "missingEvidence"):
        if attention[bucket]:
            first = attention[bucket][0]
            next_command = {"command": first.get("repairCommand") or first["detailCommand"], "reason": first["reason"], "focus": {"entityType": first["entityType"], "key": first["key"]}}
            break

    source_payloads = [dict(source.model_dump(mode="json"), sourceState=build_source_state(source)) for source in indexes.sources]
    claim_payloads = [claim.model_dump(mode="json") for claim in indexes.claims]
    workflow_blockers = [item["key"] for item in attention["stale"] + attention["missingEvidence"] + attention["blocked"]]
    dataset_gaps = [
        artifact.artifact_path
        for artifact in indexes.artifact_lineage
        if artifact.artifact_type == "dataset" and not artifact.sources
    ]
    coding_gaps = [
        artifact.artifact_path
        for artifact in indexes.artifact_lineage
        if artifact.artifact_type != "dataset"
        and (not (artifact.inputs and artifact.scripts) or not artifact.verification_runs)
        and artifact.reproducibility_mode not in {"manual", "non_reproducible"}
    ]
    agent_workflow = {
        "research": {"status": "ready", "requirements": ["Separate facts, interpretations, and open questions."]},
        "data": {"status": "blocked" if dataset_gaps else "ready", "datasetsMissingProvenance": dataset_gaps},
        "coding": {"status": "blocked" if coding_gaps else "ready", "artifactsMissingLineageOrVerification": coding_gaps},
        "artifact": {"status": "blocked" if attention["missingEvidence"] else "ready", "unsupportedItems": [item["key"] for item in attention["missingEvidence"]]},
        "health": {"status": "blocked" if workflow_blockers else "ready", "blockers": workflow_blockers},
    }
    return {
        "mode": "local",
        "summary": {
            "status": status, "headline": _headline(status, trusted, counts), "readyForPromotion": ready_for_promotion, "readyForRelease": ready_for_release,
            "assumptionCount": len(indexes.assumptions), "sourceCount": len(indexes.sources), "claimCount": len(indexes.claims), "hypothesisCount": len(indexes.hypotheses),
            "artifactCount": len(indexes.artifact_lineage), "verificationRunCount": len(indexes.verification_runs), "conflictCount": len(open_conflicts),
            "trustedSourceCount": len(trusted["sources"]), "trustedClaimCount": len(trusted["claims"]), "trustedArtifactCount": len(trusted["artifacts"]),
            "blockedCount": counts["blocked"], "staleCount": counts["stale"], "missingEvidenceCount": counts["missing"],
            "staleArtifactCount": len([item for item in attention["stale"] if item["entityType"] == "artifact"]),
            "sourceFreshnessCounts": freshness_counts, "sourceAdmissibilityCounts": admissibility_counts, "verificationStatusCounts": verification_counts,
            "promotionStateCounts": promotion_counts, "sourceCandidateCount": len(indexes.source_candidates), "claimCandidateCount": len(indexes.claim_candidates),
            "entityCandidateCount": len(indexes.entity_candidates), "gapCount": len(gaps),
        },
        "trusted": {key: value[:3] for key, value in trusted.items()}, "attention": {key: value[:3] for key, value in attention.items()},
        "nextCommand": next_command, "detailCommands": dict(DETAIL_COMMANDS),
        "indexes": {"assumptions": [row.model_dump(mode="json") for row in indexes.assumptions], "sources": source_payloads, "claims": claim_payloads,
                    "source_candidates": [row.model_dump(mode="json") for row in indexes.source_candidates], "claim_candidates": [row.model_dump(mode="json") for row in indexes.claim_candidates],
                    "entity_candidates": [row.model_dump(mode="json") for row in indexes.entity_candidates], "hypotheses": [row.model_dump(mode="json", by_alias=True) for row in indexes.hypotheses],
                    "artifact_lineage": artifact_payloads, "verification_runs": [row.model_dump(mode="json") for row in indexes.verification_runs]},
        "agentWorkflow": agent_workflow,
        "staleOutputs": [row for row in artifact_payloads if row.get("promotion_state") == "stale" or row.get("stale_reasons")], "gaps": gaps, "hypothesisRanking": [],
    }
