import asyncio
import os
import requests
from app.services import planner_service

async def launch():
    slug = "assessing-the-economic-impact-of-the-urban-enterprise-zone-program-reform"
    project = await planner_service.get_project_by_slug(slug)
    board = await planner_service.ensure_main_board(project)
    tasks = await planner_service.list_tasks(board["_id"], project=project)
    task = next((t for t in tasks if t.get("_id") == "propose-uez-impact-ontology" or t.get("id") == "propose-uez-impact-ontology"), None)
    
    if not task:
        print("Task not found")
        return

    payload = {
        "taskId": str(task["_id"]),
        "role": "data",
        "taskDescription": task["description"],
        "repoUrl": project.get("gitRepoUrl") or "https://github.com/Rutgers-Economics-Labs/" + slug,
        "branch": "main",
        "runnerName": "claude_code"
    }
    
    print(f"Launching session via API for project {slug}...")
    response = requests.post(f"http://localhost:8000/api/v1/projects/{slug}/runner/sessions", json=payload)
    print(response.json())

if __name__ == "__main__":
    asyncio.run(launch())
