"""Every test must actually RUN in some CI lane.

THE DEFECT THIS EXISTS FOR (2026-08-07, PR #1109 / T5). The whole
open-vs-generate eval table — including the three `…-still-generates` negatives,
which ARE the feature's headline safety property — was `@pytest.mark.integration`
plus `skipif(not os.getenv("ANTHROPIC_API_KEY"))`. **No workflow anywhere under
`.github/workflows/` sets that secret.** So those tests skipped in the fast lane
(excluded by the marker) AND in the integration lane (excluded by the skipif).
The feature shipped with its safety property covered only by tests that had
never executed once, and CI was green the entire time.

That is the worst possible state: not "nobody checked", but "someone checked and
it's fine" — a passing checkmark that stops anyone looking again. See
`~/sprntly-brain/learnings/guards-that-are-not-guards.md` §3.

WHAT THIS TEST DOES. It reads two things and diffs them:

  1. every environment variable name any workflow that RUNS THIS SUITE provides
     — as an `env:` key, or referenced as `${{ secrets.X }}` / `${{ vars.X }}`.
     Deploy workflows are EXCLUDED — see `_runs_this_suite`;
  2. every environment variable name any `skipif`/`skipUnless` in this suite
     makes a test's execution CONDITIONAL on.

A name in (2) that is not in (1) is a test that cannot run in CI, ever, under
any lane. Those are listed in `_KNOWN_UNRUNNABLE` below, each with the reason it
is tolerated and what deterministic coverage stands in for it. Anything NOT on
that list fails this test.

WHY A BASELINE REGISTRY AND NOT A FLAT FAIL. Failing outright today would make
this test red on main from the moment it lands, and a test that is red on main
gets deleted rather than fixed — which would leave the class unguarded. The
registry is a ratchet: it names exactly what is currently unexecuted, it is
checked for staleness (see `test_no_stale_unrunnable_entries`) so it can only
shrink, and any NEW env-gated test fails closed.

KNOWN BLIND SPOT, stated here rather than discovered later: the registry is
keyed by (file, env var). Adding a SECOND test gated on `ANTHROPIC_API_KEY` to
an already-listed file will not fire this check. Keying more finely than the
file would make the registry churn on every line move, which is worse. The
mitigation is the reason column: an entry says what deterministic coverage
stands in for the gated tests in that file, and that claim is a review target.

NOT MARKED `integration` ON PURPOSE — it reads files, costs milliseconds, and
its entire value is being answerable before a merge.
"""
from __future__ import annotations

