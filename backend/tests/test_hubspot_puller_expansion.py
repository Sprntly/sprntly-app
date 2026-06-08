"""Tests for the expanded HubSpot puller — tickets, engagements (notes/emails),
owners, line items — plus the per-sub-resource 403 scope-skip behavior."""
from __future__ import annotations

import pytest
import requests

from app.kg_ingest.pullers import hubspot


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(response=resp)


# ---------- individual sub-resource pullers ----------

def test_tickets_puller_yields_support_signals(monkeypatch):
    page = {"results": [{
        "id": "tk1",
        "properties": {
            "subject": "Login broken on SSO",
            "content": "Customer cannot log in via Okta since Tuesday",
            "hs_ticket_priority": "HIGH",
            "hs_pipeline_stage": "2",
            "hs_ticket_category": "BUG",
            "source_type": "EMAIL",
            "hubspot_owner_id": "owner-9",
            "hs_lastmodifieddate": "2026-06-01",
        },
        "associations": {"companies": {"results": [{"id": "co-7"}]}},
    }]}
    monkeypatch.setattr(hubspot, "_get", lambda tok, path, params=None: page)
    recs = list(hubspot._pull_tickets("tok"))
    assert len(recs) == 1
    r = recs[0]
    assert (r.provider, r.kind, r.external_id) == ("hubspot", "ticket", "tk1")
    assert r.properties["priority"] == "HIGH"
    assert r.properties["company_ids"] == ["co-7"]
    assert "Okta" in r.text


def test_engagements_puller_yields_notes_and_emails(monkeypatch):
    responses = {
        "/crm/v3/objects/notes": {"results": [{
            "id": "n1",
            "properties": {"hs_note_body": "Customer wants bulk export",
                           "hubspot_owner_id": "o1", "hs_timestamp": "2026-05-01"},
        }, {
            "id": "n2",  # empty body — skipped
            "properties": {"hs_note_body": "   "},
        }]},
        "/crm/v3/objects/emails": {"results": [{
            "id": "e1",
            "properties": {"hs_email_subject": "Re: renewal",
                           "hs_email_text": "We need SOC2 before signing",
                           "hs_email_direction": "INCOMING",
                           "hs_timestamp": "2026-05-02"},
        }]},
    }
    monkeypatch.setattr(hubspot, "_get",
                        lambda tok, path, params=None: responses[path])
    recs = list(hubspot._pull_engagements("tok"))
    kinds = [(r.kind, r.external_id) for r in recs]
    assert ("note", "n1") in kinds
    assert ("email", "e1") in kinds
    assert all(eid != "n2" for _, eid in kinds)  # empty note dropped
    email = next(r for r in recs if r.kind == "email")
    assert "SOC2" in email.text
    assert email.properties["direction"] == "INCOMING"


def test_owners_puller_yields_attribution(monkeypatch):
    page = {"results": [{
        "id": "o1", "email": "ae@sprntly.ai",
        "firstName": "Sam", "lastName": "Rep", "updatedAt": "2026-04-01",
    }]}
    monkeypatch.setattr(hubspot, "_get", lambda tok, path, params=None: page)
    recs = list(hubspot._pull_owners("tok"))
    assert len(recs) == 1
    assert recs[0].kind == "owner"
    assert recs[0].title == "Sam Rep"
    assert recs[0].properties["email"] == "ae@sprntly.ai"


def test_line_items_puller_yields_revenue_detail(monkeypatch):
    page = {"results": [{
        "id": "li1",
        "properties": {"name": "Enterprise seat", "quantity": "50",
                       "price": "120", "amount": "6000", "hs_sku": "ENT-50",
                       "hs_lastmodifieddate": "2026-06-02"},
        "associations": {"deals": {"results": [{"id": "d1"}]}},
    }]}
    monkeypatch.setattr(hubspot, "_get", lambda tok, path, params=None: page)
    recs = list(hubspot._pull_line_items("tok"))
    assert len(recs) == 1
    r = recs[0]
    assert r.kind == "line_item"
    assert r.properties["amount_usd"] == "6000"
    assert r.properties["sku"] == "ENT-50"
    assert r.properties["deal_ids"] == ["d1"]


# ---------- top-level pull: aggregation + graceful 403 skip ----------

def test_pull_skips_sub_resource_on_403_and_continues(monkeypatch):
    """If the tickets scope was not granted (403), the whole sync must NOT fail —
    tickets are skipped and the remaining sub-resources still yield."""
    def fake_deals(token):
        yield hubspot.RawRecord(provider="hubspot", kind="deal",
                                external_id="d1", title="Acme", text="")

    def fake_tickets(token):
        raise _http_error(403)
        yield  # pragma: no cover — generator marker

    def fake_owners(token):
        yield hubspot.RawRecord(provider="hubspot", kind="owner",
                                external_id="o1", title="Sam", text="")

    monkeypatch.setattr(hubspot, "_SUB_PULLERS", [
        ("deals", fake_deals),
        ("tickets", fake_tickets),
        ("owners", fake_owners),
    ])
    recs = list(hubspot.pull("tok"))
    kinds = [r.kind for r in recs]
    assert kinds == ["deal", "owner"]          # ticket skipped, rest survive


def test_pull_reraises_non_403_errors(monkeypatch):
    """A 500 (real outage / bad token) must propagate, not be silently swallowed."""
    def boom(token):
        raise _http_error(500)
        yield  # pragma: no cover

    monkeypatch.setattr(hubspot, "_SUB_PULLERS", [("deals", boom)])
    with pytest.raises(requests.HTTPError):
        list(hubspot.pull("tok"))


def test_pull_includes_all_granted_sub_resources(monkeypatch):
    """End-to-end with every sub-resource granted — deal + ticket + note +
    owner + line_item all flow through pull()."""
    by_path = {
        "/crm/v3/objects/deals": {"results": [{"id": "d1", "properties": {"dealname": "Acme"}}]},
        "/crm/v3/objects/tickets": {"results": [{"id": "tk1", "properties": {"subject": "Bug"}}]},
        "/crm/v3/objects/notes": {"results": [{"id": "n1", "properties": {"hs_note_body": "note"}}]},
        "/crm/v3/objects/emails": {"results": []},
        "/crm/v3/owners": {"results": [{"id": "o1", "email": "x@y.com"}]},
        "/crm/v3/objects/line_items": {"results": [{"id": "li1", "properties": {"name": "Seat"}}]},
    }
    monkeypatch.setattr(hubspot, "_get",
                        lambda tok, path, params=None: by_path[path])
    recs = list(hubspot.pull("tok"))
    kinds = sorted({r.kind for r in recs})
    assert kinds == ["deal", "line_item", "note", "owner", "ticket"]
