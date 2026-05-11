"""FastAPI service that backs the Clarion SE web UI.

Wraps the existing planner/research/generator/provision/kg_publish/livetail
modules so the UI never re-implements business logic. Streams long-running
agent and CLI output over Server-Sent Events.

Local-only by default: binds to 127.0.0.1, CORS allow-list is the Vite
dev origin (http://localhost:5173) plus the production-build origin
(http://localhost:4173). Anything else is rejected.
"""
