# Clarion Business Entity Model — Grafana Knowledge Graph

## What this is

A custom Knowledge Graph model in Grafana Cloud (Asserts) that bridges
business entities (Account → Region → Store) to IT infrastructure
(KubeCluster → Service → Pod). Enables business observability queries
via Grafana Assistant.

## Source of truth

- **Model YAML:** [`infra/grafana/clarion-business-model.yaml`](../infra/grafana/clarion-business-model.yaml)
- **Live rule name:** `clarion-business-model-1a7a1fad`
- **Source metric:** `clarion_entity_info`
- **Affinity metric (Channel→Service):** `clarion_channel_service_affinity`

## Entity hierarchy

```
Account → Region → Channel / FulfillmentCenter → Store
  └─ Store → KubeCluster → Service → Pod
  └─ Store → VM / LoadBalancer / Database / Topic
```

## Critical labelValues requirements

These must be present on the corresponding `clarion_entity_info` series, or
PROPERTY_MATCH relations break:

| Entity            | Required labels                                                     | Why |
|-------------------|---------------------------------------------------------------------|-----|
| Store             | `service`, `namespace`                                              | Store→Service HOSTS relation |
| FulfillmentCenter | `service`, `namespace`                                              | FC→Service HOSTS relation |
| Pod               | `clarion_store_id`, `node`                                          | Store→Pod CONTAINS; Node→Pod HOSTS (built-in) |
| VM                | `clarion_store_id`, `clarion_kube_cluster`                          | Store→VM CONTAINS, VM→KubeCluster RUNS_ON |
| LoadBalancer      | `clarion_store_id`                                                  | Store→LB CONTAINS |
| Database          | `clarion_store_id`                                                  | Store→DB CONTAINS |
| Topic             | `clarion_store_id`                                                  | Store→Topic CONTAINS |
| Region            | (no extra)                                                          | Region→Store joins on Region.name == Store.region_id |
| Channel           | (no extra)                                                          | Channel→Store joins on Channel.name == Store.channel_id |

## All label names on `clarion_entity_info`

```
clarion_customer
clarion_region_id
clarion_channel_id
clarion_fulfillment_center_id
clarion_store_id
clarion_pod_id
clarion_vm_id
clarion_node_id
clarion_loadbalancer_id
clarion_database_id
clarion_topic_id
clarion_kube_cluster
service
namespace
asserts_env
asserts_site
```

## Known limitations

- **Node → Pod**: now works via the **built-in** `Node HOSTS Pod` relation
  (PROPERTY_MATCH on `Node.name == Pod.node`). Each Pod observation
  carries a `node` label, deterministically round-robin-assigned to a
  kubenode in its cluster by `_attach_pod_to_node` in `kg_publish/emitter.py`.
  No custom relation required.
- **`clarion_channel_service_affinity`** metric is required for the
  Channel → Service `SERVES` relation (METRICS-based, not PROPERTY_MATCH).
  Emitted by `kg_publish/red_emitter.py`'s shared meter.
- The custom `Pod` entity (keyed on `clarion_pod_id`) is distinct from
  Grafana's built-in Kubernetes `Pod` entity type.

## Code that must stay in sync

- `src/proj_clarion/kg_publish/emitter.py` — `_observation_attrs()`
  produces the per-entity label dict that becomes `clarion_entity_info{...}`.
  Every required label in the table above must be set on the matching
  entity branch.
- `src/proj_clarion/kg_publish/red_emitter.py` — emits the
  `clarion_channel_service_affinity` metric used by the SERVES relation.
- `tests/unit/test_emitter_model_alignment.py` — locks in the per-entity
  label invariants this model depends on. If a test fails after editing
  `_observation_attrs`, you've broken a relation.

## To update the model in Grafana

Three options, in increasing severity:

1. **Edit YAML in this repo, push via gcx**:
   `gcx kg model-rules apply -f infra/grafana/clarion-business-model.yaml`
2. **Ask Grafana Assistant**: *"Read the clarion business model rule and
   apply this change: …"*
3. **Hand-edit in the Grafana UI** (Knowledge Graph → Custom Model Rules).
   If you do this, **dump the result back to the YAML file** so the repo
   stays the source of truth.
