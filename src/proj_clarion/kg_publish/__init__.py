"""Publish a DemoPlan's KG (plus synthetic infra) to Grafana Cloud Knowledge Graph.

The pipeline:
1. `expand_with_synthetic_infra(plan)` — augment the plan's KG with derived
   technical entities (Pods per Service, Nodes per Cluster, a few VMs).
   These don't correspond to anything actually deployed; they exist so the
   KG visualization shows a believable tech tier alongside the business tier.
2. `build_model_rules(plan)` — emit `model-rules.yaml` declaring the custom
   business entity types (Region, Channel, Store, FulfillmentCenter) and
   their relations. KubeCluster/Namespace/Service/Pod/Node are KG built-ins.
3. `build_prom_rules(plan)` — emit `prom-rules.yaml` with the recording
   rule that materialises `clarion_entity_info` from `target_info`.
4. `EntityEmitter` — long-running OTLP gauge process: for every entity in
   the expanded KG, emit `clarion_entity_info{...}=1` on a 30s loop with
   the right resource attributes so the entity processor materialises it.

Reusability hooks:
- Entity types come from the **plan's actual KG subtypes**, not hard-coded.
  A future plan with `partner_program` business entities will get a
  PartnerProgram entity type generated automatically.
- The OTLP attribute prefix (`clarion.*`) is configurable via
  `CLARION_OTLP_ATTR_PREFIX` for forks of this project.
"""

from proj_clarion.kg_publish.emitter import EntityEmitter
from proj_clarion.kg_publish.expand import expand_with_synthetic_infra
from proj_clarion.kg_publish.model_rules import build_model_rules
from proj_clarion.kg_publish.prom_rules import build_prom_rules

__all__ = [
    "EntityEmitter",
    "build_model_rules",
    "build_prom_rules",
    "expand_with_synthetic_infra",
]
