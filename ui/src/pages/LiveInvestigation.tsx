import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { Badge, Card, Icon } from "../components/ui";
import { human, strengthTone, usd, verdictTone } from "../format";
import { href } from "../router";
import type { LiveJob, LiveResult } from "../types";
import { Workspace } from "./InvestigationView";
import { PageTitle } from "./Lists";

const CASE_IDS = Array.from({ length: 20 }, (_, i) => `HHG-${String(i + 1).padStart(3, "0")}`);
const STATUS: Record<LiveJob["status"], { label: string; tone: "neutral" | "info" | "ok" | "danger" }> = {
  queued: { label: "Queued", tone: "neutral" }, running: { label: "Running", tone: "info" }, completed: { label: "Completed", tone: "ok" }, failed: { label: "Failed", tone: "danger" },
};

function Consistency({ r }: { r: LiveResult }) {
  const c = r.consistency;
  const tone = c.level === "match" ? "" : c.level === "differs" ? "warn" : "sim";
  return (
    <div className={`banner ${tone}`} style={c.level === "match" ? { background: "var(--ok-soft)", borderColor: "rgba(63,185,132,.3)", color: "#7fdcb4" } : undefined} role="status" aria-label="Consistency with the stored case">
      <Icon name={c.level === "match" ? "check" : "alert"} />
      <span>
        <b>{c.message}</b>
        {c.stored_revision != null && <> · stored FI_Case revision {c.stored_revision}</>}
        {c.checked && <> · content hash live {c.live_sha256} / stored {c.stored_sha256}</>}
        {c.level === "differs" && <><br />Differs in: {c.differs.map((x) => human(x)).join(", ")}. Nothing was written or repaired.</>}
      </span>
    </div>
  );
}

function Summary({ r }: { r: LiveResult }) {
  const s = r.summary;
  const acts = r.detail.actions.final;
  return (
    <Card title="Live result" hint={r.detail.header.case_id}>
      <dl className="kv">
        <dt>Verdict</dt><dd><Badge tone={verdictTone(s.verdict)} dot>{human(s.verdict)}</Badge> <Badge tone="neutral">{human(s.status)}</Badge></dd>
        <dt>Evidence strength</dt><dd><Badge tone={strengthTone(s.evidence_strength)}>{s.evidence_strength ?? "n/a"}</Badge></dd>
        <dt>Next-best actions</dt>
        <dd>{acts.length ? acts.map((a, i) => <div key={i}><b>{a.action.replace(/_/g, " ")}</b> · {a.route_label}</div>) : "None"}</dd>
        <dt>SAR decision</dt><dd>{s.sar_file ? "A suspicious-activity report is recommended" : "No report recommended"}</dd>
        <dt>Exposure</dt><dd>{usd(s.exposure_usd)}</dd>
        <dt>Measured</dt><dd>{s.tool_calls} tool calls · {s.graph_queries} graph queries · {s.latency_s} s</dd>
        <dt>Graph write</dt><dd>None (written_to_graph = false)</dd>
      </dl>
    </Card>
  );
}

export function LiveInvestigationPage({ pollMs = 2000 }: { pollMs?: number }) {
  const [caseId, setCaseId] = useState("HHG-014");
  const [job, setJob] = useState<LiveJob | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const t0 = useRef(0);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const active = starting || job?.status === "queued" || job?.status === "running";

  useEffect(() => {                                       // poll the job until it finishes
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    const id = window.setTimeout(async () => {
      try {
        const next = await api.liveStatus(job.job_id);
        if (alive.current) setJob(next);
      } catch (e) {
        if (alive.current) { setJob(null); setNotice(e instanceof Error ? e.message : "The live status could not be read."); }
      }
    }, pollMs);
    return () => window.clearTimeout(id);
  }, [job, pollMs]);

  useEffect(() => {                                       // approximate elapsed time while active
    if (!active) return;
    const id = window.setInterval(() => setElapsed(Math.round((Date.now() - t0.current) / 1000)), 500);
    return () => window.clearInterval(id);
  }, [active]);

  const run = async () => {
    setNotice(null); setJob(null); setElapsed(0); setStarting(true); t0.current = Date.now();
    try {
      const j = await api.liveStart(caseId);
      if (alive.current) setJob(j);
    } catch (e) {
      if (alive.current) setNotice(e instanceof ApiError || e instanceof Error ? e.message : "The live investigation could not be started.");
    } finally {
      if (alive.current) setStarting(false);
    }
  };

  const st = job ? STATUS[job.status] : null;
  const shown = job && !active ? Math.round(job.elapsed_s) : elapsed;
  return (
    <div className="wrap">
      <PageTitle title="LIVE INVESTIGATION" sub="Runs the deterministic investigation agent now, against live TigerGraph, for one benchmark case. Executed against live TigerGraph. No FI_Case write performed." />
      <Card title="Run a live investigation" hint="read-only preview">
        <div className="replay" style={{ gap: 12, flexWrap: "wrap" }}>
          <label htmlFor="live-case" className="faint">Case</label>
          <select id="live-case" value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={active} aria-label="Case">
            {CASE_IDS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button className="btn primary" onClick={run} disabled={active}><Icon name="play" /> Run Live Investigation</button>
          {st && <span aria-label="Status"><Badge tone={st.tone} dot>{st.label}</Badge></span>}
          {(active || job) && <span className="faint" aria-label="Elapsed time">{shown} s{active ? " elapsed (about 15 to 50 s)" : ""}</span>}
        </div>
        <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>Only the case id is sent. The server takes the trigger, the transaction and the as-of time from its own case pack.</div>
      </Card>

      {notice && <div className="banner warn" role="alert" style={{ marginTop: 12 }}><Icon name="alert" /><span>{notice}</span></div>}

      {job?.status === "failed" && (
        <div className="banner err" role="alert" style={{ marginTop: 12 }}>
          <Icon name="alert" />
          <span>
            <b>Live investigation failed: {job.error}.</b> No live result was produced, and nothing was substituted for it.
            The stored replay is unaffected: <a href={href(`/investigations/${job.case_id}`)}>open the stored replay of {job.case_id}</a> (a recorded result, not a live run).
          </span>
        </div>
      )}

      {job?.status === "completed" && job.result && (
        <div className="stack" style={{ marginTop: 12 }}>
          <div className="banner sim" role="note"><Icon name="pulse" /><span><b>LIVE INVESTIGATION</b> · Executed against live TigerGraph. No FI_Case write performed.</span></div>
          <Consistency r={job.result} />
          <Summary r={job.result} />
          <h2 style={{ margin: "8px 0 0" }}>Investigation trace</h2>
          <div className="faint" style={{ fontSize: 12 }}>
            The live run returned this trace when it finished; steps were not streamed while it ran. The controls below replay the recorded trace of this live run.
          </div>
          <Workspace d={job.result.detail} demo={false} />
        </div>
      )}
    </div>
  );
}
