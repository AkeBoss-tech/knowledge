# API Runtime

The API is optional. It wraps local project/runtime operations for clients that
prefer HTTP.

It should:

- serve project state
- trigger hydration
- run local workflows
- expose query/search/integrity endpoints
- provide an adapter for replaceable interfaces

It should not:

- require a hosted database
- imply one official frontend
- store durable truth outside the project repo

Operational API records currently use `.krail/store.json` unless
`LOCAL_STORE_PATH` points elsewhere.

