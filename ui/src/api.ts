import type { CaseDetail, CaseSummary, CustomerRow, GraphData, GraphProbe, LiveJob, Overview, PolicyDocs, SystemStatus } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path);
  } catch {
    throw new ApiError(0, "The investigation service is not reachable.");
  }
  if (!res.ok) throw new ApiError(res.status, res.status === 404 ? "Not found." : `The service returned an error (${res.status}).`);
  return res.json() as Promise<T>;
}

// The one non-GET call: it starts a read-only live investigation PREVIEW. It sends the case id in the path and nothing else (no body, no query string, no as_of).
async function startLive(caseId: string): Promise<LiveJob> {
  let res: Response;
  try {
    res = await fetch(`/api/live/${encodeURIComponent(caseId)}`, { method: "POST" });
  } catch {
    throw new ApiError(0, "The investigation service is not reachable.");
  }
  if (res.status === 429) throw new ApiError(429, "Another live investigation is running. Try again in a few seconds.");
  if (!res.ok) throw new ApiError(res.status, res.status === 404 ? "Not found." : `The service returned an error (${res.status}).`);
  return res.json() as Promise<LiveJob>;
}

// There is deliberately no graph write, no query call and no as_of parameter anywhere in the client.
export const api = {
  overview: () => get<Overview>("/api/overview"),
  cases: () => get<CaseSummary[]>("/api/cases"),
  case: (id: string) => get<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  graphCheck: (id: string) => get<GraphProbe>(`/api/cases/${encodeURIComponent(id)}/graph-check`),
  graph: () => get<GraphData>("/api/graph"),
  customers: () => get<CustomerRow[]>("/api/customers"),
  policies: () => get<PolicyDocs>("/api/policies"),
  system: () => get<SystemStatus>("/api/system"),
  liveStart: startLive,
  liveStatus: (jobId: string) => get<LiveJob>(`/api/live/${encodeURIComponent(jobId)}`),
};
