from __future__ import annotations

import textwrap
from pathlib import Path

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def _configure_mount(root: Path, child: Path, *, access_mode: str = "delegated") -> None:
    manifest = root / "rail.yaml"
    relative_child = child.relative_to(root.parent).as_posix()
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + textwrap.dedent(
            f"""

            mounts:
              - id: child
                name: Child KB
                path: ../{relative_child}
                access_mode: "{access_mode}"
                search_weight: 1.5
                tags:
                  - "child"
            """
        ),
        encoding="utf-8",
    )


def test_mount_list_and_federated_search_find(tmp_path: Path):
    root = bootstrap_future_project(tmp_path / "root", name="Root Project", slug="root-project")
    child = bootstrap_future_project(tmp_path / "child", name="Child Project", slug="child-project")
    _configure_mount(root, child)

    (root / "topics" / "local.md").write_text("# Local\n\nrootcodename local topic.\n", encoding="utf-8")
    (child / "topics" / "public.md").write_text("# Public\n\nchildcodename public topic.\n", encoding="utf-8")
    (child / "topics" / "private.md").write_text(
        "---\n"
        "visibility: private\n"
        "allowed_roles:\n"
        "  - reviewer\n"
        "---\n\n"
        "# Private\n\nchildcodename restricted topic.\n",
        encoding="utf-8",
    )

    runtime = KnowledgeRuntime(root)
    mounts = runtime.mount_list()
    federated = runtime.federated_search("childcodename", mounts=["child"])
    found = runtime.federated_find("childcodename", mounts=["child"], rag=False)

    assert mounts["summary"]["healthy"] == 1
    assert mounts["mounts"][0]["id"] == "child"
    assert mounts["mounts"][0]["ok"] is True
    assert [hit["path"] for hit in federated["hits"]] == ["child:topics/public.md"]
    assert all(result["path"] == "child:topics/public.md" for result in found["results"] if result["type"] == "document")


def test_federated_search_respects_mount_access_mode_metadata_only(tmp_path: Path):
    root = bootstrap_future_project(tmp_path / "root", name="Root Project", slug="root-project")
    child = bootstrap_future_project(tmp_path / "child", name="Child Project", slug="child-project")
    _configure_mount(root, child, access_mode="metadata_only")

    (child / "topics" / "public.md").write_text("# Public\n\nchildcodename public topic.\n", encoding="utf-8")

    runtime = KnowledgeRuntime(root)
    result = runtime.federated_search("childcodename", mounts=["child"])

    assert result["hits"][0]["path"] == "child:topics/public.md"
    assert "snippet" not in result["hits"][0]


def test_federated_think_and_graph_summary_and_mount_proxy_ops(tmp_path: Path):
    root = bootstrap_future_project(tmp_path / "root", name="Root Project", slug="root-project")
    child = bootstrap_future_project(tmp_path / "child", name="Child Project", slug="child-project")
    _configure_mount(root, child)

    (child / "topics" / "public.md").write_text("# Public\n\nchildcodename public topic.\n", encoding="utf-8")
    child_runtime = KnowledgeRuntime(child)
    child_runtime.graph_build(write=True)

    root_runtime = KnowledgeRuntime(root)
    think = root_runtime.federated_think("childcodename", mounts=["child"], limit=3)
    graph = root_runtime.federated_graph_summary(mounts=["child"])
    task = root_runtime.mount_create_task("child", "Review Child", description="inspect child", role="research")
    workflows = root_runtime.mount_workflow_list("child")

    assert think["federated"] is True
    assert "child" in think["consulted_mounts"]
    assert any(citation["path"] == "child:topics/public.md" for citation in think["citations"])
    assert any(item["mount"] == "child" for item in graph["summaries"])
    assert task["mount"] == "child"
    assert task["project"] == "child-project"
    assert workflows["mount"] == "child"
