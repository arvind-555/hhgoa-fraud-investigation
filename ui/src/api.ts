import type { CaseDetail, CaseSummary, CustomerRow, GraphData, GraphProbe, Overview, PolicyDocs, SystemStatus } from "./types";

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

// Read-only. There is deliberately no write call, no query call and no as_of parameter anywhere in the client.
export const api = {
  overview: () => get<Overview>("/api/overview"),
  cases: () => get<CaseSummary[]>("/api/cases"),
  case: (id: string) => get<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  graphCheck: (id: string) => get<GraphProbe>(`/api/cases/${encodeURIComponent(id)}/graph-check`),
  graph: () => get<GraphData>("/api/graph"),
  customers: () => get<CustomerRow[]>("/api/customers"),
  policies: () => get<PolicyDocs>("/api/policies"),
  system: () => get<SystemStatus>("/api/system"),
};
