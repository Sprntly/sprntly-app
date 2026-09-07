"""app.artifact_open — turning "the PRD for X" into a document to open.

The three outcomes ARE the contract and each is pinned here:

    0 matches → not_found, nothing to open, and (critically) no fallback into
                generating one
    1 match   → resolved, with the ids the panel needs
    2+ equal  → ambiguous, with every tied candidate carried so the client's
                chips can be real actions rather than re-sent text

Plus the scorer itself, which is pure: it decides whether the user gets a
document or a question, so its edges (partial overlap, stopword-only overlap,
phrase order) are tested directly rather than through the resolver.
"""
from __future__ import annotations

import app.artifact_open as ao


def _prd(id_, title, *, status="ready", created_at="2026-08-01", brief_id=7,
         insight_index=0, week_label="Week of Aug 1", brief_anchored=True):
    return {
        "type": "prd",
        "id": id_,
        "title": title,
        "status": status,
        "created_at": created_at,
        "brief_anchored": brief_anchored,
        "source": {"brief_id": brief_id, "week_label": week_label,
                   "insight_index": insight_index},
        "open": {"brief_id": brief_id, "insight_index": insight_index,
                 "prd_id": id_},
    }


def _evidence(id_, title, *, brief_id=7, insight_index=2):
    return {
        "type": "evidence",
        "id": id_,
        "title": title,
        "status": "ready",
        "created_at": "2026-08-01",
        "brief_anchored": True,
        "source": {"brief_id": brief_id, "week_label": None,
                   "insight_index": insight_index},
        "open": {"brief_id": brief_id, "insight_index": insight_index,
                 "evidence_id": id_},
    }


def _patch_index(monkeypatch, items):
    seen: dict = {}

    def _list(*, dataset, openable_only=False):
        seen.update(dataset=dataset, openable_only=openable_only)
        return list(items)

    import app.db.artifacts as db_artifacts

    monkeypatch.setattr(db_artifacts, "list_document_artifacts", _list)
    return seen


# ── The scorer ───────────────────────────────────────────────────────────────

def test_full_coverage_of_the_users_words_scores_regardless_of_title_length():
    """A long, specific title still fully answers a short request."""
    assert ao.score_title(
        "compliance reporting",
        "Automated Compliance Reporting for Enterprise Admins",
    ) >= 1.0


def test_contiguous_phrase_outranks_the_same_words_scattered():
    """Both titles use every word the user did; only one says what they said."""
    scattered = ao.score_title("export scheduling", "Scheduling Export Limits")
    contiguous = ao.score_title("export scheduling", "Export Scheduling v2")
    assert contiguous > scattered > ao._COVERAGE_FLOOR


def test_one_incidental_word_in_common_does_not_clear_the_bar():
    """"the reporting dashboard" is not what "compliance reporting" meant."""
    assert ao.score_title("compliance reporting", "Reporting Dashboard") <= ao._COVERAGE_FLOOR


def test_document_nouns_carry_no_weight():
    """Otherwise every PRD partially matches every open request."""
    assert ao.score_title("prd document spec", "Dark Mode PRD") == 0.0


def test_plurals_match_their_singular():
    assert ao.score_title("bulk exports", "Bulk Export Limits") > ao._COVERAGE_FLOOR


def test_empty_query_or_title_scores_nothing():
    assert ao.score_title("", "Dark Mode") == 0.0
    assert ao.score_title("dark mode", "") == 0.0
    assert ao.score_title("dark mode", None) == 0.0


# ── Ranking ──────────────────────────────────────────────────────────────────

def test_ranking_filters_by_type_and_drops_unopenable_rows():
    items = [
        _prd(1, "Compliance Reporting", status="failed"),
        _prd(2, "Compliance Reporting", status="invalidated"),
        _prd(3, "Compliance Reporting"),
        _evidence(4, "Compliance Reporting"),
    ]
    ranked = ao.rank_artifacts(items, "compliance reporting", "prd")
    assert [item["id"] for _s, item in ranked] == [3]


def test_equal_scores_are_ordered_newest_first():
    items = [
        _prd(1, "Compliance Reporting", created_at="2026-07-01"),
        _prd(2, "Compliance Reporting", created_at="2026-08-01"),
    ]
    ranked = ao.rank_artifacts(items, "compliance reporting", "prd")
    assert [item["id"] for _s, item in ranked] == [2, 1]


