import asyncio
import os
from pathlib import Path
from app.services import planner_service, planner_runtime
from app.runners import session_lifecycle

async def relaunch():
    # 1. Resolve project
    slug = "assessing-the-economic-impact-of-the-urban-enterprise-zone-program-reform"
    project = await planner_service.get_project_by_slug(slug)
    if not project:
        print(f"Project {slug} not found")
        return

    # 2. Resolve task
    task_id = "propose-uez-impact-ontology"
    # Find the task on the board to get description
    board = await planner_service.ensure_main_board(project)
    tasks = await planner_service.list_tasks(board["_id"], project=project)
    task = next((t for t in tasks if t.get("id") == task_id or t.get("slug") == task_id or str(t["_id"]) == task_id), None)
    
    if not task:
        print(f"Task {task_id} not found")
        return

    # 3. Create runner session
    # We use 'claude_code' runner as requested
    print(f"Relaunching task: {task['title']} with claude_code...")
    
    try:
        result = await session_lifecycle.create_runner_session(
            project_id=str(project["_id"]),
            project_slug=slug,
            task_id=str(task["_id"]),
            runner_name="claude_code",
            role="data",
            task_description=task["description"],
            repo_url=project.get("githubRepoUrl", ""),
            branch="main",
            local_repo_path=project.get("localRepoPath"),
            agent_role_for_secrets="data"
        )
        print(f"Relaunch successful. Session ID: {result['convex_session_id']}")
        
        # 4. Wait for session to finish so the background task doesn't die
        print("Waiting for session to complete...")
        while True:
            session = await session_lifecycle.get_runner_session(result['convex_session_id'], sync_from_runner=True)
            status = session.get("status")
            print(f"Current status: {status}")
            if status in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(10)
        print(f"Session finished with status: {status}")
    except Exception as e:
        print(f"Relaunch failed: {e}")

if __name__ == "__main__":
    asyncio.run(relaunch())
