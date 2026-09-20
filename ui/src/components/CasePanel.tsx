import { useState } from "react";
import { api } from "../api";
import type { CaseDetail, GraphProbe } from "../types";
import { actionLabel, human, patternLabel, statusLabel, usd, verdictTone } from "../format";
import { Badge, Card, Icon } from "./ui";

export function CasePanel({ d }: { d: CaseDetail }) {
  const c = d.case;
  const [probe, setProbe] = useState<GraphProbe | null>(null);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const check = async () => {
    setBusy(true);
    try { setProbe(await api.graphCheck(d.header.case_id)); } catch (e) { setProbe({ reachable: false, error: e instanceof Error ? e.message : "Check failed" }); } finally { setBusy(false); }
  };
  const txns = more ? c.affected_txn_ids : c.affected_txn_ids.slice(0, 8);
  return (
    <Card title="Case record" hint={c.graph_case_id}>
      <div className={`banner ${c.written_to_graph ? "" : "err"}`} style={{ background: c.written_to_graph ? "var(--ok-soft)" : undefined, borderColor: c.written_to_graph ? "rgba(63,185,132,.3)" : undefined, color: c.written_to_graph ? "#7fdcb4" : undefined, marginBottom: 14 }}>
        <Icon name={c.written_to_graph ? "check" : "alert"} />
        <span>
          <b>{c.written_to_graph ? "Written to TigerGraph" : "Not written to TigerGraph"}</b>
          {c.written_to_graph && <> · revision {c.revision ?? "—"}</>}
        </span>
        <button className="btn ghost" style={{ marginLeft: "auto", height: 26 }} onClick={check} disabled={busy}><Icon name="refresh" size={13} /> {busy ? "Checking…" : "Verify live"}</button>
      </div>
      {probe && (
        <div className={`banner ${probe.reachable && probe.found ? "" : "warn"}`} style={{ marginBottom: 14 }} role="status">
          <Icon name={probe.reachable && probe.found ? "check" : "alert"} />
          <span>{probe.reachable ? (probe.found ? `Live read-back: ${probe.graph_case_id} found, revision ${probe.revision ?? "?"}, status ${probe.status ?? "?"}.` : "The graph is reachable but this case vertex was not found.") : `Live check unavailable: ${probe.error ?? "graph unreachable"}. The stored result above is unaffected.`}</span>
        </div>
      )}
      <dl className="kv">
        <dt>Status</dt><dd><Badge tone="neutral">{statusLabel(c.status)}</Badge></dd>
        <dt>Verdict</dt><dd><Badge tone={verdictTone(c.verdict)} dot>{human(c.verdict)}</Badge></dd>
        <dt>Pattern</dt><dd>{patternLabel(c.pattern)}</dd>
        <dt>Affected</dt>
        <dd>
          <b>{c.affected_txn_ids.length}</b> transaction{c.affected_txn_ids.length === 1 ? "" : "s"} · <b>{usd(c.exposure_usd)}</b> exposure
          {txns.length > 0 && (
            <div className="row" style={{ marginTop: 6, gap: 4 }}>
              {txns.map((t) => <span key={t} className="chip">{t}</span>)}
              {c.affected_txn_ids.length > 8 && <button className="tl-more" onClick={() => setMore(!more)}>{more ? "Show fewer" : `+${c.affected_txn_ids.length - 8} more`}</button>}
            </div>
          )}
        </dd>
        <dt>Connected</dt><dd>{c.connected_card_ids.length} card{c.connected_card_ids.length === 1 ? "" : "s"}{c.connected_device_profiles[0] ? <div className="faint" style={{ fontSize: 12 }}>{c.connected_device_profiles[0]}</div> : null}</dd>
        <dt>Initial actions</dt><dd>{d.actions.initial.map((a) => actionLabel(a.action)).join(" · ") || "—"}</dd>
        <dt>Final actions</dt><dd>{d.actions.final.map((a) => `${actionLabel(a.action)} (${a.route})`).join(" · ") || "—"}</dd>
        <dt>Evidence requests</dt><dd>{d.requests.length ? d.requests.map((r) => `${r.label} (simulated)`).join(", ") : "None"}</dd>
        <dt>Similar cases</dt><dd><div className="row" style={{ gap: 4 }}>{c.similar_prior_cases.slice(0, 6).map((s) => <span className="chip" key={s}>{s}</span>)}{c.similar_prior_cases.length > 6 && <span className="faint">+{c.similar_prior_cases.length - 6}</span>}{!c.similar_prior_cases.length && "None"}</div></dd>
        <dt>Probability</dt><dd><Badge tone="info">Not stated</Badge><div className="faint" style={{ fontSize: 12, marginTop: 4 }}>{c.probability_note}</div></dd>
        <dt>Stop reason</dt><dd className="muted">{c.stop_reason}</dd>
      </dl>
    </Card>
  );
}

export function SarPanel({ d }: { d: CaseDetail }) {
  const s = d.sar;
  return (
    <Card title="Suspicious activity report" hint={<Badge tone={s.file ? "warn" : "neutral"}>{s.file ? "Filing recommended (L2)" : "No report required"}</Badge>}>
      <p className="muted">{s.reason}</p>
      {s.file && (
        <>
          <p style={{ marginTop: 12, lineHeight: 1.6 }}>{s.narrative}</p>
          <div className="row" style={{ marginTop: 12 }}>
            <span className="faint">Subjects</span>{s.subjects.map((x) => <span className="chip" key={x}>{x}</span>)}
            <span className="faint" style={{ marginLeft: 8 }}>Total</span><b>{usd(s.total_amount_usd)}</b>
          </div>
        </>
      )}
    </Card>
  );
}

export function SummaryPanel({ d }: { d: CaseDetail }) {
  return (
    <Card title="Investigation explanation">
      <p style={{ lineHeight: 1.65 }}>{d.case.summary}</p>
      <p className="faint" style={{ marginTop: 10, fontSize: 12 }}>
        Produced deterministically from the evidence above ({d.measured.mode} mode, {d.measured.tool_calls} tool calls, {d.measured.tokens} LLM tokens, {d.measured.latency_s.toFixed(1)} s measured).
      </p>
    </Card>
  );
}
