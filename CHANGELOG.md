# Changelog

## [0.6.5] — Pod → Node connection (built-in `Node HOSTS Pod` relation)

Per Grafana Assistant's diagnosis: the built-in KubeGraph already declares
`Node HOSTS Pod` (PROPERTY_MATCH on `Node.name == Pod.node`). The
relation never fired against our Custom Pod entity because Pod
observations didn't carry a `node` label — there was nothing to join on.

### Changed
- **`kg_publish/emitter.py`** — new `_attach_pod_to_node(kg)` helper run
  alongside `_attach_hierarchy` in `EntityEmitter.__init__`. For each
  Pod, picks a kubenode from its cluster via deterministic sorted
  round-robin (stable across re-runs of the same plan_id) and writes
  the assignment to `pod.attributes["assigned_node_id"]`.
  `_observation_attrs` then emits it as the `node` label on every Pod
  observation.
- **`infra/grafana/clarion-business-model.yaml`** — added `node` to
  Pod's group-by and `node: node` to Pod's labelValues so the entity
  carries `node` as a property the built-in PROPERTY_MATCH can read.
- **`docs/clarion-model.md`** — Node → Pod row updated; the "NOT
  possible" carryover from earlier is gone.
- **`tests/unit/test_emitter_model_alignment.py`** — new
  `test_pod_carries_node_for_built_in_node_hosts_pod_relation` locks
  in the invariant.

### Verified end-to-end against the maintainer's stack
After applying the updated model rule (`gcx kg model-rules create -f
infra/grafana/clarion-business-model.yaml`) and a 90-second smoke
with `--customer acme-retail-v2`:

```
Pod observations with `node` label:        ALL of ~105 pods (0 missing)
Pod-to-node distribution per cluster:
  cluster-store-na-2     2 nodes,  3 + 3 pods
  cluster-store-na-1      2 nodes,  2 + 2 pods
  cluster-london-flagship       3 nodes,  2 + 2 + 1 pods
  cluster-wip-emea              3 nodes,  1 + 1 + 1 pods
  cluster-prod                  5 nodes, 16+16+15+15+15 pods
Node entities with matching names:         15 (every Pod.node has a Node)
```

The 105 Node→Pod HOSTS edges should materialise in Cloud KG within
~5 minutes of the emitter starting.

### How to apply on a fresh demo
```sh
gcx kg model-rules create -f infra/grafana/clarion-business-model.yaml
just kg-publish <plan_id> --customer <fresh-slug>
```

The model push is idempotent — safe to re-apply.

### Pushback
- Pod assignment uses deterministic round-robin within a cluster, not
  K8s scheduling logic (anti-affinity, taint tolerance, resource fit).
  For demo purposes the spread is convincing enough; if a stakeholder
  starts asking "why is pod-X on node-Y", we'd need to model real
  scheduler behavior, which isn't worth the complexity for a demo
  generator.
- The `assigned_node_id` attribute we write onto Pod KG nodes is a
  side-effect of `EntityEmitter.__init__`. If the same KG is fed to a
  second EntityEmitter instance, the second instance reuses the
  existing assignments (idempotent — round-robin produces the same
  output for the same input). But if the KG is mutated between calls
  in an unexpected way, the assignment may be stale. Not a real
  scenario today; flag for v0.7.

## [0.6.4] — Scope-mismatch fix (the real reason clusters didn't connect to stores)

After v0.6.1 + v0.6.3 landed, the entity graph in Cloud KG still showed
`cluster-store-na-1` and `cluster-wip-emea` floating disconnected
from their Stores. Diagnosis showed the metric labels were correct,
but the **entity scope was different** between Store and KubeCluster
sources. Cloud KG only forms relations within a single scope, so
mismatched scope = no edges.

### Root cause

`clarion_entity_info` Store series carried:
```
asserts_env  = prod          (single)
asserts_site = demo;demo     ← DOUBLED
```

`kube_node_info` series carried:
```
asserts_env  = prod;prod     ← DOUBLED
asserts_site = demo;demo     ← DOUBLED
```

The doubling came from setting `asserts_env` / `asserts_site` in **two
places at once** — on the OTel Resource (via `clarion_resource()`) AND
as per-observation/per-log-record attributes:

| Source | Where |
|---|---|
| Resource | `observability/otlp.py::clarion_resource()` |
| Observation attrs | `kg_publish/emitter.py::_observation_attrs()` |
| Observation attrs | `kg_publish/red_emitter.py::_common_attrs()` |
| Log record attrs | `kg_publish/log_emitter.py::_loop()` |

Mimir's OTLP ingester translates Resource attr `asserts.env` (dot
notation) → Prom label `asserts_env`, and observation attr `asserts_env`
→ Prom label `asserts_env`. Same target label, two sources, **values get
merged with `;` separator** → `prod;prod`, `demo;demo`. Each scope
produced a different entity record, so Stores in scope `(prod, demo;demo)`
couldn't form RUNS_ON edges with KubeClusters in scope `(prod;prod, demo;demo)`.

### Fix
- **`kg_publish/emitter.py::_observation_attrs()`** — dropped the
  `asserts_env` / `asserts_site` keys. The Resource is the sole source.
- **`kg_publish/red_emitter.py::_common_attrs()`** — gutted to an empty
  dict with a comment. The original was justified by an out-of-date
  comment claiming OTLP→Prom doesn't copy non-target_info Resource
  attrs onto metric series; Cloud Mimir actually does.
- **`kg_publish/log_emitter.py`** — same removal from per-record extra
  dict.

### Verified end-to-end against the maintainer's stack
After a 90-second smoke with a fresh `--customer acme-retail-clean` slug:

```
$ gcx metrics query 'count by (asserts_env, asserts_site) \
    (clarion_entity_info{clarion_customer="acme-retail-clean"})'
  asserts_env=prod   asserts_site=demo   count=235   ✓ single
```