import ast
import functools
import re
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TESTS = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# The baseline: env-gated tests that no CI lane can run today.
#
# Each entry is (test file, env var) -> why it is tolerated. A reason must say
# what runs INSTEAD, because "this never runs" plus "nothing else covers it" is
# the exact state that shipped #1109's unguarded safety property.
#
# To remove an entry: either provide the variable in a workflow, or delete the
# skipif. Do not add an entry to make this test green — add it only when the
# deterministic backstop it names genuinely exists.
# ---------------------------------------------------------------------------
_KNOWN_UNRUNNABLE: dict[tuple[str, str], str] = {
    ("test_chat_intent_evals.py", "ANTHROPIC_API_KEY"): (
        "Live-model eval table for the chat intent router. Deterministic "
        "backstop: the unmarked rule-level tests in the same file, which run "
        "in the fast lane."
    ),
    ("test_qa_agent.py", "ANTHROPIC_API_KEY"): (
        "One live-model smoke test. The rest of the file is deterministic and "
        "runs in the fast lane."
    ),
    ("test_voc_routing_phrases.py", "ANTHROPIC_API_KEY"): (
        "Live-model recall/precision measurement for VoC routing. Deterministic "
        "backstop: the unmarked regex-level phrase tests in the same file."
    ),
    ("test_cir_routing_phrases.py", "ANTHROPIC_API_KEY"): (
        "Live-model recall/precision measurement for CIR routing. Deterministic "
        "backstop: the unmarked regex-level phrase tests in the same file."
    ),
    ("test_document_catalog_ranking_live.py", "DOCUMENT_CATALOG_TEST_DSN"): (
        "Needs a real Postgres with pgvector to exercise the catalog's ranking "
        "SQL; psycopg is deliberately absent from requirements. Run locally "
        "against a scratch database when touching the ranking migration."
    ),
    ("test_projects_schema_roundtrip.py", "RUN_PROJECTS_SCHEMA_ROUNDTRIP"): (
        "Needs a real local Supabase (PostgREST + Postgres) to exercise the "
        "projects/chat/memory migration set's CHECK constraints and partial "
        "unique index — the fake Supabase client has no SQL engine behind it "
        "and cannot enforce either. This is a schema-only ticket (no route/"
        "helper code shipped alongside it), so there is no deterministic unit "
        "coverage to stand in; the migration files themselves are reviewed in "
        "the PR, and this suite is the real-DB proof, run locally against the "
        "dev rig when touching this migration set."
    ),
    ("test_projects_crud_live.py", "RUN_PROJECTS_CRUD_LIVE"): (
        "Real local-Supabase round-trip for the projects CRUD + memory-entry "
        "routes/helpers (tenant-gate 404 parity, project_belongs_to_company "
        "against a genuine second company/workspace row, memory CRUD, the "
        "cached-summary read) — proves the real supabase-py client path a "
        "fake in-memory SQLite store cannot. Deterministic backstop: "
        "test_projects_routes.py and test_project_memory_entries.py cover "
        "the same behaviour against FakeSupabaseClient and run in the fast "
        "lane on every PR; this suite is the real-DB proof, run locally "
        "against the dev rig when touching these routes/helpers."
    ),
    ("test_project_memory_promotion.py", "RUN_PROJECT_MEMORY_PROMOTION_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the agent-"
        "promotion writer: proves the pme_one_provenance XOR check accepts a "
        "real insert, and that the promote -> schedule_regen -> "
        "regenerate_summary loop actually updates summary_md (not merely "
        "flips stale). Deterministic backstop: the rest of this file mocks "
        "the classifier (app.project_memory.call_json) against "
        "FakeSupabaseClient and covers provenance shape, stale-flip, "
        "never-raises, and the duplicate short-circuit in the fast lane; "
        "this suite is the real-DB/real-LLM proof, run locally against the "
        "dev rig when touching this writer."
    ),
    ("test_project_memory_promotion.py", "ANTHROPIC_API_KEY"): (
        "Same three live tests as RUN_PROJECT_MEMORY_PROMOTION_LIVE above — "
        "both variables gate the identical tests, so this is the other half "
        "of that same exemption. See that entry for the deterministic "
        "backstop."
    ),
    ("test_project_origin_seed_live.py", "RUN_PROJECT_ORIGIN_SEED_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the project-"
        "origin-seed writer: proves a real seed call actually lands a "
        "project_memory_entries row through the real supabase-py client and "
        "that the promote -> schedule_regen -> regenerate_summary loop "
        "actually updates summary_md (not merely flips stale) — a fully-"
        "stubbed LLM cannot prove either. Deterministic backstop: "
        "test_project_origin_seed.py mocks call_json/add_agent_promoted_"
        "entry/schedule_regen/get_prd/_read_turns against a fake DB and "
        "covers the writer's contract (brief+decisions shape, the one-regen "
        "call, the summarizer-failure fallback, the no-title unseeded case, "
        "never-raises, the one cost line, and the DRY reuse-not-fork check) "
        "in the fast lane; this suite is the real-DB/real-LLM proof, run "
        "locally against the dev rig when touching this writer."
    ),
    ("test_project_origin_seed_live.py", "ANTHROPIC_API_KEY"): (
        "Same live test as RUN_PROJECT_ORIGIN_SEED_LIVE above — both "
        "variables gate the identical test, so this is the other half of "
        "that same exemption. See that entry for the deterministic "
        "backstop."
    ),
    ("test_ask_project_promotion.py", "RUN_ASK_PROJECT_PROMOTION_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the individual-"
        "chat memory-promotion hook wired into ask_job_runner.run_ask_job: "
        "proves a project-scoped ask's completed answer reaches the real "
        "classifier and writes a correctly-provenanced project_memory_entries "
        "row, that the scheduled regen loop actually updates summary_md (not "
        "merely flips stale), and that a small-talk exchange promotes "
        "nothing. Deterministic backstop: the rest of this file mocks the "
        "classifier and qa_agent.answer against FakeSupabaseClient and covers "
        "project_id threading, the non-project no-op (no call/row/cost-line), "
        "the per-user _load_history regression guard, best-effort failure "
        "swallowing, and editable/removable provenance in the fast lane; this "
        "suite is the real-DB/real-LLM proof, run locally against the dev rig "
        "when touching this hook."
    ),
    ("test_ask_project_promotion.py", "ANTHROPIC_API_KEY"): (
        "Same three live tests as RUN_ASK_PROJECT_PROMOTION_LIVE above — both "
        "variables gate the identical tests, so this is the other half of "
        "that same exemption. See that entry for the deterministic backstop."
    ),
    ("test_individual_project_chat_live.py", "RUN_ASK_PROJECT_PROMOTION_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the individual-"
        "project-chat conversation binding: proves the get-or-create "
        "conversation helper is genuinely idempotent against a real Postgres, "
        "and that feeding its conversation_id through the real ask pipeline "
        "(answer stubbed, classifier real) lands a correctly-provenanced "
        "project_memory_entries row — closing the exact gap where the shipped "
        "UI never had a durable conversation_id to send. Deterministic "
        "backstop: test_individual_project_chat.py covers the same get-or-"
        "create idempotency, membership/tenant gating, the real /v1/ask route "
        "binding path, and the non-project no-op against FakeSupabaseClient "
        "(fake classifier) in the fast lane; this suite is the real-DB/"
        "real-LLM proof, run locally against the dev rig when touching this "
        "binding."
    ),
    ("test_individual_project_chat_live.py", "ANTHROPIC_API_KEY"): (
        "Same two live tests as RUN_ASK_PROJECT_PROMOTION_LIVE above — both "
        "variables gate the identical tests, so this is the other half of "
        "that same exemption. See that entry for the deterministic backstop."
    ),
    ("test_project_group_gate.py", "RUN_INTERJECTION_GATE_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the smart-"
        "interjection should-respond gate: proves the REAL classifier "
        "decision on an agent-directed question (respond=true, one real "
        "reply) and on an ordinary human-to-human exchange (respond=false, "
        "no reply) — a stubbed classifier cannot prove the gate's actual "
        "judgment, only its wiring. Deterministic backstop: the rest of "
        "this file mocks the classifier (app.project_group_gate.call_json) "
        "against FakeSupabaseClient and covers the pre-filter bound, the "
        "cost-line shape, the never-raises/mutation-proofed failure "
        "default, and the mention-bypasses-gate path in the fast lane; "
        "this suite is the real-DB/real-LLM proof, run locally against the "
        "dev rig when touching this gate."
    ),
    ("test_project_group_gate.py", "ANTHROPIC_API_KEY"): (
        "Same two live tests as RUN_INTERJECTION_GATE_LIVE above — both "
        "variables gate the identical tests, so this is the other half of "
        "that same exemption. See that entry for the deterministic "
        "backstop."
    ),
    ("test_group_trigger_live.py", "RUN_GROUP_TRIGGER_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the group "
        "smart-trigger port: proves the REAL classifier's continuation/"
        "ambiguous-work-request judgment (the AD-P10 posture shift) AND "
        "the B2 no-fabrication narration — that a 'Done' claim only ever "
        "follows an actual prd_versions write — end to end. A stubbed "
        "classifier/editor can prove wiring only, not that the model "
        "actually honors the new prompt rules or that the narration guard "
        "holds against a real editor response. Deterministic backstop: "
        "test_group_trigger_and_no_fabrication.py covers the "
        "agent_spoke_last/trigger_kind derivation, the _GroupEditOutcome "
        "three cases, the narration branch, the edit_note fallback, the "
        "addressing-note selection, and the DRY source-scans in the fast "
        "lane; this suite is the real-DB/real-LLM proof, run locally "
        "against the dev rig when touching this trigger surface."
    ),
    ("test_group_trigger_live.py", "ANTHROPIC_API_KEY"): (
        "Same live tests as RUN_GROUP_TRIGGER_LIVE above — both variables "
        "gate the identical tests, so this is the other half of that same "
        "exemption. See that entry for the deterministic backstop."
    ),
    ("test_project_answer_collapse_live.py", "RUN_PROJECT_CHAT_PARITY_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the project "
        "chat engine collapse (LT-2..LT-9): proves multi-party speaker "
        "attribution through the collapsed engine, project-awareness parity "
        "(context block + 4 read tools + delegate/execute callable), a real "
        "delegate_task call actually seeding a project_delegations row, "
        "cancel on the plain-Q&A composer path, main-chat regression, the "
        "backgrounded group reply not blocking the route, the LT-8 "
        "input-shape decision, and list_artifacts parity post-ff — a stubbed "
        "LLM can prove wiring only, never that the model actually engages "
        "the right tool or that the router/interceptor behaviour on a "
        "multi-speaker transcript is unchanged. Deterministic backstop: "
        "test_surface_scope.py and test_project_answer_collapse.py cover "
        "the byte-identity property test, the sixth-branch dispatch wiring, "
        "all four invariant mutation proofs, the backgrounding mechanics, "
        "and the queue-ready seams against fakes/monkeypatches in the fast "
        "lane; this suite is the real-DB/real-LLM proof, DEFERRED-TO-STAGING "
        "— run on staging when access lands, which also pins the LT-8 "
        "winner before merge."
    ),
    ("test_project_answer_collapse_live.py", "ANTHROPIC_API_KEY"): (
        "Same live tests as RUN_PROJECT_CHAT_PARITY_LIVE above — both "
        "variables gate the identical tests, so this is the other half of "
        "that same exemption. See that entry for the deterministic backstop."
    ),
    ("test_project_delegations.py", "RUN_PROJECT_DELEGATIONS_ROUNDTRIP"): (
        "Needs a real local Supabase (PostgREST + Postgres) to exercise the "
        "project_delegations migration's FK cascade/set-null behaviour, its "
        "three named indexes, and its RLS policy — the fake Supabase client "
        "has no SQL engine behind it and cannot enforce any of those. This is "
        "a schema-only ticket (no route code shipped alongside it), so there "
        "is no deterministic unit coverage to stand in; the migration file "
        "and helper module are reviewed in the PR, and this suite is the "
        "real-DB proof, run locally against the dev rig when touching this "
        "migration or `db/project_delegations.py`."
    ),
    ("test_delegation_events.py", "RUN_DELEGATION_EVENTS_ROUNDTRIP"): (
        "Needs a real local Supabase (PostgREST + Postgres) to exercise the "
        "delegation_events migration's CHECK constraint, FK cascade, index/"
        "RLS-policy catalog entries, and to evaluate the v_delegation_status "
        "left-join-lateral derive-at-read view — the fake Supabase client has "
        "no SQL engine behind it and cannot enforce any of those or evaluate "
        "a view. Deterministic backstop: test_project_delegation.py covers "
        "the genesis-emit contract (exactly one assigned event per hand-off, "
        "genesis-failure-does-not-rollback) against FakeSupabaseClient in the "
        "fast lane; this suite is the real-DB proof, run locally against the "
        "dev rig when touching this migration or `db/delegation_events.py`."
    ),
    ("test_delegation_followups.py", "RUN_DELEGATION_FOLLOWUPS_ROUNDTRIP"): (
        "Needs a real local Supabase (PostgREST + Postgres) to exercise the "
        "delegation_followups migration's FK cascade, partial index, and RLS "
        "policy, plus a real upsert_followup/get_followup round trip — the "
        "fake Supabase client has no SQL engine behind it and cannot enforce "
        "any of those. Deterministic backstop: test_delegation_status_ingest.py "
        "drives upsert_followup/get_followup against FakeSupabaseClient in the "
        "fast lane (partial-merge semantics, the pending_done_since clear/set "
        "contract) and test_delegation_cadence.py covers the pure cadence "
        "engine with no DB at all; this suite is the real-DB proof, run "
        "locally against the dev rig when touching this migration or "
        "`db/delegation_followups.py`."
    ),
    ("test_delegation_followup_sends.py", "RUN_DELEGATION_FOLLOWUP_SENDS_ROUNDTRIP"): (
        "Needs a real local Supabase (PostgREST + Postgres) to exercise the "
        "delegation_followup_sends migration's FK cascade, unique idempotency "
        "constraint, and RLS policy, plus a real record_send/send_exists/"
        "sends_for_person_since round trip and the list_due_followups "
        "next_check_in/muted/status pre-filter (which reads the real "
        "v_delegation_status view) — the fake Supabase client has no SQL "
        "engine behind it and cannot enforce any of those or evaluate a view. "
        "Deterministic backstop: test_delegation_followup.py drives the "
        "sweep's decision/guardrail/send logic against FakeSupabaseClient "
        "with a stubbed LLM in the fast lane; this suite is the real-DB "
        "proof, run locally against the dev rig when touching this migration "
        "or `db/delegation_followup_sends.py`/`db/delegation_followups.py`."
    ),
    ("test_conversation_read_cursors.py", "RUN_CONVERSATION_READ_CURSORS_ROUNDTRIP"): (
        "Needs a real local Supabase (PostgREST + Postgres) to prove the "
        "conversation_read_cursors migration's composite PK and RLS/policy "
        "catalog entries, plus a real set_cursor/get_cursor upsert round "
        "trip — the fake Supabase client has no SQL engine behind it and "
        "cannot enforce a composite primary key or an RLS-policy lookup. "
        "Deterministic backstop: the rest of this file drives unread "
        "derivation, read-clears-unread, advance-only clamping, per-user "
        "cursor isolation (RED->GREEN mutation proof), and the membership + "
        "client-supplied-id gates against FakeSupabaseClient in the fast "
        "lane; this suite is the real-DB proof, run locally against the dev "
        "rig when touching this migration or `db/conversation_read_cursors.py`."
    ),
    ("test_project_delegation.py", "RUN_DELEGATE_TASK_LIVE"): (
        "Real local-Supabase + real-Anthropic round-trip for the "
        "delegate_task tool: proves the REAL model actually calls the tool "
        "on a hand-off phrase, the REAL brief LLM call, and that an "
        "unresolvable assignee produces no DM/no delegation row — a stubbed "
        "model can prove the handler's contract but not that the tool "
        "actually gets invoked end to end. Deterministic backstop: the rest "
        "of this file drives `handle_delegate_task` directly against "
        "FakeSupabaseClient with `call_md` stubbed and covers the "
        "tool-description/brief-prompt properties, the authz/IDOR "
        "mutation-proof (AC3, RED->GREEN), the never-writes-a-user-turn "
        "invariant, fail-closed resolution/brief, and the cost/log-content "
        "assertions in the fast lane; this suite is the real-LLM proof, run "
        "locally against the dev rig when touching this tool or "
        "`app/project_delegation.py`."
    ),
    ("test_project_delegation.py", "ANTHROPIC_API_KEY"): (
        "Same three live tests as RUN_DELEGATE_TASK_LIVE above — both "
        "variables gate the identical tests, so this is the other half of "
        "that same exemption. See that entry for the deterministic "
        "backstop."
    ),
    ("test_project_ledger_live.py", "RUN_PROJECT_LEDGER_LIVE"): (
        "Needs a real local Supabase (PostgREST + Postgres) to evaluate the "
        "`v_delegation_status` left-join-lateral derive-at-read view through "
        "the REAL emit route + supabase-py client — the fake Supabase client "
        "has no SQL engine behind it and cannot evaluate a view (same "
        "reasoning as `test_delegation_events.py`). Deterministic backstop: "
        "`test_delegation_events_api.py` covers the pure state-machine engine, "
        "all four fail-closed authz gates (mutation-proofed, RED->GREEN), read "
        "isolation, the ledger-row DTO shape, and cost/log-content assertions "
        "against `FakeSupabaseClient` with the view stood in by a data-driven "
        "equivalent in the fast lane; this suite is the real-DB proof that the "
        "ACTUAL view + route agree, run locally against the dev rig when "
        "touching this endpoint or `db/delegation_events.py`."
    ),
    ("test_realtime_channel_auth.py", "RUN_PROJECTS_REALTIME_CHANNEL_AUTH_LIVE"): (
        "Needs a real local Supabase (Postgres + the installed Realtime "
        "service) to evaluate the group/per-user channel-join predicate "
        "functions and exercise the deployed RLS policies on "
        "`realtime.messages` — the fake Supabase client has no SQL engine "
        "behind it and cannot evaluate a PL/pgSQL function, enforce RLS, or "
        "resolve `realtime.topic()`/`auth.uid()`. This is a schema/"
        "policy-only ticket (no route/helper code path runs in CI), so "
        "there is no deterministic unit coverage to stand in; the migration "
        "SQL is reviewed in the PR, and this suite is the real-DB proof — "
        "including both AC-12 mutation proofs (group allow-all and the "
        "per-user uid-bypass) — run locally against the dev rig when "
        "touching this migration, plus the ship-gate live proof before "
        "promotion."
    ),
    ("test_resolve_candidate_live.py", "RUN_RESOLVE_CANDIDATE_LIVE"): (
        "Needs a real local Supabase (PostgREST + Postgres) to classify a "
        "real identity across t_workspace/t_company/t_newuser/t_refuse "
        "against real workspace_members/company_members/profiles rows in "
        "two tenants — the fake Supabase client cannot prove the tenancy "
        "fail-closed re-assertion holds against a genuine second "
        "company/workspace row, only that the stubbed gates were called with "
        "the right arguments. Deterministic backstop: test_resolve_candidate.py "
        "covers all five tiers, the cross-tenant fail-closed proofs (AC5, "
        "AC6 RED->GREEN mutation proof), and the needle-shape branches "
        "against monkeypatched dependencies in the fast lane; this suite is "
        "the real-DB proof, run locally against the dev rig when touching "
        "this resolver or the membership helpers it composes."
    ),
    ("test_tag_candidate_live.py", "RUN_TAG_CANDIDATE_LIVE"): (
        "Needs a real local Supabase (PostgREST + Postgres) to prove the "
        "tag-action surface across TWO real tenants: a cross-tenant refuse "
        "through the real HTTP route (403, zero writes in both tenants), a "
        "real t_workspace add that lands a project_members row, and a real "
        "t_newuser tag that creates a workspace_invites row carrying "
        "project_id — the fake Supabase client cannot prove the tenancy "
        "fail-closed re-assertion holds against a genuine second "
        "company/workspace row. Deterministic backstop: test_tag_candidate_api.py "
        "covers all five tiers, the per-tier mutation proofs (AC6), the "
        "add_member-route IDOR fix (AC7), de-gate + seat guard, and the "
        "candidate-search scoping against monkeypatched/fake-DB dependencies "
        "in the fast lane; this suite is the real-DB proof, run locally "
        "against the dev rig when touching the tag route or resolve_candidate."
    ),
    ("test_invite_project_association.py", "RUN_INVITE_PROJECT_ASSOCIATION_LIVE"): (
        "Needs a real local Supabase (PostgREST + Postgres) to prove a real "
        "accept of a project-carrying invite inserts the project_members row "
        "(Extension B) end to end through create_invite -> "
        "accept_invite_for_user against real company_members/workspace_members "
        "rows — the fake Supabase client has no real accept-flow FK/RLS engine "
        "behind it. Deterministic backstop: the fast-lane tests in the SAME "
        "file cover project_id round-trip through the invite, accept auto-add "
        "on both accept paths, and the project-less non-breakage case against "
        "FakeSupabaseClient; this env-gated case is the real-DB proof, run "
        "locally when touching the invite primitives or the accept hook."
    ),
    ("test_read_tool_idor_live.py", "RUN_READ_TOOL_IDOR_LIVE"): (
        "Real local-Supabase round-trip for the @Sprntly group agent's project "
        "read tools' tenancy scoping (`project_group_context.dispatch_read_tool`): "
        "proves a genuine cross-project id (same company, sibling project) and a "
        "genuine cross-tenant id (a second real company) are BOTH refused by "
        "get_artifact_content against real rows, that the manifest gate is "
        "load-bearing (add-ref -> content returns -> remove -> refused, RED->GREEN), "
        "and that list_project_artifacts surfaces only this project's own artifact "
        "— the manifest-intersection + get_report(id, company_id) gates a fake "
        "in-memory store cannot fully exercise. Deterministic backstop: "
        "test_project_group_context.py mutation-proofs the identical gate "
        "(manifest-off -> refused, flip-on -> foreign content returns, restore -> "
        "refused) against monkeypatched dependencies and runs in the fast lane on "
        "every PR; this suite is the real-DB proof, run locally against the dev "
        "rig when touching these read tools or `db/artifacts.py`."
    ),
    ("test_group_chat_prd_edit_live.py", "RUN_GROUP_CHAT_PRD_EDIT_LIVE"): (
        "Real local-Supabase + real-LLM round-trip for the @Sprntly GROUP "
        "chat's edit_prd dispatch (`_classify_and_maybe_edit_group_prd` -> "
        "`apply_chat_edit_scoped`, the SAME shared writer + ★ IDOR gate the "
        "private surface proves): classify-then-edit through the real group "
        "turn path, a genuine cross-project prd_id writes ZERO rows and is "
        "refused end to end, and an own-project edit persists (payload_md "
        "changes, exactly one prd_versions snapshot) AND broadcasts "
        "turn.created. Needs BOTH a live LLM (ANTHROPIC_API_KEY, the classifier "
        "AND the scoped editor both call the model) and a real Postgres "
        "fan-out (list_artifacts_for_project) a fake in-memory store cannot "
        "exercise. Deterministic backstops run every PR in the fast lane: "
        "test_group_chat_prd_edit.py mutation-proofs the identical ★ gate "
        "(cross-project/cross-tenant/own-project) through the real group turn "
        "route against a monkeypatched classifier + editor, and "
        "test_project_chat_edit.py proves the shared callable directly; this "
        "suite is the real-LLM+real-DB proof, run locally against the dev rig "
        "when touching the group responder or the shared scoped edit."
    ),
    ("test_group_chat_prd_edit_live.py", "ANTHROPIC_API_KEY"): (
        "Same live test as RUN_GROUP_CHAT_PRD_EDIT_LIVE above — it needs a "
        "real LLM in addition to the env flag, so it is gated on BOTH "
        "(_RUN_LIVE = RUN_GROUP_CHAT_PRD_EDIT_LIVE=='1' and bool(ANTHROPIC_API_KEY)) "
        "and is unrunnable in any CI lane on either count. See that entry for "
        "the deterministic fast-lane backstops."
    ),
    ("test_projects_prd_chat_edit_route_live.py", "RUN_PROJECT_CHAT_EDIT_LIVE"): (
        "Real local-Supabase + real-LLM round-trip for "
        "POST /v1/projects/{id}/prd/chat-edit — the private project chat's "
        "PRD-edit write path, through the REAL route: a genuine cross-project "
        "prd_id (same company, sibling project) writes ZERO rows and is refused "
        "end to end, and an own-project edit persists (payload_md changes, "
        "exactly one prd_versions snapshot). Needs BOTH a live LLM "
        "(ANTHROPIC_API_KEY, the scoped editor calls the model) and a real "
        "Postgres fan-out (list_artifacts_for_project) a fake in-memory store "
        "cannot exercise. Deterministic backstops run every PR in the fast "
        "lane: test_project_chat_edit.py mutation-proofs the identical ★ gate "
        "(cross-project/cross-tenant/gate-error -> zero rows, own-project -> "
        "one version) against monkeypatched dependencies, and "
        "test_projects_prd_chat_edit_route.py covers the route's own gate "
        "order (membership / flag / target-resolution) with a mocked editor; "
        "this suite is the real-LLM+real-DB proof, run locally against the dev "
        "rig when touching this route or the shared apply_chat_edit_scoped."
    ),
    ("test_projects_prd_chat_edit_route_live.py", "ANTHROPIC_API_KEY"): (
        "Same live test as RUN_PROJECT_CHAT_EDIT_LIVE above — the scoped editor "
        "calls a real LLM in addition to the env flag, so it is gated on BOTH "
        "(_RUN_LIVE = RUN_PROJECT_CHAT_EDIT_LIVE=='1' and "
        "bool(ANTHROPIC_API_KEY)) and is unrunnable in any CI lane on either "
        "count. See that entry for the deterministic fast-lane backstops."
    ),
    ("test_project_intent_route_live.py", "RUN_PROJECT_INTENT_LIVE"): (
        "Real local-Supabase + real-LLM round-trip for "
        "POST /v1/projects/{id}/chat/intent — the private project chat's "
        "classify path, through the REAL route: a project with exactly one "
        "attached PRD, an edit-phrased message classifies edit_prd carrying "
        "the server-resolved prd_id, proving the _NEEDS_PRD downgrade "
        "(chat_intent.py:431) does not fire against a REAL model's output. "
        "Needs BOTH a live LLM (resolve_chat_intent calls the model) and a "
        "real Postgres fan-out (list_artifacts_for_project) a fake "
        "in-memory store cannot exercise. Deterministic backstop runs every "
        "PR in the fast lane: test_project_intent_route.py covers the "
        "route's own gate order (membership / server-vs-client target / "
        "envelope shape) with resolve_chat_intent monkeypatched; this suite "
        "is the real-LLM+real-DB proof, run locally against the dev rig "
        "when touching this route."
    ),
    ("test_project_intent_route_live.py", "ANTHROPIC_API_KEY"): (
        "Same live test as RUN_PROJECT_INTENT_LIVE above — resolve_chat_intent "
        "calls a real LLM in addition to the env flag, so it is gated on BOTH "
        "(_RUN_LIVE = RUN_PROJECT_INTENT_LIVE=='1' and "
        "bool(ANTHROPIC_API_KEY)) and is unrunnable in any CI lane on either "
        "count. See that entry for the deterministic fast-lane backstop."
    ),
    ("test_mention_liveness_live.py", "RUN_MENTION_LIVENESS_LIVE"): (
        "Needs a real local Supabase Realtime (the running Realtime service + "
        "a live websocket) to prove a `member.added` published through the "
        "ACTUAL Broadcast REST endpoint is genuinely RECEIVED on the target's "
        "per-user channel — the fake Supabase client spies the publish call but "
        "has no realtime transport to fan the event back over a socket. "
        "Deterministic backstop: test_mention_liveness.py covers the per-user-"
        "channel-only + never-group privacy gate, the whitelisted DTO / no-"
        "content-leak assertion, the best-effort no-raise/no-rollback mutation "
        "proof, the right-branch publisher wiring, and the accept-hook publish "
        "against FakeSupabaseClient with publish_broadcast spied in the fast "
        "lane; this suite is the real-transport proof, run locally against the "
        "dev rig when touching the mention/add publishers or the tag route."
    ),
    ("test_project_artifacts_fanout_live.py", "RUN_PROJECT_ARTIFACTS_LIVE"): (
        "Real local-Supabase round-trip for the project artifacts fan-out "
        "(list_artifacts_for_project), including the regenerate-stays-"
        "attached resolve-forward reproduction: a project + PRD, then a "
        "force=True-style regenerate that mints a new prds.id in the same "
        "family, proving the project's artifact list still resolves the "
        "current generation against REAL rows — the fake Supabase client's "
        "family-collapse/resolve-forward logic is identical either way, but "
        "only real Postgres proves the write-time ownership gate and the "
        "membership gate round-trip. Deterministic backstop: "
        "test_project_artifacts_fanout.py covers the same regenerate/"
        "resolve-forward/dedupe/tolerated-stale cases against "
        "FakeSupabaseClient in the fast lane; this suite is the real-DB "
        "proof, run locally against the dev rig when touching this fan-out."
    ),
    ("test_project_artifacts_fanout_live.py", "ANTHROPIC_API_KEY"): (
        "One optional test in this file additionally drives the regenerate "
        "reproduction through the GENUINE generate-from-task(force=True) "
        "pipeline (not a direct DB insert) end to end — gated on BOTH this "
        "key and RUN_PROJECT_ARTIFACTS_LIVE above, so it is unrunnable in "
        "any CI lane on either count. See that entry for the deterministic "
        "fast-lane backstop; the DB-fixture test in this same file already "
        "proves the read path without a model."
    ),
    ("test_project_prd_content_live.py", "RUN_PROJECT_PRD_CONTENT_LIVE"): (
        "Real local-Supabase round-trip for POST /v1/projects/{id}/prd/content "
        "across TWO real tenants: a genuine cross-project prd_id and a "
        "genuine cross-tenant prd_id both refused through the REAL route with "
        "zero writes, and a real in-tenant on-project save updating "
        "prds.payload_md plus inserting exactly one prd_versions row — the "
        "fake Supabase client cannot prove the tenancy fail-closed "
        "re-assertion holds against a genuine second company/workspace row. "
        "No LLM call on this route, so no ANTHROPIC_API_KEY dependency. "
        "Deterministic backstop: test_project_prd_content_route.py covers "
        "the gate order (call-order spies), per-path mutation proofs "
        "(zero-write), the fail-closed bypass proof, the valid-save "
        "snapshot-then-update sequence, snapshot-failure swallowing, and the "
        "no-body-content/no-cost-line observability contract against "
        "FakeSupabaseClient in the fast lane; this suite is the real-DB "
        "proof, run locally against the dev rig when touching this route or "
        "`app/project_prd_gate.py`."
    ),
    ("test_project_join_greeting_live.py", "RUN_PROJECT_JOIN_GREETING_LIVE"): (
        "Real local-Supabase round-trip for the on-join greeting: a genuinely "
        "new membership, added through the real client, gets exactly one "
        "get-or-created individual conversation and one posted assistant "
        "turn — the fake Supabase client's insert/select stand-ins cannot "
        "prove the get-or-create idempotency or the write against real "
        "`conversations`/`conversation_turns` FKs. No ANTHROPIC_API_KEY "
        "dependency — the greeting reuses the cached "
        "project_memory_summary and makes no fresh LLM call. Deterministic "
        "backstop: test_project_join_greeting.py covers the compose/split "
        "helpers, the best-effort/never-raises contract, the new-only/"
        "no-duplicate rule, and the no-LLM-call proof against monkeypatched "
        "stand-ins in the fast lane; this suite is the real-DB proof, run "
        "locally against the dev rig when touching this module or "
        "`db/conversations.py`."
    ),
    ("test_group_chat_turns_live.py", "RUN_GROUP_CHAT_LIVE"): (
        "Real local-Supabase round-trip for the group-chat surface: "
        "`db/conversations.py`'s create_group_chat/get_group_chat/"
        "list_group_turns/post_group_turn helpers AND the /v1/projects/{id}/"
        "group* routes, driven over real HTTP through PostgREST against a "
        "real local Postgres — the fake Supabase client cannot enforce the "
        "uq_one_group_chat_per_project partial unique index, seed a real "
        "project_chat_members roster, or prove the membership gate against "
        "genuine rows. The one @Sprntly-triggered LLM call is stubbed here "
        "too (app.routes.projects.run_tool_loop monkeypatched), so no "
        "ANTHROPIC_API_KEY dependency. Deterministic backstop: "
        "test_group_chat_turns.py covers the same create/idempotent-create, "
        "human-vs-mention turn shape, roster/author fields, since-cursor "
        "polling, foreign-tenant 404, same-tenant non-member 403, and the "
        "individual-conversation isolation logic against FakeSupabaseClient "
        "in the fast lane; this suite is the real-DB proof, run locally "
        "against the dev rig when touching this surface."
    ),
    ("test_project_from_prd.py", "RUN_PROJECT_FROM_PRD_LIVE"): (
        "Real local-Supabase round-trip for the auto-create-from-PRD hook "
        "(app/project_from_prd.py, AD-P9): the Creation assertions "
        "(prd_auto project + membership + single project_artifacts row + "
        "conversation<->project bind) re-run against a real Postgres, "
        "proving the writes actually persist and the FKs hold — the fake "
        "Supabase client's in-memory stand-in cannot prove that. No LLM "
        "call anywhere in this hook, so no ANTHROPIC_API_KEY dependency. "
        "Deterministic backstop: this SAME file's unmarked fake-DB tests "
        "(the majority of it, above this section) already cover creation, "
        "idempotent first-write-wins, the no-conversation skip, "
        "mutation-proofed failure swallowing at both the route and helper "
        "level, all three routes/prd.py hook call sites staying wired "
        "(AC5/AC6), and the reverse find_existing_prd_auto_project dedup "
        "lookup (hook-forked, modal-forked, cross-origin, cross-artifact-"
        "type, cross-company) in the fast lane; this suite is the real-DB "
        "proof, run locally against the dev rig when touching this hook."
    ),
}


