import type { ActionView, CaseDetail } from "../types";
import { actionLabel, human, ratingTone, routeTone, verdictTone } from "../format";
import { Badge, Card, Icon } from "./ui";

const OUTCOME: Record<string, { label: string; tone: "ok" | "danger" | "warn" | "neutral" }> = {
  passed: { label: "Passed", tone: "ok" }, failed: { label: "Failed", tone: "danger" }, verified_legitimate: { label: "Verified legitimate", tone: "ok" },
  denied_or_unrecognized: { label: "Denied / not recognized", tone: "danger" }, no_response: { label: "No response", tone: "warn" },
};

export function UncertaintyPanel({ d, resolved }: { d: CaseDetail; resolved: boolean }) {
  const u = d.uncertainty;
  return (
    <Card title="Uncertainty and additional evidence">
      <p className="unc-statement">{u.statement}</p>
      {u.missing.length > 0 && <ul className="list">{u.missing.map((m) => <li key={m}>{m}</li>)}</ul>}
      {d.requests.length > 0 && (
        <>
          <div className="transition" aria-label="Verdict transition">
            <div className="tbox"><small>Before additional evidence</small><Badge tone={verdictTone(u.before_verdict)} dot>{human(u.before_verdict)}</Badge></div>
            <Icon name="arrow" />
            {resolved ? (
              <div className="tbox reveal"><small>After the response</small><Badge tone={verdictTone(u.after_verdict)} dot>{human(u.after_verdict)}</Badge><span className="faint">{human(u.after_status)}</span></div>
            ) : (
              <div className="tbox"><small>After the response</small><Badge tone="neutral">Pending</Badge></div>
            )}
          </div>
          <div className="stack" style={{ gap: 8 }}>
            {d.requests.map((q, i) => {
              const o = q.outcome ? OUTCOME[q.outcome] ?? { label: human(q.outcome), tone: "neutral" as const } : { label: "Pending", tone: "neutral" as const };
              return (
                <div className="request" key={i}>
                  <div style={{ flex: 1 }}>
                    <div className="row"><b>{q.label}</b><Badge tone="sim">Simulated</Badge></div>
                    <p>{q.assumed_response}</p>
                  </div>
                  <Badge tone={resolved ? o.tone : "neutral"} dot>{resolved ? o.label : "Pending"}</Badge>
                </div>
              );
            })}
          </div>
          <p className="faint" style={{ marginTop: 10, fontSize: 12 }}>Responses are produced by a deterministic test responder. They are not from a real customer.</p>
        </>
      )}
      {d.requests.length === 0 && <p className="faint" style={{ marginTop: 8 }}>No additional evidence was requested for this case.</p>}
    </Card>
  );
}

export function PatternPanel({ d }: { d: CaseDetail }) {
  const signals = d.evidence.filter((g) => g.category === "Pattern signals" || g.category === "Device").flatMap((g) => g.items);
  return (
    <Card title="Fraud pattern detection" hint={d.case.pattern === "none" ? "No pattern" : d.case.pattern.replace(/_/g, " ")}>
      {d.case.pattern_description && <p style={{ marginBottom: 10 }}>{d.case.pattern_description}</p>}
      {signals.length === 0 ? <p className="muted">No fraud signal fired for this case.</p> : (
        <div className="stack" style={{ gap: 8 }}>
          {signals.map((s) => (
            <div key={s.id} className="row"><Badge tone={ratingTone(s.rating)}>{s.strength}</Badge><span>{s.title}</span></div>
          ))}
        </div>
      )}
    </Card>
  );
}

function ActionRow({ a }: { a: ActionView }) {
  return (
    <div className="action-item">
      <b>{actionLabel(a.action)}</b>
      <Badge tone={routeTone(a.route)}>{a.route === "auto" ? "Auto" : a.route}</Badge>
      <p>{a.reason}</p>
    </div>
  );
}

export function ActionPanel({ d }: { d: CaseDetail }) {
  const fin = d.actions.final;
  const primary = fin[0];
  const supporting = d.evidence.flatMap((g) => g.items).filter((i) => !i.simulated && (i.rating === "HIGH" || i.rating === "MEDIUM")).slice(0, 4);
  const changed = JSON.stringify(d.actions.initial.map((a) => a.action)) !== JSON.stringify(fin.map((a) => a.action));
  if (!primary) return <Card title="Recommended next action"><p className="muted">No action recommended.</p></Card>;
  return (
    <section className="card action-card reveal" aria-label="Recommended next action">
      <div className="card-b">
        <div className="action-eyebrow">Recommended action</div>
        <div className="action-primary">
          <div className="action-name">{actionLabel(primary.action)}</div>
          <div className="route"><small>Approval route</small><b>{primary.route_label}</b></div>
        </div>
        <p className="muted" style={{ marginTop: 4 }}>{primary.reason}</p>
        <div className="row" style={{ marginTop: 10 }}>
          <Badge tone={primary.requires_approval ? routeTone(primary.route) : "ok"} dot>{primary.requires_approval ? "Requires approval" : "No approval needed"}</Badge>
          {primary.rules.map((r) => <span className="chip" key={r}>Policy {r}</span>)}
        </div>
        {fin.length > 1 && (
          <div className="action-list">
            <div className="faint" style={{ fontSize: 11.5, textTransform: "uppercase", letterSpacing: ".07em" }}>Then</div>
            {fin.slice(1).map((a, i) => <ActionRow key={i} a={a} />)}
          </div>
        )}
        {supporting.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div className="faint" style={{ fontSize: 11.5, textTransform: "uppercase", letterSpacing: ".07em", marginBottom: 4 }}>Supporting evidence</div>
            <ul className="list">{supporting.map((s) => <li key={s.id}>{s.title}</li>)}</ul>
          </div>
        )}
        <div className="change">
          <div><small>Before additional evidence</small>{d.actions.initial.map((a) => actionLabel(a.action)).join(" · ")}</div>
          <span className="arrow"><Icon name="arrow" /></span>
          <div><small>After</small>{fin.map((a) => actionLabel(a.action)).join(" · ")}</div>
        </div>
        <p className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>{changed ? d.actions.what_changed : "The recommendation did not change: " + d.actions.what_changed}</p>
        <div className="recommend-only"><Icon name="shield" size={14} /> Recommendation only. Nothing has been executed; approvals are routed to a human.</div>
      </div>
    </section>
  );
}
