"""WorkOrder — typed dispatch record handed to runners.

Replaces ad-hoc TaskPayload flattening into a prompt. Stored at
research_plan/work_orders/<wo_id>.json so RAIL can validate dispatch before
launch and any audit can reconstruct the exact task envelope after the fact.

The work order is passed to the runner two ways:
- as a human-readable prompt (existing path)
- as a machine-readable JSON file the agent can re-read via
  rail.get_work_order(wo_id) (Phase 3)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class TaskType(str, Enum):
    """Task class the work order represents.

    The set is small on purpose: each value maps to one context compiler in
    Phase 6 and one row in the runner task_affinity table. Adding values has
    a real cost; don't expand without updating profiles + compilers.
    """

    DATA_INGESTION = "data_ingestion"
    ANALYSIS = "analysis"
    SOURCE_DISCOVERY = "source_discovery"
    ARTIFACT_WRITING = "artifact_writing"
    HEALTH_REPAIR = "health_repair"
    CLAIM_EXTRACTION = "claim_extraction"
    VERIFICATION = "verification"


class Capability(str, Enum):
    """Capabilities a work order may require.

    These are what the capability router (Phase 5) matches against the
    RunnerProfile.capabilities map. Keep names concrete and verb-shaped so
    the matching stays unambiguous.
    """

    EDIT_FILES = "edit_files"
    RUN_SHELL = "run_shell"
    FETCH_REMOTE_DATA = "fetch_remote_data"
    QUERY_DUCKDB = "query_duckdb"
    EXECUTE_PYTHON = "execute_python"
    USE_MCP_TOOLS = "use_mcp_tools"
    BROWSE_WEB = "browse_web"
    EXTRACT_PDF_TABLES = "extract_pdf_tables"
    WRITE_LONG_ARTIFACTS = "write_long_artifacts"
    HANDLE_LARGE_CONTEXT = "handle_large_context"
    WRITE_STRUCTURED_OUTPUT = "write_structured_output"


class TrustPolicy(BaseModel):
    """How outputs from this work order should be treated by promotion gates.

    Aligned with the trust states from background-health-governance:
    candidate / draft / unverified / blocked_for_promotion / verified /
    rejected. Outputs start at output_trust_state; promotion to verified
    requires each gate in promotion_requires to pass.
    """

    model_config = ConfigDict(extra="forbid")

    output_trust_state: str = "candidate"
    promotion_requires: list[str] = Field(default_factory=list)


class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 2
    if_no_progress: str = "downgrade_or_escalate"


class ExpectedProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    one_of: list[str] = Field(default_factory=list)


def _validate_project_relative_paths(paths: list[str], *, field_name: str) -> list[str]:
    for path in paths:
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError(
                f"{field_name} entry {path!r} must be a relative path "
                f"within the project root (no leading / or .. segments)"
            )
    return paths


def _path_within_scopes(path: str, scopes: list[str]) -> bool:
    normalized_path = path.rstrip("/")
    for scope in scopes:
        normalized_scope = scope.rstrip("/")
        if normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/"):
            return True
    return False


class CapabilityPathScope(BaseModel):
    """Filesystem scope intended for a runner session.

    Work Order 04 starts with write paths because that is what current runners
    already understand. Read and deny are reserved so Workstream C can wire the
    same envelope into stricter adapter- and MCP-side enforcement later without
    changing the top-level contract again.
    """

    model_config = ConfigDict(extra="forbid")

    write: list[str] = Field(default_factory=list)
    read: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @field_validator("write", "read", "deny")
    @classmethod
    def _paths_stay_inside_project(cls, value: list[str], info) -> list[str]:
        return _validate_project_relative_paths(value, field_name=info.field_name)


class CapabilityToolScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class CapabilitySecretScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)


class CapabilityEnvelope(BaseModel):
    """Declarative session scope carried with a work order.

    The envelope is additive and backward-aware: the legacy compatibility fields
    (`capabilities_required`, `allowed_paths`) remain canonical for older code,
    while this structure gives newer adapters a single object to consume.

    Important: this envelope narrows runner intent and must be intersected with
    repo policy. It is not a filesystem sandbox and does not widen permissions.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "v1alpha1"
    scope_rule: Literal["intersection_with_repo_policy"] = "intersection_with_repo_policy"
    enforcement_state: Literal["declared", "partially_enforced", "enforced"] = "declared"
    required_capabilities: list[Capability] = Field(default_factory=list)
    paths: CapabilityPathScope = Field(default_factory=CapabilityPathScope)
    tools: CapabilityToolScope = Field(default_factory=CapabilityToolScope)
    secrets: CapabilitySecretScope = Field(default_factory=CapabilitySecretScope)

    @classmethod
    def from_legacy_scope(
        cls,
        *,
        required_capabilities: list[Capability],
        allowed_paths: list[str],
    ) -> "CapabilityEnvelope":
        return cls(
            required_capabilities=list(required_capabilities),
            paths=CapabilityPathScope(write=list(allowed_paths)),
        )