# ---------------------------------------------------------------------------
# (1) What the workflows provide
# ---------------------------------------------------------------------------

_CONTEXT_REF = re.compile(r"\$\{\{\s*(?:secrets|vars|env)\.([A-Za-z_][A-Za-z0-9_]*)")
#: `        FOO: bar` — a mapping key that looks like an env var name. Matched
#: textually rather than by parsing YAML so this test needs no yaml dependency
#: and cannot be defeated by an expression the parser chokes on. Over-matching
#: here is SAFE in the only direction that matters: it can only ever make the
#: "provided" set larger, i.e. make this test quieter, never louder — so a
#: false positive from this regex is impossible by construction.
_ENV_KEY = re.compile(r"^\s{2,}([A-Z][A-Z0-9_]{2,}):\s", re.MULTILINE)


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def _runs_this_suite(text: str) -> bool:
    """Can this workflow's env make a test in `backend/tests/` execute?

    ONLY a workflow that runs pytest against this suite can. That distinction is
    load-bearing and getting it wrong was a real defect in this file's first
    version, in BOTH directions:

      - FALSE GREEN. Unioning env across all 11 workflows meant a test gated on
        `SUPABASE_DB_URL` / `TOKEN_ENCRYPTION_KEY` / `GOOGLE_CLIENT_SECRET` —
        deploy-only secrets, and exactly the ones an integration test would gate
        on — read as "provided" and passed this check while skipping in both
        lanes. That is the #1109 defect itself, sailing through the guard
        written for it.
      - FALSE RED, which is worse. Adding `ANTHROPIC_API_KEY` to a DEPLOY
        workflow (an ordinary, correct change) reddened four unrelated files
        via `test_no_stale_unrunnable_entries`, whose message says "Delete them.
        The baseline is a ratchet; it only shrinks." An author who complies
        deletes all four entries and the guard goes PERMANENTLY BLIND to every
        `ANTHROPIC_API_KEY`-gated test. A guard whose failure mode teaches
        people to disable it is worse than no guard.

    `prototype-runtime.yml` runs `npm test`, but in the `prototype-runtime`
    package — it cannot make a `backend/tests/` test run, so it is correctly
    excluded by the `working-directory: backend` half of this predicate.

    Predicate, not a hardcoded filename: a new backend test lane is picked up by
    construction.
    """
    return "pytest" in text and "working-directory: backend" in text


