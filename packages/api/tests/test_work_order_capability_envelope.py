from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.runners.contracts.runner_profile import CapabilityState, CertificationStatus
from app.runners.contracts.work_order import Capability, TaskType, WorkOrder
from app.services import capability_router


def _sample_work_order(**overrides) -> WorkOrder:
    payload = {
        "work_order_id": "wo_test_scope",
        "project_slug": "demo",
        "task_type": TaskType.VERIFICATION,
        "capabilities_required": [Capability.RUN_SHELL],
        "allowed_paths": ["research_plan", "artifacts"],
        "created_by": "planner",
    }
    payload.update(overrides)
    return WorkOrder.model_validate(payload)


def test_work_order_derives_capability_envelope_from_legacy_fields() -> None:
    work_order = _sample_work_order()

    assert work_order.capability_envelope is not None
    assert work_order.capability_envelope.version == "v1alpha1"
    assert work_order.capability_envelope.scope_rule == "intersection_with_repo_policy"
    assert work_order.capability_envelope.required_capabilities == [Capability.RUN_SHELL]
    assert work_order.capability_envelope.paths.write == ["research_plan", "artifacts"]
    assert work_order.capability_envelope.paths.read == []
    assert work_order.capability_envelope.tools.allow == []


def test_work_order_rejects_capability_envelope_that_widens_scope() -> None:
    with pytest.raises(ValidationError, match="cannot widen"):
        _sample_work_order(
            capability_envelope={
                "required_capabilities": [
                    Capability.RUN_SHELL.value,
                    Capability.BROWSE_WEB.value,
                ],
                "paths": {
                    "write": ["research_plan", "docs/private"],
                },
            }
        )


@pytest.mark.asyncio
async def test_route_task_logs_capability_envelope_and_requires_yes_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = SimpleNamespace(
        agents=SimpleNamespace(
            runner_policy=SimpleNamespace(
                allowed=["codex_cli", "cursor_cli", "copilot_cli"],
                preferred=["codex_cli"],
            )
        )
    )

    monkeypatch.setattr(
        capability_router.planner_service,
        "project_root_from_record",
        lambda project: tmp_path,
    )
    monkeypatch.setattr(
        capability_router.planner_service,
        "load_validated_manifest",
        lambda project: manifest,
    )
    monkeypatch.setattr(
        capability_router,
        "load_all_profiles",
        lambda: {
            "codex_cli": SimpleNamespace(
                name="codex_cli",
                status=CertificationStatus.CERTIFIED,
                capabilities={Capability.RUN_SHELL: CapabilityState.YES},
                task_affinity={TaskType.VERIFICATION: 0.8},
            ),
            "cursor_cli": SimpleNamespace(
                name="cursor_cli",
                status=CertificationStatus.EXPERIMENTAL,
                capabilities={Capability.RUN_SHELL: CapabilityState.CONFIGURABLE},
                task_affinity={TaskType.VERIFICATION: 0.95},
            ),
            "copilot_cli": SimpleNamespace(
                name="copilot_cli",
                status=CertificationStatus.ADVISORY_ONLY,
                capabilities={Capability.RUN_SHELL: CapabilityState.YES},
                task_affinity={TaskType.VERIFICATION: 1.0},
            ),
        },
    )

    work_order = _sample_work_order()

    selected = await capability_router.route_task(
        project_slug="demo",
        work_order_id=work_order.work_order_id,
        required_capabilities=work_order.capabilities_required,
        task_type=work_order.task_type,
        capability_envelope=work_order.capability_envelope,
        project={"slug": "demo", "localRepoPath": str(tmp_path)},
    )

    assert selected == "codex_cli"

    dispatch_log = json.loads(
        (tmp_path / "research_plan" / "dispatch_log" / f"{work_order.work_order_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert dispatch_log["selected_runner"] == "codex_cli"
    assert dispatch_log["required_capabilities"] == ["run_shell"]
    assert dispatch_log["capability_envelope"]["paths"]["write"] == ["research_plan", "artifacts"]
    assert "configurable" in dispatch_log["rejection_reasons"]["cursor_cli"]
    assert "advisory_only" in dispatch_log["rejection_reasons"]["copilot_cli"]
