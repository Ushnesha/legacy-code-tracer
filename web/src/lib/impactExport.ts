import type { AffectedFn, FunctionDetail, ImpactResponse } from "../api";
import { hours, SEVERITY_WORD } from "./severity";

export type ExportFormat = "csv" | "html" | "json";

export type Deps = {
  calls: string[];
  may_call: string[];
  called_by: string[];
  fields_read: string[];
  fields_written: string[];
};

export type ReportFn = {
  id: string;
  name: string;
  qualified_name: string;
  file: string;
  line_start: number;
  line_end: number;
  /** "Changed function" for the function being changed, otherwise the number of calls away. */
  distance: number;
  severity: string;
  severity_reason: string;
  effort_hours: number;
  how: string[];
  summary: string | null;
  code: string | null;
  dependencies: Deps;
};

export type Report = {
  generated_at: string;
  repo: string;
  changed: ReportFn;
  severity: string;
  severity_reason: string;
  business_impact: string | null;
  effort: { change: number; checks: number; low: number; high: number };
  affected: ReportFn[];
};

const names = (xs: { qualified_name: string }[]) => xs.map((x) => x.qualified_name);

export function depsOf(d: FunctionDetail | undefined): Deps {
  return {
    calls: names(d?.callees.calls ?? []),
    may_call: names(d?.callees.may_call ?? []),
    called_by: names(d?.callers ?? []),
    fields_read: names(d?.fields_read ?? []),
    fields_written: names(d?.fields_written ?? []),
  };
}

const codeOf = (d: FunctionDetail | undefined) => (d?.source.available ? d.source.code : null);

/** Joins the impact answer with each function's code and connections into one flat report. */
export function buildReport(repo: string, impact: ImpactResponse, details: Map<string, FunctionDetail>, now = new Date()): Report {
  const r = impact.root;
  const rootDetail = details.get(r.id);
  const one = (a: AffectedFn): ReportFn => ({
    id: a.id,
    name: a.name,
    qualified_name: a.qualified_name,
    file: a.file,
    line_start: a.line_start,
    line_end: a.line_end,
    distance: a.distance,
    severity: a.severity,
    severity_reason: a.severity_reason,
    effort_hours: a.effort_hours,
    how: a.how,
    summary: a.summary,
    code: codeOf(details.get(a.id)),
    dependencies: depsOf(details.get(a.id)),
  });
  return {
    generated_at: now.toISOString(),
    repo,
    changed: {
      id: r.id,
      name: r.name,
      qualified_name: r.qualified_name,
      file: r.file,
      line_start: r.line_start,
      line_end: r.line_end,
      distance: 0,
      severity: impact.severity,
      severity_reason: impact.severity_reason,
      effort_hours: impact.effort.change,
      how: [],
      summary: rootDetail?.summary?.short ?? null,
      code: codeOf(rootDetail),
      dependencies: depsOf(rootDetail),
    },
    severity: impact.severity,
    severity_reason: impact.severity_reason,
    business_impact: impact.business_impact,
    effort: impact.effort,
    affected: impact.affected.map(one),
  };
}

export const toJson = (r: Report) => JSON.stringify(r, null, 2);

