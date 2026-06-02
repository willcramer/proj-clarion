/**
 * Discovery Brief exporter — turns a researched CompanyProfile into a
 * polished, self-contained deliverable an SE can hand to an AE for a
 * discovery call, share with the account team, feed to another AI
 * assistant, or use for pipeline-growth work.
 *
 * Three outputs, all derived from the same profile:
 *   - buildDiscoveryBriefHtml(p)     → self-contained, light/printable HTML
 *                                       (the on-page preview + the PDF).
 *   - buildDiscoveryBriefMarkdown(p) → clean Markdown/plain text for the
 *                                       Copy button (paste into an AI chat,
 *                                       meeting notes, a CRM, etc.).
 *   - downloadDiscoveryBrief(p)      → save the HTML as a file.
 *
 * Deliberately free of app chrome (no "demo running", no live controls):
 * this is for someone who isn't using Clarion. Self-contained inline CSS so
 * it opens/prints cleanly anywhere.
 */

// Loose shape — mirrors the fields of the profile JSON we render. All
// optional; we render whatever is present (a profile may predate a field).
export interface BriefProfile {
  profile_id?: string;
  generated_at?: string;
  research_model?: string;
  company?: {
    name?: string; legal_name?: string; primary_url?: string;
    headquarters_city?: string; headquarters_country?: string;
    founded_year?: number; ownership_type?: string; employee_count_estimate?: number;
  };
  industry_taxonomy?: { primary_industry?: string; business_model?: string; sub_industries?: string[] };
  revenue_signals?: {
    annual_revenue_usd?: number; revenue_year?: number;
    growth_direction?: string; disclosed_segments?: string[];
  };
  geographic_footprint?: {
    countries?: string[]; regions?: string[]; flagship_locations?: string[];
    languages?: string[]; currencies?: string[];
  };
  channels?: Array<{ channel_type?: string; name?: string; description?: string; notable_partners?: string[] }>;
  business_entity_candidates?: Array<{ entity_type?: string; name?: string; description?: string }>;
  recent_strategic_priorities?: Array<{ priority?: string; timeframe?: string }>;
  pain_signals?: Array<{
    pain?: string; severity?: string; evidence_quote?: string;
    relevance_to_observability?: string;
  }>;
  tech_stack_signals?: Array<{ component_type?: string; vendor_or_product?: string; confidence?: string }>;
  incumbent_observability?: Array<{ vendor?: string; scope?: string; confidence?: string }>;
  agentic_signals?: Array<{ workload_type?: string; description?: string; status?: string }>;
  synthesized_flags?: Array<{ field_path?: string; claim?: string; rationale?: string }>;
  provenance?: Array<{ citation_id?: string; url?: string; title?: string }>;
}

const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] as string));

const titleCase = (s?: string): string =>
  String(s ?? "").replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());

function hostOf(url?: string): string {
  if (!url) return "";
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

function headcount(n?: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1_000) return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return n.toLocaleString();
}

function money(n?: number): string {
  if (!n) return "";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  return `$${n.toLocaleString()}`;
}

const sevRank: Record<string, number> = { high: 0, medium: 1, low: 2 };

// ── Inline Clarion brand mark (kept inline so the deliverable is fully
//    self-contained — no imports, no external assets). ───────────────────
const CLARION_MARK =
  `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12.5" r="8.3" opacity=".35"></circle><path d="M12 4.2A8.3 8.3 0 0 1 20.3 12.5"></path><path d="M8.6 11.8 12 8.4 15.4 11.8"></path><circle cx="12" cy="14.4" r="2.3" fill="currentColor" stroke="none"></circle></svg>`;

