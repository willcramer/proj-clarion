/**
 * /docs/ai-obs — concise how-to for instrumenting a Python Claude SDK app
 * with Grafana Cloud AI Observability the same way Proj Clarion does it.
 *
 * Each step is a short paragraph + one code block. No long prose; every
 * snippet should be copy-pasteable and runnable.
 */
import { Boxes, Database, Eye, Activity, Rocket } from "lucide-react";

import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";

export function DocsPage() {
  return (
    <div className="space-y-6">
      <Header />
      <PrereqsCard />
      <Step1Otel />
      <Step2Resource />
      <Step3Wrapper />
      <Step4Phase />
      <Step5Persist />
      <Step6Guardrails />
      <Step7Verify />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────

function Header() {
  return (
    <div>
      <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
        Docs · AI Observability
      </div>
      <h1 className="mt-1 text-[32px] font-medium tracking-tight leading-tight">
        Instrument your Claude app for{" "}
        <span className="h1-display">Grafana Cloud AI Obs</span>.
      </h1>
      <p className="mt-3 text-[var(--color-text-muted)] text-[15px] leading-relaxed max-w-3xl">
        Seven steps. Every Anthropic call ends up as a <code className="font-mono text-[13px]">gen_ai.chat</code>{" "}
        span in Tempo with cost, cache savings, TTFT, and pipeline context.
        Auto-detected guardrail trips (cost spikes, runaway output, prompt
        injection) attach as span events and write to a postgres audit
        table. External-dependency health is heartbeated every 60s.
        The same wrapper instruments both the six-phase build pipeline and
        the agentic Clarion Assistant: a chat turn is an{" "}
        <code className="font-mono text-[13px]">assistant.conversation</code>{" "}
        span, the LLM rounds nest as <code className="font-mono text-[13px]">gen_ai.chat</code>,
        and every tool runs under an{" "}
        <code className="font-mono text-[13px]">execute_tool</code> span — so a
        chat session reads just like a build trace.
        This is exactly the pattern Proj Clarion uses — see{" "}
        <code className="font-mono text-[13px]">src/proj_clarion/observability/</code>{" "}
        ({" "}<code className="font-mono text-[12px]">llm_client.py</code>,{" "}
        <code className="font-mono text-[12px]">policy.py</code>,{" "}
        <code className="font-mono text-[12px]">tools.py</code>,{" "}
        <code className="font-mono text-[12px]">health.py</code>{" "}) for the
        reference implementation.
      </p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────

function PrereqsCard() {
  return (
    <Card className="p-6">
      <SectionHeader icon={Boxes} title="Prereqs" />
      <ul className="mt-3 space-y-1 text-sm text-[var(--color-text-muted)] leading-relaxed">
        <li>• A Grafana Cloud stack with OTLP endpoint + API token.</li>
        <li>• Python ≥ 3.11 and the Anthropic SDK installed.</li>
        <li>• ~30 minutes — most of it is setting env vars correctly.</li>
      </ul>
      <CodeBlock language="bash">
{`pip install \\
  anthropic \\
  opentelemetry-sdk \\
  opentelemetry-exporter-otlp-proto-http \\
  openlit`}
      </CodeBlock>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────

function Step1Otel() {
  return (
    <StepCard
      num="01"
      icon={Activity}
      title="Initialise OpenTelemetry once at startup"
      lede="One TracerProvider, one MeterProvider, OTLP/HTTP to your Grafana Cloud OTLP gateway. Call this once before any Anthropic client is created."
    >
      <CodeBlock language="python">
{`# observability.py
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
import openlit

def init_telemetry(resource):
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter()  # reads OTEL_EXPORTER_OTLP_ENDPOINT
    ))
    trace.set_tracer_provider(tp)

    mp = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    )
    metrics.set_meter_provider(mp)

    # Auto-instruments Anthropic / httpx / requests on top of our providers.
    openlit.init(application_name="my-app")`}
      </CodeBlock>
      <EnvHint />
    </StepCard>
  );
}

function EnvHint() {
  return (
    <CodeBlock language="bash">
{`# Env vars OTLP exporter reads
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-...grafana.net/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64 of instanceID:token>"
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`}
    </CodeBlock>
  );
}

// ──────────────────────────────────────────────────────────────────

function Step2Resource() {
  return (
    <StepCard
      num="02"
      icon={Eye}
      title="Set the deployment.environment Resource attribute"
      lede="This is what lets you filter prod vs. dev in Tempo without dropping spans. Read it from an env var so promotion is a one-line config change."
    >
      <CodeBlock language="python">
{`# observability.py (continued)
import os
from opentelemetry.sdk.resources import Resource

def build_resource() -> Resource:
    return Resource.create({
        "service.name":           "my-app",
        "service.namespace":      "my-org",
        "service.version":        "0.1.0",
        # Clarion-style: default to 'dev' until promoted.
        "deployment.environment": os.getenv("APP_ENVIRONMENT", "dev"),
    })

# At startup
init_telemetry(build_resource())`}
      </CodeBlock>
      <Tip>
        TraceQL: <code className="font-mono">{`{ resource.deployment.environment = "prod" && span.gen_ai.system = "anthropic" }`}</code>
      </Tip>
    </StepCard>
  );
}

// ──────────────────────────────────────────────────────────────────

function Step3Wrapper() {
  return (
    <StepCard
      num="03"
      icon={Rocket}
      title="Wrap Anthropic in ONE place — streaming + caching + Gen AI semantic conventions"
      lede="Every call site routes through this function. The wrapper drives `messages.stream()` even when callers don't need streaming — it's the only way to capture TTFT cheaply. `cache_control={type: ephemeral}` is opt-in per request and reads at 10% of input cost on the second hit within ~5 min."
    >
      <CodeBlock language="python">
{`# llm_client.py
import time
from opentelemetry import trace
from anthropic import Anthropic

_tracer = trace.get_tracer("my-app.llm")

MODEL_PRICES = {  # USD per token; update from anthropic.com/pricing
    "claude-opus-4-7":  {"input": 15e-6, "output": 75e-6, "cache_read": 1.5e-6, "cache_write": 18.75e-6},
    "claude-haiku-4-5": {"input":  1e-6, "output":  5e-6, "cache_read": 0.1e-6, "cache_write":  1.25e-6},
}

def call_anthropic(client: Anthropic, request: dict):
    model = request["model"]
    with _tracer.start_as_current_span(f"gen_ai.chat {model}") as span:
        # Gen AI semantic-convention attrs
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.request.streaming", True)
        span.set_attribute("gen_ai.request.max_tokens", request.get("max_tokens", 0))

        # Stream internally so we can capture TTFT on every call —
        # callers see the same reassembled response shape.
        started = time.monotonic()
        ttft_ms = None
        with client.messages.stream(**request) as stream:
            for chunk in stream.text_stream:
                if ttft_ms is None and chunk:
                    ttft_ms = int((time.monotonic() - started) * 1000)
                    span.set_attribute("gen_ai.ttft_ms", ttft_ms)
            response = stream.get_final_message()

        usage = response.usage
        span.set_attribute("gen_ai.usage.input_tokens",  usage.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
        if usage.cache_read_input_tokens:
            span.set_attribute("gen_ai.usage.cached_input_tokens", usage.cache_read_input_tokens)
        span.set_attribute("gen_ai.response.finish_reason", response.stop_reason)

        # Cost — own attr, joinable on the trace ID
        p = MODEL_PRICES.get(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        cost = (usage.input_tokens  * p["input"]
              + usage.output_tokens * p["output"]
              + (usage.cache_read_input_tokens   or 0) * p["cache_read"]
              + (usage.cache_creation_input_tokens or 0) * p["cache_write"])
        span.set_attribute("app.llm.cost_usd", round(cost, 6))
        # Cache savings vs paying full input price for cached tokens.
        savings = (usage.cache_read_input_tokens or 0) * (p["input"] - p["cache_read"])
        if savings > 0:
            span.set_attribute("app.llm.cache_savings_usd", round(savings, 6))

        return response`}
      </CodeBlock>
      <CodeBlock language="python">
{`# Caller — wrap the system prompt in a cache_control content block when
# it's >= 1024 tokens and repeats across calls (e.g. a JSON schema dump,
# an archetype catalog, a long instruction preamble).
response = call_anthropic(client, {
    "model": "claude-opus-4-7",
    "max_tokens": 8192,
    "system": [
        {"type": "text", "text": "You are a planning agent. ..."},
        {
            "type": "text",
            "text": SCHEMA_DUMP + ARCHETYPE_CATALOG,  # the big static block
            "cache_control": {"type": "ephemeral"},
        },
    ],
    "messages": [{"role": "user", "content": user_prompt}],
})`}
      </CodeBlock>
      <Tip>
        <strong>Never put prompt or completion text into span attributes.</strong>{" "}
        Use Sigil or your own redacted store for that — span attrs are
        cheap to query but expensive to redact later.
      </Tip>
    </StepCard>
  );
}

// ──────────────────────────────────────────────────────────────────

function Step4Phase() {
  return (
    <StepCard
      num="04"
      icon={Boxes}
      title="Propagate pipeline / phase context across subprocesses"
      lede="If your agent runs as multiple processes (CLI subprocesses, worker queues), put the phase name + run id in an env var. The wrapper reads it into a ContextVar at import time and stamps it on every span."
    >
      <CodeBlock language="python">
{`# llm_client.py (continued)
import contextvars, os

_phase  = contextvars.ContextVar("app_phase", default=os.getenv("APP_PHASE", ""))
_run_id = contextvars.ContextVar("app_run_id", default=os.getenv("APP_RUN_ID", ""))

# Inside call_anthropic's span:
if _phase.get():  span.set_attribute("app.phase",  _phase.get())
if _run_id.get(): span.set_attribute("app.run.id", _run_id.get())`}
      </CodeBlock>
      <CodeBlock language="python">
{`# Orchestrator — when you spawn a phase, inject env vars
import os, asyncio

async def spawn_phase(name: str, run_id: str, argv: list[str]):
    env = os.environ.copy()
    env["APP_PHASE"]  = name        # picked up by ContextVar default in subprocess
    env["APP_RUN_ID"] = run_id
    return await asyncio.create_subprocess_exec(*argv, env=env)`}
      </CodeBlock>
    </StepCard>
  );
}

// ──────────────────────────────────────────────────────────────────

function Step5Persist() {
  return (
    <StepCard
      num="05"
      icon={Database}
      title="Persist per-call cost + structural evals to a DB"
      lede="Spans tell you what happened live; the DB lets you answer 'what did last week cost' and 'did this prompt regress'. Keep the table narrow — one row per call, one row per eval."
    >
      <CodeBlock language="sql">
{`CREATE TABLE llm_calls (
    call_id            TEXT PRIMARY KEY,
    run_id             TEXT,
    phase              TEXT,
    prompt_template    TEXT,
    model              TEXT NOT NULL,
    input_tokens       INT NOT NULL DEFAULT 0,
    output_tokens      INT NOT NULL DEFAULT 0,
    cache_read_tokens  INT NOT NULL DEFAULT 0,
    cache_write_tokens INT NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(12,6) NOT NULL DEFAULT 0,
    cache_savings_usd  NUMERIC(12,6) NOT NULL DEFAULT 0,
    ttft_ms            INT,
    is_stream          BOOLEAN NOT NULL DEFAULT FALSE,
    error_type         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);`}
      </CodeBlock>
      <CodeBlock language="python">
{`# After the Anthropic call returns in call_anthropic():
db.execute("""
    INSERT INTO llm_calls (call_id, run_id, phase, prompt_template,
                           model, input_tokens, output_tokens,
                           cache_read_tokens, cost_usd, ttft_ms)
    VALUES (%(call_id)s, %(run_id)s, %(phase)s, %(prompt_template)s,
            %(model)s, %(in)s, %(out)s, %(cache)s, %(cost)s, %(ttft)s)
""", {...})

# Optional: structural evals after a phase produces its artefact.
def eval_research(profile, run_id):
    db.execute("INSERT INTO llm_evals (...) VALUES (...)", {
        "phase": "research",
        "eval_name": "source_count_ge_3",
        "passed":  len(profile.sources) >= 3,
        "score":   float(len(profile.sources)),
    })
    # Also emit as a span event so it joins the gen_ai trace
    trace.get_current_span().add_event("app.eval", attributes={
        "eval.name": "source_count_ge_3",
        "eval.passed": len(profile.sources) >= 3,
    })`}
      </CodeBlock>
    </StepCard>
  );
}

// ──────────────────────────────────────────────────────────────────

function Step6Guardrails() {
  return (
    <StepCard
      num="06"
      icon={Activity}
      title="Auto-detect guardrail trips, audit tool calls, and heartbeat your deps"
      lede="Three companion tables turn the spans into a regulated-customer audit story. Detectors fire from inside the wrapper (no call-site changes), the tool-call manager wraps any external system you reach for, and a 60s asyncio task probes every dependency."
    >
      <CodeBlock language="sql">
{`-- Companion tables (FK to llm_calls / your run table)
CREATE TABLE agent_policy_violations (
    violation_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          TEXT,
    llm_call_id     TEXT REFERENCES llm_calls(call_id) ON DELETE SET NULL,
    agent_name      TEXT NOT NULL,
    violation_type  TEXT NOT NULL,  -- cost_spike · output_too_long · prompt_injection · unexpected_tool · high_attempt_count
    severity        TEXT NOT NULL,  -- low · medium · high · critical
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agent_tool_calls (
    call_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          TEXT,
    llm_call_id     TEXT REFERENCES llm_calls(call_id) ON DELETE SET NULL,
    agent_name      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,  -- web_search · db_read · kg_write · api_call · …
    target_system   TEXT,           -- postgres · serper_api · grafana_cloud_api · …
    action          TEXT,
    input_summary   TEXT,           -- first 500 chars, NO PII
    output_summary  TEXT,
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg       TEXT,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE system_health (
    id            BIGSERIAL PRIMARY KEY,
    service_name  TEXT NOT NULL,
    status        TEXT NOT NULL,  -- healthy · degraded · down
    latency_ms    INTEGER,
    error_msg     TEXT,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);`}
      </CodeBlock>
      <CodeBlock language="python">
{`# policy.py — hook into the wrapper's post-call path; no call-site changes
THRESHOLDS = {"cost_spike_usd": 0.50, "output_token_limit": 8000, "max_attempts": 3}

def check_llm_call_anomalies(*, run_id, llm_call_id, agent_name,
                             cost_usd, output_tokens, attempt):
    for vtype, sev, hit, details in [
        ("cost_spike",         "medium", cost_usd > THRESHOLDS["cost_spike_usd"],
         {"cost_usd": float(cost_usd)}),
        ("output_too_long",    "medium", output_tokens > THRESHOLDS["output_token_limit"],
         {"output_tokens": int(output_tokens)}),
        ("high_attempt_count", "low",    attempt > THRESHOLDS["max_attempts"],
         {"attempt": int(attempt)}),
    ]:
        if hit:
            _persist_violation(run_id, llm_call_id, agent_name, vtype, sev, details)
            trace.get_current_span().add_event("policy_violation", {
                "violation.type": vtype, "violation.severity": sev,
            })

# Call this at the bottom of call_anthropic() after you record the row.`}
      </CodeBlock>
      <CodeBlock language="python">
{`# tools.py — wraps any external call. Emits a span named "execute_tool"
# with Gen AI semantic-convention attrs the Grafana Cloud AI-Obs **Tools**
# view filters on (gen_ai.tool.name / gen_ai.agent.name / gen_ai.provider.name).
from contextlib import contextmanager

@contextmanager
def track_tool_call(*, agent_name, tool_name, provider_name="internal",
                    target_system=None, action=None, input_summary=None,
                    run_id=None, llm_call_id=None):
    started = time.monotonic()
    out = {}
    success, err = True, None
    with _tracer.start_as_current_span("execute_tool") as span:
        # These three attrs are what the AI-Obs Tools page reads:
        span.set_attribute("gen_ai.tool.name", tool_name)
        span.set_attribute("gen_ai.agent.name", agent_name)
        span.set_attribute("gen_ai.provider.name", provider_name)
        if target_system:
            span.set_attribute("app.tool.target_system", target_system)
        try:
            yield out
        except Exception as exc:
            success = False; err = str(exc); raise
        finally:
            dur = int((time.monotonic() - started) * 1000)
            span.set_attribute("app.tool.duration_ms", dur)
            span.set_attribute("app.tool.success", success)
            _persist_tool_call(run_id, llm_call_id, agent_name, tool_name,
                               target_system, action, input_summary,
                               out.get("output"), success, err, dur)

# Usage at a real call site — pick a stable tool name and provider name;
# they become rows on the AI-Obs Tools page.
with track_tool_call(agent_name="research_agent", tool_name="web_fetch",
                    provider_name="http", target_system="data.sec.gov",
                    action="GET", input_summary=url) as r:
    text = await fetch_one(url)
    r["output"] = f"{len(text)} chars"`}
      </CodeBlock>
      <Tip>
        The span <strong>name</strong> must be <code className="font-mono text-[12px]">execute_tool</code> and the three <code className="font-mono text-[12px]">gen_ai.*</code> attrs must be present — that's the exact contract the Grafana AI-Obs Tools view reads. Anything else won't populate the page.
      </Tip>
      <CodeBlock language="python">
{`# health.py — async heartbeat loop spawned from FastAPI lifespan
import asyncio, time, httpx

PROBES = {
    "postgres":      lambda: _probe_db(),
    "anthropic":     lambda: _probe_http("https://status.anthropic.com/api/v2/status.json"),
    "grafana_cloud": lambda: _probe_http("https://status.grafana.com/api/v2/status.json"),
}

async def heartbeat_loop():
    while True:
        await asyncio.to_thread(_tick)
        await asyncio.sleep(60)

def _tick():
    for svc, probe in PROBES.items():
        status, latency, err = "healthy", None, None
        try:
            latency = probe()
            if latency > 5000: status = "degraded"
        except Exception as exc:
            status = "down"; err = str(exc)
        db.execute("INSERT INTO system_health (service_name, status, latency_ms, error_msg) "
                   "VALUES (%s, %s, %s, %s)", (svc, status, latency, err))

def _probe_http(url):
    t = time.monotonic()
    r = httpx.get(url, timeout=5, follow_redirects=True)  # follow_redirects matters; status pages move
    r.raise_for_status()
    return int((time.monotonic() - t) * 1000)`}
      </CodeBlock>
      <Tip>
        Grafana panels: <strong>Open violations (severity)</strong> →{" "}
        <code className="font-mono text-[12px]">{`SELECT severity, COUNT(*) FROM agent_policy_violations WHERE resolved = FALSE GROUP BY 1`}</code>.{" "}
        <strong>Service uptime %</strong> →{" "}
        <code className="font-mono text-[12px]">{`SELECT service_name, AVG(CASE WHEN status='healthy' THEN 1.0 ELSE 0 END) * 100 FROM system_health WHERE checked_at > NOW() - INTERVAL '24 hours' GROUP BY 1`}</code>.
      </Tip>
    </StepCard>
  );
}

function Step7Verify() {
  return (
    <StepCard
      num="07"
      icon={Eye}
      title="Verify in Grafana Cloud"
      lede="Run one call, then check Tempo + your DB. If both have a row, you're done."
    >
      <CodeBlock language="traceql">
{`# 1. Spans exist with all new attrs (streaming → ttft + cache_savings)
{ resource.service.name = "my-app" && span.gen_ai.system = "anthropic" }

# 2. TTFT is populated on every call (streaming-by-default)
{ span.gen_ai.ttft_ms > 0 }

# 3. Cost + cache savings on opus calls
{ span.app.llm.cost_usd > 0.10 }
{ span.app.llm.cache_savings_usd > 0 }

# 4. Filter to one run
{ span.app.run.id = "<your-run-id>" }

# 5. Surfacing the policy_violation span event (Tempo lists events alongside spans)
{ name =~ "gen_ai.chat .*" } | select(event="policy_violation")

# 6. Tool calls populate the AI-Obs Tools view (span name == execute_tool)
{ name = "execute_tool" && span.gen_ai.tool.name != "" }

# 6a. One tool's latency by provider (web_fetch, edgar_fetch, …)
{ name = "execute_tool" && span.gen_ai.tool.name = "web_fetch" } | rate() by (span.gen_ai.provider.name)`}
      </CodeBlock>
      <CodeBlock language="sql">
{`-- DB-side sanity, one query per audit table
SELECT phase, COUNT(*), ROUND(SUM(cost_usd)::numeric, 4) AS usd,
       ROUND(SUM(cache_savings_usd)::numeric, 4) AS saved,
       ROUND(AVG(ttft_ms)::numeric, 0) AS avg_ttft_ms
FROM llm_calls WHERE run_id = '<your-run-id>' GROUP BY 1;

SELECT violation_type, severity, COUNT(*) FROM agent_policy_violations
WHERE run_id = '<your-run-id>' GROUP BY 1, 2 ORDER BY 2 DESC, 1;

SELECT DISTINCT ON (service_name) service_name, status, latency_ms, checked_at
FROM system_health ORDER BY service_name, checked_at DESC;`}
      </CodeBlock>
      <Tip>
        Build a Cost-per-run dashboard panel with a single Postgres datasource query;
        layer in TraceQL panels for span counts + p95 latency by phase.
        The <strong>cache-hit-rate</strong> panel becomes a Postgres query too:{" "}
        <code className="font-mono text-[12px]">{`SELECT 100.0 * SUM(cache_read_tokens) / NULLIF(SUM(input_tokens + cache_read_tokens), 0) AS hit_pct FROM llm_calls WHERE created_at > NOW() - INTERVAL '7 days'`}</code>.
      </Tip>
    </StepCard>
  );
}

// ──────────────────────────────────────────────────────────────────
// Shared primitives
// ──────────────────────────────────────────────────────────────────

function StepCard({
  num, icon: Icon, title, lede, children,
}: {
  num: string; icon: typeof Boxes; title: string;
  lede: string; children: React.ReactNode;
}) {
  return (
    <Card className="p-6">
      <div className="flex items-start gap-4">
        <span className="font-mono text-2xl tabular-nums text-[var(--color-accent)] shrink-0 leading-none mt-1">
          {num}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Icon size={16} className="text-[var(--color-accent)]" />
            <h2 className="text-base font-medium tracking-tight m-0">{title}</h2>
          </div>
          <p className="text-xs text-[var(--color-text-muted)] mt-2 leading-relaxed max-w-3xl">
            {lede}
          </p>
        </div>
      </div>
      <div className="mt-4 space-y-3">{children}</div>
    </Card>
  );
}

function SectionHeader({
  icon: Icon, title,
}: { icon: typeof Boxes; title: string }) {
  return (
    <div className="flex items-center gap-2">
      <Icon size={16} className="text-[var(--color-accent)]" />
      <h2 className="text-base font-medium tracking-tight m-0">{title}</h2>
    </div>
  );
}

function CodeBlock({
  children, language,
}: { children: string; language: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)]">
        <span className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          {language}
        </span>
        <Badge tone="neutral">copy</Badge>
      </div>
      <pre className="p-3 overflow-x-auto text-[12px] leading-[1.55] font-mono text-[var(--color-text)]">
        <code>{children}</code>
      </pre>
    </div>
  );
}

function Tip({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-[var(--color-accent-border)] bg-[var(--color-accent-bg)]/40 px-3 py-2 text-xs text-[var(--color-text)] leading-relaxed">
      <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--color-accent)] mr-1.5">
        tip
      </span>
      {children}
    </div>
  );
}

