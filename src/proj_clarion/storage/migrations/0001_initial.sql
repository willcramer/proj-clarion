-- 0001_initial.sql — v0.2 storage schema
-- Idempotent: every CREATE uses IF NOT EXISTS.
-- Migration runner records this filename in _migrations once applied.

-- ============================================================
-- company_profiles — output of the Research agent
-- ============================================================
CREATE TABLE IF NOT EXISTS company_profiles (
    profile_id   TEXT PRIMARY KEY,
    source_url   TEXT NOT NULL,
    profile_json JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_company_profiles_created_at
    ON company_profiles (created_at DESC);


-- ============================================================
-- demo_plans — output of the Plan agent. JSON blob is the source of
-- truth for the plan; kg_nodes/kg_edges below are denormalised for query.
-- ============================================================
CREATE TABLE IF NOT EXISTS demo_plans (
    plan_id            UUID PRIMARY KEY,
    source_profile_id  TEXT NOT NULL REFERENCES company_profiles(profile_id) ON DELETE CASCADE,
    plan_json          JSONB NOT NULL,
    review_state       TEXT NOT NULL DEFAULT 'draft',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT demo_plans_review_state_chk CHECK (review_state IN (
        'draft', 'se_reviewed', 'approved_for_provision', 'provisioned', 'torn_down'
    ))
);

CREATE INDEX IF NOT EXISTS idx_demo_plans_source_profile
    ON demo_plans (source_profile_id);
CREATE INDEX IF NOT EXISTS idx_demo_plans_review_state
    ON demo_plans (review_state, updated_at DESC);


-- ============================================================
-- kg_nodes — flattened nodes from the plan's KnowledgeGraph,
-- one row per node, scoped to a plan_id.
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_nodes (
    plan_id             UUID NOT NULL REFERENCES demo_plans(plan_id) ON DELETE CASCADE,
    node_id             TEXT NOT NULL,
    node_type           TEXT NOT NULL,
    subtype             TEXT,
    label               TEXT NOT NULL,
    attributes          JSONB NOT NULL DEFAULT '{}'::jsonb,
    live_state_binding  JSONB,
    PRIMARY KEY (plan_id, node_id),
    CONSTRAINT kg_nodes_node_type_chk CHECK (node_type IN (
        'business_entity', 'technical_resource', 'agentic_resource'
    ))
);

CREATE INDEX IF NOT EXISTS idx_kg_nodes_type
    ON kg_nodes (plan_id, node_type);


-- ============================================================
-- kg_edges — directed edges between nodes within the same plan.
-- ============================================================
CREATE TABLE IF NOT EXISTS kg_edges (
    plan_id        UUID NOT NULL REFERENCES demo_plans(plan_id) ON DELETE CASCADE,
    edge_id        TEXT NOT NULL,
    edge_type      TEXT NOT NULL,
    from_node_id   TEXT NOT NULL,
    to_node_id     TEXT NOT NULL,
    attributes     JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (plan_id, edge_id),
    FOREIGN KEY (plan_id, from_node_id) REFERENCES kg_nodes(plan_id, node_id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id, to_node_id)   REFERENCES kg_nodes(plan_id, node_id) ON DELETE CASCADE,
    CONSTRAINT kg_edges_edge_type_chk CHECK (edge_type IN (
        'runs_on', 'depends_on', 'integrates_with', 'serves', 'contains'
    ))
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_from
    ON kg_edges (plan_id, from_node_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_to
    ON kg_edges (plan_id, to_node_id);


-- ============================================================
-- business_events — generated demo telemetry (table only in v0.2;
-- generator lands in v0.3).
-- ============================================================
CREATE TABLE IF NOT EXISTS business_events (
    event_id             BIGSERIAL PRIMARY KEY,
    plan_id              UUID NOT NULL REFERENCES demo_plans(plan_id) ON DELETE CASCADE,
    ts                   TIMESTAMPTZ NOT NULL,
    event_type           TEXT NOT NULL,
    business_entity_ids  TEXT[] NOT NULL DEFAULT '{}',
    payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id             TEXT
);

CREATE INDEX IF NOT EXISTS idx_business_events_plan
    ON business_events (plan_id);
CREATE INDEX IF NOT EXISTS idx_business_events_plan_ts
    ON business_events (plan_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_business_events_event_type
    ON business_events (plan_id, event_type);


-- ============================================================
-- plan_audit_log — review/approval trail. Append-only.
-- ============================================================
CREATE TABLE IF NOT EXISTS plan_audit_log (
    audit_id        BIGSERIAL PRIMARY KEY,
    plan_id         UUID NOT NULL REFERENCES demo_plans(plan_id) ON DELETE CASCADE,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    from_state      TEXT,
    to_state        TEXT,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_audit_log_plan
    ON plan_audit_log (plan_id, created_at DESC);


-- ============================================================
-- updated_at trigger on demo_plans
-- ============================================================
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS demo_plans_touch_updated_at ON demo_plans;
CREATE TRIGGER demo_plans_touch_updated_at
    BEFORE UPDATE ON demo_plans
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();
