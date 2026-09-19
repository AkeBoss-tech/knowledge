"""K29-HISTORICAL-CAPTURE: offline archive bytes are data, not authority."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from rail.hosted.access import (
    AccessClaims, AccessContextAuthority, MemoryRevocationRegistry,
    PacketRequestBinding,
)
from rail.registered_git_evidence import (
    RegisteredGitEvidenceBridge,
    inspect_historical_registered_git_capture,
    registered_git_evidence_request_digest,
)


NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)
CAPABILITY_DIGEST = "sha256:" + "a" * 64
CONTENT = b"Current and historical document bytes\n"


def _captured_archive(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "evidence.md").write_bytes(CONTENT)
    subprocess.run(["git", "-C", str(workspace), "add", "evidence.md"], check=True)
    subprocess.run([
        "git", "-C", str(workspace), "-c", "user.name=Archive test",
        "-c", "user.email=archive@example.invalid", "commit", "-qm", "capture",
    ], check=True)
    commit = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True,
    ).strip()
    state = tmp_path / "archived-knowledge"
    revoked = MemoryRevocationRegistry()
    authority = AccessContextAuthority({"old": b"old signing key"}, issuer="old-installation", revocations=revoked)

    def context_for_request(subject, action, ref, target):
        claims = AccessClaims(
            issuer="old-installation", tenant_id="old-installation", project_id="old-project",
            subject=subject, delegator=subject, delegation_id="old-delegation",
            capability_id="old-capability", capability_version="1.0.0",
            capability_digest=CAPABILITY_DIGEST, actions=(action,),
            source_ids=(ref.resource_id,), classifications=("restricted",),
            policy_digest=CAPABILITY_DIGEST, issued_at=NOW - timedelta(minutes=1),
            not_before=NOW - timedelta(minutes=1), expires_at=NOW + timedelta(hours=1),
            nonce="context-" + action,
        )
        context = authority.issue(claims, key_id="old")
        binding = PacketRequestBinding(
            access_context_digest=context.context_digest, tenant_id="old-installation",
            project_id="old-project", capability_id="old-capability",
            capability_version="1.0.0", capability_digest=CAPABILITY_DIGEST,
            request_digest=registered_git_evidence_request_digest(action, ref, target, state),
            purpose="registered-git-evidence", scope=ref.authority,
            issued_at=NOW - timedelta(minutes=1), not_before=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1), nonce="request-" + action,
        )
        return context, authority.issue_packet_request_binding(binding, key_id="old")

    bridge = RegisteredGitEvidenceBridge(
        workspace, state, authority=authority, context_for_request=context_for_request,
        tenant_id="old-installation", project_id="old-project",
        capability_id="old-capability", capability_version="1.0.0",
        capability_digest=CAPABILITY_DIGEST, clock=lambda: NOW,
    )
    bridge.setup(user_id="old-owner", setup_id="setup-one")
    bridge.capture(user_id="old-owner", commit=commit, path="evidence.md", capture_id="capture-one")
    return state, bridge, revoked


def test_offline_historical_capture_verifies_real_retained_bytes_without_reusing_old_review(tmp_path):
    state, bridge, revoked = _captured_archive(tmp_path)
    digest = "sha256:" + sha256(CONTENT).hexdigest()
    unreviewed = inspect_historical_registered_git_capture(
        state_root=state, capture_id="capture-one", expected_content_digest=digest,
    )
    assert unreviewed.content == CONTENT
    assert unreviewed.historical_review_id is None
    bridge.review(
        user_id="old-owner", capture_id="capture-one", review_id="historical-review",
        expected_content_digest=digest,
    )
    revoked.revoke_delegation("old-delegation", revoked_at=NOW)
    with pytest.raises(PermissionError):
        bridge.retrieve(user_id="old-owner", capture_id="capture-one")
    manifest = state / ".krail/registered-evidence-staging.json"
    sidecar = state / "sources/registered/capture-one.bin"
    before = (manifest.read_bytes(), sidecar.read_bytes(), sorted(str(p.relative_to(state)) for p in state.rglob("*")))
    historical = inspect_historical_registered_git_capture(
        state_root=state, capture_id="capture-one", expected_content_digest=digest,
    )
    assert historical.content == CONTENT and historical.content_digest == digest
    assert historical.path == "evidence.md"
    assert historical.historical_review_id == "historical-review"
    assert historical.historical_reviewed_at == NOW
    assert historical.current_authority_attested is False
    assert historical.semantic_evidence_reviewed is False
    assert before == (manifest.read_bytes(), sidecar.read_bytes(), sorted(str(p.relative_to(state)) for p in state.rglob("*")))


@pytest.mark.parametrize("fault", [
    "capture_id_traversal", "expected_digest_mismatch", "manifest_digest",
    "manifest_blob_traversal", "manifest_commit", "manifest_path_traversal",
    "manifest_path_dot",
    "manifest_partial_review", "manifest_null_review", "manifest_duplicate_key", "manifest_oversize",
    "manifest_symlink", "sidecar_tamper", "sidecar_oversize",
    "sidecar_symlink", "directory_symlink", "root_symlink",
])
def test_offline_historical_capture_rejects_untrusted_archive_shape(tmp_path, fault):
    state, _, _ = _captured_archive(tmp_path)
    manifest = state / ".krail/registered-evidence-staging.json"
    sidecar = state / "sources/registered/capture-one.bin"
    capture_id = "capture-one"
    expected = "sha256:" + sha256(CONTENT).hexdigest()
    root = state
    if fault.startswith("manifest_") and fault not in {"manifest_oversize", "manifest_symlink", "manifest_duplicate_key"}:
        data = json.loads(manifest.read_text())
        record = data[capture_id]
        if fault == "manifest_digest":
            record["digest"] = "sha256:" + "0" * 64
        elif fault == "manifest_blob_traversal":
            record["blob"] = "../../other.bin"
        elif fault == "manifest_commit":
            record["commit"] = "not-a-commit"
        elif fault == "manifest_path_traversal":
            record["path"] = "../evidence.md"
        elif fault == "manifest_path_dot":
            record["path"] = "."
        elif fault == "manifest_null_review":
            record["review_id"] = None
        else:
            record["reviewed_at"] = NOW.isoformat()
        manifest.write_text(json.dumps(data))
    elif fault == "manifest_duplicate_key":
        manifest.write_text('{"capture-one": {}, "capture-one": {}}')
    elif fault == "manifest_oversize":
        manifest.write_bytes(b"x" * (1_048_576 + 1))
    elif fault == "manifest_symlink":
        copied = tmp_path / "elsewhere-manifest.json"
        manifest.rename(copied)
        manifest.symlink_to(copied)
    elif fault == "sidecar_tamper":
        sidecar.write_bytes(b"tampered")
    elif fault == "sidecar_oversize":
        sidecar.write_bytes(b"x" * (1_048_576 + 1))
    elif fault == "sidecar_symlink":
        copied = tmp_path / "elsewhere.bin"
        sidecar.rename(copied)
        sidecar.symlink_to(copied)
    elif fault == "directory_symlink":
        directory = sidecar.parent
        moved = tmp_path / "elsewhere-directory"
        directory.rename(moved)
        directory.symlink_to(moved)
    elif fault == "root_symlink":
        root = tmp_path / "archive-alias"
        root.symlink_to(state, target_is_directory=True)
    elif fault == "capture_id_traversal":
        capture_id = "../capture-one"
    else:
        expected = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        inspect_historical_registered_git_capture(
            state_root=root, capture_id=capture_id, expected_content_digest=expected,
        )