vs the previous `--customer acme-retail-smoke` data still in retention:

```
$ gcx kg scopes list  # entity scopes seen across the stack
  env: ['default','local','prod','prod;prod']      ← `prod;prod` from old runs
  site: ['demo','demo;demo','none']                ← `demo;demo` from old runs
```

Old multi-valued-scope entities will age out of Cloud KG's entity
processor over the next ~hours; new entities are landing in the
correct `(prod, demo)` scope.

### How to validate in your stack
The cleanest path is a fresh customer slug. Pre-fix entities scoped
to `(prod;prod, demo;demo)` will linger in Cloud KG until the entity
processor evicts them; new emissions to a different `--customer`
materialise as fresh entity records with no carry-over:

```sh
just kg-publish <plan_id> --customer acme-retail-v2
```

### Operational reminder
Cloud KG entity dashboards use a 5-minute live query window. Smoke
runs of <2 min produce series that age out before you can inspect
them. For demo prep:

```sh
just kg-publish <plan_id> --customer <fresh-slug>   # leaves emitter running
```

### Pushback
- **The "Resource attrs only" rule isn't strictly OTel-spec true** —
  some collectors do strip non-canonical Resource attrs from non-target_info
  metrics. Cloud Mimir's OTLP ingester happens to copy them, which is what
  this fix relies on. If a future Cloud-side relabel rule changes that
  behavior, custom-entity model queries that filter on
  `asserts_env != ""` will silently drop every series and entities
  vanish. If that happens, re-add `asserts_env`/`asserts_site` as
  observation attrs but switch the Resource to a different attribute
  name (e.g. `clarion.asserts_env`) so they don't collide.
- Old `(prod;prod, demo;demo)`-scoped entities will linger in your
  stack until Cloud KG's entity processor evicts them. There's no
  delete API exposed via gcx. Workaround: re-run with a fresh customer
  slug so new entities are in the right scope; old ones fade.

## [0.6.3] — Central-tier namespace propagation

the maintainer reviewed the entity graph in Grafana Cloud and called out two
disconnections: (a) some KubeClusters not connected to their Stores,
(b) 53 central services floating untethered. Diagnosis showed the data
shape was already correct on the Store side after v0.6.1, but the central
tier had a real bug:

- `expand.py` set `namespace_id` on every per-store edge service AND on
  every central pod, but **not on the original-plan central service nodes
  themselves**.
- Result: `red_emitter` built per-service Resources for the central tier
  with `service.namespace=""`, fell back to `"default"`, so `target_info`
  defined those Service entities with `namespace=default`.
- Pods derived from the same services correctly carried `namespace=commerce`
  (set by expand.py at pod-creation time).
- Mismatch: Pod→Service `BELONGS_TO` PROPERTY_MATCH joins on
  `[service, namespace]`. Pods in `commerce`, services in `default` — no
  edges. The 53 central services floated as an island disconnected from
  their own pods.

### Changed
- **`kg_publish/expand.py`** — after computing `service_to_ns` from the
  planner's namespace→service edges, write the resolved `namespace_id`
  (and `cluster_id`) back onto each central service node's `attributes`.
  Now `red_emitter._namespace_for(svc)` reads the right namespace and
  `target_info` carries `service.namespace=commerce` (or whatever the
  planner labeled).

### Verified end-to-end against the maintainer's stack
After a 35s smoke run of the AcmeRetail plan, query of `target_info`:

| Namespace                       | Service count | Notes |
|---------------------------------|---------------|-------|
| `commerce`                      | 53            | central tier — was 0 before this fix |
| `store-na-1-commerce`     |  3            | per-store edge |
| `london-flagship-commerce`      |  3            | per-store edge |
| `store-na-2-commerce`    |  3            | per-store edge |
| `wip-emea-commerce`             |  3            | per-store edge |
| `default`                       | 53            | stale series from pre-fix run; aging out of Mimir's retention window |
| `proj-clarion`                  |  1            | the emitter itself (its own service.namespace) |

The fix activates the Pod→Service `BELONGS_TO` relation for the central
tier (77 pods × 53 services, all in `commerce`), so the central island
is now self-connected.

### Architectural note (now in `docs/clarion-model.md`)
**Central services are not "hosted by" any single Store** — by design.
The v0.6 model declares Store→Service `HOSTS` only for services in the
store's per-store edge cluster (the 12 `pos-edge` / `inventory-cache` /
`customer-display` services). The 53 central services represent shared
SaaS-style services that all stores depend on (commerce-checkout,
fraud-screening, etc.). They form a separate connected component:

```
KubeCluster cluster-prod
  └─ CONTAINS → 77 central Pods
                  └─ BELONGS_TO → 53 central Services (in `commerce` ns)
```

If you want central services connected to stores in the visualization,
you have three choices, in decreasing severity:

1. Add a `Store → Service` METRICS-based relation to the model using a
   new `clarion_store_service_dependency` metric. Code change in
   `red_emitter.py` + model edit.
2. Eliminate the central cluster entirely — put every service into a
   per-store cluster. Loses the "shared service" architectural truth.
3. Accept that central is a separate component — that's the architectural
   reality of a SaaS-shared backend. ← currently shipping.

### Operational note for live demos
The 5-minute Mimir query window means the EntityEmitter (and red_emitter)
must be running continuously for entity dashboards to show data. Brief
smoke runs (`scripts/smoke_alloy.py --seconds 30`) push enough metric
samples to exercise the ingest path and validate label shape, but the
emitted series age out of any short query window once the process exits.
For a real demo: run `proj-clarion kg publish <plan_id>` and leave it
running.

