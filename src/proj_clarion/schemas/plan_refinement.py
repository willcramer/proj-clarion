"""Schemas for the Refine-via-chat flow on Plan detail.

The planner agent emits structured proposals via Claude tool-use on
each refine turn; we persist those alongside the narrative. When the
SE clicks Summarize, the accumulated proposals across all turns are
collapsed into one canonical change set. Apply then decides which
pipeline phase needs to re-run.

The collapser handles three kinds of conflict:
  * `add(X) + remove(X)` across turns      → drop both (no-op)
  * multiple `modify(X)`                   → last write wins
  * `add(X)` followed by `modify(X)`       → fold modify into the add

Conflict resolution uses each ProposedChange's `identifier` field
(the entity's id when we know it). Targets that the agent doesn't
have an id for (a brand-new node it's about to propose) are left
distinct — we don't try to deduplicate by payload equality, that
direction leads to false positives.

Targets split into two layers:
  * **profile-level** — tech_stack_signal, pain_signal, channel,
    business_entity_candidate. Changing these requires extending
    the source CompanyProfile and re-running research → plan.
  * **plan-level**    — kg_node, kg_edge, process, alert, dashboard,
    incident_event. Plan-only re-run suffices.

`requires_research_rerun` on the collapsed summary is the boolean the
apply endpoint reads to decide between "plan rerun" and
"research + plan rerun".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Targets the planner agent can propose changes against. Split into
# tuples so the validators below can check membership without
# duplicating the list.
PROFILE_TARGETS: tuple[str, ...] = (
    "tech_stack_signal",
    "pain_signal",
    "channel",
    "business_entity_candidate",
    "strategic_priority",
)

PLAN_TARGETS: tuple[str, ...] = (
    "kg_node",
    "kg_edge",
    "process",
    "alert",
    "dashboard",
    "incident_event",
)

ALL_TARGETS: tuple[str, ...] = PROFILE_TARGETS + PLAN_TARGETS


ChangeKind = Literal["add", "remove", "modify"]
ChangeTarget = Literal[
    "tech_stack_signal",
    "pain_signal",
    "channel",
    "business_entity_candidate",
    "strategic_priority",
    "kg_node",
    "kg_edge",
    "process",
    "alert",
    "dashboard",
    "incident_event",
]


class ProposedChange(BaseModel):
    """One atomic proposal from the planner agent.

    The agent emits a list of these via the `propose_plan_changes`
    tool. Payload schema is intentionally loose (just `dict`) because:
      * Different target types want different shapes
      * We don't want the agent's tool call to fail validation mid-
        turn over a missing field — we'd lose the whole batch
      * Strict validation happens later, when Apply translates a
        proposal into a real profile/plan mutation

    `identifier` is the entity's id when known (e.g. an existing
    `process_id` the agent wants to modify). For `add` proposals it's
    typically None — the planner will mint the id during re-plan.
    """

    model_config = ConfigDict(extra="forbid")

    kind:       ChangeKind
    target:     ChangeTarget
    payload:    dict[str, Any] = Field(
        default_factory=dict,
        description="Entity body or patch. Loose schema, target-specific.",
    )
    rationale:  str = Field(
        description="One-line reason for the change, surfaced in the summary UI.",
    )
    identifier: str | None = Field(
        default=None,
        description="Target entity's id when known (for remove/modify).",
    )


class CollapsedSummary(BaseModel):
    """Output of `collapse_proposals` — the canonical change set after
    conflict resolution. This is what gets shown in the Summary view
    and what /apply reads to decide which phase to re-run."""

    model_config = ConfigDict(extra="forbid")

    profile_changes: list[ProposedChange] = Field(default_factory=list)
    plan_changes:    list[ProposedChange] = Field(default_factory=list)
    requires_research_rerun: bool = Field(
        description="True iff any profile_changes exist. Apply uses this to decide "
                    "between 'plan rerun' and 'research + plan rerun'.",
    )
    targets_summary: dict[str, int] = Field(
        default_factory=dict,
        description="Per-target counts for the UI strip "
                    "(e.g. {'kg_node': 12, 'alert': 3}). Counts unique entities, "
                    "not change kinds — a target appears once even if added then modified.",
    )


# ──────────────────────────────────────────────────────────────────
# Claude tool-use definition
# ──────────────────────────────────────────────────────────────────

# Anthropic SDK tool schema for `propose_plan_changes`. The agent
# calls this after its narrative reasoning each turn. Keep the schema
# narrow — every property here costs context budget on every call.
PROPOSE_PLAN_CHANGES_TOOL: dict[str, Any] = {
    "name": "propose_plan_changes",
    "description": (
        "Record one or more proposed changes to the current DemoPlan or its "
        "source CompanyProfile. Call this tool AFTER explaining your reasoning "
        "to the SE in narrative form. The SE will see your text response in the "
        "chat and the structured proposals in a summary view; nothing is applied "
        "until the SE explicitly clicks Apply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "description": "One ProposedChange per atomic change. Group related changes (e.g. adding a service plus its alerts) into one tool call.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["add", "remove", "modify"],
                            "description": "What kind of change to make to the target.",
                        },
                        "target": {
                            "type": "string",
                            "enum": list(ALL_TARGETS),
                            "description": (
                                "Which kind of entity. profile-level targets "
                                "(tech_stack_signal / pain_signal / channel / "
                                "business_entity_candidate / strategic_priority) "
                                "require a research re-run before re-planning; "
                                "plan-level targets (kg_node / kg_edge / process / "
                                "alert / dashboard / incident_event) only need a "
                                "plan re-run."
                            ),
                        },
                        "payload": {
                            "type": "object",
                            "description": (
                                "Entity body for `add`, partial patch for `modify`, "
                                "or empty {} for `remove`. Field names should match "
                                "the relevant Pydantic schema (CompanyProfile or DemoPlan)."
                            ),
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One sentence on WHY — surfaced verbatim in the Summary view so the SE can scan justifications.",
                        },
                        "identifier": {
                            "type": "string",
                            "description": "The target entity's existing id, if you're modifying or removing something already in the plan/profile. Omit for `add`.",
                        },
                    },
                    "required": ["kind", "target", "payload", "rationale"],
                },
                "minItems": 1,
            },
        },
        "required": ["changes"],
    },
}


# ──────────────────────────────────────────────────────────────────
# Collapser — accumulate proposals across turns into one canonical set
# ──────────────────────────────────────────────────────────────────

def collapse_proposals(
    proposals_by_turn: list[list[ProposedChange]],
) -> CollapsedSummary:
    """Reduce a sequence of per-turn proposal batches to one summary.

    Order matters — later turns override earlier ones for the same
    `(target, identifier)` key. Proposals without an identifier are
    treated as distinct (no dedup attempted).

    Conflict resolution:
      * add(X) then remove(X)   → drop both
      * remove(X) then add(X)   → keep add (the SE changed their mind back)
      * add(X) then modify(X)   → fold modify into add's payload
      * modify(X) then modify(X) → last modify wins
      * modify(X) then remove(X) → keep remove
    """
    # `keyed` holds proposals we can deduplicate by (target, identifier).
    # `unkeyed` holds the rest in arrival order — we don't try to merge them.
    keyed: dict[tuple[str, str], ProposedChange] = {}
    unkeyed: list[ProposedChange] = []

    for batch in proposals_by_turn:
        for change in batch:
            if change.identifier is None:
                unkeyed.append(change)
                continue
            key = (change.target, change.identifier)
            existing = keyed.get(key)
            if existing is None:
                keyed[key] = change
                continue
            # Conflict — resolve based on prior + new kinds.
            merged = _merge_pair(existing, change)
            if merged is None:
                # Cancels out (add+remove); drop both.
                del keyed[key]
            else:
                keyed[key] = merged

    all_changes = list(keyed.values()) + unkeyed
    profile_changes = [c for c in all_changes if c.target in PROFILE_TARGETS]
    plan_changes    = [c for c in all_changes if c.target in PLAN_TARGETS]

    # Per-target counts for the UI strip — counts unique entities, so an
    # add+modify pair on the same id counts once.
    targets_summary: dict[str, int] = {}
    for c in all_changes:
        targets_summary[c.target] = targets_summary.get(c.target, 0) + 1

    return CollapsedSummary(
        profile_changes=profile_changes,
        plan_changes=plan_changes,
        requires_research_rerun=bool(profile_changes),
        targets_summary=targets_summary,
    )


def _merge_pair(
    prior: ProposedChange, new: ProposedChange,
) -> ProposedChange | None:
    """Merge two proposals for the same (target, identifier).

    Returns the merged proposal, or None when they cancel.
    Pure function — the result has no side effects on inputs.
    """
    # add then remove → no-op
    if prior.kind == "add" and new.kind == "remove":
        return None
    # remove then add → keep the add (user changed mind)
    if prior.kind == "remove" and new.kind == "add":
        return new
    # add then modify → fold the modify's payload into the add
    if prior.kind == "add" and new.kind == "modify":
        merged_payload = {**prior.payload, **new.payload}
        return prior.model_copy(update={
            "payload":   merged_payload,
            "rationale": new.rationale,  # latest rationale wins; usually more current
        })
    # modify then modify → last write wins, with merged payload
    if prior.kind == "modify" and new.kind == "modify":
        merged_payload = {**prior.payload, **new.payload}
        return new.model_copy(update={"payload": merged_payload})
    # modify then remove → keep the remove
    if prior.kind == "modify" and new.kind == "remove":
        return new
    # remove then modify → degenerate (modifying a removed thing). Keep
    # the remove; if the SE really wants modify-after-remove they should
    # issue add+modify in a later turn.
    if prior.kind == "remove" and new.kind == "modify":
        return prior
    # add then add → last write wins (latest payload + rationale)
    if prior.kind == "add" and new.kind == "add":
        return new
    # remove then remove → idempotent
    if prior.kind == "remove" and new.kind == "remove":
        return prior
    # Defensive fallthrough — return the new one.
    return new