@functools.lru_cache(maxsize=1)
def _workflow_env_names() -> "frozenset[str]":
    names: set[str] = set()
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _runs_this_suite(text):
            continue
        names.update(_CONTEXT_REF.findall(text))
        names.update(_ENV_KEY.findall(text))
    return frozenset(names)


# ---------------------------------------------------------------------------
# (2) What the suite gates execution on
# ---------------------------------------------------------------------------

_GETENV_FUNCS = {"getenv", "environ"}


def _env_names_in(node: ast.AST) -> set[str]:
    """Every env var name this expression reads.

    Covers `os.getenv("X")`, `os.environ.get("X")` and `os.environ["X"]`.
    """
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = getattr(fn, "attr", None)
            if name in {"getenv", "get"} and sub.args:
                first = sub.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    # `os.getenv("X")` or `os.environ.get("X")` — but not an
                    # unrelated `d.get("x")`. Require the receiver to mention os
                    # or environ.
                    receiver = ast.unparse(fn)
                    if "getenv" in receiver or "environ" in receiver:
                        found.add(first.value)
        elif isinstance(sub, ast.Subscript):
            receiver = ast.unparse(sub.value)
            if "environ" in receiver and isinstance(sub.slice, ast.Constant):
                if isinstance(sub.slice.value, str):
                    found.add(sub.slice.value)
    return found


