"""KRAIL management protocol v1.

This module is intentionally separate from both ``rail.cli`` and the read-only
provider contract.  It exposes only bounded lifecycle operations over one
workspace selected when the service is constructed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

import rail
from rail.bootstrap import bootstrap_future_project
from rail.markdown_graph import build_markdown_graph, load_config, mermaid_for_graph, summary_for_graph
from rail.modes import get_mode


CONTRACT = "krail.management.v1"
PROTOCOL_VERSION = "1.0"
MAX_EFFECTS = 512
MAX_INIT_BYTES = 4 * 1024 * 1024
MAX_REINDEX_BYTES = 16 * 1024 * 1024
MAX_WORKSPACE_FILES = 20_000
MAX_WORKSPACE_BYTES = 256 * 1024 * 1024
_MANAGEMENT_PREFIX = ".krail/management/"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InitOptions(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._()-]*$")
    slug: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    default_branch: str = Field(default="main", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    mode: Literal["ontology_first", "markdown_graph"] = "ontology_first"
    knowledge_mode: Literal["research", "company", "personal", "software", "project"] = "research"
    pack: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    @field_validator("name", "default_branch")
    @classmethod
    def no_controls(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 for char in value):
            raise ValueError("control characters are not allowed")
        return value


class Effect(StrictModel):
    path: str
    action: Literal["create_directory", "write_file"]
    size: int = Field(ge=0)
    content_digest: str | None = None

    @model_validator(mode="after")
    def content_matches_action(self) -> "Effect":
        if self.action == "write_file" and not self.content_digest:
            raise ValueError("write_file effects require a content digest")
        if self.action == "create_directory" and self.content_digest is not None:
            raise ValueError("directory effects cannot have a content digest")
        return self


class InitPlan(StrictModel):
    contract: Literal[CONTRACT] = CONTRACT
    plan_version: Literal["1.0"] = "1.0"
    operation: Literal["init"] = "init"
    workspace_version: str
    workspace_digest: str
    options: InitOptions
    effects: list[Effect]
    effect_count: int = Field(ge=0, le=MAX_EFFECTS)
    total_write_bytes: int = Field(ge=0, le=MAX_INIT_BYTES)
    plan_digest: str

    @model_validator(mode="after")
    def totals_match_effects(self) -> "InitPlan":
        if self.effect_count != len(self.effects):
            raise ValueError("effect_count does not match effects")
        total = sum(effect.size for effect in self.effects if effect.action == "write_file")
        if self.total_write_bytes != total:
            raise ValueError("total_write_bytes does not match effects")
        return self


class RequestBase(StrictModel):
    contract: Literal[CONTRACT]
    request_id: str = Field(min_length=1, max_length=200)


class InspectRequest(RequestBase):
    operation: Literal["inspect"]


class PlanInitRequest(RequestBase):
    operation: Literal["plan_init"]
    expected_workspace_version: str
    expected_workspace_digest: str
    options: InitOptions = Field(default_factory=InitOptions)


class ApplyInitRequest(RequestBase):
    operation: Literal["apply_init"]
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    expected_workspace_version: str
    expected_workspace_digest: str
    plan: InitPlan


class DoctorRequest(RequestBase):
    operation: Literal["doctor"]


class ReindexRequest(RequestBase):
    operation: Literal["reindex"]
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    expected_workspace_version: str
    expected_workspace_digest: str


REQUEST_MODELS = {
    "inspect": InspectRequest,
    "plan_init": PlanInitRequest,
    "apply_init": ApplyInitRequest,
    "doctor": DoctorRequest,
    "reindex": ReindexRequest,
}


class ManagementError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ManagementError("UNBOUNDED_PATH", "an effect resolves outside the managed workspace") from exc


def _workspace_inventory(root: Path) -> tuple[list[dict[str, Any]], int]:
    if not root.exists():
        return [], 0
    if not root.is_dir() or root.is_symlink():
        raise ManagementError("INVALID_WORKSPACE", "managed workspace must be a directory, not a file or symlink")
    rows: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        rel = _safe_relative(path, root)
        if (
            rel == ".git"
            or rel.startswith(".git/")
            or rel == ".krail"
            or rel == ".krail/management"
            or rel.startswith(_MANAGEMENT_PREFIX)
        ):
            continue
        if path.is_symlink():
            rows.append({"path": rel, "kind": "symlink", "target": os.readlink(path)})
        elif path.is_dir():
            rows.append({"path": rel, "kind": "directory"})
        elif path.is_file():
            size = path.stat().st_size
            total += size
            if total > MAX_WORKSPACE_BYTES:
                raise ManagementError("RESOURCE_LIMIT", "workspace content exceeds the management digest byte limit")
            rows.append({"path": rel, "kind": "file", "size": size, "digest": _digest(path.read_bytes())})
        if len(rows) > MAX_WORKSPACE_FILES:
            raise ManagementError("RESOURCE_LIMIT", "workspace contains too many entries for bounded inspection")
    return rows, total


def workspace_state(root: Path) -> dict[str, Any]:
    rows, total = _workspace_inventory(root)
    digest = _digest(rows)
    return {
        "exists": root.exists(),
        "initialized": (root / "rail.yaml").is_file(),
        "workspace_version": f"1:{digest.removeprefix('sha256:')}",
        "workspace_digest": digest,
        "entries": len(rows),
        "content_bytes": total,
        "symlinks": sum(1 for row in rows if row["kind"] == "symlink"),
    }


def _initialize(root: Path, options: InitOptions) -> dict[str, Any]:
    selected_mode = get_mode(options.knowledge_mode)
    selected_pack = options.pack or selected_mode.get("default_pack")
    project_root = bootstrap_future_project(
        root,
        name=options.name or root.name,
        slug=options.slug,
        default_branch=options.default_branch,
        mode=options.mode,
        knowledge_mode=options.knowledge_mode,
        pack=selected_pack,
    )
    project = rail.local(str(project_root))
    materialized: list[str] = []
    if selected_pack:
        project.pack("use", selected_pack)
        active = project.pack("active").get("active") or {}
        for workflow_id in active.get("workflows") or []:
            if isinstance(workflow_id, str):
                result = project.init_workflow(workflow_id)
                if result.get("status") in {"written", "exists"}:
                    materialized.append(workflow_id)
    graph = project.graph_build(write=True)
    return {
        "pack": selected_pack,
        "knowledge_mode": selected_mode["id"],
        "materialized_workflows": materialized,
        "graph": {"counts": graph.get("counts", {}), "warnings": graph.get("warnings", [])},
    }


def _effects(root: Path) -> tuple[list[Effect], int]:
    inventory, _ = _workspace_inventory(root)
    effects: list[Effect] = []
    total = 0
    for item in inventory:
        if item["kind"] == "symlink":
            raise ManagementError("UNBOUNDED_EFFECT", "initialization plans may not create symlinks")
        if item["kind"] == "directory":
            effects.append(Effect(path=item["path"], action="create_directory", size=0))
        else:
            total += item["size"]
            effects.append(
                Effect(path=item["path"], action="write_file", size=item["size"], content_digest=item["digest"])
            )
    if len(effects) > MAX_EFFECTS or total > MAX_INIT_BYTES:
        raise ManagementError("RESOURCE_LIMIT", "initialization preview exceeds the bounded effect limit")
    return effects, total


def _plan_body(plan: InitPlan) -> dict[str, Any]:
    return plan.model_dump(mode="json", exclude={"plan_digest"})


def _validated_output_paths(root: Path) -> list[str]:
    config = load_config(root)
    candidates = [config.json_path, config.mermaid_path, config.summary_path, config.docs_json_path, config.docs_mermaid_path]
    result: list[str] = []
    for raw in candidates:
        if not raw:
            continue
        path = (root / raw).resolve()
        rel = _safe_relative(path, root)
        if rel in {".", ".git", ".krail/management"} or rel.startswith(".git/") or rel.startswith(_MANAGEMENT_PREFIX):
            raise ManagementError("UNBOUNDED_PATH", "reindex output overlaps protected management or Git state")
        result.append(rel)
    if len(set(result)) > 5:
        raise ManagementError("RESOURCE_LIMIT", "reindex has too many configured outputs")
    return sorted(set(result))


def _preview_reindex(root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build graph content without writes and enforce a byte bound up front."""
    graph = build_markdown_graph(root, write=False)
    config = load_config(root)
    json_bytes = (json.dumps(graph, indent=2, default=str) + "\n").encode("utf-8")
    outputs: dict[str, bytes] = {
        config.json_path: json_bytes,
        config.mermaid_path: mermaid_for_graph(graph).encode("utf-8"),
        config.summary_path: summary_for_graph(graph).encode("utf-8"),
    }
    if config.docs_json_path:
        outputs[config.docs_json_path] = json_bytes
    if config.docs_mermaid_path:
        outputs[config.docs_mermaid_path] = outputs[config.mermaid_path]
    if sum(len(content) for content in outputs.values()) > MAX_REINDEX_BYTES:
        raise ManagementError("RESOURCE_LIMIT", "reindex output exceeds the 16 MiB write limit")
    return graph, outputs


