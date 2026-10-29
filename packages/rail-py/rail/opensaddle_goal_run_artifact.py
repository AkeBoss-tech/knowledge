"""Read-only exact human Goal review binding; no Goal reconciliation or activation.

K-GOAL-REVIEWED-RUN-PROCEDURE-1. Authenticated Core audit is authority for human
acceptance. Ordinary opaque evidence remains caller_observed_unverified.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
import time
from urllib.parse import quote

import httpx
from krail.provider.v1 import ResourceRef
from rail.observed_run_artifact import RunArtifactObservation
from rail.opensaddle_run_artifact import OpenSaddleRunArtifactSource, _digest
from rail.opensaddle_reviewed_run_artifact import OpenSaddleReviewedRunArtifactSource


@dataclass(frozen=True)
class AcceptedCoreGoalReviewBinding:
    project_id: str
    goal_id: str
    run_id: str
    observer_subject: str
    review_revision: int
    audit_sequence: int
    snapshot_digest: str
    artifact_id: str
    artifact_digest: str
    review_ref: ResourceRef


class OpenSaddleGoalReviewedRunArtifactSource(OpenSaddleReviewedRunArtifactSource):
    """Reuse existing opaque artifact/procedure contracts with passive Goal audit."""

    def __init__(self, *, binding: AcceptedCoreGoalReviewBinding, **kwargs):
        OpenSaddleRunArtifactSource.__init__(self, **kwargs)
        if (not isinstance(binding, AcceptedCoreGoalReviewBinding)
                or binding.project_id != self.project_id
                or not binding.observer_subject or len(binding.observer_subject) > 512
                or any(not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) is None
                       for value in (binding.goal_id, binding.run_id, binding.artifact_id))
                or not binding.run_id.startswith("run_")
                or any(type(value) is not int or value < 1 for value in (binding.review_revision, binding.audit_sequence))
                or any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in (binding.snapshot_digest, binding.artifact_digest))
                or binding.review_ref.authority != self.authority
                or binding.review_ref.resource_type != "goal-result-review"
                or binding.review_ref.resource_id != f"{binding.goal_id}/{binding.run_id}"
                or binding.review_ref.version != str(binding.review_revision)):
            raise ValueError("accepted Core Goal review binding is invalid")
        self._binding = binding

    def _audit_page(self, client, path):
        # Core's existing passive audit caps projected pages at 900,000 bytes.
        # Keep a transport envelope bound, total-page bound and overall deadline.
        deadline = time.monotonic() + self.timeout_seconds
        try:
            with client.stream("GET", path, headers={"Authorization": "Bearer " + self._bearer,
                                                     "Accept-Encoding": "identity"}) as response:
                if (response.status_code != 200 or response.is_redirect
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise PermissionError("Core Goal audit is unavailable")
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=65536):
                    payload.extend(chunk)
                    if len(payload) > 1_000_000 or time.monotonic() > deadline:
                        raise ValueError("Core Goal audit exceeds its bound")
        except httpx.HTTPError:
            raise PermissionError("Core Goal audit is unavailable") from None
        if time.monotonic() > deadline:
            raise ValueError("Core Goal audit exceeds its deadline")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Core Goal audit is invalid")
        return value

    @staticmethod
    def _current_goal(source, goal_id, run_id, observer_subject, expected_revision):
        prefix = f"/api/v2/projects/{quote(source.project_id, safe='')}/goal-loop/{quote(goal_id, safe='')}/audit"
        cursor, total, accepted, latest = 0, 0, None, None
        deadline = time.monotonic() + source.timeout_seconds
        with source._client() as client:
            if source._json(client, "/api/v2/capabilities").get("authenticated_subject") != observer_subject:
                raise PermissionError("Core observer identity changed")
            for _ in range(32):
                page = source._audit_page(client, f"{prefix}?after={cursor}")
                items = page.get("items")
                if not isinstance(items, list) or len(items) > 100:
                    raise ValueError("Core Goal audit page is invalid")
                for event in items:
                    if (not isinstance(event, dict) or event.get("agent_id") != goal_id
                            or type(event.get("sequence")) is not int or event["sequence"] <= cursor
                            or type(event.get("revision")) is not int or event["revision"] < 1
                            or (latest is not None and event["revision"] <= latest["revision"])
                            or not isinstance(event.get("payload"), dict)):
                        raise ValueError("Core Goal audit ordering or identity differs")
                    cursor, latest = event["sequence"], event
                    total += 1
                    if total > 3200 or time.monotonic() > deadline:
                        raise ValueError("Core Goal audit exceeds its bound")
                    if event["revision"] == expected_revision:
                        accepted = event
                following = page.get("next_cursor")
                if following is None:
                    break
                if type(following) is not int or following != cursor or not items:
                    raise ValueError("Core Goal audit cursor differs")
            else:
                raise ValueError("Core Goal audit exceeds its page bound")
            if accepted is None or latest is None:
                raise PermissionError("exact accepted Goal audit is unavailable")
            snapshot = accepted["payload"]
            entries = snapshot.get("runs")
            if not isinstance(entries, list):
                raise ValueError("Core Goal runs are invalid")
            matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("run_id") == run_id]
            review = matches[0].get("result_review") if len(matches) == 1 else None
            if (not isinstance(review, dict) or matches[0].get("review") != "accepted"
                    or accepted.get("kind") != "goal.accept_result"
                    or review.get("schema_version") != "opensaddle.project-goal-result-review.v1"
                    or review.get("scope") != "exact_retained_worker_result"
                    or review.get("decision") != "accepted"
                    or review.get("project_id") != source.project_id or review.get("run_id") != run_id
                    or review.get("goal_revision") != expected_revision
                    or accepted.get("actor") != review.get("reviewed_by")
                    or not isinstance(review.get("reviewed_by"), str) or not review["reviewed_by"]
                    or review.get("artifact_type") != "native_agent_result"
                    or type(review.get("lease_epoch")) is not int or review["lease_epoch"] < 1
                    or type(review.get("result_sequence")) is not int or review["result_sequence"] < 1
                    or type(review.get("completion_sequence")) is not int
                    or review["completion_sequence"] <= review["result_sequence"]
                    or type(review.get("artifact_size_bytes")) is not int or not 0 <= review["artifact_size_bytes"] <= 512000):
                raise PermissionError("exact human-accepted Goal artifact review is unavailable")
            current = latest["payload"]
            current_entries = current.get("runs", [])
            current_matches = [entry for entry in current_entries if isinstance(entry, dict) and entry.get("run_id") == run_id]
            if (snapshot.get("project_id") != source.project_id or current.get("project_id") != source.project_id
                    or len(current_matches) != 1 or current_matches[0].get("review") != "accepted"
                    or current_matches[0].get("result_review") != review):
                raise PermissionError("retained Goal artifact review changed")
            snapshot_digest = accepted.get("snapshot_digest")
            if not isinstance(snapshot_digest, str) or re.fullmatch(r"[0-9a-f]{64}", snapshot_digest) is None:
                raise ValueError("Core Goal snapshot digest is invalid")
            ref = ResourceRef(authority=source.authority, resource_type="goal-result-review",
                resource_id=f"{goal_id}/{run_id}", version=str(expected_revision),
                digest=_digest({"review": review, "sequence": accepted["sequence"], "snapshot_digest": snapshot_digest}))
            binding = AcceptedCoreGoalReviewBinding(source.project_id, goal_id, run_id, observer_subject,
                expected_revision, accepted["sequence"], snapshot_digest, review["artifact_id"], review["artifact_digest"], ref)
            epoch = review["lease_epoch"]
            observation = RunArtifactObservation(project_id=source.project_id, run_id=run_id,
                run_ref=ResourceRef(authority=source.authority, resource_type="run", resource_id=run_id,
                    version=f"lease-{epoch}", digest=_digest({key: review[key] for key in (
                        "project_id", "run_id", "worker_id", "lease_epoch", "result_event_id", "result_sequence",
                        "completion_event_id", "completion_sequence")})),
                artifact_ref=ResourceRef(authority=source.authority, resource_type="artifact", resource_id=review["artifact_id"],
                    version=f"run-{run_id}-lease-{epoch}", digest="sha256:" + review["artifact_digest"]),
                core_run_updated_at=datetime.fromisoformat(review["run_updated_at"]))
            if source._json(client, "/api/v2/capabilities").get("authenticated_subject") != observer_subject:
                raise PermissionError("Core observer identity changed")
        return binding, observation

    @classmethod
    def pin(cls, *, goal_id, run_id, observer_subject, expected_review_revision,
            expected_artifact_id, expected_artifact_digest, **kwargs):
        if (type(expected_review_revision) is not int or expected_review_revision < 1
                or any(not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) is None
                       for value in (goal_id, run_id, expected_artifact_id))):
            raise ValueError("exact Goal review selectors are required")
        probe = OpenSaddleRunArtifactSource(**kwargs)
        probe._audit_page = cls._audit_page.__get__(probe)
        binding, _ = cls._current_goal(probe, goal_id, run_id, observer_subject, expected_review_revision)
        if (binding.artifact_id, binding.artifact_digest) != (expected_artifact_id, expected_artifact_digest):
            raise PermissionError("inspected Goal artifact changed")
        source = cls(binding=binding, **kwargs)
        source.identify(run_id)
        return source

    def identify(self, run_id):
        if run_id != self.binding.run_id:
            raise PermissionError("Run is outside pinned Goal review")
        current, observation = self._current_goal(self, self.binding.goal_id, run_id,
            self.binding.observer_subject, self.binding.review_revision)
        if current != self.binding:
            raise PermissionError("pinned Goal review changed")
        return observation

    def authorizer(self, delegate):
        return _GoalReviewAuthorizer(self, delegate)


class _GoalReviewAuthorizer:
    def __init__(self, source, delegate):
        self.source, self.delegate = source, delegate

    def authorize(self, ref, *, at=None):
        if ref.authority == self.source.authority and ref.resource_type == "goal-result-review":
            if ref != self.source.binding.review_ref:
                raise PermissionError("Goal review dependency differs")
            self.source.identify(self.source.binding.run_id)
        else:
            self.delegate.authorize(ref)


__all__ = ["AcceptedCoreGoalReviewBinding", "OpenSaddleGoalReviewedRunArtifactSource"]