/** Derived, shared by HTML + Markdown so both stay in lockstep. */
function derive(p: BriefProfile) {
  const co = p.company ?? {};
  const tax = p.industry_taxonomy ?? {};
  const geo = p.geographic_footprint ?? {};
  const rev = p.revenue_signals ?? {};
  const name = co.name || hostOf(co.primary_url) || "Company";
  const host = hostOf(co.primary_url);

  const pains = [...(p.pain_signals ?? [])].sort(
    (a, b) => (sevRank[a.severity ?? ""] ?? 3) - (sevRank[b.severity ?? ""] ?? 3),
  );
  const revenue = rev.annual_revenue_usd
    ? `${money(rev.annual_revenue_usd)}${rev.revenue_year ? ` (${rev.revenue_year})` : ""}${rev.growth_direction && rev.growth_direction !== "unknown" ? ` · ${rev.growth_direction}` : ""}`
    : "";

  const snapshot = ([
    ["Industry", tax.primary_industry ?? ""],
    ["Business model", titleCase(tax.business_model)],
    ["Revenue", revenue],
    ["Headquarters", [co.headquarters_city, co.headquarters_country].filter(Boolean).join(", ")],
    ["Founded", co.founded_year ? String(co.founded_year) : ""],
    ["Ownership", titleCase(co.ownership_type)],
    ["Headcount", co.employee_count_estimate ? `~${headcount(co.employee_count_estimate)}` : ""],
    ["Footprint", (geo.countries ?? []).slice(0, 6).join(", ")],
  ] as [string, string][]).filter(([, v]) => v);

  const questions = pains.slice(0, 4).map((x) =>
    `How does ${name} detect, measure, and respond to ${x.pain} today — and what does it cost when it slips?`);

  return {
    co, tax, geo, name, host,
    pains,
    priorities: p.recent_strategic_priorities ?? [],
    channels: p.channels ?? [],
    tech: p.tech_stack_signals ?? [],
    incumbents: p.incumbent_observability ?? [],
    agentic: p.agentic_signals ?? [],
    entities: p.business_entity_candidates ?? [],
    flags: p.synthesized_flags ?? [],
    sources: p.provenance ?? [],
    snapshot, questions,
    generated: new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }),
  };
}

export function buildDiscoveryBriefHtml(p: BriefProfile): string {
  const d = derive(p);
  const { name, host, tax } = d;

  const sevBadge = (s?: string) => {
    const tone = s === "high" ? "#b91c1c" : s === "medium" ? "#b45309" : "#64748b";
    const bg = s === "high" ? "#fee2e2" : s === "medium" ? "#fef3c7" : "#f1f5f9";
    return `<span class="badge" style="color:${tone};background:${bg}">${esc(s ?? "—")}</span>`;
  };

  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(name)} — Discovery Brief</title>
