"""Tests for the steady-state race backfill — relinking call signals that
raced ahead of their call_index row (directory.backfill_source_call_ids)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app.call_index as ci
from app.graph import GraphFacade
from app.graph.types import Signal
from app.kg_ingest import directory


@pytest.fixture
def facade(isolated_settings):
    return GraphFacade()


def _signal(facade, enterprise_id, content, *, source_call_id=None,
            provider=None, external_id=None):
    prov = {"source": "extractor"}
    if provider is not None:
        prov["provider"] = provider
    if external_id is not None:
        prov["external_id"] = external_id
    sig = Signal(
        enterprise_id=enterprise_id, source_type="customer_voice",
        kind="finding", content=content, provenance=prov,
        source_call_id=source_call_id,
    )
    facade.write_signal(enterprise_id, sig)
    return sig.id


def test_raced_signal_is_relinked_once_indexed(facade):
    """A signal written unlinked (source_call_id NULL) with a per-call
    external_id gets source_call_id set once call_index has the row."""
    sig_id = _signal(facade, "ent-a", "raced fact",
                     provider="fireflies", external_id="FF1")
    with patch.object(ci, "resolve_call_id", return_value=42) as resolve:
        linked = directory.backfill_source_call_ids(facade, "ent-a")
    resolve.assert_called_once_with("ent-a", "fireflies", "FF1")
    assert linked == 1
    assert facade.get_signal("ent-a", sig_id).source_call_id == 42


def test_legacy_signal_without_external_id_stays_null(facade):
    """A pre-branch batched signal has no per-call external_id — unlinkable by
    construction, left NULL, never counted."""
    sig_id = _signal(facade, "ent-a", "legacy fact")  # no provenance keys
    with patch.object(ci, "resolve_call_id", return_value=99) as resolve:
        linked = directory.backfill_source_call_ids(facade, "ent-a")
    resolve.assert_not_called()
    assert linked == 0
    assert facade.get_signal("ent-a", sig_id).source_call_id is None


def test_already_linked_signal_is_untouched(facade):
    """A signal that already carries source_call_id is not in the unlinked set."""
    sig_id = _signal(facade, "ent-a", "linked fact", source_call_id=7,
                     provider="fireflies", external_id="FF1")
    with patch.object(ci, "resolve_call_id", return_value=42):
        linked = directory.backfill_source_call_ids(facade, "ent-a")
    assert linked == 0
    assert facade.get_signal("ent-a", sig_id).source_call_id == 7


def test_non_call_provider_signal_is_skipped(facade):
    """An unlinked signal from a non-call provider is left alone — resolve is
    never even attempted for it."""
    sig_id = _signal(facade, "ent-a", "hubspot fact",
                     provider="hubspot", external_id="H1")
    with patch.object(ci, "resolve_call_id", return_value=42) as resolve:
        linked = directory.backfill_source_call_ids(facade, "ent-a")
    resolve.assert_not_called()
    assert linked == 0
    assert facade.get_signal("ent-a", sig_id).source_call_id is None


def test_uncatalogued_call_stays_null_until_a_later_cycle(facade):
    """If the call is still not in the index (resolve returns None), the signal
    stays unlinked rather than being given a wrong id."""
    sig_id = _signal(facade, "ent-a", "still racing",
                     provider="fireflies", external_id="FF-NEW")
    with patch.object(ci, "resolve_call_id", return_value=None):
        linked = directory.backfill_source_call_ids(facade, "ent-a")
    assert linked == 0
    assert facade.get_signal("ent-a", sig_id).source_call_id is None
