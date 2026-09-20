"""Trusted-local, read-only OpenSaddle Core adapter for opaque Run evidence.

The caller supplies a current human bearer and a stable installation authority.
Neither the bearer nor an artifact body is stored in KRAIL evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from krail.provider.v1 import ResourceRef
from rail.observed_run_artifact import MAX_RUN_ARTIFACT_BYTES, RunArtifactObservation


def _digest(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


class OpenSaddleRunArtifactSource:
    """Reads only the current final-lease generic-agent result through Core APIs."""

    def __init__(self, *, base_url: str, bearer_token: str, authority: str,
                 project_id: str, timeout_seconds: float = 10.0) -> None:
        url = urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.path not in {"", "/"}
                or url.query or url.fragment or
                (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("Core origin must be HTTPS or loopback HTTP without credentials")
        if (not isinstance(bearer_token, str) or not 1 <= len(bearer_token) <= 8192
                or not bearer_token.isascii()
                or any(not 33 <= ord(char) <= 126 for char in bearer_token)):
            raise ValueError("Core bearer is invalid")
        if not isinstance(project_id, str) or not project_id or len(project_id) > 200:
            raise ValueError("Project ID is invalid")
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 30):
            raise ValueError("Core read timeout must be bounded")
        # ResourceRef validates the declared installation identity. This is
        # configured trusted-local identity, not a Core-signed attestation.
        ResourceRef(authority=authority, resource_type="run", resource_id="shape",
                    version="1", digest="sha256:" + "0" * 64)
        self._origin = base_url.rstrip("/")
        self._bearer = bearer_token
        self.authority = authority
        self.project_id = project_id
        self.timeout_seconds = timeout_seconds

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._origin, timeout=self.timeout_seconds,
                            follow_redirects=False, trust_env=False)

    def _json(self, client: httpx.Client, path: str) -> dict:
        deadline = time.monotonic() + self.timeout_seconds
        try:
            with client.stream("GET", path, headers={"Authorization": "Bearer " + self._bearer,
                                                     "Accept-Encoding": "identity"}) as response:
                if (response.status_code != 200 or response.is_redirect
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise PermissionError("Core Run artifact is unavailable")
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=65_536):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Core metadata read exceeded its deadline")
                    payload.extend(chunk)
                    if len(payload) > 65_536:
                        raise ValueError("Core metadata exceeds its bound")
                if time.monotonic() > deadline:
                    raise TimeoutError("Core metadata read exceeded its deadline")
        except httpx.HTTPError:
            raise PermissionError("Core Run artifact is unavailable") from None
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, ValueError):
            raise ValueError("Core metadata is invalid") from None
        if not isinstance(value, dict):
            raise ValueError("Core metadata is invalid")
        return value

    def identify(self, run_id: str) -> RunArtifactObservation:
        if not isinstance(run_id, str) or not run_id.startswith("run_") or len(run_id) > 200 or not run_id.isascii() or not all(c.isalnum() or c == "_" for c in run_id):
            raise ValueError("Run ID is invalid")
        path = f"/api/v2/runs/{run_id}"
        with self._client() as client:
            run = self._json(client, path)
            if (run.get("run_id") != run_id or run.get("project_id") != self.project_id
                    or run.get("status") != "completed"):
                raise PermissionError("Core Run artifact identity differs")
            review = self._json(client, path + "/agent-result/review")
        if (review.get("project_id") != self.project_id
                or review.get("run_id") != run_id or review.get("scope") != "historical_run_result_only"
                or review.get("schema_version") != "opensaddle.agent-result-review.v1"):
            raise PermissionError("Core Run artifact identity differs")
        artifact_id, artifact_digest = review.get("artifact_id"), review.get("artifact_digest")
        lease_epoch = run.get("lease_epoch")
        if (not isinstance(artifact_id, str) or not artifact_id.isascii() or len(artifact_id) > 200
                or not all(c.isalnum() or c == "_" for c in artifact_id)
                or not isinstance(artifact_digest, str) or len(artifact_digest) != 64
                or any(c not in "0123456789abcdef" for c in artifact_digest)
                or type(lease_epoch) is not int or lease_epoch < 1):
            raise ValueError("Core artifact identity is invalid")
        # GET Run does not expose a signed provider identity or artifact media
        # type. Bind only its immutable completed-Run fields and the exact
        # current final lease. Human review revision is deliberately excluded.
        run_identity = {key: run.get(key) for key in (
            "run_id", "project_id", "source_ref", "requested_by", "created_at",
            "updated_at", "lease_epoch", "assigned_worker_id", "policy",
        )}
        run_digest = _digest(run_identity)
        run_updated_at = datetime.fromisoformat(str(run.get("updated_at")))
        run_ref = ResourceRef(authority=self.authority, resource_type="run", resource_id=run_id,
                              version=f"lease-{lease_epoch}", digest=run_digest)
        artifact_ref = ResourceRef(authority=self.authority, resource_type="artifact",
                                   resource_id=artifact_id, version=f"run-{run_id}-lease-{lease_epoch}",
                                   digest="sha256:" + artifact_digest)
        return RunArtifactObservation(project_id=self.project_id, run_id=run_id,
                                      run_ref=run_ref, artifact_ref=artifact_ref,
                                      core_run_updated_at=run_updated_at)

    def _read_bytes(self, run_id: str, artifact_id: str, *, max_bytes: int) -> bytes:
        path = f"/api/v2/runs/{run_id}/artifacts/{artifact_id}/content"
        deadline = time.monotonic() + self.timeout_seconds
        try:
            with self._client() as client:
                with client.stream("GET", path, headers={"Authorization": "Bearer " + self._bearer,
                                                        "Accept-Encoding": "identity"}) as response:
                    if (response.status_code != 200 or response.is_redirect
                            or response.headers.get("content-encoding", "identity").lower() != "identity"):
                        raise PermissionError("Core Run artifact is unavailable")
                    payload = bytearray()
                    for chunk in response.iter_raw(chunk_size=65_536):
                        if time.monotonic() > deadline:
                            raise TimeoutError("Core artifact read exceeded its deadline")
                        payload.extend(chunk)
                        if len(payload) > max_bytes:
                            raise ValueError("Core artifact exceeds its byte bound")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Core artifact read exceeded its deadline")
                    return bytes(payload)
        except httpx.HTTPError:
            raise PermissionError("Core Run artifact is unavailable") from None

    def read_artifact(self, ref: ResourceRef, *, max_bytes: int) -> bytes:
        if ref.authority != self.authority or ref.resource_type != "artifact":
            raise PermissionError("Core artifact is outside configured authority")
        if (not ref.resource_id.isascii() or len(ref.resource_id) > 200
                or not all(c.isalnum() or c == "_" for c in ref.resource_id)
                or type(max_bytes) is not int or max_bytes < 0):
            raise ValueError("Core artifact selector is invalid")
        if not ref.version.startswith("run-run_") or "-lease-" not in ref.version:
            raise ValueError("Core artifact version is invalid")
        run_id, lease = ref.version.rsplit("-lease-", 1)
        if not lease.isdigit() or not run_id.startswith("run-run_"):
            raise ValueError("Core artifact version is invalid")
        run_id = run_id.removeprefix("run-")
        if not run_id.isascii() or not all(c.isalnum() or c == "_" for c in run_id):
            raise ValueError("Core artifact version is invalid")
        if self.identify(run_id).artifact_ref != ref:
            raise PermissionError("Core artifact is not the current Project result")
        data = self._read_bytes(run_id, ref.resource_id, max_bytes=min(max_bytes, MAX_RUN_ARTIFACT_BYTES))
        if not ref.verifies(data):
            raise ValueError("Core artifact bytes differ from the final result digest")
        if self.identify(run_id).artifact_ref != ref:
            raise PermissionError("Core artifact changed during read")
        return data


__all__ = ["OpenSaddleRunArtifactSource"]
