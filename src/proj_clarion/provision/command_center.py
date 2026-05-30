"""Command-center dashboard template — single, dense, web-app-feel dashboard.

DATA-DRIVEN, NOT VERTICAL-HARDCODED. The template inspects the plan's
actual knowledge graph + dashboard_specs to decide what to render. There
is no `_VERTICAL_AXES` table that maps business_model→axis here. If the
plan has 6 stores, the primary axis is `store`. If the plan has 9
business_units and 0 stores, the primary axis is `business_unit`. The
planner LLM (informed by vertical-aware prompt steering) decides what
entities to model; this template just visualizes them faithfully.

A panel is included ONLY when its data axis has at least one entity in
the KG. Empty panels — e.g. "Revenue by Brand" on a company with zero
brand entities — are dropped at build time, not silently rendered as
empty timeseries.

Reference structure: `data/reference/business-command-center.example.json`
— a sanitized, generically-branded dashboard layout. The hero-KPI /
mid-breakdown / drill / trend / KG-link shape came from there; this
module reproduces that shape with whatever entity types the plan
actually has.

Metrics referenced are what `kg_publish/red_emitter.py` emits:
  - `clarion_customer_revenue_usd_total`  (total revenue counter)
  - `clarion_customer_orders_total`       (total orders counter)
  - `clarion_customer_health_score`       (gauge; 0-100 composite)
  - `clarion_business_revenue_usd_total{<subtype>, channel, region, customer}`
  - `clarion_business_orders_total{<subtype>, channel, region, customer}`
The `<subtype>` short-form labels (store / business_unit / brand / etc.)
are emitted by `red_emitter._entity_labels()` alongside their
`clarion_<subtype>_id` long-form counterparts so dashboard queries can
group by the short name.

Schema: Grafana dashboard v39 (JSON-rendered via `/api/dashboards/db`).
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from proj_clarion.schemas import DemoPlan


# ============================================================
# Datasource refs (reuse the conventions from dashboards.py so existing
# overrides via env vars still work)
# ============================================================

def _prom_uid() -> str:
    return os.environ.get("GRAFANA_DS_PROMETHEUS_UID", "grafanacloud-prom")


def _prom_ref() -> dict[str, str]:
    return {"type": "prometheus", "uid": _prom_uid()}


def _prom_target(expr: str, ref_id: str = "A", *, instant: bool = False) -> dict[str, Any]:
    """Single Prometheus query target. `instant=True` for stat panels (last
    value, no series); default returns range for timeseries / barchart."""
    return {
        "refId": ref_id,
        "datasource": _prom_ref(),
        "expr": expr,
        "instant": instant,
        "range": not instant,
        "format": "time_series",
        "legendFormat": "{{label}}" if not instant else "",
    }


# ============================================================
# Vertical-aware tuning
# ============================================================

# Leaf-level business subtypes that can serve as a chart axis. Region
# and channel are intentionally listed too — they're often used as
# secondary axes, and for very-small KGs (single business_unit, etc.)
# they're the most useful primary axis. The order encodes the SE's
# preferred density of breakdowns: stores first (most fine-grained for
# retail), business_unit next (most fine-grained for non-retail), and
# so on. `_axes_for_plan` walks this order and picks whichever
# subtypes have actual entities.
_AXIS_PREFERENCE: tuple[str, ...] = (
    "store",
    "business_unit",
    "fulfillment_center",
    "brand",
    "product_line",
    "partner_program",
    "channel",
    "region",
)


def _hero_titles_from_plan(plan: DemoPlan) -> tuple[str, str]:
    """Pick the revenue + transactions hero-tile titles from the plan's
    Business Health dashboard spec.

    The planner LLM produces vertical-fit panel titles in
    `plan.dashboard_specs[].primary_panels[]` (e.g. "Bookings" for an
    airline, "Claims Processed" for healthcare, "Settlement Fail Rate"
    for capital-markets). This function pulls the two best titles for
    the universal hero tiles so the user sees the company's actual
    research-driven KPIs — no `business_model→title` Python override.

    Resolution order:
      1. Fuzzy-match the Business-audience primary_panels against a wide
         keyword set covering retail (revenue/orders), airline (bookings),
         healthcare (claims/encounters), SaaS (ARR/MRR/sessions),
         financial-services (settlement/payment/AUM), industrial
         (quote-to-cash, service contracts).
      2. Fall back to the FIRST two primary_panels verbatim (the LLM's
         pick of what the company would want to track). Always research-
         driven, never the airline-shows-"Total Orders" failure mode.
      3. Final fallback (no business spec at all): "Performance" /
         "Activity" — neutral language, never wrong-vertical.

    Always returns two distinct titles when possible.
    """
    # Keep these wide. They're a *fuzzy preference* — if nothing matches,
    # we fall back to the LLM's first-two primary panels, which are
    # already vertical-fit by construction.
    revenue_keywords = (
        "revenue", "income", "gmv", "sales", "premium",
        "billing", "arr", "mrr", "aum", "settlement", "quote",
        "energy savings", "cost savings", "monetiz",
    )
    txn_keywords = (
        "order", "booking", "transaction", "claim", "signup", "session",
        "subscription", "ticket", "appointment", "visit", "encounter",
        "shipment", "delivery", "request", "trade", "payment",
        "checkout", "conversion", "pnr", "dispatch", "work order",
        "service call", "install", "fulfillment", "sla",
    )

    business_spec = next(
        (s for s in plan.dashboard_specs
         if s.audience.value == "business"),
        None,
    )
    candidates: list[str] = list(business_spec.primary_panels) if business_spec else []
    cleaned = [_strip_leading_emoji(t) for t in candidates if t.strip()]

    def _pick(keywords: tuple[str, ...], skip: str | None = None) -> str | None:
        for title in cleaned:
            if title == skip:
                continue
            low = title.lower()
            if any(kw in low for kw in keywords):
                return title
        return None

    revenue = _pick(revenue_keywords)
    # Guarantee distinct hero titles: if the txn match would duplicate the
    # revenue title, look further down the list.
    txn = _pick(txn_keywords, skip=revenue)

    # Backfill from the LLM's primary_panels in order — the planner already
    # ranked them so the first two are the most-important KPIs for THIS
    # company. This is the path that fixes "airline showing Total Orders":
    # rather than an English fallback that's wrong for the vertical, we
    # use whatever the research said matters.
    fallbacks = [t for t in cleaned if t not in (revenue, txn)]
    if revenue is None:
        revenue = fallbacks.pop(0) if fallbacks else "Performance"
    if txn is None:
        txn = fallbacks.pop(0) if fallbacks else "Activity"

    return revenue, txn


def _strip_leading_emoji(s: str) -> str:
    """Drop a single leading non-alphanumeric run (emoji + spaces) so
    titles like '📈 Bookings' coming from the LLM don't end up rendered
    as '💰 📈 Bookings' when we prepend our own tile emoji."""
    i = 0
    while i < len(s) and not s[i].isalnum() and s[i] not in "-_":
        i += 1
    return s[i:].strip() or s.strip()


def _kg_subtype_counts(plan: DemoPlan) -> dict[str, int]:
    """Count business_subtype entities in the plan's KG. Used to drive
    every axis selection + panel-inclusion decision in this module —
    nothing here is hardcoded by business_model."""
    counts: dict[str, int] = {}
    for n in plan.knowledge_graph.nodes:
        if n.business_subtype:
            counts[n.business_subtype] = counts.get(n.business_subtype, 0) + 1
    return counts


def _axes_for_plan(plan: DemoPlan) -> dict[str, str]:
    """Pick primary + secondary chart axes from the plan's actual KG
    content. The primary axis is the densest leaf subtype in the KG
    (preferring stores → business_units → … per `_AXIS_PREFERENCE`);
    the secondary is the next densest non-degenerate axis. Nothing here
    inspects business_model — the planner LLM already used vertical
    awareness when picking what to put in the KG.

    Returns axes guaranteed to have ≥1 entity in the KG. If the KG is
    too thin to support a meaningful primary/secondary split (e.g. only
    one entity type), the secondary falls back to "region" or
    "channel" so cross-tab panels still produce something.
    """
    counts = _kg_subtype_counts(plan)
    populated = [s for s in _AXIS_PREFERENCE if counts.get(s, 0) >= 1]
    primary = populated[0] if populated else "channel"
    # Secondary: next-densest axis that ALSO has at least 2 entities
    # (no point splitting by a 1-element axis), and isn't the same as
    # primary. Falls back to region / channel even if those have only
    # one entity — empty timeseries are clearer than no timeseries.
    secondary_pref = [
        s for s in _AXIS_PREFERENCE
        if s != primary and counts.get(s, 0) >= 2
    ]
    secondary = (
        secondary_pref[0] if secondary_pref
        else ("region" if primary != "region" else "channel")
    )
    return {"primary": primary, "secondary": secondary}

_PRIMARY_LABELS: dict[str, str] = {
    "store":             "Store",
    "channel":           "Channel",
    "region":            "Region",
    "business_unit":     "Business Unit",
    "brand":             "Brand",
    "product_line":      "Product Line",
    "partner_program":   "Partner",
    "fulfillment_center": "Fulfillment Center",
}

_PRIMARY_PLURAL: dict[str, str] = {
    "store":             "Stores",
    "channel":           "Channels",
    "region":            "Regions",
    "business_unit":     "Business Units",
    "brand":             "Brands",
    "product_line":      "Product Lines",
    "partner_program":   "Partners",
    "fulfillment_center": "Fulfillment Centers",
}

_PRIMARY_EMOJI: dict[str, str] = {
    "store":             "🏪",
    "channel":           "📡",
    "region":            "🌍",
    "business_unit":     "🏛️",
    "brand":             "🏷️",
    "product_line":      "📦",
    "partner_program":   "🤝",
    "fulfillment_center": "📦",
}


def _label_for(axis: str) -> str:
    return _PRIMARY_LABELS.get(axis, axis.replace("_", " ").title())


def _plural_for(axis: str) -> str:
    return _PRIMARY_PLURAL.get(axis, _label_for(axis) + "s")


def _emoji_for(axis: str) -> str:
    return _PRIMARY_EMOJI.get(axis, "📊")


# ============================================================
# Panel builders
# ============================================================

def _grid_pos(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def _hero_banner_panel(
    panel_id: int, *,
    company_name: str, vertical: str, summary_facts: list[str],
) -> dict[str, Any]:
    """The wide HTML banner at the top of the dashboard. Mirrors the
    AcmeRetail-7c reference's gradient-bg banner with company name + a
    handful of summary facts (regions, stores, etc.). Uses inline styles
    because Grafana's text panel sandboxes scripts but allows inline
    style — same approach the reference uses."""
    facts_html = " &middot; ".join(summary_facts) or "Live telemetry"
    content = (
        '<div style="background: linear-gradient(135deg, #0a0e1a 0%, #0d1b35 50%, #0a2444 100%); '
        'padding: 22px 32px; border-radius: 8px; border-left: 6px solid #F5A623; '
        "font-family: 'Inter', 'Helvetica Neue', sans-serif; display: flex; "
        'align-items: center; justify-content: space-between; min-height: 72px;">'
        '<div>'
        '<div style="font-size: 30px; font-weight: 900; color: #FFFFFF; letter-spacing: 3px; '
        f'text-transform: uppercase; line-height: 1;">{company_name}</div>'
        '<div style="font-size: 15px; font-weight: 500; color: #90bdef; letter-spacing: 2px; '
        f'margin-top: 6px; text-transform: uppercase;">{vertical} Command Center</div>'
        '<div style="font-size: 11px; color: #557090; margin-top: 8px; '
        f'text-transform: uppercase; letter-spacing: 2px;">Project Clarion &middot; '
        f'Powered by Grafana Cloud &middot; Production &middot; {facts_html}</div>'
        '</div>'
        '<div style="text-align: right;">'
        '<div style="font-size: 13px; color: #48BB78; font-weight: 700; '
        'letter-spacing: 1px;">&#9679; LIVE TELEMETRY</div>'
        '<div style="font-size: 11px; color: #557090; margin-top: 6px;">Real-time business &amp; IT signals</div>'
        '<div style="font-size: 11px; color: #F5A623; margin-top: 4px; '
        'font-weight: 600;">Project Clarion Intelligence</div>'
        '</div>'
        '</div>'
    )
    return {
        "id": panel_id,
        "type": "text",
        "title": "",
        "gridPos": _grid_pos(0, 0, 24, 3),
        "options": {"mode": "html", "content": content},
    }


def _stat_panel(
    panel_id: int, *,
    title: str, expr: str, unit: str, color_steps: list[tuple[float, str]],
    description: str, x: int, y: int, w: int = 6, h: int = 4,
) -> dict[str, Any]:
    """Hero KPI tile — large background-filled stat with a single value.
    Color thresholds let us visually score health/volume against a band."""
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": _prom_ref(),
        "gridPos": _grid_pos(x, y, w, h),
        "targets": [_prom_target(expr, instant=True)],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "decimals": 0,
                "unit": unit,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": c, "value": v} for v, c in color_steps],
                },
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
        },
    }


def _gauge_panel(
    panel_id: int, *,
    title: str, expr: str, description: str, x: int, y: int, w: int = 6, h: int = 4,
) -> dict[str, Any]:
    """Health-score gauge tile — 0-100 with traffic-light thresholds."""
    return {
        "id": panel_id,
        "type": "gauge",
        "title": title,
        "description": description,
        "datasource": _prom_ref(),
        "gridPos": _grid_pos(x, y, w, h),
        "targets": [_prom_target(expr, instant=True)],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "min": 0, "max": 100, "unit": "percent",
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": 0},
                        {"color": "orange", "value": 70},
                        {"color": "green", "value": 90},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def _barchart_panel(
    panel_id: int, *,
    title: str, expr: str, description: str, x: int, y: int, w: int = 8, h: int = 8,
) -> dict[str, Any]:
    """Horizontal bar chart for "by-dimension" breakdowns (Revenue by Channel,
    Orders by Store, etc.). Sort descending so the biggest contributor is on top."""
    return {
        "id": panel_id,
        "type": "barchart",
        "title": title,
        "description": description,
        "datasource": _prom_ref(),
        "gridPos": _grid_pos(x, y, w, h),
        "targets": [_prom_target(expr, instant=True)],
        "fieldConfig": {"defaults": {"unit": "short", "color": {"mode": "palette-classic"}}, "overrides": []},
        "options": {
            "orientation": "horizontal",
            "showValue": "auto",
            "legend": {"showLegend": False},
        },
    }


def _piechart_panel(
    panel_id: int, *,
    title: str, expr: str, description: str, x: int, y: int, w: int = 8, h: int = 8,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "type": "piechart",
        "title": title,
        "description": description,
        "datasource": _prom_ref(),
        "gridPos": _grid_pos(x, y, w, h),
        "targets": [_prom_target(expr, instant=True)],
        "fieldConfig": {"defaults": {"unit": "short", "color": {"mode": "palette-classic"}}, "overrides": []},
        "options": {
            "displayLabels": ["name", "percent"],
            "legend": {"displayMode": "table", "placement": "right", "values": ["value"]},
            "pieType": "donut",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def _timeseries_panel(
    panel_id: int, *,
    title: str, expr: str, legend_format: str, description: str,
    x: int, y: int, w: int = 12, h: int = 8,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": _prom_ref(),
        "gridPos": _grid_pos(x, y, w, h),
        "targets": [{
            "refId": "A",
            "datasource": _prom_ref(),
            "expr": expr,
            "legendFormat": legend_format,
        }],
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 18,
                    "lineWidth": 2,
                    "pointSize": 5,
                    "showPoints": "never",
                    "spanNulls": True,
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def _kg_link_panel(panel_id: int, *, x: int, y: int) -> dict[str, Any]:
    """Bottom panel pointing to the live KG entity catalog. The reference
    dashboard had a screenshot panel; we just link to the live page so it
    always reflects current state."""
    href = "/a/grafana-asserts-app/entities?definitionId=1001"
    content = (
        '<div style="background: linear-gradient(135deg, #0a0e1a 0%, #1a2040 100%); '
        'padding: 20px; border-radius: 6px; text-align: center;">'
        '<div style="font-size: 14px; color: #F5A623; font-weight: 700; '
        'letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px;">'
        '🗳️ Knowledge Graph &middot; Entity Catalog</div>'
        '<div style="font-size: 12px; color: #90bdef; margin-bottom: 10px;">'
        'Topology view of every Customer, Region, Store, Service, Pod and Cluster '
        'this build emitted to Grafana Cloud.</div>'
        f'<a href="{href}" target="_blank" style="color: #48BB78; '
        'font-weight: 600; text-decoration: none; font-size: 13px;">'
        'Open the entity catalog &rarr;</a></div>'
    )
    return {
        "id": panel_id,
        "type": "text",
        "title": "",
        "gridPos": _grid_pos(x, y, 24, 4),
        "options": {"mode": "html", "content": content},
    }


# ============================================================
# Main builder
# ============================================================

def build_command_center_dashboard(
    plan: DemoPlan,
    *,
    customer: str | None = None,
    folder_uid: str | None = None,
) -> dict[str, Any]:
    """Build the single command-center dashboard for a plan.

    Returns a Grafana dashboard JSON dict in v39 schema, suitable for
    `wrap_for_push` and `POST /api/dashboards/db`. Caller is responsible
    for picking the customer slug to display in the hero banner — when
    omitted we derive from `plan.source_profile_id`.
    """
    plan_id = str(plan.plan_id)
    customer_slug = (customer or _customer_slug(plan)).lower()
    customer_display = customer_slug.replace("-", " ").title()
    business_model = _business_model(plan)
    vertical_label = _humanize_vertical(business_model)
    # Axes are chosen from the plan's actual KG content, not from a
    # hardcoded business_model→axis table. If the planner produced 6
    # stores and 2 channels for a retail demo, primary=store. If it
    # produced 9 business_units and 0 stores for an airline,
    # primary=business_unit. The vertical-aware planner prompt steered
    # WHAT entities exist; this code just visualizes them.
    axes = _axes_for_plan(plan)
    primary = axes["primary"]
    secondary = axes["secondary"]
    counts = _kg_subtype_counts(plan)
    # Helpers for "should I include this panel?" decisions below. A
    # panel is only emitted when its primary data axis has ≥1 entity
    # in the KG. Empty panels are dropped at build time rather than
    # rendered as blank timeseries.
    has_axis = lambda s: counts.get(s, 0) >= 1  # noqa: E731
    has_multiple = lambda s: counts.get(s, 0) >= 2  # noqa: E731

    # Customer-scoped filter for every Prometheus query — avoids cross-
    # customer leakage when multiple plans coexist in the same stack.
    cust_filter = f'clarion_customer="{customer_slug}"'
    pri = primary
    sec = secondary

    summary_facts = _summary_facts(plan)

    panels: list[dict[str, Any]] = []
    pid = 1

    # ── Hero banner (full-width, top) ──
    panels.append(_hero_banner_panel(
        pid,
        company_name=f"🏷️ {customer_display}",
        vertical=f"{vertical_label} Business & Technology",
        summary_facts=summary_facts,
    ))
    pid += 1

    # ── Row 1: Hero KPI stat tiles (4 wide × 4 tall, side-by-side) ──
    # Tile titles come from the planner's research-driven Business Health
    # dashboard_spec — for an airline that's "Bookings" / "Load Factor",
    # for healthcare it's "Claims Processed" / "Member Acquisition", etc.
    # Underlying metrics stay universal (revenue + orders counters); the
    # title reflects what the company actually CALLS those quantities.
    revenue_title, orders_title = _hero_titles_from_plan(plan)
    y = 3
    panels.append(_stat_panel(
        pid, x=0, y=y, w=6, h=4,
        title=f"💰 {revenue_title}",
        description=f"{revenue_title} across all channels and "
                    f"{_plural_for(primary).lower()} in the selected window.",
        expr=f'sum(increase(clarion_customer_revenue_usd_total{{{cust_filter}}}[$__range]))',
        unit="currencyUSD",
        color_steps=[(0, "#1a365d"), (10000, "#2b6cb0"), (100000, "#48bb78")],
    )); pid += 1
    panels.append(_stat_panel(
        pid, x=6, y=y, w=6, h=4,
        title=f"📦 {orders_title}",
        description=f"{orders_title} volume in the selected window.",
        expr=f'sum(increase(clarion_customer_orders_total{{{cust_filter}}}[$__range]))',
        unit="short",
        color_steps=[(0, "#2c5282"), (100, "#3182ce"), (1000, "#48bb78")],
    )); pid += 1
    # Pre-resolve human-readable names for primary + secondary so titles
    # and tooltips read naturally regardless of vertical.
    primary_label  = _label_for(primary)
    primary_plural = _plural_for(primary)
    primary_emoji  = _emoji_for(primary)
    sec_label      = _label_for(sec)
    sec_plural     = _plural_for(sec)
    # Primary-axis stat tile: only emit when the KG actually has primary
    # entities. Otherwise the tile would show 0 forever (e.g. "Active
    # Stores: 0" on an airline) — better to skip and let the platform
    # health gauge widen.
    if has_axis(primary):
        panels.append(_stat_panel(
            pid, x=12, y=y, w=6, h=4,
            title=f"{primary_emoji} Active {primary_plural}",
            description=f"Live count of active {primary_label} entities reporting telemetry.",
            # Counts `clarion_entity_info` series filtered to the primary
            # axis label — works for any subtype, no business_model branch.
            expr=(
                f'count(count by (clarion_{primary}_id) ('
                f'clarion_entity_info{{{cust_filter}, clarion_{primary}_id!=""}}))'
            ),
            unit="short",
            color_steps=[(0, "#742a2a"), (1, "#dd6b20"), (3, "#48bb78")],
        )); pid += 1
        gauge_x = 18
    else:
        # No primary entities → push the gauge into its slot so the row
        # still aligns at 24-col width.
        gauge_x = 12
    panels.append(_gauge_panel(
        pid, x=gauge_x, y=y, w=6 if gauge_x == 18 else 12, h=4,
        title="💚 Platform Health",
        description="Composite health score (0-100) computed from error rate, "
                    "latency, and saturation across all monitored services.",
        expr=f'avg(clarion_customer_health_score{{{cust_filter}}})',
    )); pid += 1

    # ── Row 2: Mid-section breakdowns ──
    # Each panel only emits if its `group by` axis has ≥1 entity in the KG.
    # We re-flow the y-coordinate so dropped panels don't leave gaps.
    y = 7
    row2_panels: list[dict[str, Any]] = []
    if has_axis(secondary):
        row2_panels.append(_barchart_panel(
            pid, x=0, y=y, w=8, h=8,
            title=f"📊 Revenue by {sec_label}",
            description=f"Revenue split by {sec_label.lower()}, sorted descending so the top contributor leads.",
            expr=(
                f'sort_desc(sum by({sec}) (increase('
                f'clarion_business_revenue_usd_total{{{cust_filter}}}[$__range])))'
            ),
        )); pid += 1
    if has_axis("region"):
        row2_panels.append(_piechart_panel(
            pid, x=0, y=y, w=8, h=8,
            title="🌍 Revenue by Region",
            description="Donut split of revenue by geographic region.",
            expr=f'sum by(region) (increase(clarion_business_revenue_usd_total{{{cust_filter}}}[$__range]))',
        )); pid += 1
    if has_axis(primary):
        row2_panels.append(_timeseries_panel(
            pid, x=0, y=y, w=8, h=8,
            title=f"📈 Revenue Trend by {primary_label}",
            description=f"Revenue rate over time, broken out by {primary_label.lower()}.",
            expr=f'sum by({primary}) (rate(clarion_business_revenue_usd_total{{{cust_filter}}}[$__rate_interval]))',
            legend_format=f"{{{{{primary}}}}}",
        )); pid += 1
    # Re-flow x positions so the row uses available width evenly.
    if row2_panels:
        per_panel_w = 24 // len(row2_panels)
        for i, p in enumerate(row2_panels):
            p["gridPos"]["x"] = i * per_panel_w
            p["gridPos"]["w"] = per_panel_w
        panels.extend(row2_panels)

    # ── Row 3: Primary-axis drill (Orders/Revenue per primary entity) ──
    # Only emit when there are at least 2 primary entities — a single
    # bar chart with one bar is uninformative.
    y = 15 if row2_panels else 7
    if has_multiple(primary):
        panels.append(_barchart_panel(
            pid, x=0, y=y, w=12, h=8,
            title=f"📮 Orders by {primary_label}",
            description=f"Order volume per {primary_label.lower()}, sorted descending.",
            expr=(
                f'sort_desc(sum by({primary}) (increase('
                f'clarion_business_orders_total{{{cust_filter}}}[$__range])))'
            ),
        )); pid += 1
        panels.append(_barchart_panel(
            pid, x=12, y=y, w=12, h=8,
            title=f"💵 Revenue by {primary_label}",
            description=f"Revenue per {primary_label.lower()}, sorted descending.",
            expr=(
                f'sort_desc(sum by({primary}) (increase('
                f'clarion_business_revenue_usd_total{{{cust_filter}}}[$__range])))'
            ),
        )); pid += 1
        y += 8

    # ── Row 4: Region trend (full-width timeseries) ──
    # Only emit when there are at least 2 regions — single-region
    # companies don't get useful insight from a region-split timeseries.
    if has_multiple("region"):
        panels.append(_timeseries_panel(
            pid, x=0, y=y, w=24, h=8,
            title="🌎 Revenue Trend by Region",
            description="Revenue/sec rate across regions over time. Useful for "
                        "spotting regional incident impact or campaign lift.",
            expr=f'sum by(region) (rate(clarion_business_revenue_usd_total{{{cust_filter}}}[$__rate_interval]))',
            legend_format="{{region}}",
        )); pid += 1
        y += 8

    # ── Industrial-ops section: OEE + plant geomap ──
    # Only renders when the KG carries plant business_units (detected by
    # `bu-plant-` node_id prefix from the planner's b2b_industrial output
    # OR explicit latitude attribute on a business_unit). Skips silently
    # for retail / healthcare / SaaS demos where these panels would be
    # meaningless.
    plant_nodes = [
        n for n in plan.knowledge_graph.nodes
        if n.business_subtype == "business_unit"
        and (n.node_id.startswith("bu-plant-")
             or n.attributes.get("latitude") is not None)
    ]
    if plant_nodes:
        # Section divider — a row header so SEs / Eric see where the
        # industrial-ops story starts on the dashboard.
        panels.append({
            "id": pid,
            "type": "row",
            "title": "🏭 Plant Operations — OEE & Global Footprint",
            "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
            "panels": [],
        }); pid += 1
        y += 1

        # OEE headline gauge — left tile, single value across all plants.
        oee_expr = (
            'avg('
            f'clarion_plant_availability_ratio{{{cust_filter}}}'
            f' * clarion_plant_performance_ratio{{{cust_filter}}}'
            f' * clarion_plant_quality_ratio{{{cust_filter}}}'
            ')'
        )
        panels.append(_gauge_panel(
            pid, x=0, y=y, w=6, h=8,
            title="🎯 Overall Equipment Effectiveness",
            description="OEE = Availability × Performance × Quality. World-class "
                        "manufacturing threshold is 85%. Sourced from synthetic "
                        "feeders emitted per plant + line.",
            expr=oee_expr,
        )); pid += 1

        # OEE trend by plant — right tile, timeseries.
        panels.append(_timeseries_panel(
            pid, x=6, y=y, w=12, h=8,
            title="📈 OEE Trend by Plant",
            description="OEE components multiplied per plant, averaged across "
                        "production lines. Each line is one of the named "
                        "facilities (St. Paul, Oakdale, Nanjing, Shirwal, Costa Rica).",
            expr=(
                'avg by (plant) ('
                f'clarion_plant_availability_ratio{{{cust_filter}}}'
                f' * clarion_plant_performance_ratio{{{cust_filter}}}'
                f' * clarion_plant_quality_ratio{{{cust_filter}}}'
                ')'
            ),
            legend_format="{{plant}}",
        )); pid += 1

        # Defect rate by plant — far right, the "quality story" tile.
        panels.append(_timeseries_panel(
            pid, x=18, y=y, w=6, h=8,
            title="🚨 Defect Rate by Plant",
            description="1 − quality_ratio per plant. Spikes correlate with "
                        "incoming raw-material lot variance or operator changeover.",
            expr=f'avg by (plant) (1 - clarion_plant_quality_ratio{{{cust_filter}}})',
            legend_format="{{plant}}",
        )); pid += 1
        y += 8

        # Geomap — full-width, 8 tall, capping the industrial section.
        panels.append({
            "id": pid,
            "type": "geomap",
            "title": "🌍 Global Plant Footprint",
            "description": f"All {customer_display} manufacturing facilities with live "
                           "telemetry. Marker location derived from clarion_latitude "
                           "/ clarion_longitude labels on the plant business_unit "
                           "entity. Click a marker to drill into per-plant OEE.",
            "datasource": _prom_ref(),
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 8},
            "targets": [_prom_target(
                f'last_over_time(clarion_entity_info{{{cust_filter},'
                f' clarion_entity_kind="business_unit", clarion_latitude!=""}}[5m])',
                instant=True,
            )],
            "fieldConfig": {
                "defaults": {"custom": {"hideFrom": {"viz": False, "legend": False}}},
                "overrides": [],
            },
            "options": {
                "view": {"id": "zero", "lat": 20, "lon": 30, "zoom": 1.6},
                "controls": {"showZoom": True, "showAttribution": True},
                "basemap": {"type": "default"},
                "layers": [{
                    "type": "markers",
                    "name": "Facilities",
                    "config": {
                        "showLegend": True,
                        "style": {"size": {"fixed": 10}, "color": {"fixed": "#2b6cb0"}},
                    },
                    "location": {
                        "mode": "coords",
                        "latitude": "clarion_latitude",
                        "longitude": "clarion_longitude",
                    },
                }],
            },
        }); pid += 1
        y += 8

    # ── Final row: KG entity catalog link card ──
    panels.append(_kg_link_panel(pid, x=0, y=y)); pid += 1

    # Log the final shape so SEs can audit what got built (and what got
    # dropped) for a given plan without inspecting the raw JSON.
    import structlog
    structlog.get_logger().info(
        "command_center.dashboard_built",
        plan_id=plan_id,
        customer=customer_slug,
        primary_axis=primary,
        secondary_axis=secondary,
        panel_count=len(panels),
        kg_subtype_counts=counts,
    )

    return {
        "uid": f"clarion-cc-{plan_id[:12]}",
        "title": f"{customer_display} | Business Command Center",
        "description": (
            f"Project Clarion — {customer_display} business intelligence and "
            f"technology landscape. Powered by Grafana Cloud."
        ),
        "tags": [
            "proj-clarion", "command-center",
            f"plan:{plan_id[:8]}", f"customer:{customer_slug}",
            f"vertical:{business_model}",
        ],
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "timezone": "browser",
        "panels": panels,
        "templating": {"list": []},
        "annotations": {"list": []},
    }


# ============================================================
# Helpers
# ============================================================

def _customer_slug(plan: DemoPlan) -> str:
    pid = (plan.source_profile_id or "").strip()
    if pid.startswith("prof-"):
        pid = pid[len("prof-"):]
    return pid.strip("-").lower() or "clarion"


def _business_model(plan: DemoPlan) -> str:
    """Lookup the business_model from the plan's source profile. Returns
    "other" on any failure so the template still produces a valid (if
    generic) dashboard."""
    try:
        from proj_clarion.storage import ProfileRepo, session_scope
        with session_scope() as s:
            profile = ProfileRepo().get(s, plan.source_profile_id)
        if profile is None:
            return "other"
        return profile.industry_taxonomy.business_model.value
    except Exception:  # noqa: BLE001 — never fail dashboard build on metadata
        return "other"


def _humanize_vertical(business_model: str) -> str:
    return {
        "b2c_retail":         "Retail",
        "omnichannel_retail": "Omnichannel Retail",
        "b2b_saas":           "B2B SaaS",
        "manufacturing":      "Industrial",
        "logistics":          "Logistics",
        "healthcare":         "Healthcare",
        "financial_services": "Financial Services",
        "media_content":      "Media",
        "marketplace_multi_sided": "Marketplace",
        "b2c_digital":        "Digital",
        "b2b_direct":         "B2B Direct",
        "other":              "Business",
    }.get(business_model, "Business")


def _summary_facts(plan: DemoPlan) -> list[str]:
    """Build the small summary facts shown in the hero banner footer
    (e.g. '2 Regions · 4 Stores · 185 Pods'). Counts come from the
    plan's KG so they reflect what was modeled, not what's in Mimir
    (which would tie the dashboard to live state)."""
    from collections import Counter

    nodes = plan.knowledge_graph.nodes
    counts = Counter()
    for n in nodes:
        if n.business_subtype:
            counts[n.business_subtype] += 1
        elif n.technical_subtype:
            counts[n.technical_subtype] += 1
    facts: list[str] = []
    pluralize = {"region": "Regions", "channel": "Channels", "store": "Stores",
                 "fulfillment_center": "DCs", "business_unit": "Business Units",
                 "brand": "Brands", "service": "Services"}
    for kind, label in pluralize.items():
        if counts[kind]:
            facts.append(f"{counts[kind]} {label}")
    return facts[:5]
