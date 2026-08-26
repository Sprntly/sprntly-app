"""Tests for app.kg_ingest.directory — person minting + action-item owner
resolution, and the off-evidence-path guarantee that a `person` node can never
leak into a brief or theme retrieval."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.call_index import IndexedCall
from app.graph import GraphFacade
from app.graph.types import Relationship, Signal
from app.kg_ingest import directory


@pytest.fixture
def facade(isolated_settings):
    return GraphFacade()


def _call(external_id, participants, *, provider="fireflies"):
    return IndexedCall(
        external_id=external_id, title="Sync", call_date="2026-08-01",
        duration_min=30.0, participants=participants, account=None,
        summary="", provider=provider,
    )


def _persons(facade, enterprise_id):
    return facade.query_entities(enterprise_id, type="person")


# ── person minting ───────────────────────────────────────────────────────────

def test_shared_attendee_across_two_calls_is_one_person(facade):
    """Two calls that share an attendee email → exactly ONE person node."""
    own: set[str] = set()  # membership empty; nothing is 'ours'
    c1 = _call("FF1", ["jane.doe@acme.com", "rep@vendorco.com"])
    c2 = _call("FF2", ["jane.doe@acme.com", "someone@beta.io"])
    directory.mint_persons_for_call(facade, "ent-a", c1, own)
    directory.mint_persons_for_call(facade, "ent-a", c2, own)

    people = _persons(facade, "ent-a")
    janes = [p for p in people if p.canonical_label == "Jane Doe"]
    assert len(janes) == 1, f"expected one Jane Doe, got {[p.canonical_label for p in people]}"
    assert janes[0].properties["company"] == "Acme"
    assert janes[0].properties["internal"] is False


def test_same_name_different_company_is_two_people(facade):
    """Two different people who share a name at different companies → TWO nodes.
    Deterministic (name, company) key, not embedding collapse."""
    own: set[str] = set()
    directory.mint_persons_for_call(
        facade, "ent-a", _call("FF1", ["jane.doe@acme.com"]), own)
    directory.mint_persons_for_call(
        facade, "ent-a", _call("FF2", ["jane.doe@globex.com"]), own)

    janes = [p for p in _persons(facade, "ent-a") if p.canonical_label == "Jane Doe"]
    companies = sorted(p.properties["company"] for p in janes)
    assert companies == ["Acme", "Globex"]


def test_consumer_domain_and_nameonly_participants_are_skipped(facade):
    """A consumer domain (gmail) and an address-less bare name → no
    company-less person is minted for either."""
    own: set[str] = set()
    c = _call("FF1", ["personal@gmail.com", "Just A Name", "real@acme.com"])
    directory.mint_persons_for_call(facade, "ent-a", c, own)

    labels = sorted(p.canonical_label for p in _persons(facade, "ent-a"))
    assert labels == ["Real"]  # only the acme.com participant survives


def test_own_domain_participant_is_internal(facade):
    """A participant on our own domain is kept, flagged internal, company = ours."""
    own = {"vendorco.com"}
    c = _call("FF1", ["boss@vendorco.com"])
    directory.mint_persons_for_call(facade, "ent-a", c, own)
    p = _persons(facade, "ent-a")[0]
    assert p.properties["internal"] is True
    assert p.properties["company"] == "Vendorco"


def test_new_person_links_scoped_to_company_root(facade):
    """A minted person is wired person —SCOPED_TO→ company (entity→entity), and
    the edge is not duplicated on a re-mint."""
    own: set[str] = set()
    c = _call("FF1", ["jane.doe@acme.com"])
    directory.mint_persons_for_call(facade, "ent-a", c, own)
    directory.mint_persons_for_call(facade, "ent-a", c, own)  # idempotent re-run

    person = _persons(facade, "ent-a")[0]
    edges = facade.edges_from("ent-a", person.id, type="SCOPED_TO")
    assert len(edges) == 1
    assert edges[0].source_kind == "entity" and edges[0].target_kind == "entity"
    company_id = facade.ensure_company_entity("ent-a")
    assert edges[0].target_id == company_id


# ── owner resolution ─────────────────────────────────────────────────────────

def _action_item(facade, enterprise_id, external_id, owner, *,
                 provider="fireflies", content="ship the thing"):
    sig = Signal(
        enterprise_id=enterprise_id, source_type="customer_voice",
        kind="finding", content=content,
        properties={"owner": owner, "due": "Friday", "status": "open"},
        provenance={"source": "extractor", "provider": provider,
                    "external_id": external_id},
    )
    facade.write_signal(enterprise_id, sig)
    return sig.id


def test_owner_matching_a_participant_gets_owner_person_id(facade):
    """properties.owner='Jane Doe' with jane.doe@acme.com in participants →
    owner_person_id set to that (minted) person; raw owner name retained."""
    own: set[str] = set()
    sig_id = _action_item(facade, "ent-a", "FF1", "Jane Doe")
    stamped = directory.resolve_owners_for_call(
        facade, "ent-a", "fireflies", "FF1",
        ["jane.doe@acme.com", "rep@vendorco.com"], own)
    assert stamped == 1
    sig = facade.get_signal("ent-a", sig_id)
    pid = sig.properties["owner_person_id"]
    assert sig.properties["owner"] == "Jane Doe"  # raw name kept
    person = facade.get_entity("ent-a", pid)
    assert person.type == "person"
    assert person.canonical_label == "Jane Doe"
    assert person.properties["company"] == "Acme"


def test_owner_not_in_participants_leaves_owner_person_id_unset(facade):
    """An owner not among the participants → owner_person_id unset, name kept.
    Never fabricated."""
    own: set[str] = set()
    sig_id = _action_item(facade, "ent-a", "FF1", "Someone Elsewhere")
    stamped = directory.resolve_owners_for_call(
        facade, "ent-a", "fireflies", "FF1",
        ["jane.doe@acme.com", "bob@acme.com"], own)
    assert stamped == 0
    sig = facade.get_signal("ent-a", sig_id)
    assert "owner_person_id" not in sig.properties
    assert sig.properties["owner"] == "Someone Elsewhere"


def test_owner_matches_by_local_part_pattern(facade):
    """jdoe@acme.com resolves 'Jane Doe' via the finitial+last pattern."""
    own: set[str] = set()
    sig_id = _action_item(facade, "ent-a", "FF1", "Jane Doe")
    directory.resolve_owners_for_call(
        facade, "ent-a", "fireflies", "FF1", ["jdoe@acme.com"], own)
    sig = facade.get_signal("ent-a", sig_id)
    assert "owner_person_id" in sig.properties


def test_nameonly_owner_match_yields_no_person(facade):
    """A Meet-style name-only participant that matches the owner confirms who
    but cannot mint a company-bearing person, so owner_person_id stays unset."""
    own: set[str] = set()
    sig_id = _action_item(facade, "ent-a", "MEET1", "Jane Doe", provider="google_meet")
    stamped = directory.resolve_owners_for_call(
        facade, "ent-a", "google_meet", "MEET1", ["Jane Doe"], own)
    assert stamped == 0
    sig = facade.get_signal("ent-a", sig_id)
    assert "owner_person_id" not in sig.properties
    assert not _persons(facade, "ent-a")


def test_owner_resolution_ignores_other_calls_signals(facade):
    """Only the named call's action items are touched; another call's are not."""
    own: set[str] = set()
    mine = _action_item(facade, "ent-a", "FF1", "Jane Doe", content="mine")
    other = _action_item(facade, "ent-a", "FF2", "Jane Doe", content="other")
    directory.resolve_owners_for_call(
        facade, "ent-a", "fireflies", "FF1", ["jane.doe@acme.com"], own)
    assert "owner_person_id" in facade.get_signal("ent-a", mine).properties
    assert "owner_person_id" not in facade.get_signal("ent-a", other).properties


# ── off evidence/retrieval path ──────────────────────────────────────────────

def test_person_does_not_leak_into_convergence_or_theme_query(facade):
    """A person entity (and its person→company edge) contributes NOTHING to
    brief sufficiency, and is never returned by a theme query."""
    from app.synthesis.convergence import compute_convergence

    # A real theme with one supporting signal → the brief-relevant baseline.
    theme = _entity(facade, "ent-a", "theme", "AI authoring")
    sig = Signal(enterprise_id="ent-a", source_type="customer_voice",
                 kind="feature_request", content="wants AI authoring")
    facade.write_signal("ent-a", sig)
    facade.write_relationship("ent-a", Relationship(
        enterprise_id="ent-a", type="SUPPORTS", source_kind="signal",
        source_id=sig.id, target_kind="entity", target_id=theme.id))

    before = compute_convergence(facade, "ent-a")

    # Now mint a person and wire it to the company root.
    directory.mint_persons_for_call(
        facade, "ent-a", _call("FF1", ["jane.doe@acme.com"]), set())

    after = compute_convergence(facade, "ent-a")

    # The person is not a theme…
    theme_labels = {t.theme_label for t in after}
    assert "Jane Doe" not in theme_labels
    # The theme query never returns the person, even though it now exists.
    queried = facade.query_entities("ent-a", type="theme")
    assert all(t.type == "theme" for t in queried)
    assert "Jane Doe" not in {t.canonical_label for t in queried}
    # …and it changes no theme's score or signal count.
    assert [(t.theme_id, t.signal_count) for t in before] \
        == [(t.theme_id, t.signal_count) for t in after]


def _entity(facade, enterprise_id, type_, label):
    from app.graph.types import Entity
    ent = Entity(enterprise_id=enterprise_id, type=type_, canonical_label=label)
    facade.create_entity(enterprise_id, ent)
    return ent


# ── runner wiring (sync_provider → resolve_owners) ───────────────────────────

def test_runner_stamps_owner_person_id_for_a_call_provider(facade, monkeypatch):
    """A fireflies sync whose extraction writes an action item with a
    participant-matching owner gets owner_person_id stamped by the post-loop
    owner-resolution pass — end-to-end through runner.sync_provider."""
    from app.kg_ingest import runner
    from app.kg_ingest.types import RawRecord

    written: dict[str, str] = {}

    def fake_extract(f, enterprise_id, *, doc_name, text, source_ref=None, **kw):
        provider, external_id = source_ref
        sig = Signal(
            enterprise_id=enterprise_id, source_type="customer_voice",
            kind="finding", content=f"action from {external_id}",
            properties={"owner": "Jane Doe", "status": "open"},
            provenance={"source": "extractor", "provider": provider,
                        "external_id": external_id},
        )
        f.write_signal(enterprise_id, sig)
        written[external_id] = sig.id
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)

    rec = RawRecord(provider="fireflies", kind="meeting", external_id="FF1",
                    title="Sync", text="body",
                    properties={"participants": ["jane.doe@acme.com",
                                                 "rep@vendorco.com"]})
    runner.sync_provider(facade, "ent-a", "fireflies", token="t", records=[rec])

    sig = facade.get_signal("ent-a", written["FF1"])
    pid = sig.properties.get("owner_person_id")
    assert pid, f"expected owner_person_id, got {sig.properties}"
    assert facade.get_entity("ent-a", pid).canonical_label == "Jane Doe"
