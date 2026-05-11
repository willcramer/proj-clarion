"""Generate `model-rules.yaml` for `gcx kg model-rules create -f -`.

We derive entity types from the **plan's actual KG subtypes** (not hard-coded).
The mapping plan-subtype → KG entity type:

    business_subtype "region"             → entity type "Region"
    business_subtype "channel"            → entity type "Channel"
    business_subtype "store"              → entity type "Store"
    business_subtype "fulfillment_center" → entity type "FulfillmentCenter"
    business_subtype "brand"              → entity type "Brand"
    business_subtype "partner_program"    → entity type "PartnerProgram"
    business_subtype "product_line"       → entity type "ProductLine"
    business_subtype "business_unit"      → entity type "BusinessUnit"

KubeCluster, Namespace, Service, Pod, Node are KG built-ins — we reference
them in relations but don't redefine them.

For VMs (which Grafana KG doesn't know natively), we declare a custom `VM`
entity type so they show up in the graph.

Each entity type's `definedBy` query selects rows from `clarion_entity_info`
(emitted by `entity_emitter.py`) where the right `clarion.<subtype>.id` label
is non-empty.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import yaml

from proj_clarion.schemas import DemoPlan, KnowledgeGraph, NodeType


# Plan subtype → human-friendly KG entity type name
_BUSINESS_TYPE_NAMES = {
    "region": "Region",
    "channel": "Channel",
    "store": "Store",
    "fulfillment_center": "FulfillmentCenter",
    "brand": "Brand",
    "partner_program": "PartnerProgram",
    "product_line": "ProductLine",
    "business_unit": "BusinessUnit",
}

# Business subtypes that can host a K8s cluster — i.e. the
# `_PHYSICAL_ANCHOR_PRIORITY` list from expand.py. Any entity in this set
# carries a `cluster` property so it can join to KubeCluster on
# `cluster ↔ name` in the entity graph. Channels / partner programs /
# product lines are deliberately absent — they're abstract groupings,
# not deployment targets.
_PHYSICAL_ANCHOR_SUBTYPES = {
    "store",
    "fulfillment_center",
    "business_unit",
    "region",
    "brand",
}


def _label_for_subtype(subtype: str) -> str:
    """The Prometheus label that carries this entity's ID, e.g.
    `region` → `clarion_region_id`.
    """
    return f"clarion_{subtype}_id"


def _build_business_entity_yaml(subtype: str, parent_subtypes: list[str]) -> dict[str, Any]:
    """One YAML entry per business entity type.

    `parent_subtypes` are the labels we additionally select on for the
    Store-style entities that inherit their parent IDs (so Store carries
    region_id, channel_id, cluster). Every entity carries `customer` so
    the user can filter the KG view to a single demo's entities.
    """
    name_label = _label_for_subtype(subtype)
    extra_group_by = [_label_for_subtype(p) for p in parent_subtypes]
    extra_group_by.extend([
        "clarion_kube_cluster", "service", "namespace", "clarion_customer",
    ])

    group_by_clause = ", ".join([name_label, *extra_group_by, "asserts_env", "asserts_site"])
    selector = f'{name_label}!="", asserts_env!=""'

    label_values: dict[str, str] = {
        f"{subtype}_id": name_label,
        "customer":      "clarion_customer",
    }
    for p in parent_subtypes:
        label_values[f"{p}_id"] = _label_for_subtype(p)
    # Any business entity that can host a K8s cluster gets a `cluster`
    # property so the cross-tier PROPERTY_MATCH (<X>.cluster ↔
    # KubeCluster.name) draws an edge in the Asserts entity graph.
    # `_PHYSICAL_ANCHOR_SUBTYPES` mirrors expand.py's anchor priority
    # so any subtype that can be picked as a cluster home lights up the
    # cross-tier relation.
    if subtype in _PHYSICAL_ANCHOR_SUBTYPES:
        label_values["cluster"] = "clarion_kube_cluster"

    return {
        "type": _BUSINESS_TYPE_NAMES.get(subtype, subtype.title()),
        "name": name_label,
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                f"group by ({group_by_clause}) (\n"
                f"  clarion_entity_info{{{selector}}}\n"
                f")"
            ),
            "labelValues": label_values,
        }],
    }


def _build_vm_entity_yaml() -> dict[str, Any]:
    return {
        "type": "VM",
        "name": "clarion_vm_id",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_vm_id, clarion_kube_cluster, "
                "clarion_store_id, clarion_customer, "
                "asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_vm_id!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "vm_id":    "clarion_vm_id",
                "cluster":  "clarion_kube_cluster",
                "store":    "clarion_store_id",
                "customer": "clarion_customer",
            },
        }],
    }


def _build_customer_entity_yaml() -> dict[str, Any]:
    """Top-of-hierarchy entity — the Customer (company) being demoed.

    Uses the built-in `Account` type name to inherit the Asserts Account icon
    (Asserts' built-in entity registry has hardcoded icons; defining a custom
    entity with the same TYPE NAME picks up the icon, even though the
    definedBy query is ours, not the built-in's).
    """
    return {
        "type": "Account",
        "name": "clarion_customer",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_customer, asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_customer!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "account_id": "clarion_customer",
            },
        }],
    }


def _build_node_entity_yaml() -> dict[str, Any]:
    """Built-in `Node` type name — k8s worker node icon."""
    return {
        "type": "Node",
        "name": "clarion_node_id",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_node_id, clarion_kube_cluster, "
                "clarion_customer, asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_node_id!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "node_id":  "clarion_node_id",
                "cluster":  "clarion_kube_cluster",
                "customer": "clarion_customer",
            },
        }],
    }


def _build_loadbalancer_entity_yaml() -> dict[str, Any]:
    """Built-in `LoadBalancer` type — picks up the Asserts LB icon."""
    return {
        "type": "LoadBalancer",
        "name": "clarion_loadbalancer_id",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_loadbalancer_id, clarion_store_id, "
                "clarion_customer, asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_loadbalancer_id!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "loadbalancer_id": "clarion_loadbalancer_id",
                "store":           "clarion_store_id",
                "customer":        "clarion_customer",
            },
        }],
    }


def _build_database_entity_yaml() -> dict[str, Any]:
    """Built-in `Database` type — cylinder icon."""
    return {
        "type": "Database",
        "name": "clarion_database_id",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_database_id, clarion_store_id, "
                "clarion_customer, asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_database_id!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "database_id": "clarion_database_id",
                "store":       "clarion_store_id",
                "customer":    "clarion_customer",
            },
        }],
    }


def _build_topic_entity_yaml() -> dict[str, Any]:
    """Built-in `Topic` type — Kafka topic icon."""
    return {
        "type": "Topic",
        "name": "clarion_topic_id",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_topic_id, clarion_store_id, "
                "clarion_customer, asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_topic_id!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "topic_id":  "clarion_topic_id",
                "store":     "clarion_store_id",
                "customer":  "clarion_customer",
            },
        }],
    }


def _build_cloud_entity_yaml() -> dict[str, Any]:
    """Cloud — re-uses the built-in `Cloud` entity TYPE NAME so the
    Asserts icon registry maps "AWS"/"Azure"/"GCP"/etc. to their native
    provider icons automatically. Emerges from the `clarion_cloud` label
    on every cluster's `clarion_entity_info` series. One entity per
    unique provider value across this customer's clusters.

    Per-cluster assignment lives in
    `expand._assign_cloud_providers` (5 distinct clouds rotate across
    the 5 clusters).
    """
    return {
        "type": "Cloud",
        "name": "clarion_cloud",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_cloud, clarion_customer, "
                "asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_cloud!=\"\", "
                "asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "name":     "clarion_cloud",
                "customer": "clarion_customer",
            },
        }],
    }


def _build_cloud_region_entity_yaml() -> dict[str, Any]:
    """CloudRegion — distinct from the BUSINESS `Region` entity (which
    represents sales geographies like region-emea / region-americas).
    A CloudRegion is a real cloud-provider availability area: us-east-1,
    eastus, europe-west1, etc.

    Each CloudRegion is bound to its parent Cloud so the entity graph
    shows the natural IT-arch hierarchy: Cloud → CloudRegion →
    KubeCluster (rather than mashing cloud and business regions
    together).
    """
    return {
        "type": "CloudRegion",
        "name": "clarion_cloud_region",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_cloud_region, clarion_cloud, "
                "clarion_customer, asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_cloud_region!=\"\", "
                "asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "name":     "clarion_cloud_region",
                # `cloud` property carries the parent Cloud's name so
                # `Cloud HOSTS CloudRegion` PROPERTY_MATCH (cloud.name ↔
                # cloud_region.cloud) can join.
                "cloud":    "clarion_cloud",
                "customer": "clarion_customer",
            },
        }],
    }


def _cloud_hosts_cloud_region_relation() -> dict[str, Any]:
    """Cloud HOSTS CloudRegion — joins on Cloud.name ↔ CloudRegion.cloud."""
    return {
        "type": "HOSTS",
        "startEntityType": "Cloud",
        "endEntityType": "CloudRegion",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["cloud", "env", "site"],
        },
    }


def _cloud_region_hosts_cluster_relation() -> dict[str, Any]:
    """CloudRegion HOSTS KubeCluster — joins on
    CloudRegion.name ↔ KubeCluster.cloud_region. Replaces the previous
    direct `CloudProvider HOSTS KubeCluster` so the chain reads
    Cloud → CloudRegion → KubeCluster (real IT topology).
    """
    return {
        "type": "HOSTS",
        "startEntityType": "CloudRegion",
        "endEntityType": "KubeCluster",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["cloud_region", "env", "site"],
        },
    }


def _service_contains_pod_relation() -> dict[str, Any]:
    """Service CONTAINS Pod — the relation that gives every Service
    entity in the entity graph an edge down to its replicas.

    Joins on Service.name ↔ Pod.service. Both values are the unprefixed
    OTLP `service.name` semconv label (RedEmitter sets this on the
    per-service Resource; expand.py sets the matching `service` label
    on every Pod's `clarion_entity_info` series). Without this relation,
    services render as a disconnected green blob in the entity graph
    (the "platform/X" floaters in the user's Sentinel screenshot).
    """
    return {
        "type": "CONTAINS",
        "startEntityType": "Service",
        "endEntityType": "Pod",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["service", "env", "site"],
        },
    }


def _service_uses_database_relation() -> dict[str, Any]:
    """Service USES Database — driven by the existing
    `clarion_service_database_affinity` co-labeled metric (one series per
    (service, database) `depends_on` pair from the plan KG, topped up to
    100% coverage by `expand._ensure_service_database_topology`).

    Pattern mirrors `_serves_relation` (Channel SERVES Service): METRICS
    join keyed off `service` (matches the built-in Service entity's
    `name` property) and `clarion_database_id` (matches the custom
    Database entity's `database_id`).
    """
    return {
        "type": "USES",
        "startEntityType": "Service",
        "endEntityType": "Database",
        "definedBy": {
            "_yaml_tag": "METRICS",
            "pattern": (
                "group by (service, clarion_database_id, namespace, "
                "asserts_env, asserts_site) (\n"
                "  clarion_service_database_affinity{service!=\"\", "
                "clarion_database_id!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "startEntityMatchers": {
                "name":      "service",
                "namespace": "namespace",
                "env":       "asserts_env",
                "site":      "asserts_site",
            },
            "endEntityMatchers": {
                "name": "clarion_database_id",
                "env":  "asserts_env",
                "site": "asserts_site",
            },
        },
    }


def _build_kubecluster_entity_yaml() -> dict[str, Any]:
    """KubeCluster — sourced from `clarion_entity_info`, NOT `kube_node_info`.

    ⚠️ HARD REQUIREMENT every entity type must satisfy: its source series
    must carry `asserts_env` AND `asserts_site` labels, or the Asserts KG
    builder silently drops every candidate. We keep being burned by this:
    when v0.6.4 emptied the observation `_common_attrs` (to fix an
    `asserts_env=prod;prod` doubling regression) `kube_node_info` lost
    those labels, and the BUILT-IN KubeCluster discovery — which scopes
    by `asserts_env` on `kube_node_info` — produced 0 entities silently.

    Pods/Stores still wired to "KubeCluster" via PROPERTY_MATCH but the
    target type didn't exist, so the relations dangled. AcmeRetail-shape
    builds looked OK because we'd accept an empty Cluster column without
    flagging it; doctor.py now catches this.

    Confirmed via Grafana Assistant 2026-05-08:
      - clarion_entity_info: 7 distinct clarion_kube_cluster values, all
        labelled asserts_env=prod ✓
      - kube_node_info: same 7 cluster values but NO asserts_env label ✗

    By defining the KubeCluster type explicitly here, we take precedence
    over the built-in's broken discovery and use a source series we
    actually control. The existing PROPERTY_MATCH relations (KubeCluster
    CONTAINS Pod, Store RUNS_ON KubeCluster) match on cluster<->name,
    where `name` is now `clarion_kube_cluster` — which is what Pod/Store
    already expose as their `cluster` property. Zero relation changes.
    """
    return {
        "type": "KubeCluster",
        "name": "clarion_kube_cluster",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_kube_cluster, clarion_cloud, "
                "clarion_cloud_region, clarion_customer, "
                "asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_kube_cluster!=\"\", "
                "asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "name":         "clarion_kube_cluster",
                # `cloud_region` (not `cloud`!) is the join key for the
                # `CloudRegion HOSTS KubeCluster` PROPERTY_MATCH. The
                # `cloud` property is kept on the cluster for
                # observability — KubeCluster→Cloud isn't a direct edge
                # in the model (it goes through CloudRegion), but having
                # the value handy makes ad-hoc PromQL pivots easier.
                "cloud":        "clarion_cloud",
                "cloud_region": "clarion_cloud_region",
                "customer":     "clarion_customer",
            },
        }],
    }


def _build_pod_entity_yaml() -> dict[str, Any]:
    """Pods aren't built-in (Service/Namespace/KubeCluster are; Pod sometimes
    is, sometimes isn't depending on the stack). Declaring our own custom
    Pod type so we control the shape and it shows up in the graph
    regardless of stack defaults.

    `service` property maps from the `service` label (unprefixed, e.g.
    `checkout`) so the Service CONTAINS Pod join matches the
    built-in Service entity's name (which is the value of service.name on
    the per-service Resource — also unprefixed).
    """
    return {
        "type": "Pod",
        "name": "clarion_pod_id",
        "scope": {"env": "asserts_env", "site": "asserts_site"},
        "definedBy": [{
            "query": (
                "group by (clarion_pod_id, service, "
                "namespace, clarion_kube_cluster, clarion_customer, "
                "asserts_env, asserts_site) (\n"
                "  clarion_entity_info{clarion_pod_id!=\"\", "
                "service!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "labelValues": {
                "pod_id":     "clarion_pod_id",
                "service":    "service",
                "namespace":  "namespace",
                "cluster":    "clarion_kube_cluster",
                "customer":   "clarion_customer",
            },
        }],
    }


def _hierarchical_relation(
    start_type: str, end_type: str, end_parent_label: str,
) -> dict[str, Any]:
    """Region/Channel CONTAINS Store-style relation.

    Joins on positional property match: start.name == end.<parent>_id.
    """
    return {
        "type": "CONTAINS",
        "startEntityType": start_type,
        "endEntityType": end_type,
        "definedBy": {
            # YAML tag preserved as-is for the assistant-given format
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": [end_parent_label, "env", "site"],
        },
    }


def _runs_on_relation(start_type: str) -> dict[str, Any]:
    """`<BusinessEntity> RUNS_ON KubeCluster` — uses the `cluster` property
    on the start entity (mapped from `clarion_kube_cluster`).

    Generic across Store / FulfillmentCenter / BusinessUnit / Region /
    Brand — any business entity from `_PHYSICAL_ANCHOR_SUBTYPES`. This
    is the relation that bridges the business and tech tiers in the
    Asserts entity graph: without it the K8s blob renders disconnected
    from the business hierarchy (Sentinel-shape bug).
    """
    return {
        "type": "RUNS_ON",
        "startEntityType": start_type,
        "endEntityType": "KubeCluster",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["cluster", "env", "site"],
            "endEntityProperties": ["name", "env", "site"],
        },
    }


def _serves_relation(start_type: str) -> dict[str, Any]:
    """Channel SERVES Service (built-in). METRICS join requires both
    identifiers on the SAME series (per Grafana Assistant 2026-05-07).
    `clarion_entity_info` series carry channel_id OR service_id, never
    both, so we emit a dedicated co-labeled metric
    `clarion_channel_service_affinity` with one series per (channel, service)
    pair from the plan's KG `serves` edges.

    The built-in Service entity is keyed off `service` (the unprefixed
    OTLP semconv service.name), so endEntityMatchers.name uses `service`,
    not `clarion_service_id`.
    """
    return {
        "type": "SERVES",
        "startEntityType": start_type,
        "endEntityType": "Service",
        "definedBy": {
            "_yaml_tag": "METRICS",
            "pattern": (
                "group by (clarion_channel_id, service, namespace, "
                "asserts_env, asserts_site) (\n"
                "  clarion_channel_service_affinity{clarion_channel_id!=\"\", "
                "service!=\"\", asserts_env!=\"\"}\n"
                ")"
            ),
            "startEntityMatchers": {
                "name": "clarion_channel_id",
                "env":  "asserts_env",
                "site": "asserts_site",
            },
            "endEntityMatchers": {
                "name":      "service",
                "namespace": "namespace",
                "env":       "asserts_env",
                "site":      "asserts_site",
            },
        },
    }


def _vm_runs_on_cluster_relation() -> dict[str, Any]:
    return {
        "type": "RUNS_ON",
        "startEntityType": "VM",
        "endEntityType": "KubeCluster",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["cluster", "env", "site"],
            "endEntityProperties": ["name", "env", "site"],
        },
    }


def _pod_to_cluster_relation() -> dict[str, Any]:
    """⚠️ DEPRECATED — kept for reference, NOT emitted into model rules.

    This used to declare `KubeCluster CONTAINS Pod` directly via cluster<->name
    PROPERTY_MATCH. Grafana Assistant diagnosed (2026-05-08) that this
    creates 213 incorrect direct edges in the KG: pods aren't architecturally
    contained by clusters — they're contained by Stores. The correct
    traversal already exists via:
        KubeCluster ← RUNS_ON ← Store → (Service/etc. → Pod by service)

    Direct cluster→pod edges flatten the hierarchy and make the entity
    graph less interpretable on the Cloud-side viewer (every pod becomes
    a cluster's direct child, drowning out the Store→Service→Pod chain).
    Removed from `build_model_rules` — see GS diagnosis log. If we ever
    add a Pod.store_id property we could revisit a Store CONTAINS Pod
    relation, but the indirect traversal is sufficient.
    """
    return {
        "type": "CONTAINS",
        "startEntityType": "KubeCluster",
        "endEntityType": "Pod",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["cluster", "env", "site"],
        },
    }


def _customer_contains_relation(end_type: str, end_parent_label: str = "customer") -> dict[str, Any]:
    """Account (Customer) CONTAINS X — wherever X carries a `customer` property
    whose value equals the Account entity's name. Used to roll up Region,
    Channel, etc. under the top-of-hierarchy Account.
    """
    return {
        "type": "CONTAINS",
        "startEntityType": "Account",
        "endEntityType": end_type,
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": [end_parent_label, "env", "site"],
        },
    }


def _store_contains_vm_relation() -> dict[str, Any]:
    """Store CONTAINS VM — VMs are dedicated infrastructure for a store.
    VM's `store` property matches Store's `name`.
    """
    return {
        "type": "CONTAINS",
        "startEntityType": "Store",
        "endEntityType": "VM",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["store", "env", "site"],
        },
    }


def _store_contains_loadbalancer_relation() -> dict[str, Any]:
    return {
        "type": "CONTAINS",
        "startEntityType": "Store",
        "endEntityType": "LoadBalancer",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["store", "env", "site"],
        },
    }


def _store_contains_database_relation() -> dict[str, Any]:
    return {
        "type": "CONTAINS",
        "startEntityType": "Store",
        "endEntityType": "Database",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["store", "env", "site"],
        },
    }


def _store_contains_topic_relation() -> dict[str, Any]:
    return {
        "type": "CONTAINS",
        "startEntityType": "Store",
        "endEntityType": "Topic",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["store", "env", "site"],
        },
    }


def _cluster_contains_node_relation() -> dict[str, Any]:
    """KubeCluster CONTAINS Node — every k8s worker belongs to its cluster."""
    return {
        "type": "CONTAINS",
        "startEntityType": "KubeCluster",
        "endEntityType": "Node",
        "definedBy": {
            "_yaml_tag": "PROPERTY_MATCH",
            "startEntityProperties": ["name", "env", "site"],
            "endEntityProperties": ["cluster", "env", "site"],
        },
    }


# ============================================================
# YAML serialization with the !<TAG> shape gcx expects
# ============================================================

class _PropertyMatch:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


class _MetricsMatch:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


def _literal_str(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Multi-line strings → block-literal `|` style for readability."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _property_match_repr(dumper: yaml.Dumper, data: _PropertyMatch) -> yaml.Node:
    # Use a placeholder tag we'll string-replace post-dump to avoid PyYAML
    # URL-escaping the angle brackets in `!<PROPERTY_MATCH>`.
    return dumper.represent_mapping("__CLARION_TAG_PROPERTY_MATCH__", data.payload)


def _metrics_match_repr(dumper: yaml.Dumper, data: _MetricsMatch) -> yaml.Node:
    return dumper.represent_mapping("__CLARION_TAG_METRICS__", data.payload)


yaml.add_representer(str, _literal_str)
yaml.add_representer(_PropertyMatch, _property_match_repr)
yaml.add_representer(_MetricsMatch, _metrics_match_repr)


def _wrap_definedBy(rel: dict[str, Any]) -> dict[str, Any]:
    """Convert internal `_yaml_tag` marker into the right yaml-tagged class."""
    db = rel["definedBy"]
    tag = db.pop("_yaml_tag")
    wrapped = _PropertyMatch(db) if tag == "PROPERTY_MATCH" else _MetricsMatch(db)
    out = dict(rel)
    out["definedBy"] = wrapped
    return out


# ============================================================
# Public API
# ============================================================

def _customer_slug_from_plan(plan: DemoPlan) -> str:
    """Derive a customer slug from the plan's source profile_id.
    `prof-initech_industrial` → `initech_industrial`. Falls back to a generic
    `clarion` when there's no usable profile_id (shouldn't happen in
    practice but keeps the file name well-formed)."""
    pid = (plan.source_profile_id or "").strip()
    if pid.startswith("prof-"):
        pid = pid[len("prof-"):]
    pid = pid.strip("-").lower()
    return pid or "clarion"


def build_model_rules(
    plan: DemoPlan,
    *,
    kg: KnowledgeGraph | None = None,
    customer: str | None = None,
) -> str:
    """Return the model-rules YAML body, ready to push via `gcx kg model-rules create -f -`.

    `customer` overrides the auto-derived slug used for the file name; pass
    the same value the entity emitter uses (CLI: `--customer`) so the
    Cloud-side model rule file and the emitted `clarion_customer` label
    line up. When omitted we derive from `plan.source_profile_id`.
    """
    kg = kg or plan.knowledge_graph
    plan_id = str(plan.plan_id)
    customer_slug = (customer or _customer_slug_from_plan(plan)).lower()

    # 1. Find which business subtypes are present in this plan's KG
    subtypes_present: set[str] = set()
    for n in kg.nodes:
        if n.node_type == NodeType.BUSINESS_ENTITY and n.business_subtype:
            subtypes_present.add(n.business_subtype)

    # 2. Determine parent subtypes for each child (for hierarchy)
    # For AcmeRetail-shape: Region contains Store, Channel contains Store.
    parent_map = {
        "store": [s for s in ("region", "channel") if s in subtypes_present],
        "fulfillment_center": [s for s in ("region",) if s in subtypes_present],
        "kiosk": ["store"] if "store" in subtypes_present else [],
    }

    entities: list[dict[str, Any]] = []

    # 0. Customer at the top
    entities.append(_build_customer_entity_yaml())

    # 3. Build entity entries in dependency order: parents first
    order = ["region", "channel", "brand", "partner_program", "business_unit",
             "product_line", "fulfillment_center", "store", "kiosk"]
    for subtype in order:
        if subtype not in subtypes_present:
            continue
        parents = parent_map.get(subtype, [])
        entities.append(_build_business_entity_yaml(subtype, parents))

    # 4. Custom tech entities — KubeCluster / Pod / VM / Node / LoadBalancer /
    # Database / Topic. Several borrow built-in TYPE NAMES so they pick up
    # nice icons from Asserts' icon registry; only definedBy is ours.
    has_pod = any(n.attributes.get("kind") == "pod" for n in kg.nodes)
    has_vm = any(n.attributes.get("kind") == "vm" for n in kg.nodes)
    has_kubenode = any(n.attributes.get("kind") == "kubenode" for n in kg.nodes)
    has_lb = any(n.attributes.get("kind") == "loadbalancer" for n in kg.nodes)
    # Match BOTH `attributes.kind=="database"` (synth DBs from expand.py
    # use this) AND `technical_subtype=="database"` (planner-emitted DBs
    # like `db-billing`, `db-orders` use this — they don't carry the
    # `kind` attribute, so the original kind-only check missed them and
    # the Service USES Database relation never got emitted for non-retail
    # plans).
    has_db = any(
        n.attributes.get("kind") == "database" or n.technical_subtype == "database"
        for n in kg.nodes
    )
    has_topic = any(n.attributes.get("kind") == "topic" for n in kg.nodes)
    # KubeCluster is needed whenever ANY of those tech entities exist,
    # because they all attach via CONTAINS / RUNS_ON relations whose
    # endEntityType is KubeCluster. Without an explicit definition the
    # built-in discovery silently fails (see _build_kubecluster_entity_yaml
    # docstring for the asserts_env-on-kube_node_info regression).
    has_kubecluster = has_pod or has_vm or has_kubenode
    if has_kubecluster:
        entities.append(_build_kubecluster_entity_yaml())
        # Cloud + CloudRegion sit above KubeCluster; emit whenever any
        # cluster exists. Each definedBy filters on its respective
        # `clarion_cloud!=""` / `clarion_cloud_region!=""`, so missing
        # labels just produce zero series — no harm.
        entities.append(_build_cloud_entity_yaml())
        entities.append(_build_cloud_region_entity_yaml())
    if has_pod:
        entities.append(_build_pod_entity_yaml())
    if has_vm:
        entities.append(_build_vm_entity_yaml())
    if has_kubenode:
        entities.append(_build_node_entity_yaml())
    if has_lb:
        entities.append(_build_loadbalancer_entity_yaml())
    if has_db:
        entities.append(_build_database_entity_yaml())
    if has_topic:
        entities.append(_build_topic_entity_yaml())

    # 5. Build relations — top-down hierarchy, plus side-fork edges
    relations: list[dict[str, Any]] = []

    # Account at the very top contains every business entity type that's
    # present. Pre-fix this only covered Region/Channel/Store/FC; verticals
    # without those (like Sentinel, with only Brand/BU/PartnerProgram/etc.)
    # ended up with their entire business tier disconnected from Account.
    for child_type, child_subtype in (
        ("Region",          "region"),
        ("Channel",         "channel"),
        ("Store",           "store"),
        ("FulfillmentCenter", "fulfillment_center"),
        ("Brand",           "brand"),
        ("BusinessUnit",    "business_unit"),
        ("PartnerProgram",  "partner_program"),
        ("ProductLine",     "product_line"),
    ):
        if child_subtype in subtypes_present:
            relations.append(_customer_contains_relation(child_type))

    # Region/Channel CONTAINS Store/FC (existing hierarchical relations)
    if "store" in subtypes_present:
        if "region" in subtypes_present:
            relations.append(_hierarchical_relation("Region", "Store", "region_id"))
        if "channel" in subtypes_present:
            relations.append(_hierarchical_relation("Channel", "Store", "channel_id"))
        relations.append(_runs_on_relation("Store"))
    if "fulfillment_center" in subtypes_present:
        if "region" in subtypes_present:
            relations.append(
                _hierarchical_relation("Region", "FulfillmentCenter", "region_id")
            )
        relations.append(_runs_on_relation("FulfillmentCenter"))
    if "channel" in subtypes_present:
        relations.append(_serves_relation("Channel"))

    # Bridge to tech tier for non-store anchors. Verticals without stores
    # (airline, banking, SaaS, industrial) anchor their clusters at the
    # densest physical entity available — see expand.py
    # `_PHYSICAL_ANCHOR_PRIORITY`. Without these RUNS_ON relations the
    # business tier renders as a disconnected blob in the Asserts entity
    # graph (Sentinel-shape bug). Safe to emit even when no cluster anchors
    # at this subtype: PROPERTY_MATCH simply finds zero joins, no harm.
    if has_kubecluster:
        for cross_tier_subtype in ("business_unit", "region", "brand"):
            if cross_tier_subtype in subtypes_present:
                relations.append(
                    _runs_on_relation(_BUSINESS_TYPE_NAMES[cross_tier_subtype])
                )
        # Cloud → CloudRegion → KubeCluster chain. Real IT topology has
        # Cloud (AWS/Azure/GCP) at the root, CloudRegion (us-east-1,
        # eastus, …) inside it, then KubeClusters in that region. We
        # emit both edges so the entity graph shows the full hierarchy.
        relations.append(_cloud_hosts_cloud_region_relation())
        relations.append(_cloud_region_hosts_cluster_relation())

    # Service → Pod / Database — closes the long-standing gap where
    # Service entities floated disconnected in the entity graph. Pods
    # snap up to their owning Service; Databases snap to whichever
    # Services depend on them (driven by clarion_service_database_affinity).
    if has_pod:
        relations.append(_service_contains_pod_relation())
    if has_db:
        relations.append(_service_uses_database_relation())

    # Tech-tier hierarchy: Cluster -> Node, Store -> VM/LB/DB/Topic
    # NOTE: KubeCluster CONTAINS Pod is intentionally NOT emitted here
    # (per GS 2026-05-08 diagnosis). Pods reach KubeCluster via the
    # Store → KubeCluster RUNS_ON edge instead, which preserves the
    # hierarchy (Cluster → Store → Service → Pod) the entity viewer
    # is meant to render. See `_pod_to_cluster_relation` docstring.
    if has_kubenode:
        relations.append(_cluster_contains_node_relation())
    if has_vm:
        relations.append(_vm_runs_on_cluster_relation())
        if "store" in subtypes_present:
            relations.append(_store_contains_vm_relation())
    if has_lb and "store" in subtypes_present:
        relations.append(_store_contains_loadbalancer_relation())
    if has_db and "store" in subtypes_present:
        relations.append(_store_contains_database_relation())
    if has_topic and "store" in subtypes_present:
        relations.append(_store_contains_topic_relation())

    relations = [_wrap_definedBy(r) for r in relations]

    # File name carries the customer slug so it's instantly readable in
    # the Cloud UI ("clarion-business-model-initech_industrial-5ac44b56")
    # instead of a bare plan-id hash. Plan-id suffix preserves uniqueness
    # across multiple plans for the same customer.
    doc = {
        "name": f"clarion-business-model-{customer_slug}-{plan_id[:8]}",
        "entities": entities,
        "relations": relations,
    }

    header = (
        f"# gcx kg model-rules create -f model-rules.yaml\n"
        f"# Generated by Proj Clarion for plan_id={plan_id} customer={customer_slug}\n"
        f"# Entity types reflect this plan's KG subtypes; rerunning the planner\n"
        f"# with a different vertical (logistics, retail, healthcare) regenerates\n"
        f"# this file with the appropriate entity set.\n"
    )
    body = yaml.dump(doc, sort_keys=False, default_flow_style=False, width=100)
    # PyYAML escapes the `<>` in `!<TAG>` to %3C/%3E, which gcx rejects.
    # Swap our placeholder tags back to the literal form gcx expects.
    body = body.replace("!<__CLARION_TAG_PROPERTY_MATCH__>", "!<PROPERTY_MATCH>")
    body = body.replace("!<__CLARION_TAG_METRICS__>", "!<METRICS>")
    return header + body
