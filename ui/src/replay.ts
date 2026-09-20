import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CaseDetail, ReplayEvent } from "./types";

export interface Replay {
  shown: number; total: number; playing: boolean; done: boolean; speed: number;
  revealed: Set<string>; current: ReplayEvent | null;
  play: () => void; pause: () => void; restart: () => void; finish: () => void; setSpeed: (s: number) => void;
  visible: (panel: string) => boolean;
}

const BASE_DELAY_MS = 1100;

/** Controlled playback of the STORED investigation events. It only decides how much of the backend result is on screen; it never computes anything.
 *  autoplay=false shows the complete result immediately (a "replay" can still be started). */
export function useReplay(detail: CaseDetail | null, autoplay: boolean): Replay {
  const events = detail?.activity.events ?? [];
  const total = events.length;
  const [shown, setShown] = useState(autoplay ? 0 : total);
  const [playing, setPlaying] = useState(autoplay);
  const [speed, setSpeed] = useState(1);
  const key = detail?.header.case_id ?? "";
  const seeded = useRef("");

  useEffect(() => {
    if (!detail || seeded.current === key + String(autoplay)) return;
    seeded.current = key + String(autoplay);
    setShown(autoplay ? 0 : detail.activity.events.length);
    setPlaying(autoplay);
  }, [detail, key, autoplay]);

  useEffect(() => {
    if (!playing) return;
    if (shown >= total) {
      setPlaying(false);
      return;
    }
    const t = window.setTimeout(() => setShown((s) => Math.min(total, s + 1)), BASE_DELAY_MS / speed);
    return () => window.clearTimeout(t);
  }, [playing, shown, total, speed]);

  const revealed = useMemo(() => {
    const s = new Set<string>();
    events.slice(0, shown).forEach((e) => e.reveal.forEach((r) => s.add(r)));
    return s;
  }, [events, shown]);

  const restart = useCallback(() => {
    setShown(0);
    setPlaying(true);
  }, []);
  const noRequests = (detail?.requests.length ?? 0) === 0;
  const visible = useCallback(
    (panel: string) => shown >= total || revealed.has(panel) || (panel === "requests" && noRequests && revealed.has("uncertainty")) || (panel === "sar" && revealed.has("summary")),
    [shown, total, revealed, noRequests],
  );

  return {
    shown, total, playing, done: shown >= total, speed, revealed, current: shown > 0 && shown <= total ? events[shown - 1] : null,
    play: () => { if (shown >= total) setShown(0); setPlaying(true); }, pause: () => setPlaying(false), restart,
    finish: () => { setPlaying(false); setShown(total); }, setSpeed, visible,
  };
}