# ── The 0 / 1 / many contract ────────────────────────────────────────────────

def test_one_match_resolves_with_the_ids_the_panel_needs(monkeypatch):
    _patch_index(monkeypatch, [
        _prd(11, "Compliance Reporting Automation"),
        _prd(12, "Dark Mode"),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="compliance reporting",
        dataset="acme",
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["id"] == 11
    assert out["artifact"]["prd_id"] == 11
    assert out["artifact"]["title"] == "Compliance Reporting Automation"
    assert out["candidates"] == [out["artifact"]]


def test_two_equal_matches_are_ambiguous_and_carry_both_ids(monkeypatch):
    """The live failure: two PRDs match, the assistant asks — and the chips it
    offers must be openable, which means the ids travel with them."""
    _patch_index(monkeypatch, [
        _prd(2216, "Compliance Reporting", created_at="2026-08-02"),
        _prd(2214, "Compliance Reporting", created_at="2026-07-02"),
        _prd(9, "Dark Mode"),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="compliance reporting",
        dataset="acme",
    )
    assert out["status"] == "ambiguous"
    assert out["artifact"] is None
    assert [c["id"] for c in out["candidates"]] == [2216, 2214]
    assert all(c["prd_id"] is not None for c in out["candidates"])


def test_a_clearly_better_match_wins_instead_of_asking(monkeypatch):
    """A weaker partial match must not turn a good hit into a question."""
    _patch_index(monkeypatch, [
        _prd(1, "Compliance Reporting Automation"),
        _prd(2, "Vendor Compliance Checklist"),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="compliance reporting",
        dataset="acme",
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["id"] == 1


def test_no_match_opens_nothing(monkeypatch):
    _patch_index(monkeypatch, [_prd(1, "Dark Mode"), _prd(2, "Bulk Export")])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="compliance reporting",
        dataset="acme",
    )
    assert out["status"] == "not_found"
    assert out["artifact"] is None
    assert out["candidates"] == []


def test_candidates_are_capped(monkeypatch):
    _patch_index(monkeypatch, [
        _prd(i, "Compliance Reporting", created_at=f"2026-08-{i:02d}")
        for i in range(1, 12)
    ])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="compliance reporting",
        dataset="acme",
    )
    assert out["status"] == "ambiguous"
    assert len(out["candidates"]) == ao.MAX_CANDIDATES


def test_evidence_resolves_with_its_insight_coordinates(monkeypatch):
    """The Evidence panel is scoped by (brief, insight), not by an evidence id
    — a candidate missing those cannot be opened."""
    _patch_index(monkeypatch, [
        _evidence(31, "Bulk Export Demand", brief_id=5, insight_index=3),
        _prd(32, "Bulk Export Demand"),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="evidence", query="bulk export demand",
        dataset="acme",
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["type"] == "evidence"
    assert out["artifact"]["brief_id"] == 5
    assert out["artifact"]["insight_index"] == 3


def test_no_dataset_short_circuits_without_a_lookup(monkeypatch):
    seen = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    assert ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="",
    )["status"] == "not_found"
    assert not seen, "no dataset must not hit the index"


def test_empty_query_resolves_the_sole_artifact(monkeypatch):
    """A bare "open the PRD" (no title) arrives here as an EMPTY query. It no
    longer short-circuits to not_found — it resolves to the sole openable
    artifact of the kind (the project-chat single-PRD case), which is what keeps
    the client off the answer engine's "that's a UI action" refusal."""
    _patch_index(monkeypatch, [_prd(1, "Only PRD")])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="   ", dataset="acme",
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["prd_id"] == 1


def test_empty_query_with_several_artifacts_is_ambiguous(monkeypatch):
    """Several openable artifacts and no title → ask which, with real chips —
    never a silent pick, never not_found."""
    _patch_index(monkeypatch, [_prd(1, "Alpha"), _prd(2, "Beta")])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="", dataset="acme",
    )
    assert out["status"] == "ambiguous"
    assert {c["prd_id"] for c in out["candidates"]} == {1, 2}


def test_empty_query_with_no_artifacts_is_not_found(monkeypatch):
    _patch_index(monkeypatch, [])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="", dataset="acme",
    )
    assert out["status"] == "not_found"


def test_lookup_failure_degrades_to_not_found(monkeypatch):
    import app.db.artifacts as db_artifacts

    def _boom(**_kwargs):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db_artifacts, "list_document_artifacts", _boom)
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="acme",
    )
    assert out["status"] == "not_found"


