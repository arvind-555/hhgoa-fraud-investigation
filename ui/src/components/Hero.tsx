import type { CaseDetail } from "../types";
import { patternLabel, statusLabel, strengthTone, triggerLabel, usd, verdictTone, human } from "../format";
import { Badge, Icon } from "./ui";

export function CaseHeader({ d, demo, settled = true }: { d: CaseDetail; demo: boolean; settled?: boolean }) {
  const h = d.header;
  return (
    <div className="case-head">
      <div className="case-title">
        <h1>{h.case_id}</h1>
        {settled ? (
          <>
            <Badge tone={verdictTone(h.verdict)} dot>{human(h.verdict)}</Badge>
            <Badge tone="neutral">{statusLabel(h.status)}</Badge>
            <Badge tone="info">{triggerLabel(h.trigger_type)}</Badge>
            <Badge tone={strengthTone(h.evidence_strength)}>Evidence: {h.evidence_strength ?? "n/a"}</Badge>
            <Badge tone={h.pattern === "none" ? "neutral" : "warn"}>{patternLabel(h.pattern)}</Badge>
          </>
        ) : (
          <>
            <Badge tone="info" dot>Investigating</Badge>
            <Badge tone="info">{triggerLabel(h.trigger_type)}</Badge>
          </>
        )}
        <span className="faint" title={h.as_of_note}>As-of {h.opened_at}</span>
      </div>
      {demo && (
        <div className="banner sim" role="note">
          <Icon name="info" />
          <span><b>Demo mode.</b> This is a controlled replay of a stored, validated investigation. Customer and step-up responses are <b>simulated</b> by a deterministic test responder, not from a real customer.</span>
        </div>
      )}
      {!d.integrity.ok && (
        <div className="banner warn" role="alert">
          <Icon name="alert" />
          <span>{d.integrity.note || "The stored investigation record does not fully match the submitted answer file."} {d.integrity.checks.filter((c) => !c.match).map((c) => c.name).join(", ")}</span>
        </div>
      )}
    </div>
  );
}

export function Hero({ d, settled = true }: { d: CaseDetail; settled?: boolean }) {
  const t = d.trigger;
  return (
    <section className="card hero reveal" aria-label="Why this investigation started">
      <div className="hero-l">
        <div className="eyebrow">Why this investigation started</div>
        <p className="trigger">{t.text}</p>
        <div className="row" style={{ marginTop: 12 }}>
          <Badge tone="info">{triggerLabel(t.trigger_type)}</Badge>
          <span className="faint">opened {t.opened_at}</span>
        </div>
      </div>
      <div className="hero-r">
        {t.amount_usd != null && <div className="stat"><label>Amount</label><b className="big">{usd(t.amount_usd)}</b></div>}
        {!t.channel.startsWith("Not stated") && <div className="stat"><label>Channel</label><b>{t.channel}</b></div>}
        <div className="stat"><label>Transaction</label><b className="mono">{t.flagged_txn_id}</b></div>
        <div className="stat"><label>Card</label><b className="mono">{t.card_id}</b></div>
        <div className="stat"><label>Customer</label><b className="mono">{t.customer_id}</b></div>
        <div className="stat"><label>State</label><b>{settled ? statusLabel(d.header.status) : "Investigating"}</b></div>
      </div>
    </section>
  );
}

/** One-line story of the finished investigation, assembled only from fields the backend already returned (finding -> evidence -> action -> case). */
export function OutcomeStrip({ d }: { d: CaseDetail }) {
  const items = d.evidence.flatMap((g) => g.items).filter((i) => !i.simulated);
  const top = items.find((i) => i.rating === "HIGH") ?? items.find((i) => i.rating === "MEDIUM");
  const c = d.case;
  const first = d.actions.final[0];
  const req = d.requests[0];
  const finding = top ? top.title : "No validated fraud signal";
  const n = c.affected_txn_ids.length;
  const k = c.connected_card_ids.length;
  const facts = [n ? `${n} transaction${n === 1 ? "" : "s"} · ${usd(c.exposure_usd)}` : "", k ? `${k} connected card${k === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
  const outcome = req?.outcome ? req.outcome.replace(/_/g, " ") : "";
  return (
    <section className="outcome reveal" aria-label="Investigation outcome">
      <div><small>Finding</small><b>{finding}</b>{facts && <span>{facts}</span>}</div>
      <Icon name="arrow" />
      <div><small>Additional evidence</small><b>{req ? req.label : "None requested"}</b>{req && <span>{outcome} · simulated</span>}</div>
      <Icon name="arrow" />
      <div><small>Action</small><b>{first ? first.action.replace(/_/g, " ") : "None"}</b>{first && <span>{first.route_label}</span>}</div>
      <Icon name="arrow" />
      <div><small>Case</small><b>{c.written_to_graph ? "Written to TigerGraph" : "Not written"}</b><span>{c.graph_case_id}{c.revision ? ` · revision ${c.revision}` : ""}</span></div>
    </section>
  );
}