### Pushback
- Stale property accumulation in Cloud KG: a Store that previously
  emitted with `cluster=cluster-prod` (pre-v0.6.1) and now emits with
  `cluster=cluster-store-na-2` will hold both values as
  multi-valued properties until the entity processor evicts the old
  one. Visually, this can show old (now-broken) RUNS_ON edges
  alongside the correct new ones for several minutes after a fix
  ships. Not a code bug; a property-retention artifact. Workaround:
  use a fresh `--customer` label for clean validation runs.
- `target_info` still doesn't carry `kube_cluster` (red_emitter's
  per-service Resource sets only `service.name` and `service.namespace`).
  Not needed by the current model, but if a future relation joins
  Service to KubeCluster directly, this needs filling in.

## [0.6.2] — Pre-flight ingest estimate + real OTLP probe

Closes the two `v0.6 candidate` carryovers from v0.5: the silent
misconfig in `check env`, and the silent rate-limit blackhole in
`live-tail run`. Both are now caught before they can ruin a demo.

### Added
- **`src/proj_clarion/livetail/preflight.py`** — pre-flight ingest estimate.
  - `estimate_livetail_rate(plan_id, batch_size, poll_interval_seconds, cursor_value)`
    samples `pg_column_size(payload)` over 100 rows, computes the resulting
    OTLP record bytes (payload + ~600 envelope bytes, measured empirically
    on the v0.5 smoke), and returns rows/sec, bytes/sec, drain time, and
    a tier-limit verdict.
  - Tier limit comes from `CLARION_LOKI_BYTES_PER_SEC` env var. Unset →
    informational only. Set → warns and (when over) suggests a `--batch`
    that targets 80% of the limit at the same `--interval`.
  - `format_estimate(est)` renders a Rich-friendly text block.
- **`proj-clarion live-tail run`** prints the estimate before starting
  the loop. If the estimate exceeds the tier limit, prompts for explicit
  confirmation. New flags: `--yes` (skip confirm), `--no-preflight`
  (skip the estimate entirely — useful for quick iteration in CI).
- **`proj-clarion check env --probe`** — sends one OTLP log record
  through the configured endpoint, force-flushes, and reports
  success/failure with the result code or HTTP error. Tags the record
  with `clarion.probe=true` so an SE can grep for it in Loki to confirm
  it actually landed (not just that the OTLP gateway accepted the bytes).
- **`.env.example`** documents `CLARION_LOKI_BYTES_PER_SEC`. Example
  value `873813` (free/dev tier).

### Tests
- 11 unit tests in `tests/unit/test_livetail_preflight.py` covering the
  rate math, tier-limit comparison, suggested-batch tuning, and the
  text rendering.
- 4 integration tests in `tests/integration/test_livetail_preflight.py`
  exercising the SQL probe against a testcontainers Postgres: empty plan,
  populated plan with 200 events, tier-limit env override, and cursor
  offset (backlog should count rows ABOVE cursor, not the table total).
- Total: 64 unit (+15) + 18 integration (+4) passing.

### Verified end-to-end against the maintainer's stack
- Pre-flight against the AcmeRetail plan:
  ```
  $ CLARION_LOKI_BYTES_PER_SEC=873813 \
      proj-clarion live-tail run 1a7a1fad --batch 5000 --interval 1
  backlog rows         31,593
  avg payload bytes    243  (sampled 100 rows)
  avg OTLP record      843 bytes  (payload + ~600 envelope)
  rate                 5000 rows/s  →  4.0 MB/s
  backlog drain        ~6s
  tier limit           853.3 KB/s  (estimate uses 482%)
  [!] estimated bytes/sec EXCEEDS tier limit. Loki will reject batches with HTTP 429.
      suggested:  --batch 829  (or --interval 6.0, or both)
  ```
  This is exactly the v0.5 smoke scenario; the warning fires loudly
  before the run starts, with concrete tuning suggestions.
- `check env --probe` against Cloud (Mode B): probe record landed in
  Loki (`{service_name="proj-clarion-check-env"} |= "probe"` returns it
  within ~5s of the run).
