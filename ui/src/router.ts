import { useEffect, useState } from "react";

export interface Route { path: string[]; query: URLSearchParams }

function parse(): Route {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [p, q = ""] = raw.split("?");
  return { path: p.split("/").filter(Boolean), query: new URLSearchParams(q) };
}

export function useRoute(): Route {
  const [r, setR] = useState<Route>(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

export const href = (path: string, query?: Record<string, string>) => `#${path}${query ? "?" + new URLSearchParams(query).toString() : ""}`;
export const go = (path: string, query?: Record<string, string>) => {
  window.location.hash = href(path, query).slice(1);
};
