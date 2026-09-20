import { useMemo, useState } from "react";
import { api } from "../api";
import { GraphView } from "../components/GraphView";
import { AsyncView, Badge, Card, EmptyState, Icon, useAsync } from "../components/ui";
import { actionLabel, human, patternLabel, routeTone, strengthTone, triggerLabel, usd, verdictTone } from "../format";
import { go, href } from "../router";
import type { CaseSummary } from "../types";

type SortKey = "case_id" | "trigger_type" | "pattern" | "verdict" | "evidence_strength" | "status" | "top_action" | "exposure_usd" | "opened_at";
const STRENGTH_ORDER: Record<string, number> = { strong: 3, moderate: 2, weak: 1, none: 0 };

export function InvestigationsPage() {
  const a = useAsync(api.cases, []);
  return (
    <div className="wrap">
      <PageTitle title="Investigations" sub="All 20 benchmark investigations, produced by the deterministic agent." />
      <AsyncView a={a}>{(rows) => <CaseTable rows={rows} />}</AsyncView>
    </div>
  );
}

export function PageTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <h1 style={{ fontSize: 22, letterSpacing: "-.01em" }}>{title}</h1>
      {sub && <p className="muted" style={{ marginTop: 2 }}>{sub}</p>}
    </div>
  );
}

