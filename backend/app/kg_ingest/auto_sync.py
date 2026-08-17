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

import json
import logging
import threading
import time

from app import db
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.db.client import utc_now
from app.graph.facade import GraphFacade
from app.kg_ingest.runner import PULLERS, sync_provider, token_for

logger = logging.getLogger(__name__)

# Refresh an OAuth access token this many seconds BEFORE its nominal expiry, so
# a sync never races a just-expired token.
_TOKEN_REFRESH_SKEW_S = 300


def _token_is_fresh(token_json: dict) -> bool:
    """True iff we can PROVE the access token is still valid — `obtained_at +
    expires_in` is in the future past a safety skew. If freshness can't be
    proven (fields missing/non-numeric), return False so the caller refreshes
    rather than risk a 401."""
    obtained = token_json.get("obtained_at")
    expires_in = token_json.get("expires_in")
    if not isinstance(obtained, (int, float)) or not isinstance(expires_in, (int, float)):
        return False
    return time.time() < obtained + expires_in - _TOKEN_REFRESH_SKEW_S


def _maybe_refresh_token(
    company_id: str, provider: str, token_json: dict, *, force: bool = False
) -> dict:
    """Refresh an expiring OAuth access token, persist it, and return the updated
    token_json.

    GitHub user-to-server tokens expire ~8h, so a connection that synced
    yesterday would 401 every cycle without this. Uses the stored `refresh_token`
    (GitHub rotates it, so we persist the whole new payload). No-op for providers
    without refresh, when there's no `refresh_token`, or (unless `force`) when the
    current token is provably fresh.

    Best-effort: a refresh failure (refresh token expired ~6mo / revoked / OAuth
    not configured) logs a WARNING and returns the input unchanged, so the
    caller's sync surfaces the usual 401 → "reconnect required".

    Jira, Confluence (Atlassian) and Zoom are handled alongside github: their
    access tokens expire ~1h and their refresh tokens ROTATE, so — like github —
    we persist the whole new payload on every refresh. Confluence and Zoom
    additionally require company_id to survive the rewrite, because that is the
    credential their pullers are handed (see
    confluence_oauth.token_payload_to_store).

    Google Meet is here for a DIFFERENT reason. Its refresh tokens do not
    rotate, so nothing is stranded by a throwaway refresh — but Google's refresh
    response omits `refresh_token` ENTIRELY, so persisting it verbatim blanks
    the stored one and the connection dies at the following cycle. It carries
    the same company_id obligation as Confluence and Zoom."""
    if provider not in ("github", "jira", "confluence", "zoom", "google_meet"):
        return token_json
    refresh_token = token_json.get("refresh_token")
    if not refresh_token:
        return token_json
    if not force and _token_is_fresh(token_json):
        return token_json
    try:
        from app.connectors.tokens import encrypt_token_json

        if provider == "confluence":
            from app.connectors import confluence_oauth

            new_json_str = confluence_oauth.token_payload_to_store(
                confluence_oauth.refresh_access_token(refresh_token),
                # Dropping this here breaks the NEXT sync, not this refresh:
                # token_for("confluence", ...) reads exactly this field.
                company_id=company_id,
                keep_refresh_token=refresh_token,
            )
        elif provider == "zoom":
            from app.connectors import zoom_oauth

            new_json_str = zoom_oauth.token_payload_to_store(
                zoom_oauth.refresh_access_token(refresh_token),
                # Same trap as confluence above: dropping this here breaks the
                # NEXT sync, not this refresh, because token_for("zoom", ...)
                # reads exactly this field.
                company_id=company_id,
                keep_refresh_token=refresh_token,
            )
        elif provider == "google_meet":
            from app.connectors import google_meet

            new_json_str = google_meet.token_payload_to_store(
                google_meet.refresh_access_token(refresh_token),
                # Same trap as confluence/zoom above: dropping this here breaks
                # the NEXT sync, not this refresh, because
                # token_for("google_meet", ...) reads exactly this field.
                company_id=company_id,
                # And this one is not optional on Google: the refresh response
                # has no refresh_token at all, so without the carry-forward the
                # stored credential is replaced by nothing.
                keep_refresh_token=refresh_token,
            )
        elif provider == "jira":
            from app.connectors import jira_oauth

            new_json_str = jira_oauth.token_payload_to_store(
                jira_oauth.refresh_access_token(refresh_token)
            )
        else:
            from app.connectors import github_app

            new_json_str = github_app.token_payload_to_store(
                github_app.refresh_user_token(refresh_token)
            )
        db.update_connection_tokens(
            company_id, provider, encrypt_token_json(new_json_str)
        )
        logger.info("auto-sync: refreshed %s access token for %s", provider, company_id)
        return json.loads(new_json_str)
    except Exception:  # noqa: BLE001 — refresh is best-effort
        logger.warning(
            "auto-sync: %s token refresh failed for %s — surfacing reconnect",
            provider, company_id, exc_info=True,
        )
        return token_json