def _module_env_aliases(tree: ast.Module) -> dict[str, set[str]]:
    """`_DSN = os.getenv("X")` -> {"_DSN": {"X"}}.

    Without this the check is blind to the very common shape of hoisting the
    lookup to module level and writing `skipif(not _DSN, ...)` — which is how
    `test_document_catalog_ranking_live.py` is written, so this is load-bearing
    and not defensive over-engineering.
    """
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = _env_names_in(node.value)
            if not names:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.setdefault(target.id, set()).update(names)
    return aliases


def _is_skip_marker(call: ast.Call) -> bool:
    fn = ast.unparse(call.func)
    return fn.endswith("skipif") or fn.endswith("skipUnless")


def _gated_env_names(path: Path) -> set[str]:
    """Env var names this file makes test EXECUTION conditional on."""
    try:
        with warnings.catch_warnings():
            # Some suite files contain regexes with invalid escapes; compiling
            # their AST is not the place to relitigate that.
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
        return set()

    aliases = _module_env_aliases(tree)
    gated: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_skip_marker(node)):
            continue
        condition = node.args[0] if node.args else None
        if condition is None:
            continue
        gated.update(_env_names_in(condition))
        for sub in ast.walk(condition):
            if isinstance(sub, ast.Name) and sub.id in aliases:
                gated.update(aliases[sub.id])
    return gated


