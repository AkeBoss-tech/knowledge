"""Read-only binding to one current human-accepted Core result review.

K-REVIEWED-RUN-PROCEDURE-1. Core authenticates the observer and owns the review.
This adapter adds an exact review dependency; opaque Run evidence remains
caller_observed_unverified and procedure promotion remains a separate action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from urllib.parse import quote, urlencode

from krail.provider.v1 import ResourceRef
from rail.observed_run_artifact import ObservedRunArtifactRepository, RunArtifactAuthorizer
from rail.opensaddle_run_artifact import OpenSaddleRunArtifactSource


@dataclass(frozen=True)
class AcceptedCoreResultReviewBinding:
    """Caller-retained selectors, never standing authority or a signed receipt."""

    project_id: str
    run_id: str
    observer_subject: str
    review_id: str
    review_revision: int
    artifact_id: str
    artifact_digest: str
    review_ref: ResourceRef


class OpenSaddleReviewedRunArtifactSource(OpenSaddleRunArtifactSource):
    """Same Core read transport, additionally fenced by one exact accepted review."""

    def __init__(self, *, binding: AcceptedCoreResultReviewBinding, **kwargs):
        super().__init__(**kwargs)
        if (not isinstance(binding, AcceptedCoreResultReviewBinding)
                or binding.project_id != self.project_id
                or re.fullmatch(r"run_[A-Za-z0-9_]{1,196}", binding.run_id) is None
                or re.fullmatch(r"review_[A-Za-z0-9_]{1,193}", binding.review_id) is None
                or type(binding.review_revision) is not int or binding.review_revision < 1
                or re.fullmatch(r"[0-9a-f]{64}", binding.artifact_digest) is None
                or not binding.observer_subject or len(binding.observer_subject) > 512
                or binding.review_ref.authority != self.authority
                or binding.review_ref.resource_type != "agent-result-review"
                or binding.review_ref.resource_id != f"{binding.run_id}/{binding.review_id}"
                or binding.review_ref.version != str(binding.review_revision)):
            raise ValueError("accepted Core review binding is invalid")
        self._binding = binding

    @property
    def binding(self) -> AcceptedCoreResultReviewBinding:
        return self._binding

    @staticmethod
    def _current(source: OpenSaddleRunArtifactSource, run_id: str, observer_subject: str):
        with source._client() as client:
            before = source._json(client, "/api/v2/capabilities")
            if before.get("authenticated_subject") != observer_subject:
                raise PermissionError("Core observer identity changed")
            observation = OpenSaddleRunArtifactSource.identify(source, run_id)
            state = source._json(client, f"/api/v2/runs/{run_id}/agent-result/review")
            review = state.get("review")
            if (state.get("schema_version") != "opensaddle.agent-result-review.v1"
                    or state.get("scope") != "historical_run_result_only"
                    or state.get("project_id") != source.project_id or state.get("run_id") != run_id
                    or not isinstance(review, dict) or review.get("decision") != "accepted"
                    or review.get("is_current") is not True
                    or type(review.get("revision")) is not int or review["revision"] < 1
                    or state.get("review_revision") != review["revision"]
                    or state.get("artifact_id") != observation.artifact_ref.resource_id
                    or state.get("artifact_digest") != observation.artifact_ref.digest.removeprefix("sha256:")
                    or review.get("artifact_id") != state["artifact_id"]
                    or review.get("artifact_digest") != state["artifact_digest"]
                    or not isinstance(review.get("review_id"), str)
                    or re.fullmatch(r"review_[A-Za-z0-9_]{1,193}", review["review_id"]) is None
                    or not isinstance(review.get("reviewed_by"), str) or not review["reviewed_by"]
                    or not isinstance(review.get("reviewed_at"), str)):
                raise PermissionError("current human-accepted Core result review is unavailable")
            # The exact immutable review content is hashed, including its note;
            # neither note, bearer, HTTP origin nor result bytes enter evidence.
            content = {key: review[key] for key in ("review_id", "artifact_id", "artifact_digest",
                       "revision", "decision", "reviewed_by", "reviewed_at")}
            content.update(project_id=source.project_id, run_id=run_id,
                           note_digest=sha256(str(review.get("note", "")).encode()).hexdigest())
            digest = "sha256:" + sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            ref = ResourceRef(authority=source.authority, resource_type="agent-result-review",
                              resource_id=f"{run_id}/{review['review_id']}",
                              version=str(review["revision"]), digest=digest)
            if source._json(client, "/api/v2/capabilities").get("authenticated_subject") != observer_subject:
                raise PermissionError("Core observer identity changed")
        return AcceptedCoreResultReviewBinding(source.project_id, run_id, observer_subject,
            review["review_id"], review["revision"], state["artifact_id"], state["artifact_digest"], ref), observation

    @classmethod
    def pin(cls, *, run_id: str, observer_subject: str, expected_review_id: str,
            expected_review_revision: int, expected_artifact_id: str,
            expected_artifact_digest: str, **kwargs):
        """Pin the exact review the caller inspected, through authenticated Core reads."""
        if type(expected_review_revision) is not int or expected_review_revision < 1:
            raise ValueError("an exact positive integer review revision is required")
        probe = OpenSaddleRunArtifactSource(**kwargs)
        binding, _ = cls._current(probe, run_id, observer_subject)
        if ((binding.review_id, binding.review_revision, binding.artifact_id, binding.artifact_digest)
                != (expected_review_id, expected_review_revision, expected_artifact_id, expected_artifact_digest)):
            raise PermissionError("inspected Core review target changed")
        source = cls(binding=binding, **kwargs)
        source.identify(run_id)
        return source

    def identify(self, run_id: str):
        if run_id != self.binding.run_id:
            raise PermissionError("Run is outside pinned review")
        current, observation = self._current(self, run_id, self.binding.observer_subject)
        if current != self.binding:
            raise PermissionError("pinned Core result review changed")
        return observation

    def authorizer(self, delegate: RunArtifactAuthorizer):
        return _ReviewedRunAuthorizer(self, delegate)

    def verify(self, receipt) -> None:
        """CoreProvenanceTrust: compare with the actual authenticated Core receipt.

        Configuration provenance stays configuration-only. A digest supplied by
        the caller, including a valid self-consistent digest, is insufficient.
        """
        from rail.core_provenance import CoreProvenanceReceipt

        try:
            if not isinstance(receipt, CoreProvenanceReceipt):
                raise ValueError("a typed Core receipt is required")
            receipt = CoreProvenanceReceipt.model_validate(receipt.model_dump(mode="python"))
            environment = receipt.environment_ref
            if (environment.resource_id != f"opensaddle/environment-revision/{self.project_id}"
                    or not environment.version.isascii() or not environment.version.isdigit()):
                raise ValueError("Core environment is outside configured Project")
            params = {"environment_revision": int(environment.version)}
            if receipt.invocation_id is not None:
                params["invocation_id"] = receipt.invocation_id
            else:
                params["command_id"] = receipt.command_ref.resource_id.removeprefix("opensaddle/command/")
            self.identify(self.binding.run_id)
            path = f"/api/v2/projects/{quote(self.project_id, safe='')}/provenance-observation?{urlencode(params)}"
            with self._client() as client:
                actual = CoreProvenanceReceipt.model_validate(self._json(client, path).get("observation"))
            if actual != receipt:
                raise PermissionError("Core provenance receipt differs from authenticated observation")
            self.identify(self.binding.run_id)
        except (ValueError, TypeError, KeyError) as exc:
            raise PermissionError("Core provenance observation is unavailable") from exc

    def observe(self, repository: ObservedRunArtifactRepository, *, authorizer: RunArtifactAuthorizer,
                at: datetime, input_refs: tuple[ResourceRef, ...] = ()):
        """Retain ordinary unverified evidence with its exact accepted-review dependency."""
        if not isinstance(input_refs, tuple):
            raise ValueError("input refs must be a bounded tuple")
        refs = tuple(dict.fromkeys((self.binding.review_ref, *input_refs)))
        evidence = repository.observe(self.binding.run_id, source=self,
            authorizer=self.authorizer(authorizer), at=at, input_refs=refs)
        self.identify(self.binding.run_id)
        return evidence


class _ReviewedRunAuthorizer:
    def __init__(self, source: OpenSaddleReviewedRunArtifactSource, delegate: RunArtifactAuthorizer):
        self.source, self.delegate = source, delegate

    def authorize(self, ref: ResourceRef, *, at=None) -> None:
        if ref.authority == self.source.authority and ref.resource_type == "agent-result-review":
            if ref != self.source.binding.review_ref:
                raise PermissionError("review dependency differs from pinned Core result")
            self.source.identify(self.source.binding.run_id)
        else:
            self.delegate.authorize(ref)


__all__ = ["AcceptedCoreResultReviewBinding", "OpenSaddleReviewedRunArtifactSource"]
