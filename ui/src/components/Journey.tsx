import type { CaseDetail } from "../types";
import type { Replay } from "../replay";
import { Icon } from "./ui";

const STEPS: { key: string; label: string }[] = [
  { key: "hero", label: "Fraud signal" }, { key: "agent", label: "Investigation agent" }, { key: "evidence", label: "Evidence" }, { key: "graph", label: "Graph relationships" },
  { key: "patterns", label: "Pattern detection" }, { key: "uncertainty", label: "Uncertainty" }, { key: "requests", label: "Additional evidence" },
  { key: "action", label: "Next best action" }, { key: "approval", label: "Approval route" }, { key: "case", label: "Case creation" }, { key: "summary", label: "Explanation" },
];

export function Journey({ detail, replay }: { detail: CaseDetail; replay: Replay }) {
  const vis = (k: string) => replay.visible(k === "agent" ? "hero" : k === "approval" ? "action" : k);
  let lastDone = -1;
  STEPS.forEach((s, i) => { if (vis(s.key)) lastDone = i; });
  const label = (k: string, l: string) => (k === "requests" && detail.requests.length === 0 ? "No extra evidence needed" : l);
  return (
    <nav className="journey" aria-label="Investigation journey">
      {STEPS.map((s, i) => {
        const done = vis(s.key);
        const now = replay.playing && i === lastDone;
        return (
          <div key={s.key} className={`jstep ${done ? "done" : ""} ${now ? "now" : ""}`} aria-current={now ? "step" : undefined}>
            <span className="node">{done && !now ? <Icon name="check" size={12} /> : null}</span>
            <span>{label(s.key, s.label)}</span>
          </div>
        );
      })}
    </nav>
  );
}
