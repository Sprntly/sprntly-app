"""Multi-agent document storage — QA test cases, technical design,
risk analysis, and traceability matrix.

All documents follow the same lifecycle as PRDs/Evidence:
  generating → ready | failed

Stored in a single `multi_agent_docs` table with a `doc_type` discriminator
(qa_test_cases, technical_design, risk_analysis, traceability_matrix).
"""
from app.db.client import require_client, retry_on_disconnect


DOC_TYPES = frozenset({
    "qa_test_cases",
    "technical_design",
    "risk_analysis",
    "traceability_matrix",
})


@retry_on_disconnect
def start_doc(
    brief_id: int,
    insight_index: int,
    prd_id: int | None,
    doc_type: str,
    title: str,
    run_id: str | None = None,
) -> int:
    """Insert a generating row and return its id."""
    assert doc_type in DOC_TYPES, f"unknown doc_type: {doc_type}"
    c = require_client()
    resp = c.table("multi_agent_docs").insert({
        "brief_id": brief_id,
        "insight_index": insight_index,
        "prd_id": prd_id,
        "doc_type": doc_type,
        "title": title,
        "payload_md": "",
        "status": "generating",
        "run_id": run_id,
    }).execute()
    return resp.data[0]["id"]


@retry_on_disconnect
def complete_doc(doc_id: int, title: str, md: str) -> None:
    c = require_client()
    c.table("multi_agent_docs").update({
        "title": title,
        "payload_md": md,
        "status": "ready",
        "error": None,
    }).eq("id", doc_id).execute()


@retry_on_disconnect
def fail_doc(doc_id: int, error: str) -> None:
    c = require_client()
    c.table("multi_agent_docs").update({
        "status": "failed",
        "error": (error or "")[:500],
    }).eq("id", doc_id).execute()


@retry_on_disconnect
def get_doc(doc_id: int) -> dict | None:
    c = require_client()
    resp = c.table("multi_agent_docs").select("*").eq("id", doc_id).limit(1).execute()
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def find_existing_doc(
    brief_id: int, insight_index: int, doc_type: str,
    run_id: str | None = None,
) -> dict | None:
    c = require_client()
    q = (
        c.table("multi_agent_docs")
        .select("*")
        .eq("brief_id", brief_id)
        .eq("insight_index", insight_index)
        .eq("doc_type", doc_type)
        .in_("status", ["ready", "generating"])
        .order("id", desc=True)
        .limit(1)
    )
    if run_id:
        q = q.eq("run_id", run_id)
    resp = q.execute()
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def get_docs_by_run(run_id: str) -> list[dict]:
    """Get all docs for a multi-agent run."""
    c = require_client()
    resp = (
        c.table("multi_agent_docs")
        .select("*")
        .eq("run_id", run_id)
        .order("doc_type")
        .execute()
    )
    return resp.data or []


@retry_on_disconnect
def get_run_status(run_id: str) -> dict:
    """Aggregate status for a multi-agent run."""
    docs = get_docs_by_run(run_id)
    statuses = [d["status"] for d in docs]
    if all(s == "ready" for s in statuses):
        overall = "ready"
    elif any(s == "generating" for s in statuses):
        overall = "generating"
    elif any(s == "failed" for s in statuses):
        overall = "partial"
    else:
        overall = "unknown"
    return {
        "run_id": run_id,
        "status": overall,
        "docs": {d["doc_type"]: {"id": d["id"], "status": d["status"], "title": d["title"]} for d in docs},
    }
