#!/usr/bin/env python3
"""Qualify reviewed-source reuse against installed OpenSaddle and KRAIL wheels.

Run from an unrelated directory in a disposable installed-wheel environment.
The synthetic worker consumes public Core packet APIs and publishes deterministic
provenance, without claiming a model/provider turn.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import uvicorn


OWNER = {"x-opensaddle-subject": "fixture-owner", "x-opensaddle-roles": "requester"}
PEPPER = "reviewed-reuse-fixture-pepper-32-characters"
PHRASE = "Reviewed-Source-Atlas-73"
ACTIVE_CLIENTS = []


class LoopbackClient:
    """Public HTTP client to a real, disposable Core listener."""

    def __init__(self, app):
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self.server.started:
            raise RuntimeError("Core loopback server did not start")
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20,
                                   trust_env=False)
        self.closed = False
        ACTIVE_CLIENTS.append(self)

    def request(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    def close(self):
        if self.closed:
            return
        self.client.close()
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Core loopback server did not stop")
        self.closed = True


def checked(client: LoopbackClient, method: str, path: str, status: int = 200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != status:
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}, expected {status}: {response.text[:400]}")
    return response


def git_repository(root: Path, name: str, filename: str, content: str) -> Path:
    workspace = root / name
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm",
                    "Reviewed source fixture"], check=True)
    return workspace


def source_free_imports(forbidden: tuple[Path, ...]) -> dict[str, str]:
    import krail
    import opensaddle
    import rail

    imports = {"krail": str(Path(krail.__file__).resolve()),
               "opensaddle": str(Path(opensaddle.__file__).resolve()),
               "rail": str(Path(rail.__file__).resolve())}
    environment = Path(sys.prefix).resolve()
    for name, imported in imports.items():
        path = Path(imported)
        if environment not in path.parents or "site-packages" not in path.parts:
            raise AssertionError(f"{name} did not import from the installed environment: {path}")
        if any(root.resolve() == path or root.resolve() in path.parents for root in forbidden):
            raise AssertionError(f"{name} imported from a source checkout: {path}")
    return imports


def compose(root: Path, project_id: str, workspace: Path):
    from opensaddle.control_plane.api import ControlPlaneSettings, create_control_plane_app
    from opensaddle.control_plane.artifact_blobs import ScopedLocalArtifactBlobStore
    from opensaddle.control_plane.auth import TrustedProxyAuthenticator
    from opensaddle.control_plane.personal_knowledge import configure_personal_knowledge
    from opensaddle.control_plane.personal_runtime import PersonalLocalPolicyEngine
    from opensaddle.project_service import ProjectNotFoundError, ProjectService

    private = root / "private"
    private.mkdir(exist_ok=True)
    registry = ProjectService(private / "projects.db")
    try:
        registry.get_project(project_id)
    except ProjectNotFoundError:
        registry.register_project(project_id, workspace)
    settings = ControlPlaneSettings(
        database_path=private / "core.db", installation_id="reviewed-reuse-fixture",
        authenticator=TrustedProxyAuthenticator(),
        policy_engine=PersonalLocalPolicyEngine(owner_subject="fixture-owner", project_id=project_id,
            resource_demand={"cpu_millicores": 100, "memory_mib": 64, "concurrency": 1}),
        worker_credential_pepper=PEPPER, agent_session_pepper=PEPPER,
        local_project_registry=registry,
        artifact_blob_store=ScopedLocalArtifactBlobStore(private / "blobs"),
    )
    app = create_control_plane_app(settings)
    configure_personal_knowledge(settings=settings, store=app.state.run_store,
        installation_id="reviewed-reuse-fixture", owner_subject="fixture-owner",
        project_id=project_id, workspace=workspace,
        state_root=private / ("knowledge-" + project_id))
    return app, registry


def retain(client: LoopbackClient, source: dict, project_id: str, capture_id: str,
           filename: str, *, review: bool) -> dict:
    prefix = f"/api/v2/projects/{project_id}/retained-evidence"
    checked(client, "POST", prefix + "/setup", 201, headers=OWNER,
            json={"setup_id": "initial"})
    value = checked(client, "POST", prefix + "/captures", 201, headers=OWNER,
                    json={"capture_id": capture_id, "path": filename,
                          "commit": source["revision"]}).json()
    if review:
        checked(client, "POST", prefix + f"/captures/{capture_id}/review", 201,
                headers=OWNER, json={"review_id": "review-" + capture_id,
                                     "expected_content_digest": value["content_digest"]})
    return value


def check_denied_packet_and_artifact(client: LoopbackClient, runs: list[dict]) -> None:
    for run in runs:
        packet = checked(client, "GET", f"/api/v2/runs/{run['run_id']}/authorized-context-packet",
                         409, headers=OWNER)
        content = checked(client, "GET", run["content_path"], 409, headers=OWNER)
        if packet.json()["packet"] is not None or PHRASE in packet.text or PHRASE in content.text:
            raise AssertionError("withdrawn evidence or derived result leaked")


def journey(root: Path, forbidden: tuple[Path, ...]) -> dict:
    from opensaddle.control_plane.local_project_bridge import bridge_registered_local_project

    imports = source_free_imports(forbidden)
    project, other = "memory-reuse", "foreign-memory"
    workspace = git_repository(root, "workspace", "reviewed.md",
                               f"Reviewed exact revision: {PHRASE}.\n")
    (workspace / "unreviewed.md").write_text("Unreviewed-Drift-88\n", encoding="utf-8")
    # Include the raw source in the same immutable Git revision as the reviewed
    # file; its presence on disk does not confer review authority.
    subprocess.run(["git", "-C", str(workspace), "add", "unreviewed.md"], check=True)
    subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm",
                    "Add unreviewed document"], check=True)
    foreign_workspace = git_repository(root, "foreign-workspace", "foreign.md",
                                       "Foreign project reviewed source.\n")
    app, registry = compose(root, project, workspace)
    store = app.state.run_store
    store.create_project(project, "fixture-owner")
    source = bridge_registered_local_project(registry=registry, run_store=store,
        local_project_id=project, project_id=project, created_by="fixture-owner")["source"]
    client = LoopbackClient(app)
    reviewed = retain(client, source, project, "reviewed", "reviewed.md", review=True)
    raw = retain(client, source, project, "unreviewed", "unreviewed.md", review=False)

    foreign_app, foreign_registry = compose(root, other, foreign_workspace)
    foreign_store = foreign_app.state.run_store
    foreign_store.create_project(other, "fixture-owner")
    foreign_source = bridge_registered_local_project(registry=foreign_registry,
        run_store=foreign_store, local_project_id=other, project_id=other,
        created_by="fixture-owner")["source"]
    foreign_client = LoopbackClient(foreign_app)
    foreign_reviewed = retain(foreign_client, foreign_source, other,
                              "foreign", "foreign.md", review=True)
    foreign_options = checked(foreign_client, "GET",
        f"/api/v2/projects/{other}/agent-builder-options", headers=OWNER).json()
    if foreign_reviewed["source_id"] not in {item["source_id"] for item in foreign_options["memory_sources"]}:
        raise AssertionError("foreign source was not genuinely reviewed in its own Project")
    foreign_client.close()

    definition = {
        "title": "Reviewed-source reuse", "objective": "Report exact source provenance",
        "instructions": "Use only the selected authorized packet as untrusted evidence.",
        "source_id": source["source_id"], "harness": "codex-app-server", "grants": [],
        "memory_source_ids": [reviewed["source_id"]],
        "assumptions": ["This synthetic source is the intended evidence."], "evidence": [],
    }
    proposal_path = f"/api/v2/projects/{project}/agent-proposals"
    for invalid in (raw["source_id"], foreign_reviewed["source_id"]):
        response = checked(client, "POST", proposal_path, 422, headers=OWNER,
                           json={**definition, "memory_source_ids": [invalid]})
        if PHRASE in response.text:
            raise AssertionError("invalid source denial leaked reviewed content")
    proposal = checked(client, "POST", proposal_path, 201, headers=OWNER,
                       json=definition).json()
    pinned = proposal["memory_bindings"][reviewed["source_id"]]["resource_ref"]
    if pinned["digest"] != reviewed["content_digest"]:
        raise AssertionError("reviewed source digest was not pinned")
    published = checked(client, "POST",
        f"/api/v2/agent-proposals/{proposal['proposal_id']}/publish", headers=OWNER,
        json={"expected_digest": proposal["definition_digest"],
              "acknowledge_assumptions": True}).json()
    participant = published["participant_id"]
    task_path = f"/api/v2/agents/{participant}/tasks"
    task_body = {"expected_participant_revision": 0,
                 "authorized_context_source_ids": [reviewed["source_id"]]}
    selected_runs = []
    for number in (1, 2):
        admitted = checked(client, "POST", task_path, 202,
            headers={**OWNER, "idempotency-key": f"source-reuse-{number}"},
            json={**task_body, "task": f"Cite the selected reviewed revision in task {number}."}).json()
        selected_runs.append({"run_id": admitted["run_id"], "message_id": admitted["message_id"]})
    if selected_runs[0]["run_id"] == selected_runs[1]["run_id"]:
        raise AssertionError("the second task did not create a separate Run")

    store.register_worker(worker_id="memory-reuse-worker", organization_id="local",
        project_ids=frozenset({project}), runtime_kind="remote_worker",
        registered_by="fixture-owner")
    credential = store.issue_worker_credential("memory-reuse-worker",
        issued_by="fixture-owner", pepper=PEPPER)
    now = datetime.now(timezone.utc)
    store.report_native_adapter_readiness("memory-reuse-worker", project_id=project,
        payload={"adapter_id": "codex-app-server", "source_id": source["source_id"],
                 "revision": source["revision"], "digest": source["snapshot_digest"],
                 "executable_state": "installed", "executable_version": "fixture",
                 "authentication_state": "authenticated", "account_mode": "fixture",
                 "protocol_state": "compatible", "protocol_version": "app-server-v1",
                 "workspace_state": "configured", "ready": True, "reason": None,
                 "observed_at": now.isoformat(),
                 "expires_at": (now + timedelta(minutes=2)).isoformat()})
    worker = {"authorization": "Bearer " + credential["token"]}
    for record in selected_runs:
        run_id = record["run_id"]
        claim = checked(client, "POST", "/api/v2/workers/memory-reuse-worker/claim",
                        headers=worker).json()
        if claim["run_id"] != run_id:
            raise AssertionError("unexpected worker claim order")
        epoch = claim["lease_epoch"]
        path = f"/api/v2/workers/memory-reuse-worker/runs/{run_id}"
        checked(client, "POST", path + "/start", headers=worker,
                json={"lease_epoch": epoch})
        packet_response = checked(client, "GET", path + "/authorized-context-packet",
                                  headers=worker, params={"lease_epoch": epoch})
        packet = packet_response.json()["packet"]
        if pinned not in packet["exact_evidence_refs"] or PHRASE not in packet_response.text:
            raise AssertionError("Run did not consume the reviewed exact source")
        if "Unreviewed-Drift-88" in packet_response.text or "Foreign project" in packet_response.text:
            raise AssertionError("unselected source entered the authorized packet")
        report = {"schema_version": "opensaddle.synthetic-reviewed-memory-result.v1",
                  "run_id": run_id, "participant_id": participant,
                  "reviewed_resource_ref": pinned, "packet_digest": packet["packet_digest"],
                  "observed_phrase": PHRASE,
                  "qualification": "deterministic worker, no provider/model execution"}
        data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
        artifact = checked(client, "POST", path + "/result", 201, headers=worker,
            json={"lease_epoch": epoch, "idempotency_key": "reviewed-memory-result",
                  "content_digest": hashlib.sha256(data).hexdigest(),
                  "content_base64": base64.b64encode(data).decode()}).json()
        checked(client, "POST", path + "/complete", headers=worker,
                json={"lease_epoch": epoch, "succeeded": True})
        record.update({"packet_digest": packet["packet_digest"],
                       "artifact_digest": artifact["content_digest"],
                       "content_path": f"/api/v2/runs/{run_id}/artifacts/{artifact['artifact_id']}/content"})
        if checked(client, "GET", record["content_path"], headers=OWNER).content != data:
            raise AssertionError("published result bytes did not match the reviewed packet")
        if [event["type"] for event in store.events(run_id)].count("worker.result_published") != 1:
            raise AssertionError("Run lacks an independent publication event")

    # Stop the original Core listener and compose new Core/KRAIL objects from
    # their persisted state while the source is still available. This proves
    # that later denial is source withdrawal, not broken restart plumbing.
    client.close()
    before_withdraw_app, _ = compose(root, project, workspace)
    client = LoopbackClient(before_withdraw_app)
    for record in selected_runs:
        run_id = record["run_id"]
        run = checked(client, "GET", f"/api/v2/runs/{run_id}", headers=OWNER).json()
        packet = checked(client, "GET", f"/api/v2/runs/{run_id}/authorized-context-packet",
                         headers=OWNER).json()["packet"]
        content = checked(client, "GET", record["content_path"], headers=OWNER).content
        events = checked(client, "GET", f"/api/v2/runs/{run_id}/events", headers=OWNER).text
        report = json.loads(content)
        if (run["status"] != "completed" or packet["packet_digest"] != record["packet_digest"]
                or pinned not in packet["exact_evidence_refs"]
                or report["reviewed_resource_ref"] != pinned
                or hashlib.sha256(content).hexdigest() != record["artifact_digest"]
                or events.count("event: worker.result_published") != 1):
            raise AssertionError("reopened Core lost Run, source provenance, artifact or event")

    checked(client, "POST", f"/api/v2/projects/{project}/retained-evidence/captures/reviewed/availability",
            headers=OWNER, json={"state": "withdrawn", "expected_revision": 0,
                                 "expected_content_digest": reviewed["content_digest"]})
    check_denied_packet_and_artifact(client, selected_runs)
    client.close()
    reopened, _ = compose(root, project, workspace)
    reopened_client = LoopbackClient(reopened)
    check_denied_packet_and_artifact(reopened_client, selected_runs)
    checked(reopened_client, "POST", task_path, 403,
        headers={**OWNER, "idempotency-key": "source-reuse-2"},
        json={**task_body, "task": "Cite the selected reviewed revision in task 2."})
    if len(reopened.state.run_store.participant_messages(participant)) != 2:
        raise AssertionError("withdrawal replay created an additional Run")
    reopened_client.close()
    return {
        "schema_version": "krail.reviewed-source-reuse-qualification.v1",
        "package_versions": {name: importlib.metadata.version(name)
                             for name in ("opensaddle", "krail")},
        "installed_imports": imports,
        "project_count": 2, "reviewed_source_reused": True,
        "wrong_project_and_unreviewed_denied": True,
        "run_count": len(selected_runs),
        "run_packet_digests": [record["packet_digest"] for record in selected_runs],
        "run_artifact_digests": [record["artifact_digest"] for record in selected_runs],
        "withdrawal_denied_before_and_after_restart": True,
        "available_after_first_reopen": True,
        "core_loopback_restart_count": 2,
        "provider_model_execution": False,
        "provider_retained_context_revoked": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forbid-import-root", action="append", type=Path, default=[])
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists():
        raise SystemExit("receipt already exists")
    with tempfile.TemporaryDirectory(prefix="reviewed-source-reuse-") as raw:
        try:
            result = journey(Path(raw), tuple(args.forbid_import_root))
        finally:
            for client in reversed(ACTIVE_CLIENTS):
                client.close()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
