import asyncio
from app.services import running_agent_service, planner_service
import time

async def cleanup():
    slug = "assessing-the-economic-impact-of-the-urban-enterprise-zone-program-reform"
    project = await planner_service.get_project_by_slug(slug)
    if not project:
        print("Project not found")
        return
        
    print(f"Cleaning up active sessions for project: {slug}")
    agents = await running_agent_service.list_project_running_agents(project["_id"], active_only=True)
    for agent in agents:
        print(f"Finalizing session {agent['_id']}...")
        await running_agent_service.finalize_running_agent(
            agent["_id"],
            status="failed",
            ended_at=int(time.time() * 1000)
        )
    print("Done.")

if __name__ == "__main__":
    asyncio.run(cleanup())