function csvCell(v: string | number | null): string {
  const s = v === null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

const CSV_HEAD = [
  "role",
  "function",
  "file",
  "line_start",
  "line_end",
  "distance",
  "risk",
  "risk_reason",
  "hours_to_update",
  "how_affected",
  "what_it_does",
  "calls",
  "may_call",
  "called_by",
  "fields_read",
  "fields_written",
  "code",
];

/** One row per function: the changed one first, then everything it could affect. Lists are joined with "; ". */
export function toCsv(r: Report): string {
  const row = (role: string, f: ReportFn) =>
    [
      role,
      f.qualified_name,
      f.file,
      f.line_start,
      f.line_end,
      f.distance,
      f.severity,
      f.severity_reason,
      f.effort_hours,
      f.how.join("; "),
      f.summary,
      f.dependencies.calls.join("; "),
      f.dependencies.may_call.join("; "),
      f.dependencies.called_by.join("; "),
      f.dependencies.fields_read.join("; "),
      f.dependencies.fields_written.join("; "),
      f.code,
    ]
      .map(csvCell)
      .join(",");
  const lines = [CSV_HEAD.join(","), row("changed", r.changed), ...r.affected.map((a) => row("affected", a))];
  return "﻿" + lines.join("\r\n") + "\r\n"; // BOM so Excel reads UTF-8 properly
}

const esc = (s: string | number | null | undefined) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const list = (xs: string[]) => (xs.length ? `<ul>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "<p class=none>None</p>");

function fnSection(f: ReportFn, heading: string): string {
  const d = f.dependencies;
  return `<section class=fn>
<h3>${esc(f.qualified_name)} <span class="badge ${esc(f.severity)}">${esc(f.severity)} risk</span></h3>
<p class=meta>${esc(heading)} · ${esc(f.file)} lines ${f.line_start}–${f.line_end}</p>
${f.how.length ? `<ul>${f.how.map((h) => `<li>${esc(h)}.</li>`).join("")}</ul>` : ""}
${f.summary ? `<p><b>What it does:</b> ${esc(f.summary)}</p>` : ""}
<p class=meta>${esc(f.severity_reason)}</p>
<h4>Code</h4>
${f.code ? `<pre>${esc(f.code)}</pre>` : "<p class=none>Source not available.</p>"}
<h4>Dependencies</h4>
<div class=deps>
<div><b>Calls</b>${list(d.calls)}</div>
<div><b>May call</b>${list(d.may_call)}</div>
<div><b>Called by</b>${list(d.called_by)}</div>
<div><b>Reads</b>${list(d.fields_read)}</div>
<div><b>Changes</b>${list(d.fields_written)}</div>
</div>
</section>`;
}

/** A single self-contained page: overview, summary table, then each function with its code and connections. */
export function toHtml(r: Report): string {
  const table = r.affected
    .map(
      (a) =>
        `<tr><td>${esc(a.qualified_name)}</td><td>${esc(a.file)}</td><td>${a.distance}</td><td>${esc(SEVERITY_WORD[a.severity as keyof typeof SEVERITY_WORD] ?? a.severity)}</td><td>${a.effort_hours > 0 ? esc(hours(a.effort_hours)) : "Full test run"}</td><td>${esc(a.how.join("; "))}</td></tr>`,
    )
    .join("");
  return `<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Impact report: ${esc(r.changed.qualified_name)}</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.5rem}h3{margin-bottom:.2rem}h4{margin:1rem 0 .3rem;color:#555}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}th{background:#f3f3f3}
pre{background:#f6f6f6;border:1px solid #ddd;padding:.7rem;overflow:auto;font-size:13px}
.fn{border-top:2px solid #ddd;margin-top:2rem;padding-top:.5rem}.meta{color:#666;font-size:13px;margin:.2rem 0}.none{color:#888;margin:.2rem 0}
.badge{font-size:12px;font-weight:400;border:1px solid #999;border-radius:4px;padding:1px 6px}.badge.high{border-color:#c2410c;color:#c2410c}.badge.medium{border-color:#b45309;color:#b45309}
.deps{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}.deps ul{margin:.2rem 0;padding-left:1.2rem;font-size:13px}
@media (prefers-color-scheme:dark){body{background:#161616;color:#e5e5e5}th{background:#262626}th,td,pre{border-color:#444}pre{background:#1f1f1f}h4,.meta{color:#aaa}}
</style></head><body>
<h1>If you change ${esc(r.changed.qualified_name)}</h1>
<p class=meta>Repo ${esc(r.repo)} · created ${esc(r.generated_at)}</p>
<h2>Overview</h2>
<p><b>Severity:</b> ${esc(SEVERITY_WORD[r.severity as keyof typeof SEVERITY_WORD] ?? r.severity)}. ${esc(r.severity_reason)}</p>
<p><b>Business impact:</b> ${esc(r.business_impact ?? "Not written yet.")}</p>
<p><b>Rough effort:</b> About ${esc(r.effort.low)}–${esc(hours(r.effort.high))} (making the change ${esc(hours(r.effort.change))}, updating and retesting ${esc(hours(r.effort.checks))}).</p>
<h2>Affected functions (${r.affected.length})</h2>
${r.affected.length ? `<table><thead><tr><th>Function</th><th>File</th><th>Calls away</th><th>Risk</th><th>Time</th><th>How</th></tr></thead><tbody>${table}</tbody></table>` : "<p>Nothing else calls this or reads the data it changes.</p>"}
<h2>Details</h2>
${fnSection(r.changed, "The function being changed")}
${r.affected.map((a) => fnSection(a, `${a.distance} ${a.distance === 1 ? "call" : "calls"} away`)).join("\n")}
</body></html>
`;
}

const MIME: Record<ExportFormat, string> = {
  csv: "text/csv;charset=utf-8",
  html: "text/html;charset=utf-8",
  json: "application/json;charset=utf-8",
};

export function serialize(r: Report, format: ExportFormat): string {
  return format === "csv" ? toCsv(r) : format === "html" ? toHtml(r) : toJson(r);
}

export const reportFileName = (r: Report, format: ExportFormat) =>
  `impact-${r.changed.name.replace(/[^\w.-]+/g, "_")}.${format}`;

export function download(r: Report, format: ExportFormat): void {
  const url = URL.createObjectURL(new Blob([serialize(r, format)], { type: MIME[format] }));
  const a = document.createElement("a");
  a.href = url;
  a.download = reportFileName(r, format);
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
