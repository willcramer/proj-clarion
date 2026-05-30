"""Postgres storage layer.

The pipeline persists three kinds of artifact: CompanyProfiles, DemoPlans
(with their nested KnowledgeGraph), and BusinessEvents. Every artifact lives
in Postgres; raw JSON copies under data/ are convenience snapshots, not the
source of truth.

Public surface:
- `connect()`   — get an Engine bound to the env-configured DSN
- `apply_migrations()` — run pending raw-SQL migrations
- `ProfileRepo`, `PlanRepo`, `KGRepo`, `AuditRepo` — small write/read API
"""

from proj_clarion.storage.db import build_dsn, connect, session_scope
from proj_clarion.storage.migrator import apply_migrations, drop_all
from proj_clarion.storage.repositories import (
    AgentToolCallRepo,
    AssistantConversationRepo,
    AssistantTurnRepo,
    AuditRepo,
    DemoSessionRepo,
    KGRepo,
    LlmCallRepo,
    LlmEvalRepo,
    PipelineRepo,
    PlanRefinementSessionRepo,
    PlanRefinementTurnRepo,
    PlanRepo,
    PolicyViolationRepo,
    ProfileAuditRepo,
    ProfileRepo,
    SystemHealthRepo,
)

__all__ = [
    "AgentToolCallRepo",
    "AssistantConversationRepo",
    "AssistantTurnRepo",
    "AuditRepo",
    "DemoSessionRepo",
    "KGRepo",
    "LlmCallRepo",
    "LlmEvalRepo",
    "PipelineRepo",
    "PlanRefinementSessionRepo",
    "PlanRefinementTurnRepo",
    "PlanRepo",
    "PolicyViolationRepo",
    "ProfileAuditRepo",
    "ProfileRepo",
    "SystemHealthRepo",
    "apply_migrations",
    "build_dsn",
    "connect",
    "drop_all",
    "session_scope",
]
