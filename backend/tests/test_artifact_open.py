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
         insight_index=0, week_label="Week of Aug 1"):
    return {
        "type": "prd",
        "id": id_,
        "title": title,
        "status": status,
        "created_at": created_at,
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
        "source": {"brief_id": brief_id, "week_label": None,
                   "insight_index": insight_index},
        "open": {"brief_id": brief_id, "insight_index": insight_index,
                 "evidence_id": id_},
    }


def _patch_index(monkeypatch, items):
    seen: dict = {}

    def _list(*, dataset):
        seen.update(dataset=dataset)
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


def test_empty_query_or_dataset_short_circuits_without_a_lookup(monkeypatch):
    seen = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    assert ao.resolve_open_artifact(
        artifact_type="prd", query="   ", dataset="acme",
    )["status"] == "not_found"
    assert ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="",
    )["status"] == "not_found"
    assert not seen, "no query / no dataset must not hit the index"


def test_lookup_failure_degrades_to_not_found(monkeypatch):
    import app.db.artifacts as db_artifacts

    def _boom(**_kwargs):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db_artifacts, "list_document_artifacts", _boom)
    out = ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode", dataset="acme",
    )
    assert out["status"] == "not_found"


def test_unknown_artifact_type_falls_back_to_prd(monkeypatch):
    _patch_index(monkeypatch, [_prd(1, "Dark Mode"), _evidence(2, "Dark Mode")])
    out = ao.resolve_open_artifact(
        artifact_type="prototype", query="dark mode",
        dataset="acme",
    )
    assert out["artifact_type"] == "prd"
    assert out["status"] == "resolved"
    assert out["artifact"]["id"] == 1


def test_the_index_is_read_for_the_caller_scope_only(monkeypatch):
    seen = _patch_index(monkeypatch, [_prd(1, "Dark Mode")])
    ao.resolve_open_artifact(
        artifact_type="prd", query="dark mode",
        dataset="acme--design",
    )
    assert seen == {"dataset": "acme--design"}
