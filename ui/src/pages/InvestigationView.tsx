import { api } from "../api";
import { CasePanel, SarPanel, SummaryPanel } from "../components/CasePanel";
import { ActionPanel, PatternPanel, UncertaintyPanel } from "../components/Decision";
import { EvidenceCenter } from "../components/Evidence";
import { GraphView } from "../components/GraphView";
import { CaseHeader, Hero, OutcomeStrip } from "../components/Hero";
import { Journey } from "../components/Journey";
import { AgentActivity, Timeline } from "../components/Timeline";
import { AsyncView, Badge, Card, Icon, useAsync } from "../components/ui";
import { href } from "../router";
import { useReplay, type Replay } from "../replay";
import type { CaseDetail } from "../types";

function Waiting({ label }: { label: string }) {
  return <div className="pending" role="status"><span className="pulse">Waiting for {label}…</span></div>;
}

function ReplayBar({ r, demo }: { r: Replay; demo: boolean }) {
  const pct = r.total ? Math.round((r.shown / r.total) * 100) : 0;
  return (
    <div className="replay" aria-label="Replay controls">
      {r.playing ? (
        <button className="btn primary" onClick={r.pause}><Icon name="pause" /> Pause</button>
      ) : (
        <button className="btn primary" onClick={r.done ? r.restart : r.play}><Icon name={r.done ? "refresh" : "play"} /> {r.done ? (demo ? "Replay investigation" : "Replay investigation") : "Resume"}</button>
      )}
      <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Replay progress"><i style={{ width: `${pct}%` }} /></div>
      <div className="now-line" aria-live="polite">
        <b>{r.done ? "Investigation complete" : r.current ? r.current.label : "Ready"}</b>
        {!r.done && r.current && <span>{r.current.summary}</span>}
      </div>
      <div className="seg" role="group" aria-label="Playback speed">
        {[1, 2, 4].map((s) => <button key={s} aria-pressed={r.speed === s} onClick={() => r.setSpeed(s)}>{s}×</button>)}
      </div>
      <button className="btn ghost" onClick={r.finish} disabled={r.done}>Skip to result</button>
    </div>
  );
}

export function InvestigationView({ id, demo }: { id: string; demo: boolean }) {
  const a = useAsync(() => api.case(id), [id]);
  return (
    <div className="wrap">
      <AsyncView a={a}>{(d) => <Workspace d={d} demo={demo} />}</AsyncView>
    </div>
  );
}

export function Workspace({ d, demo }: { d: CaseDetail; demo: boolean }) {
  const r = useReplay(d, demo);
  const v = r.visible;
  const ring = d.graph.nodes.find((n) => n.kind === "device" && n.ring && Number(n.detail?.shared_by_cards) > 0);
  return (
    <>
      <CaseHeader d={d} demo={demo} settled={v("action")} />
      <ReplayBar r={r} demo={demo} />
      <div className="stack">
        <Journey detail={d} replay={r} />
        {v("case") && <OutcomeStrip d={d} />}
        {v("hero") ? <Hero d={d} settled={v("action")} /> : <Waiting label="the trigger" />}
        <div className="inv-grid">
          <div className="col">
            <Card title="Relationship graph" hint={d.graph.nodes.length ? `${d.graph.nodes.length} entities · ${d.graph.edges.length} relationships` : undefined} flush>
              {v("graph") && ring && (
                <div className="ring-callout" role="note"><Icon name="alert" size={14} /> Shared device <b className="mono">{ring.label}</b> links this case to <b>{String(ring.detail?.shared_by_cards)}</b> other cards.</div>
              )}
              {v("graph") ? <GraphView data={d.graph} title={`Relationship graph for ${d.header.case_id}`} /> : <div style={{ padding: 16 }}><Waiting label="graph relationships" /></div>}
              {v("graph") && d.graph.note && <div className="faint" style={{ padding: "8px 16px", fontSize: 11.5 }}>{d.graph.note}</div>}
            </Card>
            <Timeline d={d} replay={r} />
            {v("evidence") ? <EvidenceCenter d={d} /> : <Waiting label="evidence" />}
            {v("patterns") ? <PatternPanel d={d} /> : <Waiting label="pattern detection" />}
            <AgentActivity d={d} replay={r} />
          </div>
          <div className="col">
            {v("action") ? <ActionPanel d={d} /> : <Waiting label="the next-best action" />}
            {v("uncertainty") ? <UncertaintyPanel d={d} resolved={v("requests")} /> : <Waiting label="the uncertainty assessment" />}
            {v("case") ? <CasePanel d={d} /> : <Waiting label="case creation" />}
            {v("summary") ? <SummaryPanel d={d} /> : <Waiting label="the explanation" />}
            {v("sar") ? <SarPanel d={d} /> : <Waiting label="the SAR decision" />}
            <a className="btn ghost" href={href("/investigations")}><Icon name="list" /> All investigations</a>
            {d.measured.mode && <div className="faint" style={{ fontSize: 11.5 }}><Badge tone="neutral">{d.measured.mode}</Badge> backend result; the interface only presents it.</div>}
          </div>
        </div>
      </div>
    </>
  );
}
