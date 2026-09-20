import { useEffect, useState, type ReactNode } from "react";
import type { Tone } from "../format";

const PATHS: Record<string, string> = {
  check: "M20 6 9 17l-5-5",
  home: "M3 11 12 3l9 8M5 10v10h5v-6h4v6h5V10",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  users: "M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M21 20v-1a4 4 0 0 0-3-3.9M16 4.1a3.5 3.5 0 0 1 0 6.8",
  graph: "M6 6a2 2 0 1 0 0 .01M18 5a2 2 0 1 0 0 .01M12 19a2 2 0 1 0 0 .01M8 7l8-1M7 8l4 9M17 7l-4 10",
  book: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 19a2 2 0 0 1 2-2h13",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  play: "M7 4v16l13-8z",
  pause: "M8 5v14M16 5v14",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16M21 21l-4.3-4.3",
  alert: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0",
  info: "M12 16v-4M12 8h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20",
  chevron: "m9 6 6 6-6 6",
  down: "m6 9 6 6 6-6",
  arrow: "M5 12h14M13 6l6 6-6 6",
  db: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  shield: "M12 3 4 6v6c0 5 3.4 8 8 9 4.6-1 8-4 8-9V6z",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  x: "M18 6 6 18M6 6l12 12",
  sort: "M8 9l4-4 4 4M16 15l-4 4-4-4",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.info} />
    </svg>
  );
}

export function Badge({ tone = "neutral", dot, children, title }: { tone?: Tone | "sim"; dot?: boolean; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export function Card({ title, hint, children, className = "", flush }: { title?: string; hint?: ReactNode; children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <header className="card-h">
          <h3>{title}</h3>
          {hint && <span className="hint">{hint}</span>}
        </header>
      )}
      <div className={flush ? "" : "card-b"}>{children}</div>
    </section>
  );
}

export function Skeleton({ h = 16, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} aria-hidden="true" />;
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="stack" role="status" aria-label={label}>
      <Skeleton h={28} w="40%" />
      <Skeleton h={120} />
      <Skeleton h={220} />
    </div>
  );
}

export function EmptyState({ title, body }: { title: string; body?: string }) {
  return (
    <div className="state">
      <Icon name="folder" size={22} />
      <h4>{title}</h4>
      {body && <p>{body}</p>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state err" role="alert">
      <Icon name="alert" size={22} />
      <h4>Something went wrong</h4>
      <p>{message}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          <Icon name="refresh" /> Try again
        </button>
      )}
    </div>
  );
}

export type Async<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then(
      (data) => live && setState({ data, error: null, loading: false }),
      (e: unknown) => live && setState({ data: null, error: e instanceof Error ? e.message : "Unexpected error", loading: false }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

export function AsyncView<T>({ a, children, empty }: { a: Async<T>; children: (d: T) => ReactNode; empty?: (d: T) => boolean }) {
  if (a.loading && !a.data) return <LoadingBlock />;
  if (a.error) return <ErrorState message={a.error} onRetry={a.reload} />;
  if (!a.data) return <EmptyState title="Nothing to show" />;
  if (empty?.(a.data)) return <EmptyState title="No results" body="Nothing matches the current view." />;
  return <>{children(a.data)}</>;
}
