"""Background brief generation. Kicked off on app startup; ensures a brief
exists for each configured dataset without blocking startup.
"""
import asyncio
import logging

from app.corpus import load_corpus
from app.db import get_current_brief, save_brief
from app.llm import call_json
from app.prompts import BRIEF_SCHEMA_VERSION, BRIEF_SYSTEM, BRIEF_USER_TEMPLATE

logger = logging.getLogger(__name__)

# In-memory transient state. Single uvicorn worker → a dict is fine.
_status: dict[str, str] = {}
_errors: dict[str, str] = {}


def get_status(dataset: str) -> dict:
    """Return one of: ready, generating, failed, empty (+ error message if any)."""
    if get_current_brief(dataset):
        return {"status": "ready"}
    s = _status.get(dataset, "empty")
    out: dict = {"status": s}
    if s == "failed" and dataset in _errors:
        out["error"] = _errors[dataset]
    return out


def _run_sync(dataset: str) -> None:
    corpus = load_corpus(dataset)
    user = BRIEF_USER_TEMPLATE.format(dataset=dataset, corpus=corpus.joined())
    payload = call_json(system=BRIEF_SYSTEM, user=user)
    save_brief(
        dataset,
        payload.get("week_label", ""),
        payload,
        schema_version=BRIEF_SCHEMA_VERSION,
    )


async def auto_generate_brief(dataset: str) -> None:
    """Generate a brief for `dataset` if one doesn't already exist.

    Errors are logged and stored on `_errors[dataset]`; the service keeps
    serving. The user can retry by restarting the service (e.g. after fixing
    the Anthropic API key).
    """
    if get_current_brief(dataset):
        logger.info("Brief already cached for %s, skipping auto-generate", dataset)
        return
    _status[dataset] = "generating"
    _errors.pop(dataset, None)
    logger.info("Auto-generating brief for %s ...", dataset)
    try:
        await asyncio.to_thread(_run_sync, dataset)
        _status[dataset] = "ready"
        logger.info("Brief generated for %s", dataset)
    except Exception as exc:
        _status[dataset] = "failed"
        _errors[dataset] = f"{type(exc).__name__}: {exc}"[:300]
        logger.exception("Brief generation failed for %s", dataset)


# Datasets the service will auto-generate briefs for on startup.
AUTO_DATASETS: tuple[str, ...] = ("asurion",)


async def auto_generate_all() -> None:
    for dataset in AUTO_DATASETS:
        await auto_generate_brief(dataset)
