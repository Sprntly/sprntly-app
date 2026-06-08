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
        logger.exception("auto-sync failed for %s/%s", company_id, provider)
        try:
            db.update_connection_sync(
                company_id, provider, last_sync_at=utc_now(),
                last_sync_error=str(e)[:500],
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
        # sync paths, not a kg_ingest puller — nothing to kick off here.
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