- Failure path: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:9999`
  returns `fail` with the connection-refused error in the detail
  column. So the probe distinguishes auth/ingest issues from transport
  failures cleanly.

### Pushback / things worth flagging
- **Estimate assumes drain mode** (rows/sec = batch / interval). For
  live tailing of an actively-growing event stream, the right rate is
  the event arrival rate — which we don't currently track. If
  generators ever start emitting events continuously into a running
  demo, the estimate will need a "live arrival rate" probe alongside
  the backlog count. Not a problem for v0.6; flag for v0.7.
- **Probe doesn't verify the record reached Loki**, only that the OTLP
  gateway returned 2xx. Cloud's gateway accepts the bytes and forwards
  asynchronously; a 200 here doesn't catch downstream Loki tier
  rejections. Adequate for the auth-misconfig case it's targeting; if
  this becomes an issue, follow up with a brief Loki query roundtrip
  in the same command.
- The OTLP exporter result enum is split across `LogExportResult`
  (public) and `LogRecordExportResult` (private internal); compared on
  `.name == "SUCCESS"` to decouple. If the SDK consolidates these in a
  future release, the probe still works without code change.

## [0.5.0] — Alloy + live-tail loop

### Added
- **`deploy/alloy/config.alloy`** — single Alloy config that receives OTLP
  metrics/logs/traces from local Python components on `0.0.0.0:4317` (gRPC)
  and `:4318` (HTTP) and forwards them to Grafana Cloud's OTLP gateway.
  - Pipeline: `otelcol.receiver.otlp` → `memory_limiter` (75% / 15% spike) →
    `resourcedetection` (env, system) → `attributes` (insert `asserts.env`,
    `asserts.site`, `alloy.routed=true` only when missing) → `batch` (8k size,
    5s timeout) → `otelcol.exporter.otlphttp` to `GRAFANA_CLOUD_OTLP_ENDPOINT`
    with `Authorization` from `GRAFANA_CLOUD_OTLP_AUTH`.
  - Resource attributes set by Python (Resource on the EntityEmitter,
    LiveTail logger, generator tracer) pass through unchanged.

- **`alloy` service in `deploy/docker/compose.yaml`** under the `cloud` profile,
  sibling to `pdc-agent`. `docker compose --profile cloud up` brings up
  Postgres + PDC + Alloy together. Ports 4317/4318 (OTLP) and 12345 (Alloy UI)
  exposed on host.

- **`src/proj_clarion/livetail/`** — Postgres `business_events` → OTLP logs
  - `cursor.py` — file-backed monotonic int cursor per `plan_id`, atomically
    persisted at `data/livetail/<plan_id>.cursor`. Refuses to go backwards;
    survives torn writes.
  - `emitter.py` — dedicated `LoggerProvider` + `OTLPLogExporter` →
    `OTEL_EXPORTER_OTLP_ENDPOINT/v1/logs`. One `LogRecord` per event row;
    body=`event_type`, attributes carry `clarion.{event_id, plan_id, event_type,
    business_entity_ids, payload, trace_id}`. trace_id is parsed and stamped on
    the LogRecord so Tempo↔Loki correlation works in Explore.
  - `runner.py` — `LiveTailer.run()`: poll `WHERE event_id > $cursor LIMIT $batch`
    on the BIGSERIAL primary key, emit, advance cursor, sleep `interval` only
    when there's no backlog. SIGINT/SIGTERM-safe; flushes the OTLP exporter
    on shutdown.

- **CLI**: `proj-clarion live-tail run <plan_id>` (with `--customer`,
  `--batch`, `--interval`, `--from-start`) and `proj-clarion live-tail status
  <plan_id>` (cursor + lag report). Just recipes: `just live-tail <id>`,
  `just live-tail-status <id>`.

- **Tests**
  - `tests/unit/test_livetail_cursor.py` — 6 unit tests: zero-init, persist
    across instances, refuses-backwards, reset, corrupt-file fallback,
    per-plan isolation.
  - `tests/integration/test_livetail.py` — 3 integration tests: order +
    cursor advancement, resume from persisted cursor, cross-plan isolation.

### Changed
- **`.env.example`** documents two routing modes:
  - **Mode A (default v0.5+)** — `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`
    points Python at Alloy; `GRAFANA_CLOUD_OTLP_ENDPOINT` + `GRAFANA_CLOUD_OTLP_AUTH`
    hold the Cloud creds Alloy uses.
  - **Mode B (legacy / Alloy-down)** — Python pushes direct to Cloud, same as
    v0.4.

### Verified
- Alloy 1.x boots clean against the config: component graph evaluates without
  errors; HTTP server listens on :12345; OTLP receivers bind :4317/:4318.
  `docker run grafana/alloy:latest fmt deploy/alloy/config.alloy` round-trips.
- Full unit suite: 30/30 passing (24 pre-existing + 6 new cursor).
- Live-tail integration suite: 3/3 passing under testcontainers Postgres 16.

### Carryover / known limits
- Manual Cloud round-trip not yet exercised end-to-end. The Python →
  Alloy → Cloud path is identical in shape to v0.4's direct push (same OTLP
  endpoint, same Basic auth header), so the risk surface is mostly the
  Alloy config itself, which boots cleanly.
- Live-tail polls (1s default) instead of using `LISTEN/NOTIFY`. Adequate
  for demo cadence; switch to NOTIFY if backlog growth becomes a real
  problem (would need a `business_events` insert trigger).
- Memory-limiter at 75% is the default reasonable shape. Under a 14-day
  AcmeRetail run (3.4M spans) this hasn't been load-tested; if Alloy starts
  dropping, drop the limiter to 60% and bump `send_batch_size`.
- `livetail.emitter` doesn't currently expose batch metrics back to the
  process. The OTel SDK's BatchLogRecordProcessor metrics are available
  via the meter provider but not surfaced in the CLI status output.

### Cleanup landed in v0.5 (post-initial-ship pass)
- **`src/proj_clarion/observability/otlp.py`** — single source of truth for
  Clarion OTel Resource construction and OTLP endpoint discovery. Exposes
  `clarion_resource(...)`, `otlp_{endpoint,logs,metrics,traces}_endpoint()`,
  `clarion_env()`, `clarion_site()`, `using_alloy_hop()`. Replaces three
  hand-rolled Resource + endpoint dicts in `observability/__init__.py`,
  `kg_publish/emitter.py`, and `livetail/emitter.py` (and pulls the
  asserts.* defaults out of `kg_publish/log_emitter.py`). 10 new unit
  tests in `tests/unit/test_observability_otlp.py`.

- **`proj-clarion check env`** — diagnostics command (also `just check-env`).
  Probes `OTEL_EXPORTER_OTLP_ENDPOINT`, reports Mode A vs Mode B, TCP-connects
  to the endpoint, and (in Mode A) hits Alloy's `/-/ready`. Warns when
  Mode A is selected but `GRAFANA_CLOUD_OTLP_AUTH` is unset (Alloy would
  receive but couldn't forward), and when Mode B is selected but
  `OTEL_EXPORTER_OTLP_HEADERS` is unset (Cloud would reject). Closes the
  silent-misconfig gap flagged in the original v0.5 carryover.

- **`scripts/smoke_alloy.py`** — one-shot smoke test. Bypasses the CLI's
  `load_dotenv(override=True)` so it can pin OTEL endpoint to the local
  Alloy without modifying the user's `.env`. Spins up `EntityEmitter` +
  `LiveTailer` against a chosen plan, runs for N seconds, prints the
  Cloud-side queries to verify with.

- **`LiveTailer` is now thread-safe.** `signal.signal()` can only run on
  the main thread; `run()` now installs SIGINT/SIGTERM handlers
  conditionally and exposes `stop()` so background-thread callers
  (the smoke script) can drive shutdown explicitly. Existing CLI
  behavior unchanged.

### Verified end-to-end against the maintainer's stack
- Smoke run against the AcmeRetail plan (`1a7a1fad…`): 90 seconds of Mode-A
  emission. Pushed through `clarion-alloy` → `otlp-gateway-prod-<region>`.
  - **Metrics confirmed in Cloud:** `count by (alloy_routed,clarion_entity_kind)
    (clarion_entity_info{clarion_customer="acme-retail-smoke"})` returns
    16 series, all stamped `alloy_routed="true"`, totaling 227 entities —
    exact match for the expanded AcmeRetail KG (4 stores, 4 channels, 8
    databases, 95 pods, 65 services, …). Confirms the
    `processor.attributes` step ran and the resource attributes survived
    the Mimir round-trip.
  - **Logs path validated to the Cloud edge:** Alloy successfully
    forwarded to `https://otlp-gateway-prod-<region>.grafana.net/otlp/v1/logs`.
    Loki returned HTTP 429 ResourceExhausted (limit 873,813 bytes/sec for
    user 1544637); Alloy retried with exponential backoff. Path is
    correctly wired; the failure mode is the user's Loki ingestion
    tier, not a config issue.
  - **Traces path:** not exercised by the smoke (the EntityEmitter and
    LiveTailer don't emit spans), but uses the same `otelcol.exporter.otlphttp`
    sink as metrics, so it's covered by parity.

### Pushback / things worth flagging
- **Live-tail volume can saturate Loki tiers quickly.** Dumping 31,593
  events through the live-tailer in 90 seconds (one full AcmeRetail day's
  events at ~1KB/event) blew past the user's 873KB/s rate limit and
  Alloy spent the rest of the run retrying. Real demos need either
  (a) a smaller `--days` value for the generator, (b) `--interval` /
  `--batch` tuning on `live-tail run`, or (c) a tier upgrade. Worth
  surfacing this in `proj-clarion live-tail run` as a pre-flight
  estimate ("this plan has N events × ~1KB ≈ Y KB/s expected; your
  tier is Z"). v0.6 candidate.
- `proj-clarion check env` doesn't yet do a real OTLP probe (it only
  does TCP connect + Alloy `/-/ready`). A 1-record OTLP POST would
  prove auth + ingest in one go but adds the cost of building a real
  exporter. Adequate signal for now; deepen if false negatives show up.

## [0.6.1] — KG model alignment (post-UI-edit pass)

the maintainer updated the live custom KG model rule
(`clarion-business-model-1a7a1fad`) in the Grafana Cloud UI. This release
brings the emitter back in sync with the new model so every relation it
declares actually fires.

### Added
- **`infra/grafana/clarion-business-model.yaml`** — canonical YAML for the
  custom KG model rule. The repo now owns the source of truth; UI edits
  must be reflected back here.
- **`docs/clarion-model.md`** — model overview, label-shape table, and the
  list of code paths that must stay in sync with the YAML.
- **`tests/unit/test_emitter_model_alignment.py`** — 13 invariant tests
  that lock in the per-entity-kind labels every PROPERTY_MATCH and
  METRICS relation depends on. Will fail loudly if a future
  `_observation_attrs` change drops a required label.

### Changed
- **`kg_publish/emitter.py`** — Store/FulfillmentCenter `clarion_entity_info`
  observations now fan out across the services in their per-store cluster,
  and carry a reliable `clarion_kube_cluster` value:
  - **New `_compute_store_cluster_map(kg)`** — for each Store/FC, looks up
    the per-store cluster (via `cluster.attributes.store_id == store.node_id`,
    the back-reference `expand.py` already creates) and the services in
    that cluster.
  - **New `_observations_for_store(node)`** — emits one Observation per
    (Store, service, namespace) tuple. Falls back to a single Observation
    when no per-store cluster is found, so the Store entity still exists
    in Mimir even if HOSTS won't fire for it.
  - `_emit_all` dispatches Stores/FCs to the fan-out path, everything else
    to the existing single-Observation path.

### Why this matters
The new v0.6 model declares two relations on Stores that didn't exist in
the v0.6.0 emitter shape:

  | Relation | Required label | Was emitted? |
  |---|---|---|
  | Store → KubeCluster (RUNS_ON) | `clarion_kube_cluster` | ❌ — pulled from `node.attributes.cluster_id`, which neither planner-original nor synthesised stores have |
  | Store → Service (HOSTS) | `service`, `namespace` | ❌ — Stores emitted one Observation with no service info |

Both are now emitted, both relations now fire.

### Verified end-to-end against the maintainer's stack
- Smoke run against the AcmeRetail plan (`1a7a1fad…`) through Alloy:
  - **12 Store series** in Mimir (4 stores × 3 edge services each), each
    carrying its own `(clarion_kube_cluster, service, namespace)` triple:
    ```
    store-na-6  cluster-store-na-2  pos-edge          store-na-2-commerce
    store-na-6  cluster-store-na-2  inventory-cache   …
    store-na-6  cluster-store-na-2  customer-display  …
    (× 4 stores)
    ```
  - **4 Store entities** materialised in Cloud KG with `customer="acme-retail-smoke"`.
- Full unit suite: 53/53 passing (40 pre-existing + 13 new model-alignment).
- Live-tail integration suite: 3/3 passing.

### Carryover / known limits
- **FulfillmentCenter HOSTS won't fire for planner-original FCs.** The
  AcmeRetail FC (`fc-us-manufacturing`) has no per-FC cluster — `expand.py`
  only builds clusters per Store, not per FC. The FC entity is still
  emitted (so Region→FC and Account→FC relations work), but it's a
  business-only node in the KG. To enable FC→Service, either: (a) extend
  `expand.py` to build per-FC edge clusters when an FC exists, or (b)
  surface a `clarion_kube_cluster` on the planner-original FC node.
  v0.7 candidate.
- **Cardinality:** 4 stores × 3 services + the rest ≈ 235 active
  series for the AcmeRetail plan. Trivial. Larger plans with more edge
  services per store will scale linearly.

## [0.6.0] — Knowledge Graph topology + vertical-aware planner prompts

(This work landed in calendar order before v0.5 but is logically the next
layer above provisioning, so it's numbered v0.6 to keep the version line
linear.)

### Added
- **`src/proj_clarion/kg_publish/`** — turn a DemoPlan's KG into Grafana
  Cloud Knowledge Graph entities + relations.
  - `expand.py` — `expand_with_synthetic_infra(plan)` augments the KG with
    derived technical entities (Pods per Service, Nodes per Cluster, a few
    VMs, Topics, LoadBalancers, Databases) so the KG visualization shows a
    believable tech tier alongside the business tier. None of these
    correspond to anything actually deployed.
  - `model_rules.py` — `build_model_rules(plan, kg=...)` emits the
    `model-rules.yaml` declaring custom business entity types (Region,
    Channel, Store, FulfillmentCenter, Kiosk, ...) and their relations.
    Built-in KG types (KubeCluster, Namespace, Service, Pod, Node) are
    referenced rather than redeclared.
  - `prom_rules.py` — `build_prom_rules(plan)` emits `prom-rules.yaml` with
    the recording rule materialising `clarion_entity_info` from `target_info`
    so the entity processor picks it up.
  - `emitter.py` — `EntityEmitter`: ONE MeterProvider, ONE
    `clarion_entity_info` observable gauge, N observations per cycle (one
    per KG entity). Single-Resource shape avoids the v0.4-prototype's bug
    where every emitted entity created a fake Service in Cloud KG's
    Service entity type.
  - `red_emitter.py` — RED metrics (rate / errors / duration) per service
    so each Service entity in Cloud KG has insight panels with data.
    Diurnal-pattern weighted (driven from `plan.data_blueprint`).
  - `log_emitter.py` — synthetic logs per service (separate provider,
    same Resource) so each entity's Logs tab in Explore has data.

- **CLI**: `proj-clarion kg preview <plan_id>`, `kg publish <plan_id>` (push
  YAMLs via `gcx kg model-rules create -f` and `gcx kg rules create -f`,
  then start the entity emitter), `kg verify <plan_id>` (queries Cloud KG
  via `gcx kg entities list -o json` and reports counts per entity type).
  Just recipes: `just kg-preview`, `just kg-publish`, `just kg-verify`.

- **Vertical-aware planner prompts** in `agents/planner.py` — the analyze
  phase now extracts a vertical-specific `peak_event` (e.g. "Black Friday
  surge", "open enrollment crunch") from the CompanyProfile and threads it
  through every downstream phase, so processes and incidents are framed
  around the customer's actual stress event rather than a generic one.

### Verified
- AcmeRetail plan KG-publish run: model-rules.yaml + prom-rules.yaml created;
  pushed via gcx; entity emitter ran for >1h emitting `clarion_entity_info`
  every 30s. `kg verify` returns ~80 entities across the 6 declared types.

### Carryover / known limits
- `EntityEmitter` reaches into private OTel SDK internals to control the
  per-Resource shape (single Resource for the emitter itself, not one per
  entity). Same private-API risk as `generator/telemetry.py`'s `Span._context`
  pattern — pin the OTel SDK version and watch for upstream changes.
- KG model rules are vertical-shaped but not vertical-customized. Every
  retail plan emits the same Region/Channel/Store/FulfillmentCenter type
  tree. A SaaS plan would need different types; the rule-builder accepts
  plan-derived subtypes but the heuristic for what counts as a
  business entity hasn't been generalized beyond retail. Worth retiring
  the heuristic in v0.7 once we have a second vertical to compare against.

## [0.4.0] — Provisioning to Grafana Cloud

### Added
- **`src/proj_clarion/provision/`** — turn DemoPlan's `dashboard_specs` and
  `alert_specs` into real Grafana folders, dashboards, and alert rules.
  - `dashboards.py` — `DashboardSpec → Grafana dashboard JSON`. Heuristic
    panel selection from `primary_panels` titles (revenue / error / latency
    / channel-mix / top-N / trace-explorer); plugin types (`grafana-postgresql-datasource`,
    `prometheus`, `loki`, `tempo`); per-stack datasource UIDs via env
    (`GRAFANA_DS_POSTGRES_UID`, etc.).
  - `alerts.py` — `AlertSpec → Grafana managed alert rule` (v1 provisioning
    API shape, with reduce + math expression nodes for the threshold).
  - `folders.py` — deterministic folder-uid-from-plan-id (`clarion-<32hex>`).
  - `assembler.py` — `build_assets(plan)` (pure: builds dashboards + rules
    in memory) and `push_assets(client, assets)` (folder POST → dashboards
    POST → alert rules PUT-if-exists/POST-if-new). `save_assets_to_disk()`
    writes the same JSON under `data/generated/<plan_id>/`.
  - `client.py` — `GrafanaClient` shells out to **`gcx`** (`gcx api PATH -X
    METHOD --agent`) instead of using a token directly. Sidesteps the
    `grafana-api:write` scope question entirely; gcx is OAuth-authenticated
    against the user's stack.

- **CLI**: `proj-clarion provision run <plan_id>` (default `--dry-run` writes
  JSON to disk; `--push` actually creates resources in Cloud) and
  `proj-clarion provision clear <plan_id>` (deletes the plan's folder +
  cascade). Just recipes: `just provision`, `just provision-clear`.

- **Tests** (`tests/unit/test_provision.py`)
  - 10 unit tests: predicate parser, folder-uid determinism, build_assets
    fanout, panel datasource refs, plan-id template, alert label round-trip,
    alert datasource UID match, on-disk file count, push-flow endpoint
    sequence, PUT-vs-POST for existing alert rules.

### Verified end-to-end against the maintainer's stack
Pushed AcmeRetail plan via `just provision 1a7a1fad --push`:
- Folder `Proj Clarion / 1a7a1fad` created
- 3 dashboards (Business Health, Technical Health, Pivot) landed
- 18 alert rules pushed; all `alrt-*` UIDs preserved; all reference the
  new `proj-clarion-local` Postgres datasource
- Datasource `proj-clarion-local` (uid `bfldsfhng9i4ge`) provisioned via
  `gcx api /api/datasources -X POST` with `pdcInjected: true`

### Carryover / known limits
- **Dashboards render "no data" until PDC is operational** for the new
  Postgres datasource. The datasource is configured with `pdcInjected: true`
  but no agent is running locally yet. PDC agent is stubbed in `compose.yaml`
  under the `cloud` profile; needs `GCLOUD_PDC_*` env vars to start.
- The Tempo / Prometheus default UIDs (`grafanacloud-traces`, `grafanacloud-prom`)
  match Cloud out-of-the-box names. If a future user has different UIDs,
  override via env vars.
- Alert push doesn't currently validate against Grafana's expression
  schema before sending; if a query is malformed, the rule lands but stays
  in `error` state. Acceptable for v0.4; structured-output validation is
  v0.7-flavored work.

### Pushback / things worth flagging
- Heuristic panel-title-to-query mapping is a stopgap; v0.6 should give the
  SE a real panel editor. For now the SE either accepts the inferred panel
  or hand-edits the JSON before push.
- Pushing 18 alert rules one-at-a-time isn't ideal; could batch via the
  alert rule group API. Not a bottleneck at current scale.

## [0.3.0] — Generator (events + traces)

### Added
- **Generator package** (`src/proj_clarion/generator/`)
  - `topology.py` — derives the service-call chain (`service_chain_for_step`)
    by walking `depends_on`/`integrates_with` edges from a step's
    `services_implementing`; also exposes `business_context_for_services`
    and a subtype-grouped lookup.
  - `diurnal.py` — five diurnal patterns (`retail_us`, `retail_global`,
    `saas_b2b`, `ecommerce_us`, `flat`) and three weekly patterns;
    composite weighting drives per-minute event rates.
  - `events.py` — `BusinessEvent` dataclass and `generate_events_for_plan()`
    which samples per-minute Poisson-shaped events across the historical
    window. Deterministic: RNG seeded from `plan_id`. `persist_events()`
    bulk-inserts via SQLAlchemy + JSONB.
  - `incident.py` — `apply_incident_script()` modifies the event stream
    inside each incident's window: `latency_spike` /
    `queue_back_pressure` / `dependency_unavailable` amplify
    `duration_ms`; `error_burst` lifts the error rate;
    `business_kpi_drop` / `throughput_drop` drop a fraction of events
    so the dip is visible in dashboards.
  - `telemetry.py` — emits one OTel trace per event with backdated
    timestamps (Tempo accepts past times). Root span = the business
    event; one child span per service in the call chain. Flushed in
    batches via the configured `BatchSpanProcessor`.

- **CLI**
  - `proj-clarion generate run <plan_id>` (with `--days N`,
    `--no-traces`, `--anchor-now`)
  - `proj-clarion generate clear <plan_id>` — wipe a plan's events
  - Just recipes: `just generate <id> [args]`, `just generate-clear <id>`

- **Tests** (`tests/integration/test_generator.py`)
  - Five integration tests covering: topology walks, deterministic
    generation, volume staying within ±15% of the daily target,
    incident-window latency amplification, Postgres round-trip with
    correct counts and per-event-type distribution.

### Verified end-to-end
AcmeRetail plan, `--days 1`: 31,593 events in Postgres in ~5 seconds (lower
than the 40k baseline because the `business_kpi_drop` incident drops
events by design). 200-event trace probe: 3 batched POSTs to Cloud
Tempo, all 200 OK. Verify in your stack with TraceQL
`{ resource.service.name = "proj-clarion" && clarion.plan_id = "<id>" }`.

### Carryover / known limits
- Telemetry emission relies on setting `Span._context` directly so
  pre-computed trace/span IDs survive the SDK's lifecycle. This is
  pragmatic but private-API; if a future OTel SDK release changes that
  attribute name, `telemetry.py` needs a one-line fix.
- Metrics and logs intentionally NOT emitted in v0.3 — Mimir's OTLP
  gateway rejects historical metric timestamps (>1h old), and Postgres
  is the source of truth for business KPIs anyway. Live metrics +
  Loki logs land in v0.5 alongside Alloy.
- Full AcmeRetail run (14 days × 40k/day = 560k events ≈ 3.4M spans)
  not exercised end-to-end yet — likely a 5–10 minute pass at non-
  trivial Cloud quota usage. Default to `--days 1` until you've
  budgeted the run.

## [0.2.0] — Plan agent + storage + SE review

### Added
- **Postgres storage layer** (`src/proj_clarion/storage/`)
  - `migrations/0001_initial.sql` — DDL for `company_profiles`, `demo_plans`,
    `kg_nodes`, `kg_edges`, `business_events`, `plan_audit_log` with
    appropriate indexes and a `touch_updated_at` trigger on `demo_plans`
  - `db.py` — SQLAlchemy 2.0 + psycopg engine factory; reads DSN from env
  - `migrator.py` — idempotent raw-SQL migration runner with `_migrations`
    tracking table (no Alembic in v0.2 by design)
  - `repositories.py` — `ProfileRepo`, `PlanRepo`, `KGRepo`, `AuditRepo`
    with intentionally narrow surfaces
  - `just db-init` (idempotent) and `just db-reset` (drops + reapplies)

- **Plan agent** (`src/proj_clarion/agents/planner.py`)
  - Six-phase pipeline: `analyze_profile` → `model_processes` → `build_kg` →
    `script_incident` → `propose_dashboards_and_alerts` →
    `propose_assistant_tools`
  - Each phase wrapped in an OTel span (`plan.<phase>`) for meta-observability
  - Output: a complete `DemoPlan` that validates against the schema and
    passes `KnowledgeGraph.validate_referential_integrity()`
  - Belt-and-braces sanitizers strip the most common LLM regressions before
    schema validation (e.g. `name` → `label`, `description` → `narrator_cue`)

- **SE review CLI** (`src/proj_clarion/cli/main.py`)
  - `proj-clarion plan run <profile_path>` — generate a plan and persist
  - `proj-clarion plan show <plan_id>` — one-screen rich-tree summary
    (plan_id prefix accepted), plus audit history
  - `proj-clarion plan approve <plan_id> --note "..."` — `draft →
    approved_for_provision`, requires justification, writes to audit log
  - `proj-clarion plan list` — recent plans
  - Just recipes: `just plan`, `just plan-show`, `just plan-approve`, `just plans`

- **Tests**
  - `tests/unit/test_planner.py` — full plan run with a fake Anthropic client
    (queue of canned per-phase JSON), 7 invariants asserted (schema validates,
    KG integrity, every referenced service in KG, every alert datasource valid,
    every incident target in KG, dashboard audiences cover business/technical/
    pivot, alert/process counts proportional)
  - `tests/integration/test_storage.py` — applies migrations and round-trips
    profiles + plans + KG + audit through repos
  - `tests/integration/test_plan_pipeline.py` — full pipeline: profile →
    planner (mocked LLM) → DB persist → DB read → re-validate
  - Integration tests gated behind `pytest -m integration` /
    `just test-integration`; spin up an ephemeral Postgres via testcontainers

- **Grafana Sigil instrumentation** (`src/proj_clarion/observability/sigil_helper.py`)
  - All Anthropic calls in `agents/planner.py` and `agents/research.py` route
    through `call_anthropic()`, which emits a normalized Sigil Generation
    record per call when `SIGIL_*` env vars are set
  - Multi-agent dependency DAG: `parent_generation_ids` chains the planner's
    six phases (e.g. `build_kg` lists `analyze_profile` and all
    `model_processes` generations as parents)
  - Falls back to a direct provider call when Sigil is not configured, so
    unit tests stay offline
  - `observability/__init__.py` now sets up explicit `TracerProvider` and
    `MeterProvider` (the Sigil SDK requires the app to own both) and
    auto-points `SSL_CERT_FILE` at certifi's bundle to fix macOS brew-Python's
    stdlib `urlopen` cert verification

### Changed
- `pyproject.toml`: pytest gates integration tests behind a marker; `[dev]`
  adds `testcontainers[postgres]>=4.7` (only used by the integration suite,
  hence opt-in); main deps add `sigil-sdk>=0.2` and `sigil-sdk-anthropic>=0.2`.
- `.env(.example)` adds `SIGIL_ENDPOINT`, `SIGIL_PROTOCOL`, `SIGIL_AUTH_MODE`,
  `SIGIL_AUTH_TENANT_ID`, `SIGIL_AUTH_TOKEN`,
  `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` (Mimir
  rejects delta), and an explicit `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`.

### AcmeRetail example
Generated and shipped as `data/plans/acme-retail-example.json` (96 KB):
6 processes · 77 KG nodes · 118 edges · 3-event incident · 3 dashboards ·
**18 alerts** · 5 assistant tools. The 18 alerts cover one or more failure
modes per process (D2C web, fulfillment, wholesale, POS, WIP collab drops,
returns).

### Carryover / known limits
- Sigil ingest requires the access-policy token to include `sigil:write` in
  addition to the OTLP `metrics:write` / `traces:write` / `logs:write` scopes;
  the SDK accepts and silently drops generations on flush if the scope is
  missing (logs `WARNING:sigil_sdk:... HTTP Error 401: Unauthorized`).
- The planner makes 9 Anthropic calls (one per phase + one per modeled
  process). AcmeRetail run takes ~5 minutes end-to-end at ~$1–2 in tokens.
- Phase prompts ship with literal-shape JSON examples after observing real
  Claude output drift to natural-language field names (`name`, `description`).
  Sanitizers in `_sanitize_kg_payload` and `_sanitize_incident_payload` are
  the second line of defense.

### Pushback / things to revisit
- `research.py` still does manual TypedDict orchestration (not LangGraph);
  v0.2 mirrored that shape in `planner.py` for codebase consistency, even
  though the brief mentions LangGraph. Worth a follow-up to either retrofit
  both onto LangGraph or formalize this as the project's chosen pattern.
- The Sigil SDK's `AnthropicOptions` doesn't expose `parent_generation_ids`,
  so `sigil_helper.py` reaches into `sigil_sdk_anthropic.provider`'s private
  `_start_payload` / `_messages_from_request_response` to inject the parent
  list. If those internals move in a future SDK release, the helper needs
  to track them. Worth opening an upstream issue.

## [0.1.0] — Walking skeleton
- Four pipeline schemas (`CompanyProfile`, `DemoPlan`, `KnowledgeGraph`,
  `IncidentScript`) as Pydantic v2 models
- Hand-curated AcmeRetail fixture, plus 7 schema unit tests
- Research agent (`agents/research.py`) with allowlisted fetcher and
  citation tracking
- Local Docker Compose stack (Postgres only — visualization on Grafana Cloud
  via PDC)
- CLI scaffold and `justfile` dev loop