<style>
  :root { --ink:#0f172a; --muted:#475569; --faint:#94a3b8; --line:#e2e8f0; --accent:#0d9488; --wash:#f0fdfa; }
  * { box-sizing: border-box; }
  body { margin:0; background:#eef2f6; color:var(--ink);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif; }
  .page { max-width:840px; margin:32px auto; background:#fff; border:1px solid var(--line);
    border-radius:16px; padding:40px 48px 36px; box-shadow:0 10px 40px -16px rgba(15,23,42,.22); }
  .brand { display:flex; align-items:center; gap:9px; color:var(--accent); }
  .mark { width:30px;height:30px;border-radius:8px;display:grid;place-items:center;
    background:linear-gradient(135deg,#14b8a6,#0d9488);color:#fff; }
  .brand .eyebrow { font:600 11px/1 ui-monospace,monospace; letter-spacing:.13em; text-transform:uppercase; }
  .brand .tag { margin-left:auto; font:600 9.5px/1 ui-monospace,monospace; letter-spacing:.1em; text-transform:uppercase; color:var(--faint); }
  h1 { font-size:30px; letter-spacing:-.02em; margin:18px 0 4px; }
  .sub { color:var(--muted); font-size:14px; }
  .sub a { color:var(--accent); text-decoration:none; }
  h2 { font-size:13px; letter-spacing:.08em; text-transform:uppercase; color:var(--faint);
    margin:32px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
  .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:14px 28px; }
  .cell .k { font:600 10px/1 ui-monospace,monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--faint); }
  .cell .v { font-size:15px; margin-top:3px; }
  ul { margin:0; padding-left:20px; } li { margin:6px 0; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th { text-align:left; font:600 10px/1 ui-monospace,monospace; letter-spacing:.06em; text-transform:uppercase;
    color:var(--faint); padding:0 0 8px; border-bottom:1px solid var(--line); }
  td { padding:9px 0; border-bottom:1px solid var(--line); vertical-align:top; }
  td .d { color:var(--muted); font-size:13px; margin-top:2px; }
  .badge { display:inline-block; font:600 11px/1 ui-monospace,monospace; padding:3px 7px; border-radius:6px; text-transform:uppercase; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { font:600 11px/1.4 ui-monospace,monospace; padding:4px 9px; border-radius:999px; background:#f1f5f9; color:var(--muted); }
  .note { background:var(--wash); border:1px solid #99f6e4; color:#0f766e; border-radius:10px; padding:12px 14px; font-size:13.5px; margin-top:12px; }
  .verify li { color:var(--muted); }
  .quote { color:var(--muted); font-style:italic; }
  .foot { margin-top:34px; padding-top:14px; border-top:1px solid var(--line); color:var(--faint); font-size:12px; }
  @media print { body { background:#fff; } .page { box-shadow:none; border:0; margin:0; border-radius:0; max-width:none; padding:0; } }
</style></head><body><div class="page">

  <div class="brand">
    <span class="mark">${CLARION_MARK}</span>
    <span class="eyebrow">Discovery Brief</span>
    <span class="tag">Proj-Clarion research</span>
  </div>

  <h1>${esc(name)}</h1>
  <div class="sub">${[host ? `<a href="https://${esc(host)}">${esc(host)}</a>` : "", esc(tax.primary_industry ?? ""), esc(titleCase(tax.business_model))].filter(Boolean).join(" · ")}</div>

  ${d.snapshot.length ? `<h2>At a glance</h2><div class="grid">${d.snapshot.map(([k, v]) =>
    `<div class="cell"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>` : ""}

  ${d.priorities.length ? `<h2>Why now — strategic priorities</h2><ul>${d.priorities
    .map((x) => `<li>${esc(x.priority)}</li>`).join("")}</ul>` : ""}

  ${d.pains.length ? `<h2>Pain signals to probe</h2>
  <table><thead><tr><th>Signal</th><th style="width:90px">Severity</th></tr></thead><tbody>${d.pains
    .map((x) => `<tr><td>${esc(x.pain)}${x.evidence_quote ? `<div class="d quote">“${esc(x.evidence_quote)}”</div>` : ""}</td><td>${sevBadge(x.severity)}</td></tr>`).join("")}</tbody></table>
  ${d.questions.length ? `<div class="note"><strong>Discovery questions to open with:</strong><ul style="margin-top:8px">${
    d.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></div>` : ""}` : ""}

  ${d.channels.length ? `<h2>Go-to-market channels</h2>
  <table><tbody>${d.channels.map((c) =>
    `<tr><td style="width:34%"><strong>${esc(c.name || titleCase(c.channel_type))}</strong><div class="d">${esc(titleCase(c.channel_type))}</div></td><td>${esc(c.description ?? "")}${c.notable_partners?.length ? `<div class="d">Partners: ${esc(c.notable_partners.join(", "))}</div>` : ""}</td></tr>`).join("")}</tbody></table>` : ""}

  ${d.tech.length ? `<h2>Tech &amp; observability footprint</h2>
  <table><thead><tr><th>Area</th><th>Vendor / product</th><th style="width:90px">Confidence</th></tr></thead><tbody>${d.tech
    .map((t) => `<tr><td>${esc(titleCase(t.component_type))}</td><td>${esc(t.vendor_or_product)}</td><td>${esc(t.confidence ?? "—")}</td></tr>`).join("")}</tbody></table>
  ${d.incumbents.length ? `<div class="note"><strong>Incumbent observability:</strong> ${d.incumbents
    .map((i) => `${esc(i.vendor)}${i.scope ? ` (${esc(i.scope)})` : ""}`).join("; ")} — a consolidation + cost angle: position unified metrics, logs, and traces in one stack.</div>`
    : `<div class="note">Where an incumbent monitoring tool appears, that's a consolidation + cost angle — position unified metrics, logs, and traces in one stack.</div>`}` : ""}

  ${d.agentic.length ? `<h2>AI / agentic workloads</h2>
  <table><tbody>${d.agentic.map((a) =>
    `<tr><td style="width:34%"><strong>${esc(titleCase(a.workload_type))}</strong><div class="d">${esc(titleCase(a.status))}</div></td><td>${esc(a.description ?? "")}</td></tr>`).join("")}</tbody></table>` : ""}

  ${d.entities.length ? `<h2>Candidate business entities</h2>
  <div class="sub" style="margin-bottom:10px">How the business would be modelled as first-class entities in Grafana.</div>
  <div class="chips">${d.entities.map((e) =>
    `<span class="chip">${esc(e.name || titleCase(e.entity_type))}${e.entity_type ? ` · ${esc(titleCase(e.entity_type))}` : ""}</span>`).join("")}</div>` : ""}

  ${d.flags.length ? `<h2>Verify before quoting</h2>
  <div class="sub" style="margin-bottom:8px">Synthesised without a direct source — confirm before using in front of the customer.</div>
  <ul class="verify">${d.flags
    .map((f) => `<li><strong>${esc(f.claim || f.field_path)}</strong>${f.rationale ? ` — ${esc(f.rationale)}` : ""}</li>`).join("")}</ul>` : ""}

  <div class="foot">Generated ${esc(d.generated)} · Proj-Clarion · synthesised from ${d.sources.length || "public"} public source${d.sources.length === 1 ? "" : "s"} — verify flagged items before quoting figures.</div>
</div></body></html>`;
}

/** Clean Markdown / plain text for the Copy button. Built to paste straight
 *  into another AI assistant, meeting notes, or a CRM — no HTML, no chrome. */
export function buildDiscoveryBriefMarkdown(p: BriefProfile): string {
  const d = derive(p);
  const L: string[] = [];
  L.push(`# ${d.name} — discovery brief`);
  const subline = [d.host, d.tax.primary_industry, titleCase(d.tax.business_model)].filter(Boolean).join(" · ");
  if (subline) L.push(`_${subline}_`);
  L.push("");

  if (d.snapshot.length) {
    L.push("## At a glance");
    for (const [k, v] of d.snapshot) L.push(`- **${k}:** ${v}`);
    L.push("");
  }
  if (d.priorities.length) {
    L.push("## Why now — strategic priorities");
    for (const x of d.priorities) if (x.priority) L.push(`- ${x.priority}`);
    L.push("");
  }
  if (d.pains.length) {
    L.push("## Pain signals to probe");
    for (const x of d.pains) {
      const sev = x.severity ? `[${x.severity.toUpperCase()}] ` : "";
      const q = x.evidence_quote ? ` — “${x.evidence_quote}”` : "";
      L.push(`- ${sev}${x.pain ?? ""}${q}`);
    }
    L.push("");
    if (d.questions.length) {
      L.push("### Discovery questions");
      d.questions.forEach((q, i) => L.push(`${i + 1}. ${q}`));
      L.push("");
    }
  }
  if (d.channels.length) {
    L.push("## Go-to-market channels");
    for (const c of d.channels) {
      const partners = c.notable_partners?.length ? ` (partners: ${c.notable_partners.join(", ")})` : "";
      L.push(`- **${c.name || titleCase(c.channel_type)}** — ${c.description ?? ""}${partners}`);
    }
    L.push("");
  }
  if (d.tech.length) {
    L.push("## Tech & observability footprint");
    for (const t of d.tech) L.push(`- ${titleCase(t.component_type)}: ${t.vendor_or_product}${t.confidence ? ` (${t.confidence} confidence)` : ""}`);
    if (d.incumbents.length) {
      L.push(`- Incumbent observability: ${d.incumbents.map((i) => `${i.vendor}${i.scope ? ` (${i.scope})` : ""}`).join("; ")}`);
    }
    L.push("");
  }
  if (d.agentic.length) {
    L.push("## AI / agentic workloads");
    for (const a of d.agentic) L.push(`- ${titleCase(a.workload_type)} (${a.status}): ${a.description ?? ""}`);
    L.push("");
  }
  if (d.entities.length) {
    L.push("## Candidate business entities (for KG modelling)");
    for (const e of d.entities) L.push(`- ${e.name || titleCase(e.entity_type)}${e.entity_type ? ` (${titleCase(e.entity_type)})` : ""}${e.description ? ` — ${e.description}` : ""}`);
    L.push("");
  }
  if (d.flags.length) {
    L.push("## Verify before quoting (synthesised, unsourced)");
    for (const f of d.flags) L.push(`- ${f.claim || f.field_path}${f.rationale ? ` — ${f.rationale}` : ""}`);
    L.push("");
  }
  L.push("---");
  L.push(`Generated ${d.generated} by Proj-Clarion from ${d.sources.length || "public"} public source${d.sources.length === 1 ? "" : "s"}. Verify flagged items before quoting figures.`);
  return L.join("\n");
}

/** Print the deliverable → "Save as PDF" WITHOUT opening a new window (so
 *  pop-up blockers never interfere). Prints the on-page preview iframe if
 *  passed; otherwise mounts a hidden off-screen iframe from `html`. */
export function printDeliverable(target?: HTMLIFrameElement | null, html?: string): void {
  const fire = (w: Window | null | undefined) => {
    if (!w) return;
    try { w.focus(); } catch { /* ignore */ }
    w.print();
  };
  if (target?.contentWindow) { fire(target.contentWindow); return; }
  if (!html) return;
  const iframe = document.createElement("iframe");
  Object.assign(iframe.style, {
    position: "fixed", left: "-10000px", top: "0",
    width: "840px", height: "1100px", border: "0", opacity: "0",
  } as CSSStyleDeclaration);
  iframe.setAttribute("aria-hidden", "true");
  iframe.onload = () => {
    fire(iframe.contentWindow);
    window.setTimeout(() => iframe.remove(), 60_000);
  };
  iframe.srcdoc = html;
  document.body.appendChild(iframe);
}

/** Build the brief and trigger a browser download as a self-contained .html. */
export function downloadDiscoveryBrief(p: BriefProfile): void {
  const name = p.company?.name || hostOf(p.company?.primary_url) || "company";
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 48) || "company";
  const blob = new Blob([buildDiscoveryBriefHtml(p)], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug}-discovery-brief.html`;
  a.click();
  URL.revokeObjectURL(url);
}