export function CaseTable({ rows }: { rows: CaseSummary[] }) {
  const [q, setQ] = useState("");
  const [verdict, setVerdict] = useState("all");
  const [trigger, setTrigger] = useState("all");
  const [approval, setApproval] = useState("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "case_id", dir: 1 });

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const f = rows.filter((r) =>
      (verdict === "all" || r.verdict === verdict) && (trigger === "all" || r.trigger_type === trigger) &&
      (approval === "all" || (approval === "needs" ? r.final_actions.some((x) => x.route !== "auto") : r.final_actions.every((x) => x.route === "auto"))) &&
      (!needle || [r.case_id, r.customer_id, r.card_id, r.pattern, r.verdict, r.top_action ?? "", ...r.final_actions.map((x) => x.action)].join(" ").toLowerCase().includes(needle)));
    const val = (r: CaseSummary) => (sort.key === "evidence_strength" ? STRENGTH_ORDER[r.evidence_strength ?? ""] ?? -1 : r[sort.key] ?? "");
    return [...f].sort((x, y) => (val(x) > val(y) ? 1 : val(x) < val(y) ? -1 : 0) * sort.dir);
  }, [rows, q, verdict, trigger, approval, sort]);

  const th = (key: SortKey, label: string, num = false) => (
    <th className={num ? "num" : ""} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))}>{label}<Icon name="sort" size={12} /></button>
    </th>
  );
  return (
    <>
      <div className="toolbar">
        <div style={{ position: "relative" }}>
          <input className="input" style={{ paddingLeft: 32 }} placeholder="Search case, customer, card, pattern, action" aria-label="Search investigations" value={q} onChange={(e) => setQ(e.target.value)} />
          <span style={{ position: "absolute", left: 10, top: 9, color: "var(--text-3)" }}><Icon name="search" size={15} /></span>
        </div>
        <select className="select" aria-label="Filter by verdict" value={verdict} onChange={(e) => setVerdict(e.target.value)}>
          <option value="all">All verdicts</option><option value="fraud">Fraud</option><option value="uncertain">Uncertain</option><option value="legitimate">Legitimate</option>
        </select>
        <select className="select" aria-label="Filter by trigger" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
          <option value="all">All triggers</option><option value="risk_score">Model alert</option><option value="customer_report">Customer report</option><option value="analyst_request">Analyst request</option>
        </select>
        <select className="select" aria-label="Filter by approval" value={approval} onChange={(e) => setApproval(e.target.value)}>
          <option value="all">Any approval</option><option value="needs">Needs approval</option><option value="auto">Automatic only</option>
        </select>
        <span className="faint" style={{ marginLeft: "auto" }}>{shown.length} of {rows.length}</span>
      </div>
      <Card flush>
        {shown.length === 0 ? <EmptyState title="No investigations match" body="Try clearing the search or filters." /> : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr>{th("case_id", "Case")}{th("trigger_type", "Trigger")}{th("pattern", "Pattern")}{th("verdict", "Verdict")}{th("evidence_strength", "Evidence")}{th("status", "Status")}{th("top_action", "Action")}{th("exposure_usd", "Exposure", true)}{th("opened_at", "Opened")}</tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.case_id} className="click" tabIndex={0} onClick={() => go(`/investigations/${r.case_id}`)} onKeyDown={(e) => e.key === "Enter" && go(`/investigations/${r.case_id}`)}>
                    <td><a href={href(`/investigations/${r.case_id}`)} onClick={(e) => e.stopPropagation()}><b>{r.case_id}</b></a></td>
                    <td>{triggerLabel(r.trigger_type)}</td>
                    <td>{patternLabel(r.pattern)}</td>
                    <td><Badge tone={verdictTone(r.verdict)} dot>{human(r.verdict)}</Badge></td>
                    <td><Badge tone={strengthTone(r.evidence_strength)}>{human(r.evidence_strength ?? "n/a")}</Badge></td>
                    <td>{human(r.status)}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <span>{r.top_action ? actionLabel(r.top_action) : "—"}</span>
                        {r.final_actions.some((x) => x.route !== "auto") && <Badge tone={routeTone(r.final_actions.some((x) => x.route === "L2") ? "L2" : "L1")}>{r.final_actions.some((x) => x.route === "L2") ? "L2" : "L1"}</Badge>}
                        {r.final_actions.length > 1 && <span className="faint">+{r.final_actions.length - 1}</span>}
                      </div>
                    </td>
                    <td className="num">{usd(r.exposure_usd)}</td>
                    <td className="faint">{r.opened_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

export function CasesPage() {
  const a = useAsync(api.cases, []);
  return (
    <div className="wrap">
      <PageTitle title="Cases" sub="Case records written to TigerGraph as FI_Case vertices." />
      <AsyncView a={a}>{(rows) => (
        <Card flush>
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Graph case</th><th>Status</th><th>Verdict</th><th>Written</th><th className="num">Revision</th><th>SAR</th><th>Evidence</th><th className="num">Affected</th><th className="num">Exposure</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.case_id} className="click" tabIndex={0} onClick={() => go(`/investigations/${r.case_id}`)} onKeyDown={(e) => e.key === "Enter" && go(`/investigations/${r.case_id}`)}>
                    <td><b className="mono">{r.graph_case_id}</b></td>
                    <td>{human(r.status)}</td>
                    <td><Badge tone={verdictTone(r.verdict)} dot>{human(r.verdict)}</Badge></td>
                    <td><Badge tone={r.written_to_graph ? "ok" : "danger"} dot>{r.written_to_graph ? "Written" : "Not written"}</Badge></td>
                    <td className="num">{r.revision ?? "—"}</td>
                    <td>{r.sar_filed ? <Badge tone="warn">Filing recommended</Badge> : <span className="faint">None</span>}</td>
                    <td>{r.simulated_evidence ? <Badge tone="sim">Includes simulated</Badge> : <span className="faint">Graph only</span>}</td>
                    <td className="num">{r.affected_txn_count}</td>
                    <td className="num">{usd(r.exposure_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}</AsyncView>
    </div>
  );
}

export function CustomersPage() {
  const a = useAsync(api.customers, []);
  return (
    <div className="wrap">
      <PageTitle title="Customers" sub="Customers involved in the benchmark investigations (pseudonymous dataset IDs only)." />
      <AsyncView a={a} empty={(d) => d.length === 0}>{(rows) => (
        <Card flush>
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Customer</th><th>Cards</th><th>Investigations</th><th className="num">Exposure</th></tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.customer_id} className="click" tabIndex={0} onClick={() => go(`/investigations/${c.cases[0].case_id}`)}>
                    <td className="mono"><b>{c.customer_id}</b></td>
                    <td className="mono">{c.cards.join(", ")}</td>
                    <td><div className="row">{c.cases.map((x) => <Badge key={x.case_id} tone={verdictTone(x.verdict)}>{x.case_id}</Badge>)}</div></td>
                    <td className="num">{usd(c.exposure_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}</AsyncView>
    </div>
  );
}

export function GraphPage() {
  const a = useAsync(api.graph, []);
  return (
    <div className="wrap">
      <PageTitle title="Graph" sub="Cases, the cards they concern, and shared device profiles across all investigations. Open an investigation for the full relationship graph." />
      <Card flush><AsyncView a={a}>{(g) => <GraphView data={g} tall title="Cross-case graph" />}</AsyncView></Card>
    </div>
  );
}