@functools.lru_cache(maxsize=1)
def _suite_gates() -> "frozenset[tuple[str, str]]":
    """Cached: this AST-parses all ~412 suite files (~2.6s) and is called by
    three tests. Uncached that was 8s of the ~10s this file cost, and the entire
    value of these guards is being cheap enough that nobody minds them."""
    out: set[tuple[str, str]] = set()
    for path in sorted(TESTS.rglob("test_*.py")):
        for name in _gated_env_names(path):
            out.add((path.relative_to(TESTS).as_posix(), name))
    return frozenset(out)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def test_workflow_env_extraction_is_not_vacuously_empty():
    """A guard whose input silently became empty passes forever.

    If the workflow glob stops matching (directory moved, files renamed to an
    extension this does not read), `_workflow_env_names()` returns the empty set
    — and then EVERY gated var reads as unprovided, which is loud, not silent.
    The dangerous direction is the reverse: an extraction bug that returns
    everything. This pins both ends by asserting on a name we know is there.
    """
    assert WORKFLOWS.is_dir(), f"no workflows directory at {WORKFLOWS}"

    running = [
        p.name for p in _workflow_files()
        if _runs_this_suite(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert running, (
        "no workflow appears to run this pytest suite — the scoping predicate "
        "is broken, which would make EVERY gated var read as unprovided (loud, "
        f"but wrong). Workflows present: {[p.name for p in _workflow_files()]}"
    )
    assert "test-backend.yml" in running, (
        f"test-backend.yml is not recognised as running this suite: {running}"
    )
    # The scoping must EXCLUDE deploy workflows, or the false green returns.
    assert "sync-backend-env.yml" not in running and "deploy-backend.yml" not in running, (
        f"a deploy workflow is being counted as a test lane: {running}. Its "
        "secrets cannot make a test run, and counting them is how a test gated "
        "on a deploy-only secret passes this check while never executing."
    )

    provided = _workflow_env_names()
    assert "DESIGN_AGENT_NODE_PATH" in provided, (
        "env extraction found no DESIGN_AGENT_NODE_PATH — test-backend.yml sets "
        f"it, so the extraction is broken. Found: {sorted(provided)}"
    )


def test_skipif_extraction_is_not_vacuously_empty():
    """Same argument, other side of the diff.

    This one matters MORE: an extraction that quietly finds nothing makes this
    whole file a green checkmark over an unread question. Pinned against a shape
    the suite genuinely contains — a module-level alias (`_DSN = os.getenv(...)`
    then `skipif(not _DSN)`), which is the harder of the two shapes to parse.
    """
    gates = set(_suite_gates())
    assert ("test_document_catalog_ranking_live.py", "DOCUMENT_CATALOG_TEST_DSN") in gates, (
        "skipif extraction missed the module-level-alias shape "
        "(`_DSN = os.getenv(...)`; `skipif(not _DSN, ...)`). Found: "
        f"{sorted(gates)}"
    )
    assert any(name == "ANTHROPIC_API_KEY" for _f, name in gates), (
        "skipif extraction missed the direct `skipif(not os.getenv(...))` shape"
    )


def test_no_test_is_gated_on_an_env_var_no_workflow_provides():
    """A skipif on a variable CI never sets is a test that never runs.

    Fix, in preference order:
      1. Make the test deterministic so it needs no secret at all — the fix
         #1109 took, and the only one that actually restores coverage.
      2. Provide the variable in the workflow that should run it.
      3. If neither is possible, add it to `_KNOWN_UNRUNNABLE` above WITH the
         deterministic coverage that stands in for it. An entry with no such
         coverage is a lie that will pass this test and fail a customer.
    """
    provided = _workflow_env_names()
    unrunnable = {
        key for key in _suite_gates() if key[1] not in provided
    }
    new = sorted(unrunnable - set(_KNOWN_UNRUNNABLE))
    assert not new, (
        "these tests are gated on environment variables that NO workflow under "
        ".github/workflows/ provides, so they skip in EVERY CI lane:\n"
        + "\n".join(f"  {path}  needs  {var}" for path, var in new)
        + "\n\nA green CI run says nothing about them. This is how #1109 shipped "
        "its headline safety property covered only by tests that had never "
        "executed. Make the test deterministic, or provide the variable, or "
        "record it in _KNOWN_UNRUNNABLE with the coverage that replaces it."
    )


def test_no_stale_unrunnable_entries():
    """The baseline may only shrink.

    Without this, an exemption outlives the skipif it excused and the registry
    slowly becomes a list of things nobody can check — which reads as
    permission rather than as debt.
    """
    gates = set(_suite_gates())
    provided = _workflow_env_names()
    stale = sorted(
        key for key in _KNOWN_UNRUNNABLE
        if key not in gates or key[1] in provided
    )
    assert not stale, (
        "these _KNOWN_UNRUNNABLE entries no longer describe reality — the "
        "skipif is gone, the file moved, or CI now provides the variable:\n"
        + "\n".join(f"  {path}  /  {var}" for path, var in stale)
        + "\n\nDelete them. The baseline is a ratchet; it only shrinks."
    )


@pytest.mark.parametrize(
    "marker_line",
    [
        '@pytest.mark.skipif(not os.getenv("TOTALLY_UNSET_SECRET"), reason="x")',
        'pytestmark = pytest.mark.skipif(not os.environ.get("TOTALLY_UNSET_SECRET"), reason="x")',
        '_K = os.getenv("TOTALLY_UNSET_SECRET")\npytestmark = pytest.mark.skipif(not _K, reason="x")',
    ],
)
def test_the_detector_sees_each_gating_shape(tmp_path, marker_line):
    """Self-test: the detector is run against known-bad input, in the suite.

    The three shapes are the ones this repo actually uses. A detector that
    silently stopped recognising one of them would leave this whole file green
    while the class went unguarded — which is the failure mode the file exists
    to prevent, so it is checked here rather than trusted.
    """
    src = tmp_path / "test_probe.py"
    src.write_text(f"import os\nimport pytest\n{marker_line}\n\ndef test_x():\n    pass\n")
    assert _gated_env_names(src) == {"TOTALLY_UNSET_SECRET"}


def test_ci_lane_registry_has_tag_and_invite_live():
    """AC backstop: both env-gated tag-action live suites are registered in
    `_KNOWN_UNRUNNABLE` with the env var that gates them. Removing either
    entry reddens this test (and `test_no_test_is_gated_on_an_env_var_no_
    workflow_provides` above), which is exactly the ratchet's intent —
    a live security proof must never silently drop out of the accounted set."""
    assert ("test_tag_candidate_live.py", "RUN_TAG_CANDIDATE_LIVE") in _KNOWN_UNRUNNABLE
    assert (
        "test_invite_project_association.py",
        "RUN_INVITE_PROJECT_ASSOCIATION_LIVE",
    ) in _KNOWN_UNRUNNABLE


def test_ci_lane_registry_has_join_greeting_live():
    """AC backstop: the on-join greeting's env-gated real-DB round trip is
    registered in `_KNOWN_UNRUNNABLE` with the env var that gates it.
    Removing this entry reddens this test (and `test_no_test_is_gated_on_
    an_env_var_no_workflow_provides` above) — the live proof must never
    silently drop out of the accounted set."""
    assert (
        "test_project_join_greeting_live.py",
        "RUN_PROJECT_JOIN_GREETING_LIVE",
    ) in _KNOWN_UNRUNNABLE


def test_ci_lane_registry_has_project_prd_content_live():
    """AC backstop: the project PRD-content route's env-gated two-tenant live
    round-trip is registered in `_KNOWN_UNRUNNABLE` with the env var that
    gates it. Removing this entry reddens this test (and `test_no_test_is_
    gated_on_an_env_var_no_workflow_provides` above) — the cross-tenant IDOR
    live proof must never silently drop out of the accounted set."""
    assert (
        "test_project_prd_content_live.py",
        "RUN_PROJECT_PRD_CONTENT_LIVE",
    ) in _KNOWN_UNRUNNABLE


def test_ci_lane_registry_has_group_trigger_live():
    """AC backstop: the group smart-trigger live suite is registered in
    `_KNOWN_UNRUNNABLE` under both env vars that gate it. Removing either
    entry reddens this test (and `test_no_test_is_gated_on_an_env_var_no_
    workflow_provides` above) — the live no-fabrication proof must never
    silently drop out of the accounted set."""
    assert ("test_group_trigger_live.py", "RUN_GROUP_TRIGGER_LIVE") in _KNOWN_UNRUNNABLE
    assert ("test_group_trigger_live.py", "ANTHROPIC_API_KEY") in _KNOWN_UNRUNNABLE
