-- ============================================================
-- 0006 — llm_evals
--
-- Inline structural evals run after a pipeline phase produces
-- its artefact (research → profile, plan → DemoPlan). Each row
-- captures one boolean/scalar check — e.g. "profile has ≥3
-- citation sources", "plan KG has ≥5 nodes", "plan JSON validates
-- against schema". Multiple eval rows per (pipeline_id, phase).
--
-- The same eval names + results are also emitted as OTel span
-- events (`clarion.eval` on the gen_ai parent span) so they show
-- up in Tempo alongside the call that produced them. The DB
-- copy is here for the SE-facing dashboard query "which phases
-- regressed since last week".
--
-- Lifecycle: append-only. Re-running a phase appends a new set
-- of rows; the old ones stay so we can see eval drift over time.
-- ============================================================

CREATE TABLE IF NOT EXISTS llm_evals (
    eval_id          BIGSERIAL PRIMARY KEY,
    pipeline_id      TEXT REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    phase            TEXT NOT NULL,
    eval_name        TEXT NOT NULL,
    score            NUMERIC(10, 4),
    passed           BOOLEAN NOT NULL,
    model            TEXT,
    prompt_version   TEXT,
    details          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT llm_evals_phase_chk CHECK (
        phase IN ('research', 'plan', 'approve', 'generate', 'provision', 'kg-publish')
    )
);

CREATE INDEX IF NOT EXISTS idx_llm_evals_pipeline
    ON llm_evals (pipeline_id, created_at DESC)
    WHERE pipeline_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_evals_phase_name
    ON llm_evals (phase, eval_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_evals_created
    ON llm_evals (created_at DESC);
