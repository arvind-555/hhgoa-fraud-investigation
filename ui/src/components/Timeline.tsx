import { useState } from "react";
import type { CaseDetail } from "../types";
import type { Replay } from "../replay";
import { ms } from "../format";
import { Badge, Card, EmptyState, Icon } from "./ui";

export function Timeline({ d, replay }: { d: CaseDetail; replay: Replay }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const events = d.activity.events.slice(0, replay.shown);
  const stepOf = (n: number) => d.activity.steps.find((s) => s.step === n);
  const toggle = (i: number) => setOpen((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });
  return (
    <Card title="Investigation timeline" hint={d.activity.total_ms ? `${ms(d.activity.total_ms)} measured` : undefined} flush>
      {events.length === 0 ? (
        <EmptyState title="Waiting for the investigation to start" body="Press play to watch the agent work." />
      ) : (
        <ol className="tl" style={{ listStyle: "none", margin: 0, padding: "6px 0" }}>
          {events.map((e, idx) => {
            const st = stepOf(e.step);
            const now = replay.playing && idx === events.length - 1;
            const cls = `tl-item reveal ${e.simulated ? "sim" : "done"} ${now ? "now" : ""}`;
            return (
              <li key={e.i} className={cls}>
                <span className="tl-dot"><Icon name={e.simulated ? "users" : "check"} /></span>
                <div>
                  <div className="tl-head">
                    <b>{e.label}</b>
                    <small>{e.step === 0 ? `as-of ${d.header.opened_at}` : st ? (idx === 0 || events[idx - 1].step !== e.step ? `step ${e.step} of ${d.activity.steps.length} · ${ms(st.duration_ms)}${st.tool_calls ? ` · ${st.tool_calls} tool calls` : ""}` : `step ${e.step}`) : ""}</small>
                    {e.simulated && <Badge tone="sim">Simulated</Badge>}
                  </div>
                  <div className="tl-sum">{e.summary}</div>
                  <div className="faint" style={{ fontSize: 11.5 }}>{e.source}</div>
                  {e.detail.length > 0 && (
                    <>
                      <button className="tl-more" onClick={() => toggle(e.i)} aria-expanded={open.has(e.i)}>{open.has(e.i) ? "Hide details" : "Show details"}</button>
                      {open.has(e.i) && <div className="tl-detail">{e.detail.map((x, k) => <div key={k}>{x}</div>)}</div>}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

export function AgentActivity({ d, replay }: { d: CaseDetail; replay: Replay }) {
  const rows = d.activity.events.filter((e) => e.tool || e.stage === "synthesis");
  const doneIdx = replay.shown;
  const pending = replay.playing ? rows.find((e) => e.i >= doneIdx) : undefined;
  return (
    <Card title="Agent activity" hint={replay.playing ? <span className="pulse">Agent investigating…</span> : replay.done ? "Complete" : "Paused"} flush>
      {rows.map((e) => {
        const done = e.i < doneIdx;
        return (
          <div className="act" key={e.i} style={{ opacity: done ? 1 : 0.4 }}>
            {done ? <Icon name="check" /> : <span className={pending === e ? "pulse" : ""} style={{ width: 15 }}>•</span>}
            <div>
              <b>{e.label}</b>
              {done && <p>{e.summary}</p>}
            </div>
            <span className="faint mono">{e.tool ?? ""}</span>
          </div>
        );
      })}
    </Card>
  );
}