WorkOrderId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_:.\-]+$"),
]


class WorkOrder(BaseModel):
    """A typed dispatch record.

    The work order is the contract between the planner and the runner.
    It declares what needs to happen, what the runner is allowed to touch,
    what capabilities are required, and what shape the output must take.

    Backwards compat: legacy TaskPayload still works; a translation shim
    (Phase 2) wraps it as a WorkOrder with conservative defaults. New
    planner-emitted dispatches go through this schema directly.
    """

    model_config = ConfigDict(extra="forbid")

    work_order_id: WorkOrderId
    project_slug: str
    task_type: TaskType
    phase: str | None = None

    # Track B: Liveness and Anti-Stuck
    expected_progress: ExpectedProgress = Field(default_factory=ExpectedProgress)
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    idempotency_key: str | None = None
    input_hash: str | None = None

    # Routing
    capabilities_required: list[Capability]
    runner_preferred: str | None = None
    """Operator override. If set, the router must dispatch to this runner
    or fail loudly — not silently fall back."""
    runner_allowed: list[str] | None = None
    """Project-level allow-list from rail.yaml. None = all certified runners
    eligible. Empty list is an error (no runners can satisfy)."""

    # Filesystem scope
    allowed_paths: list[str]
    """Paths the agent may write within (relative to project root).
    Enforced by the runner adapter, not just documented in the prompt."""
    capability_envelope: CapabilityEnvelope | None = None
    """Structured session-scope record for newer runners and audits.
    Additive with the legacy compatibility fields above and below."""

    # Inputs / outputs
    inputs: dict[str, str] = Field(default_factory=dict)
    """Named references to input files (e.g. {"brief": "topics/brief.md"}).
    Resolved relative to project root."""
    outputs_required: list[str] = Field(default_factory=list)
    """Output types the runner must emit before the session can be promoted
    (e.g. ["claims", "verification_command", "session_result_json"])."""

    # Trust / cost / time
    trust_policy: TrustPolicy = Field(default_factory=TrustPolicy)
    cost_budget_usd: float | None = None
    wall_time_budget_minutes: int | None = None

    # Q&A (Phase 4)
    questions_allowed: bool = True
    """If false, the agent must either complete or block — no asking.
    Used for narrowly-scoped tasks where any ambiguity should escalate."""

    # Dependencies
    depends_on: list[WorkOrderId] = Field(default_factory=list)
    """Other work_order_ids that must reach a terminal state first."""

    # Provenance
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str
    """session_id of the planner session, or "planner" for direct planner
    output. Useful for tracing dispatch decisions back to a session."""

    @field_validator("allowed_paths")
    @classmethod
    def _no_escape_from_project_root(cls, paths: list[str]) -> list[str]:
        return _validate_project_relative_paths(paths, field_name="allowed_paths")

    @field_validator("runner_allowed")
    @classmethod
    def _runner_allowed_nonempty_if_set(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) == 0:
            raise ValueError(
                "runner_allowed must be None (no restriction) or a non-empty list. "
                "Empty list would mean no runner can satisfy this work order."
            )
        return value

    @field_validator("capabilities_required")
    @classmethod
    def _capabilities_nonempty(cls, value: list[Capability]) -> list[Capability]:
        if not value:
            raise ValueError(
                "capabilities_required must declare at least one capability. "
                "A work order with no capability requirements cannot be routed."
            )
        return value

    @model_validator(mode="after")
    def _hydrate_capability_envelope(self) -> "WorkOrder":
        if self.capability_envelope is None:
            self.capability_envelope = CapabilityEnvelope.from_legacy_scope(
                required_capabilities=self.capabilities_required,
                allowed_paths=self.allowed_paths,
            )
            return self

        if not self.capability_envelope.required_capabilities:
            self.capability_envelope.required_capabilities = list(self.capabilities_required)

        widened_caps = set(self.capability_envelope.required_capabilities) - set(self.capabilities_required)
        if widened_caps:
            widened = ", ".join(sorted(cap.value for cap in widened_caps))
            raise ValueError(
                "capability_envelope.required_capabilities cannot widen "
                f"capabilities_required: {widened}"
            )

        if not self.capability_envelope.paths.write:
            self.capability_envelope.paths.write = list(self.allowed_paths)

        widened_paths = [
            path
            for path in self.capability_envelope.paths.write
            if not _path_within_scopes(path, self.allowed_paths)
        ]
        if widened_paths:
            widened = ", ".join(sorted(widened_paths))
            raise ValueError(
                "capability_envelope.paths.write cannot widen allowed_paths: "
                f"{widened}"
            )

        return self