def _run_sync(company_id: str, provider: str) -> None:
    """Blocking sync body — runs inside the daemon thread. Fully isolated:
    any failure is logged and stamped as last_sync_error, never raised."""
    try:
        # Start-of-pull trace, pairing with the "auto-sync done" line below —
        # without it a hung provider pull is indistinguishable from one that
        # never started.
        logger.info("auto-sync: START pull %s/%s", company_id, provider)
        row = db.get_connection(company_id, provider)
        if not row:
            logger.info("auto-sync: %s no longer connected for %s — skipping",
                        provider, company_id)
            return
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        # Proactively refresh an expiring OAuth token (github) before the pull,
        # so a day-old connection doesn't 401 on every sync.
        token_json = _maybe_refresh_token(company_id, provider, token_json)
        facade = GraphFacade()
        try:
            result = sync_provider(
                facade, company_id, provider, token=token_for(provider, token_json)
            )
        except Exception as exc:  # noqa: BLE001 — narrow to auth, else re-raise
            # Reactive fallback: a token that slipped past the freshness check
            # (clock skew, or revoked-then-reissued server-side) — force one
            # refresh + retry before surfacing the failure.
            if getattr(exc, "status_code", None) not in (401, 403):
                raise
            refreshed = _maybe_refresh_token(
                company_id, provider, token_json, force=True
            )
            if refreshed.get("access_token") in (None, token_json.get("access_token")):
                raise  # refresh produced nothing new → graceful reconnect handling
            result = sync_provider(
                facade, company_id, provider, token=token_for(provider, refreshed)
            )
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


def _run_drive_sync(company_id: str) -> None:
    """Blocking Google Drive sync body — runs inside the daemon thread.
    Fully isolated: sync_google_drive/sync_service_account stamp their own
    per-file errors; genuine failures raised before stamping are caught and
    stamped here best-effort.

    Mode-aware, reading ``settings.google_drive_access_mode`` the same way
    the connector routes do. In ``service_account`` mode the picked-file
    gate does not apply — that mode has no Picker selection, so it is
    unconfigured only when there is no dataset, or no service account has
    been provisioned yet; a connected-but-unconfigured row is then a quiet
    no-op, not an error, so a scheduler cycle never stamps a scary Settings
    error for a state the user never acted on. ``oauth``/``oauth_folder``
    keep the original gate (dataset AND at least one picked file/folder)
    unchanged."""
    try:
        import json as _json

        from app.config import settings
        from app.connectors.google_drive_sync import sync_google_drive
        from app.connectors.google_service_account import sync_service_account

        row = db.get_connection(company_id, "google_drive")
        if not row:
            return
        try:
            config = _json.loads(row.get("config_json") or "{}")
        except (TypeError, ValueError):
            config = {}

        if settings.google_drive_access_mode == "service_account":
            if not config.get("dataset"):
                logger.info(
                    "auto-sync: google_drive (service-account mode) for %s "
                    "has no dataset yet — skipping", company_id,
                )
                return
            if not row.get("sa_key_encrypted"):
                logger.info(
                    "auto-sync: google_drive (service-account mode) for %s "
                    "has no service account provisioned yet — skipping",
                    company_id,
                )
                return
            result = sync_service_account(company_id)
        else:
            if not (config.get("dataset") and config.get("files")):
                logger.info(
                    "auto-sync: google_drive for %s has no dataset/picked "
                    "files yet — skipping", company_id,
                )
                return
            result = sync_google_drive(company_id=company_id)

        logger.info(
            "auto-sync done: %s/google_drive synced=%s kg_queued=%s",
            company_id, len(result.synced), len(result.kg_queued),
        )
    except Exception as e:  # noqa: BLE001 — fully isolated
        logger.warning("auto-sync: google_drive sync failed for %s: %s",
                       company_id, e)
        try:
            db.update_connection_sync(
                company_id, "google_drive", last_sync_at=utc_now(),
                last_sync_error=str(e)[:500],
            )
        except Exception:  # noqa: BLE001
            logger.warning("auto-sync: could not stamp error for %s/google_drive",
                           company_id, exc_info=True)


