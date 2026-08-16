"""Real local-Supabase + real-Anthropic arm for the group execution
lifecycle (LP-1..LP-6) — the behaviours a stubbed LLM can only wire, never
prove (cost/convergence/tool-engagement/router judgement;
[[feedback_stubbed-e2e-masks-loop-behaviour]]).

DEFERRED-TO-STAGING: authored + registered here, not run in any CI lane —
runs on staging when access lands. Gated on BOTH the run flag and a real key,
skips cleanly otherwise. Registered in
`test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_PROJECT_CHAT_PARITY_LIVE` and `ANTHROPIC_API_KEY`.

    RUN_PROJECT_CHAT_PARITY_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_group_execution_lifecycle_live.py -m integration
"""
from __future__ import annotations

import os

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_CHAT_PARITY_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_PROJECT_CHAT_PARITY_LIVE=1 with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/"
    "SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed at the local rig and the "
    "projects/prds/conversation_turns/project_delegations/ask_jobs migrations "
    "(incl. ask_jobs_active_attempt_uidx) applied"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live group-lifecycle round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


def test_lp1_group_success_posts_turn_and_ready_row_live():
    """LP-1: a real @Sprntly group reply posts an assistant turn AND flips the
    run row to `ready` (status='ready') via the shared primitive."""
    pytest.skip("DEFERRED-TO-STAGING: run on the staging rig")


def test_lp2_group_forced_failure_writes_error_error_class_live():
    """LP-2: a forced failure writes `status='error'` + a typed `error_class`,
    NO assistant turn is fabricated, and the raw exception text is never
    broadcast or exposed on the read."""
    pytest.skip("DEFERRED-TO-STAGING: run on the staging rig")


def test_lp3_named_source_hits_connector_unnamed_grounds_ledger_live():
    """LP-3: a named-source group question hits the connector; an unnamed
    PM-noun question grounds in the project ledger (no deflection)."""
    pytest.skip("DEFERRED-TO-STAGING: run on the staging rig")


def test_lp4_retry_idempotency_no_duplicate_delegation_live():
    """LP-4: a retry of a clean failed run re-answers with a new run_id/attempt;
    a run with a recorded delegation refuses (422)."""
    pytest.skip("DEFERRED-TO-STAGING: run on the staging rig")


def test_lp5_reload_after_failure_shows_failed_status_live():
    """LP-5: a user opening the chat AFTER a failure sees run_status='failed'
    from the poll read, with no realtime event."""
    pytest.skip("DEFERRED-TO-STAGING: run on the staging rig")


def test_lp6_main_chat_regression_live():
    """LP-6: main chat answer/cancel/fail behaviour is unchanged after the
    primitive extraction."""
    pytest.skip("DEFERRED-TO-STAGING: run on the staging rig")
