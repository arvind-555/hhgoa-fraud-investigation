"""Starlette app for the UI. READ-ONLY: no write endpoints, no GSQL, no credentials in responses, as_of never accepted from the client.

Run: python src/ui_api/server.py [port]        (default 8787; serves ui/dist too when it has been built)
"""
import asyncio
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from ui_api.service import CaseNotFound, CaseService  # noqa: E402

DIST = ROOT / "ui" / "dist"
GRAPH_TIMEOUT_S = 25


def make_app(service=None, graph_probe=None):
    svc = service or CaseService()
    probe = graph_probe or _live_probe

    async def health(request):
        return JSONResponse({"status": "ok", "cases": len(svc.ids()), "mode": "read-only"})

    async def cases(request):
        return JSONResponse(svc.list_cases())

    async def case(request):
        try:
            return JSONResponse(svc.get_case(request.path_params["case_id"]))
        except CaseNotFound:
            raise HTTPException(404, "case not found")

    async def graph_check(request):
        cid = request.path_params["case_id"]
        try:
            gid = svc.get_case(cid)["case"]["graph_case_id"]
        except CaseNotFound:
            raise HTTPException(404, "case not found")
        return JSONResponse(await _run(probe, gid))

    async def overview(request):
        return JSONResponse(svc.overview())

    async def overview_graph(request):
        return JSONResponse(svc.overview_graph())

    async def customers(request):
        return JSONResponse(svc.customers())

    async def policies(request):
        return JSONResponse(_policies())

    async def system(request):
        return JSONResponse({"api": {"ok": True, "cases": len(svc.ids()), "records": sum(1 for c in svc.ids() if svc._raw(c)[1] is not None)},
                             "graph": await _run(probe, "CASE-HHG-014"),
                             "safety": ["Read-only API: no write endpoints", "No GSQL accepted from the client", "as_of is fixed by the backend runner, never by the UI", "No credentials in any response"]})

    routes = [Route("/api/health", health), Route("/api/cases", cases), Route("/api/cases/{case_id}", case), Route("/api/cases/{case_id}/graph-check", graph_check),
              Route("/api/overview", overview), Route("/api/graph", overview_graph), Route("/api/customers", customers), Route("/api/policies", policies), Route("/api/system", system)]
    if (DIST / "index.html").exists():
        routes.append(Mount("/assets", StaticFiles(directory=DIST / "assets")))
        routes.append(Route("/{path:path}", lambda request: FileResponse(DIST / "index.html")))
    return Starlette(routes=routes)


async def _run(fn, *a):
    try:
        return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, fn, *a), GRAPH_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"reachable": False, "error": "TigerGraph did not answer in time"}


def _live_probe(graph_case_id):
    """Read-only: fetch one FI_Case vertex through the whitelisted GraphIO. Never returns credentials or raw error bodies."""
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
        from agent.graphio import GraphIO
        v = GraphIO().get_vertex("FI_Case", graph_case_id)
    except Exception as e:  # noqa: BLE001 - surface only the exception class
        return {"reachable": False, "error": f"graph unavailable ({type(e).__name__})"}
    if not v:
        return {"reachable": True, "found": False, "graph_case_id": graph_case_id}
    a = v.get("attributes", v)
    return {"reachable": True, "found": True, "graph_case_id": graph_case_id, "revision": a.get("revision"), "as_of_epoch": a.get("as_of_epoch"), "status": a.get("status")}


def _policies():
    from rag.chunks import build_all
    docs = [{"ref": c["source_ref"].replace("policy:", ""), "text": c["text"]} for c in build_all() if c["doc_type"] == "policy"]
    return {"documents": docs, "routes": {"auto": "No approval", "L1": "Team lead", "L2": "Fraud manager"}}


app = make_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8787, log_level="warning")