def kickoff_sync(company_id: str, provider: str) -> bool:
    """Fire-and-forget: start a background ingest for a just-connected provider.

    Returns True if a sync thread was started, False if the provider has no
    ingest puller (nothing to sync). Never blocks; never raises into the
    caller's connect flow."""
    if provider == "google_drive":
        # Drive has no token puller — its records come from the connection's
        # picked-file config. Run the full corpus+KG sync in the background
        # (downloads changed files, refreshes the corpus copy, and hands
        # changed docs to kg_ingest.drive_extract as connector-origin signals).
        try:
            t = threading.Thread(
                target=_run_drive_sync, args=(company_id,),
                name="auto-sync-google-drive", daemon=True,
            )
            t.start()
            return True
        except Exception:  # noqa: BLE001 — never let a thread-spawn failure break connect
            logger.exception("auto-sync: failed to start thread for %s/google_drive",
                             company_id)
            return False
    if provider not in PULLERS:
        # Providers like figma / slack have their own corpus sync paths, not a
        # kg_ingest puller — kick a corpus seed instead (see
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


def _run_slack_corpus_sync(company_id: str) -> None:
    """Blocking Slack corpus sync + KG seed — runs inside the daemon thread.

    Company-level: one sync per company per refresh cycle, using the
    company's Slack sync connection and its shared pull-channel selection
    (see connectors/slack_company.py). Fully isolated — any failure is
    logged, never raised."""
    from app.connectors.slack_sync import sync_slack
    from app.db.companies import slug_for_company_id

    try:
        slug = slug_for_company_id(company_id)
        if not slug:
            logger.warning(
                "slack-refresh: no dataset slug for company=%s — skipping",
                company_id,
            )
            return
        result = sync_slack(slug, company_id=company_id)
        # A SYNC THAT READ NOTHING IS NOT "done". Six staging tenants spent
        # days logging this line at INFO with `channels=0 messages=0 errors=1`
        # while their Slack credential was dead — the word "done" and the INFO
        # level are why nobody looked (found 2026-08-16). A run that produced
        # no messages AND carried errors is reported at WARNING, with the
        # reasons, so it reads as the failure it is.
        if result.errors and not result.messages_count:
            logger.warning(
                "slack-refresh FAILED: %s (slug=%s) channels=%s messages=0 — %s",
                company_id, slug, result.channels_count,
                "; ".join(result.errors)[:400],
            )
        else:
            logger.info(
                "slack-refresh done: %s (slug=%s) channels=%s messages=%s errors=%s",
                company_id, slug, result.channels_count, result.messages_count,
                len(result.errors),
            )
        # The corpus file landed — extract it into the KG now instead of
        # waiting for the next brief's seed (same path as the manual sync
        # route's _seed_corpus_after_sync).
        _run_corpus_seed(company_id, slug)
    except Exception:  # noqa: BLE001 — fully isolated
        logger.exception("slack-refresh failed for %s", company_id)


