"""K21-OPAQUE-RUN-ARTIFACT: exact bytes are evidence, never approval."""

import hashlib
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from krail.provider.v1 import ResourceRef
from rail.observed_run_artifact import (
    MAX_RUN_ARTIFACT_BYTES, ObservedRunArtifactRepository,
    RunArtifactAwareProcedureAuthorizer, RunArtifactObservation,
    record_observed_run_artifact,
)
from rail.opensaddle_run_artifact import OpenSaddleRunArtifactSource
from rail.procedural_memory import (
    add_observed_run_artifact_to_procedure_candidate,
    authorize_procedure, create_procedure,
)


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def ref(kind, identifier, content):
    return ResourceRef(authority="opensaddle://fixture" if kind in {"run", "artifact"} else "krail://fixture",
                       resource_type=kind, resource_id=identifier, version="1",
                       digest="sha256:" + hashlib.sha256(content).hexdigest())


class Source:
    def __init__(self, artifact: bytes):
        self.artifact = artifact
        self.observation = RunArtifactObservation(
            project_id="p", run_id="run_1", run_ref=ref("run", "run_1", b"run"),
            artifact_ref=ref("artifact", "art_1", artifact), core_run_updated_at=NOW,
        )
        self.identifies = 0

    def identify(self, run_id):
        assert run_id == "run_1"
        self.identifies += 1
        return self.observation

    def read_artifact(self, artifact_ref, *, max_bytes):
        assert artifact_ref == self.observation.artifact_ref
        assert max_bytes == MAX_RUN_ARTIFACT_BYTES
        return self.artifact


class Authorizer:
    def __init__(self):
        self.denied = set()

    def authorize(self, resource):
        if resource.resource_id in self.denied:
            raise PermissionError("withdrawn")


def test_opaque_native_bytes_reopen_as_desired_procedure_with_live_dependencies(tmp_path):
    # Native text is not parsed or reserialized as InvocationResult.output.
    source = Source(b"Native text result\nwith non-JSON bytes: \xff\x00")
    access = Authorizer()
    input_ref = ref("document", "doc_1", b"selected source")
    evidence = record_observed_run_artifact("run_1", source=source, authorizer=access,
                                            input_refs=(input_ref,))
    assert evidence.observation.artifact_ref.verifies(source.artifact)
    assert evidence.evidence_state == "caller_observed_unverified"
    assert evidence.observation.provider_metadata_state == "not_attested"
    path = str(tmp_path / "semantic.json")
    repository = ObservedRunArtifactRepository(path, tenant_id="personal", project_id="p")
    repository.save(evidence, source=source, authorizer=access, at=NOW)
    reopened = ObservedRunArtifactRepository(path, tenant_id="personal", project_id="p")
    assert reopened.read_exact(evidence.exact_ref(), source=source, authorizer=access) == evidence
    previous = create_procedure(
        procedure_id="p/summary", procedure_version="1.0.0", lifecycle="reviewed",
        authority="krail://fixture", writer_family="human-reviewed",
        valid_from=NOW, recorded_at=NOW, package_refs=(),
        command_refs=(ref("command", "cmd_1", b"command"),),
        environment_refs=(ref("environment", "env_1", b"environment"),),
        test_evidence_refs=(ref("test", "test_1", b"test"),), dependency_refs=(),
        rationale="Previously reviewed procedure.",
        review_ref=ref("review", "review_1", b"review"),
    )
    candidate = add_observed_run_artifact_to_procedure_candidate(
        previous, evidence, procedure_version="1.1.0-candidate",
        rationale="Observed an unverified native result; review required.", recorded_at=NOW,
    )
    assert candidate.lifecycle == "desired"
    assert candidate.review_ref is None and candidate.activation_ref is None
    assert candidate.test_evidence_refs == ()
    assert {r.exact_key for r in candidate.dependency_refs} == {
        evidence.exact_ref().exact_key, input_ref.exact_key,
        evidence.observation.run_ref.exact_key, evidence.observation.artifact_ref.exact_key,
    }
    wrapped = RunArtifactAwareProcedureAuthorizer(reopened, source, access)
    assert authorize_procedure(candidate, wrapped) == candidate
    access.denied.add("doc_1")
    with pytest.raises(PermissionError, match="procedure access denied"):
        authorize_procedure(candidate, wrapped)
    access.denied.clear()
    access.denied.add("art_1")
    with pytest.raises(PermissionError, match="procedure access denied"):
        authorize_procedure(candidate, wrapped)


def test_opaque_observation_refuses_bytes_drift_and_in_call_revocation(tmp_path):
    source = Source(b"original native bytes")
    access = Authorizer()
    source.artifact = b"different bytes"
    with pytest.raises(ValueError, match="bytes differ"):
        record_observed_run_artifact("run_1", source=source, authorizer=access)

    source = Source(b"original native bytes")
    class RevokingSource(Source):
        def read_artifact(self, artifact_ref, *, max_bytes):
            data = super().read_artifact(artifact_ref, max_bytes=max_bytes)
            access.denied.add("art_1")
            return data
    source = RevokingSource(source.artifact)
    with pytest.raises(PermissionError, match="observed Run artifact access denied"):
        record_observed_run_artifact("run_1", source=source, authorizer=access)

    access.denied.clear()
    source = Source(b"original native bytes")
    source.artifact = b"x" * (MAX_RUN_ARTIFACT_BYTES + 1)
    with pytest.raises(ValueError, match="bytes differ"):
        record_observed_run_artifact("run_1", source=source, authorizer=access)

    source = Source(b"original native bytes")
    evidence = record_observed_run_artifact("run_1", source=source, authorizer=access)
    repo = ObservedRunArtifactRepository(str(tmp_path / "semantic.json"), tenant_id="personal", project_id="p")
    repo.save(evidence, source=source, authorizer=access, at=NOW)
    source.artifact = b"tampered"
    with pytest.raises(ValueError, match="bytes differ"):
        repo.read_exact(evidence.exact_ref(), source=source, authorizer=access)


def test_core_adapter_never_forwards_human_bearer_on_redirect_or_decodes_response():
    observed = []

    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

    class RedirectOrCompressed(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("run_redirect"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/leak")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                self.wfile.write(b"not trusted identity bytes")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectOrCompressed)
    threads = [threading.Thread(target=item.serve_forever, daemon=True) for item in (server, destination)]
    for thread in threads:
        thread.start()
    try:
        adapter = OpenSaddleRunArtifactSource(
            base_url=f"http://127.0.0.1:{server.server_port}",
            bearer_token="private-fixture-token", authority="opensaddle://fixture", project_id="p",
        )
        with pytest.raises(PermissionError):
            adapter.identify("run_redirect")
        assert observed == [], "redirect target must never receive the bearer"
        with pytest.raises(PermissionError):
            adapter.identify("run_encoded")
    finally:
        for item in (server, destination):
            item.shutdown()
            item.server_close()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