def test_a_kind_this_panel_cannot_show_says_so_instead_of_substituting(monkeypatch):
    """"Open the dark mode prototype" with a dark mode PRD sitting right there
    is the trap: returning that PRD looks like success and is the wrong
    document. The kind is reported back unchanged so the client can say where
    prototypes actually open."""
    seen = _patch_index(monkeypatch, [_prd(1, "Dark Mode"), _evidence(2, "Dark Mode")])
    out = ao.resolve_open_artifact(
        artifact_type="prototype", query="dark mode",
        dataset="acme",
    )
    assert out["status"] == "unsupported_type"
    assert out["artifact_type"] == "prototype"
    assert out["artifact"] is None
    assert out["candidates"] == []
    assert not seen, "an unopenable kind must not even hit the index"


def test_the_lookup_asks_for_openable_rows_only(monkeypatch):
    """`openable_only` is what makes the status filter run BEFORE the
    regeneration family collapses — see db.artifacts.list_document_artifacts."""
    seen = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="acme",
    )
    assert seen["openable_only"] is True


def test_brief_anchoring_travels_with_the_candidate(monkeypatch):
    """A chat/uploaded PRD's insight_index is a storage SENTINEL, not insight 0.

    The client uses this flag to decide whether to hand the pair to the panel's
    Evidence tab — which resolves (briefId, insightIndex) into a document — so
    losing it would render the brief's first finding under an unrelated PRD."""
    _patch_index(monkeypatch, [
        _prd(1, "Dark Mode", brief_anchored=False, insight_index=0),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="acme",
    )
    assert out["artifact"]["brief_anchored"] is False
    assert out["artifact"]["insight_index"] == 0

    _patch_index(monkeypatch, [
        _prd(2, "Bulk Export", brief_anchored=True, insight_index=3),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="bulk export", dataset="acme",
    )
    assert out["artifact"]["brief_anchored"] is True
    assert out["artifact"]["insight_index"] == 3


def test_the_index_is_read_for_the_caller_scope_only(monkeypatch):
    seen = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode",
        dataset="acme--design",
    )
    assert seen == {"dataset": "acme--design", "openable_only": True}


# ── The thread-born kinds: reports, ticket sets, team documents ──────────────
#
# These three were reported `unsupported_type` while the chat panel already had
# a Reports tab, a Tickets tab and a Document tab — so "show me the report" was
# answered by pointing at a different screen for a document the panel beside the
# reader could render. OPENABLE_TYPES had simply gone stale against a panel that
# grew tabs underneath it.
#
# They come from a DIFFERENT listing (the five-table fan-out, not the PRD/
# evidence index) and speak a different type vocabulary ("ticket_set",
# "custom_artifact" where the user said "tickets", "document"), so both the
# source selection and the mapping are pinned here.

def _report(id_, title, *, conversation_id=None, conversation_title=None,
            created_at="2026-08-01"):
    return {
        "type": "report",
        "id": id_,
        "title": title,
        "status": "",
        "created_at": created_at,
        "source": {"conversation_id": conversation_id,
                   "conversation_title": conversation_title},
        "open": {"report_id": id_},
    }


def _ticket_set(id_, title, *, status="ready", conversation_id=None):
    return {
        "type": "ticket_set",
        "id": id_,
        "title": title,
        "status": status,
        "created_at": "2026-08-01",
        "source": {"conversation_id": conversation_id, "conversation_title": None},
        "open": {"ticket_set_id": id_},
    }


def _document(id_, title, *, status="ready", conversation_id=None):
    return {
        "type": "custom_artifact",
        "id": id_,
        "title": title,
        "status": status,
        "created_at": "2026-08-01",
        "source": {"conversation_id": conversation_id, "conversation_title": None},
        "open": {"custom_artifact_id": id_},
    }


def _patch_fanout(monkeypatch, items):
    """Stand in for the five-table listing the thread-born kinds read."""
    seen: dict = {}

    def _list(*, dataset, company_id):
        seen.update(dataset=dataset, company_id=company_id)
        return list(items)

    import app.db.artifacts as db_artifacts

    monkeypatch.setattr(db_artifacts, "list_artifacts_for_company", _list)
    return seen


def test_a_report_opens_instead_of_being_refused(monkeypatch):
    """The reported bug at its narrowest: asking to see a report used to come
    back "a report doesn't open in this panel"."""
    _patch_fanout(monkeypatch, [_report(9, "Onboarding drop-off")])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="onboarding drop-off",
        dataset="acme", company_id="co-1",
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["report_id"] == 9


def test_the_users_word_is_mapped_onto_the_listings_type(monkeypatch):
    """"tickets" and "document" are what the USER says; the listing says
    "ticket_set" and "custom_artifact". Comparing the two directly filters every
    row out and reports not_found for a document sitting right there."""
    _patch_fanout(monkeypatch, [
        _ticket_set(3, "Checkout rework"), _document(4, "Launch plan"),
    ])
    tickets = ao.resolve_open_artifact(
        artifact_type="tickets", query="checkout rework",
        dataset="acme", company_id="co-1",
    )
    assert tickets["status"] == "resolved"
    assert tickets["artifact"]["ticket_set_id"] == 3

    doc = ao.resolve_open_artifact(
        artifact_type="document", query="launch plan",
        dataset="acme", company_id="co-1",
    )
    assert doc["status"] == "resolved"
    assert doc["artifact"]["custom_artifact_id"] == 4


def test_the_open_id_and_the_conversation_stamps_travel(monkeypatch):
    """The client opens on the SAME id its `list_artifacts` cards use, and needs
    the conversation to decide whether the document is this thread's or another
    one's (which is what `standalone` means in the panel)."""
    _patch_fanout(monkeypatch, [
        _report(9, "Churn review", conversation_id=41, conversation_title="Churn chat"),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="churn review",
        dataset="acme", company_id="co-1",
    )
    assert out["artifact"]["report_id"] == 9
    assert out["artifact"]["conversation_id"] == 41
    assert out["artifact"]["conversation_title"] == "Churn chat"


def test_a_bare_open_prefers_this_conversations_own_report(monkeypatch):
    """"Show me the report", said in the chat that just wrote one, means THAT
    report. Scored against the whole library the same sentence would return a
    disambiguation over documents the reader never mentioned."""
    _patch_fanout(monkeypatch, [
        _report(1, "Older report", conversation_id=7),
        _report(2, "This thread's", conversation_id=88),
        _report(3, "Someone else's", conversation_id=9),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="", dataset="acme",
        company_id="co-1", conversation_id=88,
    )
    assert out["status"] == "resolved"
    assert out["artifact"]["report_id"] == 2


def test_a_thread_owning_none_gets_nothing_not_someone_elses(monkeypatch):
    """REPORTED: a chat with no ticket set of its own was handed the
    workspace's only one — another conversation's tickets, opened inside this
    one. A thread-born kind is scoped to its thread, so "none here" is the
    answer, never a substitution from the library."""
    _patch_fanout(monkeypatch, [
        _report(1, "One", conversation_id=7), _report(2, "Two", conversation_id=9),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="", dataset="acme",
        company_id="co-1", conversation_id=88,
    )
    assert out["status"] == "not_found"
    assert out["artifact"] is None
    assert out["candidates"] == []


def test_a_thread_owning_several_asks_among_ITS_OWN(monkeypatch):
    """REPORTED: a chat holding two reports was asked to choose between five
    from across the whole workspace. Two of this thread's is still a question —
    but the question is about this thread's two."""
    _patch_fanout(monkeypatch, [
        _report(1, "Mine A", conversation_id=88),
        _report(2, "Mine B", conversation_id=88),
        _report(3, "Someone else's", conversation_id=9),
        _report(4, "Also not mine", conversation_id=None),
    ])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="", dataset="acme",
        company_id="co-1", conversation_id=88,
    )
    assert out["status"] == "ambiguous"
    assert {c["report_id"] for c in out["candidates"]} == {1, 2}


def test_a_TITLED_open_is_thread_scoped_too(monkeypatch):
    """The scope is the thread, not the phrasing. Naming a title must not be a
    way back out to the library — otherwise "show me the churn report" opens a
    document from a conversation the reader has never seen."""
    _patch_fanout(monkeypatch, [_report(3, "Churn review", conversation_id=9)])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="churn review", dataset="acme",
        company_id="co-1", conversation_id=88,
    )
    assert out["status"] == "not_found"


def test_tickets_and_documents_are_thread_scoped_the_same_way(monkeypatch):
    """All three thread-born kinds, one rule — the ticket set is the one the
    report actually named."""
    _patch_fanout(monkeypatch, [
        _ticket_set(3, "Checkout rework", conversation_id=9),
        _document(4, "Launch plan", conversation_id=9),
    ])
    for kind in ("tickets", "document"):
        out = ao.resolve_open_artifact(
            artifact_type=kind, query="", dataset="acme",
            company_id="co-1", conversation_id=88,
        )
        assert out["status"] == "not_found", kind


def test_a_PROJECT_scope_is_not_narrowed_to_the_open_chat(monkeypatch):
    """A project's container is the PROJECT, and its listing holds artifacts no
    chat produced — a document uploaded to it has no conversation at all.
    Filtering those down to whichever chat is open would hide artifacts that
    genuinely belong to the project the reader is standing in."""
    seen: dict = {}

    def _list(*, project_id, dataset, company_id):
        seen.update(project_id=project_id)
        return [_document(4, "Launch plan", conversation_id=None)]

    import app.db.artifacts as db_artifacts
    monkeypatch.setattr(db_artifacts, "list_artifacts_for_project", _list)

    out = ao.resolve_open_artifact(
        artifact_type="document", query="", dataset="acme",
        company_id="co-1", project_id=12, conversation_id=88,
    )
    assert seen["project_id"] == 12, "the project listing is the source"
    assert out["status"] == "resolved"
    assert out["artifact"]["custom_artifact_id"] == 4


def test_a_first_turn_with_no_conversation_row_still_reads_the_library(monkeypatch):
    """`conversation_id` is None before the row exists. There is no thread to
    scope to and a brand-new chat has produced nothing, so the library-wide
    behaviour stands rather than refusing everything."""
    _patch_fanout(monkeypatch, [_report(1, "Only one", conversation_id=7)])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="", dataset="acme", company_id="co-1",
    )
    assert out["status"] == "resolved"


def test_prds_are_exempt_from_thread_scoping(monkeypatch):
    """A PRD is a LIBRARY document — any chat may legitimately open one, and it
    has its own resume-the-originating-thread path. Scoping it to the open chat
    would break "open the checkout PRD" from anywhere but the chat that wrote
    it."""
    _patch_index(monkeypatch, [_prd(1, "Checkout")])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="checkout", dataset="acme",
        company_id="co-1", conversation_id=88,
    )
    assert out["status"] == "resolved"