def kickoff_slack_corpus_sync(company_id: str) -> bool:
    """Fire-and-forget: refresh the company's Slack corpus + KG.

    Called by the scheduled connector refresh for every company with an
    active Slack connection. Returns False (nothing started) when the
    company has no usable Slack sync connection. Never blocks; never
    raises into the scheduler loop."""
    from app.connectors.slack_company import resolve_company_slack_row

    try:
        if not resolve_company_slack_row(company_id):
            return False
        t = threading.Thread(
            target=_run_slack_corpus_sync, args=(company_id,),
            name="slack-refresh", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a spawn failure break the cycle
        logger.exception("slack-refresh: failed to start thread for %s", company_id)
        return False


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


# ── Roadmap → KG on upload ──────────────────────────────────────────────────
# The workspace roadmap has its own one-per-workspace, replace-semantics ingest
# (kg_ingest.roadmap) rather than riding the corpus path — it's a priorities
# anchor, not corpus evidence. Same shape as kickoff_corpus_seed above: a daemon
# thread so the onboarding strategy step's upload response never waits on an
# extraction, per-company lock so a burst of replaces serializes, and total error
# isolation because synthesis_brief.seed_incremental re-runs it on the next brief
# anyway (it doubles as the retry + grandfather path).

def _roadmap_ingest_lock(company_id: str) -> "threading.RLock":
    """The per-company roadmap-ingest lock.

    Owned by kg_ingest.roadmap so EVERY entry point serializes on the same
    object — this kickoff AND synthesis_brief's seed leg, which calls
    ingest_roadmap directly. A lock private to this module would leave the seed
    leg racing the upload. Reentrant, so holding it here and re-acquiring inside
    ingest_roadmap is safe."""
    from app.kg_ingest.roadmap import ingest_lock

    return ingest_lock(company_id)


def _run_roadmap_ingest(company_id: str, workspace_id: str | None) -> None:
    """Blocking roadmap extraction — runs inside the daemon thread.

    Fully isolated: any failure is logged, never raised. Serialized per company
    so two quick replaces don't race each other's expiry pass; the queued run
    reads whatever roadmap_doc holds at that point, and the content-hash ledger
    makes a redundant run free."""
    from app.kg_ingest.roadmap import ingest_roadmap

    with _roadmap_ingest_lock(company_id):
        try:
            result = ingest_roadmap(company_id, workspace_id,
                                    facade=GraphFacade())
            logger.info("roadmap-ingest done: %s (ws=%s) %s",
                        company_id, workspace_id, result)
        except Exception:  # noqa: BLE001 — fully isolated
            logger.exception("roadmap-ingest failed for %s (ws=%s)",
                             company_id, workspace_id)


def kickoff_roadmap_ingest(company_id: str, workspace_id: str | None) -> bool:
    """Fire-and-forget: extract a just-uploaded roadmap into the KG.

    Called right after POST /v1/company/roadmap-doc stores the file so the
    company's stated bets reach the graph in seconds instead of waiting for the
    next brief. Never blocks the upload response; never raises into the request
    flow. A dropped kickoff self-heals — seed_incremental ingests the same
    roadmap on the next brief generation."""
    try:
        t = threading.Thread(
            target=_run_roadmap_ingest, args=(company_id, workspace_id),
            name="roadmap-ingest", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a thread-spawn failure break the request
        logger.exception("roadmap-ingest: failed to start thread for %s (ws=%s)",
                         company_id, workspace_id)
        return False


# ── Call index refresh ──────────────────────────────────────────────────────
#
# The call index (app/call_index.py) holds cheap metadata for every call in a
# connected transcript source, so a listing question is a DB read instead of a
# 168-second corpus pass. It is only worth anything if it is POPULATED, and an
# index nobody fills fails in the quietest possible way: every interception in
# qa_agent returns None, the question degrades to the old expensive path, and
# nothing anywhere reports a problem.
#
# So it gets the same two triggers every other connector has — the moment it
# connects, and every scheduler cycle thereafter — plus a third the others do
# not need: `call_index.ensure_fresh` tops it up inline on the read path when a
# call may have landed since the last cycle. This is the same gap Fortune's
# d30ca7ee closed for Slack ("sync the moment it's connected, not six hours
# later"), with the read-path top-up added because a 6-hour-old call list is
# not merely incomplete — `answer_listing` states a COUNT, so it is WRONG.

def _run_call_index_sync(company_id: str) -> None:
    """Blocking call-index refresh — runs inside the daemon thread. Fully
    isolated: `call_index.sync_all_sources` already stamps each provider's own
    failure on `call_index_sync` (which is what makes the failure visible to
    the read path), so this only has to keep it out of the caller's flow.

    Every connected source, in one pass. Kicking per provider instead would
    race two threads of the same name onto the same company and duplicate the
    work for a tenant that has both.

    INCREMENTAL after the first success, exactly like `ensure_fresh`'s
    read-path top-up. Passing no `since` made every 20-minute scheduler cycle
    a full ten-page re-sync per company, which burned through a tenant's
    Fireflies daily API quota on 2026-08-15 and 429-blocked every other
    Fireflies read for that account until the next UTC midnight. The first
    sync of a fresh connection still gets `since=None` — the full history
    pull it needs."""
    from app import call_index

    try:
        written = call_index.sync_all_sources(
            company_id, since=call_index.incremental_since(company_id)
        )
        if written is None:
            logger.info("call-index: no transcript source for %s — nothing to do",
                        company_id)
            return
        logger.info("call-index refresh done: %s calls=%s", company_id, written)
    except Exception:  # noqa: BLE001 — fully isolated; already stamped
        logger.warning("call-index refresh failed for %s", company_id, exc_info=True)


def kickoff_call_index_sync(company_id: str) -> bool:
    """Fire-and-forget: refresh this company's call index.

    Called from the Fireflies connect route and from the scheduled connector
    refresh. Returns False when nothing was started. Never blocks; never raises
    into the caller's flow."""
    try:
        t = threading.Thread(
            target=_run_call_index_sync, args=(company_id,),
            name="call-index-refresh", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a spawn failure break connect
        logger.exception("call-index: failed to start refresh thread for %s",
                         company_id)
        return False
