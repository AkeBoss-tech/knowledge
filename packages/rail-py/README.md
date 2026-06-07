# rail-py

KRAIL client supporting local project mode and the local FastAPI runtime.

## Usage Examples

```python
import rail

# API mode
project = rail.connect("nj-economics")

# DataFrame queries
df = project.query("SELECT county_name, unemployment_rate FROM County ORDER BY unemployment_rate DESC LIMIT 10")

# Agent research
answer = project.agent.ask("What counties had unemployment above 10% in 2020?")
print(answer)

# Streaming agent
for event in project.agent.ask("Compare Hudson and Bergen County unemployment trends", stream=True):
    if event["type"] == "text_delta":
        print(event["text"], end="", flush=True)

# Direct local mode
project = rail.local("./nj-economics")
ont = project.ontology()
counties = ont.individuals("County")
print(f"Loaded {len(counties)} counties")
```