class ManagementService:
    """Execute management-v1 operations against exactly one bound workspace."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()

    def _success(self, request_id: str, operation: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "operation": operation,
            "ok": True,
            "result": result,
        }

    def _receipt_path(self, key: str) -> Path:
        name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"
        return self.workspace / ".krail" / "management" / "v1" / "receipts" / name

    def _request_fingerprint(self, request: ApplyInitRequest | ReindexRequest) -> str:
        return _digest(request.model_dump(mode="json", exclude={"request_id"}))

    def _replay(self, request: ApplyInitRequest | ReindexRequest) -> dict[str, Any] | None:
        path = self._receipt_path(request.idempotency_key)
        if not path.is_file():
            return None
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ManagementError("CORRUPT_RECEIPT", "stored idempotency receipt is not readable") from exc
        if receipt.get("request_fingerprint") != self._request_fingerprint(request):
            raise ManagementError(
                "IDEMPOTENCY_CONFLICT",
                "idempotency key was already used for a different mutation request",
                details={"idempotency_key": request.idempotency_key},
            )
        result = dict(receipt["result"])
        result["replayed"] = True
        return result

    def _store_receipt(self, request: ApplyInitRequest | ReindexRequest, result: dict[str, Any]) -> None:
        path = self._receipt_path(request.idempotency_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {
            "contract": CONTRACT,
            "operation": request.operation,
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": self._request_fingerprint(request),
            "result": result,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(_canonical(receipt) + b"\n")
        os.replace(temporary, path)

    @staticmethod
    def _check_expected(state: dict[str, Any], version: str, digest: str) -> None:
        if state["workspace_version"] != version or state["workspace_digest"] != digest:
            raise ManagementError(
                "STALE_WORKSPACE",
                "workspace version or digest no longer matches the caller's expectation",
                details={
                    "expected_workspace_version": version,
                    "actual_workspace_version": state["workspace_version"],
                    "expected_workspace_digest": digest,
                    "actual_workspace_digest": state["workspace_digest"],
                },
            )

    def inspect(self, request: InspectRequest) -> dict[str, Any]:
        state = workspace_state(self.workspace)
        return self._success(request.request_id, request.operation, {**state, "capabilities": list(REQUEST_MODELS)})

    def plan_init(self, request: PlanInitRequest) -> dict[str, Any]:
        state = workspace_state(self.workspace)
        self._check_expected(state, request.expected_workspace_version, request.expected_workspace_digest)
        if state["entries"]:
            raise ManagementError("WORKSPACE_NOT_EMPTY", "initialization is limited to a missing or empty workspace")
        resolved = request.options.model_copy(update={"name": request.options.name or self.workspace.name})
        with tempfile.TemporaryDirectory(prefix="krail-plan-") as temporary:
            preview = Path(temporary) / "workspace"
            _initialize(preview, resolved)
            effects, total = _effects(preview)
        plan = InitPlan(
            workspace_version=state["workspace_version"],
            workspace_digest=state["workspace_digest"],
            options=resolved,
            effects=effects,
            effect_count=len(effects),
            total_write_bytes=total,
            plan_digest="sha256:" + "0" * 64,
        )
        plan.plan_digest = _digest(_plan_body(plan))
        return self._success(request.request_id, request.operation, {"plan": plan.model_dump(mode="json")})

    def apply_init(self, request: ApplyInitRequest) -> dict[str, Any]:
        replay = self._replay(request)
        if replay is not None:
            return self._success(request.request_id, request.operation, replay)
        if request.plan.plan_digest != _digest(_plan_body(request.plan)):
            raise ManagementError("PLAN_DIGEST_MISMATCH", "initialization plan content does not match its digest")
        if request.plan.workspace_version != request.expected_workspace_version or request.plan.workspace_digest != request.expected_workspace_digest:
            raise ManagementError("PLAN_PRECONDITION_MISMATCH", "apply preconditions do not match the previewed workspace")
        state = workspace_state(self.workspace)
        self._check_expected(state, request.expected_workspace_version, request.expected_workspace_digest)
        if state["entries"]:
            raise ManagementError("WORKSPACE_NOT_EMPTY", "initialization is limited to a missing or empty workspace")

        parent = self.workspace.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".krail-apply-", dir=parent))
        try:
            domain_result = _initialize(staging, request.plan.options)
            actual_effects, total = _effects(staging)
            if actual_effects != request.plan.effects or total != request.plan.total_write_bytes:
                raise ManagementError("PLAN_DRIFT", "current runtime would not produce the previewed initialization effects")
            if self.workspace.exists():
                if any(self.workspace.iterdir()):
                    raise ManagementError("WORKSPACE_CHANGED", "workspace changed while initialization was being staged")
                self.workspace.rmdir()
            os.replace(staging, self.workspace)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        after = workspace_state(self.workspace)
        result = {
            "status": "applied",
            "replayed": False,
            "plan_digest": request.plan.plan_digest,
            "applied_effects": request.plan.effect_count,
            "workspace_version": after["workspace_version"],
            "workspace_digest": after["workspace_digest"],
            "domain": domain_result,
        }
        self._store_receipt(request, result)
        return self._success(request.request_id, request.operation, result)

    def doctor(self, request: DoctorRequest) -> dict[str, Any]:
        state = workspace_state(self.workspace)
        if not state["initialized"]:
            raise ManagementError("NOT_INITIALIZED", "doctor requires an initialized KRAIL workspace")
        project = rail.local(str(self.workspace))
        report = project.doctor(check_cli_version=False)
        return self._success(request.request_id, request.operation, {"workspace": state, "report": report})

    def reindex(self, request: ReindexRequest) -> dict[str, Any]:
        replay = self._replay(request)
        if replay is not None:
            return self._success(request.request_id, request.operation, replay)
        before = workspace_state(self.workspace)
        self._check_expected(before, request.expected_workspace_version, request.expected_workspace_digest)
        if not before["initialized"]:
            raise ManagementError("NOT_INITIALIZED", "reindex requires an initialized KRAIL workspace")
        if before["symlinks"]:
            raise ManagementError("UNBOUNDED_PATH", "reindex refuses workspaces containing symlinks")
        outputs = _validated_output_paths(self.workspace)
        graph, rendered = _preview_reindex(self.workspace)
        for rel, content in rendered.items():
            path = self.workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.krail-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                temporary = Path(handle.name)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        after = workspace_state(self.workspace)
        result = {
            "status": "applied",
            "replayed": False,
            "bounded_outputs": outputs,
            "written": sorted(rendered),
            "counts": graph.get("counts", {}),
            "warnings": graph.get("warnings", []),
            "workspace_version": after["workspace_version"],
            "workspace_digest": after["workspace_digest"],
        }
        self._store_receipt(request, result)
        return self._success(request.request_id, request.operation, result)

    def handle(self, payload: Any) -> dict[str, Any]:
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        operation = payload.get("operation") if isinstance(payload, dict) else None
        try:
            if not isinstance(payload, dict):
                raise ManagementError("INVALID_REQUEST", "request must be a JSON object")
            model = REQUEST_MODELS.get(str(operation))
            if model is None:
                raise ManagementError("UNSUPPORTED_OPERATION", "operation is not part of krail.management.v1")
            request = model.model_validate(payload)
            return getattr(self, str(operation))(request)
        except ValidationError as exc:
            error = ManagementError(
                "INVALID_REQUEST",
                "request failed contract validation",
                details={"issues": exc.errors(include_url=False, include_context=False)},
            )
        except ManagementError as exc:
            error = exc
        except Exception as exc:
            error = ManagementError("INTERNAL_ERROR", "management operation failed", details={"exception_type": type(exc).__name__})
        return {
            "contract": CONTRACT,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "operation": operation,
            "ok": False,
            "error": {"code": error.code, "message": error.message, "details": error.details},
        }
