"""Auto-sync on connect — kick a connector's ingest right after it connects.

When a user connects a tool (OAuth callback or API-key connect), we want the
KG to actually populate without waiting for the weekly run. This module exposes
a fire-and-forget kickoff that runs the provider's `sync_provider` in a daemon
thread so it never blocks the callback redirect, stamping last_sync_at /
last_sync_error on the connection row so Settings can show status.

Error-isolated by design: a kickoff failure (bad token, API down, no puller)
is logged + stamped on the row, never raised into the connect flow. Providers
with no ingest puller are silently no-ops.
"""
from __future__ import annotations

import logging
import threading

from app import db
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.db.client import utc_now
from app.graph.facade import GraphFacade
from app.kg_ingest.runner import PULLERS, sync_provider, token_for

logger = logging.getLogger(__name__)


def _run_sync(company_id: str, provider: str) -> None:
    """Blocking sync body — runs inside the daemon thread. Fully isolated:
    any failure is logged and stamped as last_sync_error, never raised."""
    try:
        import json

        row = db.get_connection(company_id, provider)
        if not row:
            logger.info("auto-sync: %s no longer connected for %s — skipping",
                        provider, company_id)
            return
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        token = token_for(provider, token_json)
        facade = GraphFacade()
        result = sync_provider(facade, company_id, provider, token=token)
        err = "; ".join(result.get("errors") or []) or None
        db.update_connection_sync(
            company_id, provider, last_sync_at=utc_now(),
            last_sync_error=err[:500] if err else None,
        )
        logger.info("auto-sync done: %s/%s records=%s signals=%s",
                    company_id, provider, result.get("records"), result.get("signals"))
    except (TokenEncryptionError, Exception) as e:  # noqa: BLE001 — fully isolated
        # An auth failure (401/403) means the stored OAuth token expired or was
        # revoked — an EXPECTED, recoverable condition. Don't flood ERROR logs
        # with a full traceback every sync cycle: log a WARNING and stamp the
        # connection so the UI can prompt a reconnect. Genuine errors still get
        # the full ERROR traceback.
        status = getattr(e, "status_code", None)
        if status in (401, 403):
            logger.warning(
                "auto-sync: %s token for %s is invalid (%s) — reconnect required",
                provider, company_id, status,
            )
            error_msg = f"{provider} authorization expired — reconnect required"
        else:
            logger.exception("auto-sync failed for %s/%s", company_id, provider)
            error_msg = str(e)
        try:
            db.update_connection_sync(
                company_id, provider, last_sync_at=utc_now(),
                last_sync_error=error_msg[:500],
            )
        except Exception:  # noqa: BLE001
            logger.warning("auto-sync: could not stamp error for %s/%s",
                           company_id, provider, exc_info=True)


def kickoff_sync(company_id: str, provider: str) -> bool:
    """Fire-and-forget: start a background ingest for a just-connected provider.

    Returns True if a sync thread was started, False if the provider has no
    ingest puller (nothing to sync). Never blocks; never raises into the
    caller's connect flow."""
    if provider not in PULLERS:
        # Providers like figma / slack / google-drive have their own corpus
        # sync paths, not a kg_ingest puller — kick a corpus seed instead (see
        # kickoff_corpus_seed, wired into those providers' sync-to-corpus routes).
        return False
    try:
        t = threading.Thread(
            target=_run_sync, args=(company_id, provider),
            name=f"auto-sync-{provider}", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a thread-spawn failure break connect
        logger.exception("auto-sync: failed to start thread for %s/%s",
                         company_id, provider)
        return False


# ─────────────────────── corpus-seed-on-arrival ───────────────────────
#
# Connector pullers (kickoff_sync above) cover GitHub/ClickUp/HubSpot/Fireflies.
# But docs arrive on the *corpus* path too — manual file uploads and the
# Drive/Slack/Figma sync-to-corpus routes — and those weren't reaching the KG
# until the next brief ran a seed. kickoff_corpus_seed closes that gap: it runs
# the same incremental, content-hash-deduped corpus extraction the brief uses,
# but eagerly in the background the moment a doc lands. By brief time the KG is
# already warm, so the brief's own seed is a cheap no-op.

# Per-company locks so overlapping kickoffs (e.g. several files uploaded at once)
# serialize instead of redundantly re-extracting the same corpus in parallel.
_corpus_seed_locks: dict[str, threading.Lock] = {}
_corpus_seed_locks_guard = threading.Lock()


def _corpus_seed_lock(company_id: str) -> threading.Lock:
    with _corpus_seed_locks_guard:
        lock = _corpus_seed_locks.get(company_id)
        if lock is None:
            lock = threading.Lock()
            _corpus_seed_locks[company_id] = lock
        return lock


def _run_corpus_seed(company_id: str, slug: str) -> None:
    """Blocking incremental corpus extraction — runs inside the daemon thread.

    Fully isolated: any failure is logged, never raised. Serialized per company
    via a lock so a burst of uploads doesn't spin up redundant parallel seeds;
    a queued run picks up everything on disk (incl. whatever triggered it), and
    extraction is content-keyed idempotent so a re-extract self-dedups."""
    # Lazy import avoids a module-load cycle (synthesis_brief → kg_ingest.runner).
    from app.synthesis_brief import _seed_from_corpus

    lock = _corpus_seed_lock(company_id)
    with lock:
        try:
            facade = GraphFacade()
            result = _seed_from_corpus(facade, company_id, slug)
            logger.info(
                "corpus-seed done: %s (slug=%s) docs=%s signals=%s unchanged=%s",
                company_id, slug, result.get("docs"), result.get("signals"),
                result.get("unchanged"),
            )
        except Exception:  # noqa: BLE001 — fully isolated
            logger.exception("corpus-seed failed for %s (slug=%s)", company_id, slug)


def kickoff_corpus_seed(company_id: str, slug: str) -> bool:
    """Fire-and-forget: extract newly-arrived corpus docs into the KG.

    Called right after a file upload or a connector→corpus sync (Drive/Slack/
    Figma) so manually- or connector-supplied docs reach the KG without waiting
    for the next brief. Incremental + content-hash deduped, so repeated kickoffs
    are cheap. Never blocks; never raises into the caller's request flow."""
    try:
        t = threading.Thread(
            target=_run_corpus_seed, args=(company_id, slug),
            name="corpus-seed", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a thread-spawn failure break the request
        logger.exception("corpus-seed: failed to start thread for %s (slug=%s)",
                         company_id, slug)
        return False