def test_a_thread_kind_without_a_company_reads_nothing(monkeypatch):
    """The fan-out is keyed by the company UUID as well as the dataset slug.
    Missing one is not_found, never a lookup against a guessed tenant."""
    seen = _patch_fanout(monkeypatch, [_report(9, "Churn review")])
    out = ao.resolve_open_artifact(
        artifact_type="report", query="churn review", dataset="acme",
    )
    assert out["status"] == "not_found"
    assert not seen


def test_prds_still_come_from_the_openable_only_index(monkeypatch):
    """The fan-out is for the thread-born kinds ONLY. A PRD open keeps reading
    `list_document_artifacts(openable_only=True)`, whose filter-before-collapse
    order is what keeps a family reachable after a restart invalidates its
    head."""
    fanout = _patch_fanout(monkeypatch, [_report(9, "Dark Mode")])
    index = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="acme", company_id="co-1",
    )
    assert out["status"] == "resolved"
    assert index["openable_only"] is True
    assert not fanout, "a PRD open must not pay for the five-table fan-out"


def test_a_prototype_is_still_refused_and_still_costs_no_lookup(monkeypatch):
    """The one kind that genuinely is not a panel: a prototype opens on its own
    route, which means LEAVING the conversation."""
    fanout = _patch_fanout(monkeypatch, [_report(9, "Dark Mode")])
    index = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    out = ao.resolve_open_artifact(
        artifact_type="prototype", query="dark mode", dataset="acme",
        company_id="co-1",
    )
    assert out["status"] == "unsupported_type"
    assert not fanout and not index


def test_a_failed_document_is_not_offered(monkeypatch):
    """A run that produced nothing is not an artifact, and offering it turns a
    good match into a dead click."""
    _patch_fanout(monkeypatch, [_document(4, "Launch plan", status="failed")])
    out = ao.resolve_open_artifact(
        artifact_type="document", query="", dataset="acme", company_id="co-1",
    )
    assert out["status"] == "not_found"
