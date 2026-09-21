import { api } from "../api";
import { AsyncView, Badge, Card, Icon, useAsync } from "../components/ui";
import { human, usd, verdictTone } from "../format";
import { href } from "../router";
import { PageTitle } from "./Lists";

function Bars({ data, tone }: { data: Record<string, number>; tone?: (k: string) => string }) {
  const max = Math.max(1, ...Object.values(data));
  return (
    <div className="bars">
      {Object.entries(data).map(([k, v]) => (
        <div className="bar" key={k}><span>{human(k)}</span><span><i style={{ width: `${(v / max) * 100}%`, background: tone?.(k) }} /></span><b>{v}</b></div>
      ))}
    </div>
  );
}
const VC: Record<string, string> = { fraud: "var(--danger)", uncertain: "var(--warn)", legitimate: "var(--ok)" };

export function OverviewPage() {
  const a = useAsync(api.overview, []);
  return (
    <div className="wrap">
      <PageTitle title="Overview" sub="Fraud Investigation Command Center · benchmark investigations run by the deterministic agent." />
      <AsyncView a={a}>{(o) => (
        <div className="stack">
          <section className="card launch">
            <div>
              <Badge tone="info">Demo mode</Badge>
              <h2>Watch an investigation unfold</h2>
              <p className="muted" style={{ maxWidth: "62ch" }}>
                {o.showcase ?? "A case"} starts from an analyst request about a shared device. The agent discovers the ring, expands the connected cards and asks for customer verification
                (simulated: no reply is assumed). The evidence alone supports a case, a report (Level 2 approval), monitoring of the connected cards and an analyst escalation.
                Replay is deterministic and uses the stored, validated result. It never waits on an external model.
              </p>
            </div>
            {o.showcase && <a className="btn primary" style={{ height: 40, padding: "0 18px" }} href={href(`/investigations/${o.showcase}`, { demo: "1" })}><Icon name="play" /> Launch demo</a>}
          </section>
          <div className="kpis">
            <Card className="kpi"><label>Investigations</label><b>{o.total}</b><small>{Object.entries(o.triggers).map(([k, v]) => `${v} ${human(k).toLowerCase()}`).join(" · ")}</small></Card>
            <Card className="kpi"><label>Written to TigerGraph</label><b>{o.written_to_graph}/{o.total}</b><small>FI_Case records</small></Card>
            <Card className="kpi"><label>Needs approval</label><b>{o.needs_approval}</b><small>L1 / L2 routed</small></Card>
            <Card className="kpi"><label>SAR filings recommended</label><b>{o.sar_filed}</b><small>recommendation only</small></Card>
            <Card className="kpi"><label>Exposure identified</label><b>{usd(o.exposure_usd)}</b><small>across affected transactions</small></Card>
          </div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
            <Card title="Verdicts"><Bars data={o.verdicts} tone={(k) => VC[k]} /></Card>
            <Card title="Patterns"><Bars data={o.patterns} /></Card>
          </div>
          <div className="banner sim"><Icon name="info" /><span>Customer and step-up responses in these investigations are <b>simulated</b> (the default assumption is that no reply arrives) and <b>never decide a verdict</b>: where the evidence is insufficient the verdict is uncertain. No accuracy is claimed: there is no ground-truth answer key. <b>Fraud probability is not stated</b> because the supplied data does not support a calibrated per-case probability.</span></div>
        </div>
      )}</AsyncView>
    </div>
  );
}

export function PoliciesPage() {
  const a = useAsync(api.policies, []);
  return (
    <div className="wrap">
      <PageTitle title="Policies" sub="The fraud policy the agent operates under (version 1.0). Rendered as stored; the interface cannot change it." />
      <AsyncView a={a}>{(p) => (
        <div className="stack">
          <Card title="Approval routes">
            <div className="row">{Object.entries(p.routes).map(([k, v]) => <Badge key={k} tone={k === "L2" ? "danger" : k === "L1" ? "warn" : "neutral"}>{k === "auto" ? "Auto" : k} · {v}</Badge>)}</div>
          </Card>
          <Card title="Policy documents" flush>
            {p.documents.map((d) => (
              <details className="pol" key={d.ref}><summary>{/^R\d/.test(d.ref) ? `Rule ${d.ref}` : `Section ${d.ref}`}</summary><pre className="policy-doc">{d.text}</pre></details>
            ))}
          </Card>
        </div>
      )}</AsyncView>
    </div>
  );
}

export function SystemPage() {
  const a = useAsync(api.system, []);
  return (
    <div className="wrap">
      <PageTitle title="System status" sub="Read-only checks. The interface holds no credentials." />
      <AsyncView a={a}>{(s) => (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          <Card title="Investigation service">
            <dl className="kv"><dt>API</dt><dd><Badge tone={s.api.ok ? "ok" : "danger"} dot>{s.api.ok ? "Online" : "Offline"}</Badge></dd><dt>Cases</dt><dd>{s.api.cases}</dd><dt>Stored records</dt><dd>{s.api.records}</dd></dl>
          </Card>
          <Card title="TigerGraph" hint="live read-only probe">
            <dl className="kv">
              <dt>Connection</dt><dd><Badge tone={s.graph.reachable ? "ok" : "warn"} dot>{s.graph.reachable ? "Reachable" : "Unavailable"}</Badge></dd>
              {s.graph.reachable && <><dt>Sample case</dt><dd>{s.graph.graph_case_id} {s.graph.found ? `· revision ${s.graph.revision}` : "· not found"}</dd></>}
              {!s.graph.reachable && <><dt>Detail</dt><dd className="muted">{s.graph.error}</dd></>}
            </dl>
            {!s.graph.reachable && <p className="faint" style={{ marginTop: 10, fontSize: 12 }}>Stored investigation results and Demo mode keep working without a live graph.</p>}
          </Card>
          <Card title="Safety boundaries"><ul className="list">{s.safety.map((x) => <li key={x}>{x}</li>)}</ul></Card>
          <Card title="Verdict key"><div className="row"><Badge tone={verdictTone("fraud")} dot>Fraud</Badge><Badge tone={verdictTone("uncertain")} dot>Uncertain</Badge><Badge tone={verdictTone("legitimate")} dot>Legitimate</Badge></div></Card>
        </div>
      )}</AsyncView>
    </div>
  );
}
