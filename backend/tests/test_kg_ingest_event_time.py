"""Event-time fidelity for dated connector records (Fireflies meetings).

The bug these pin: every Fireflies signal in prod carried
`provenance={"doc": "fireflies-sync-batch-N"}` and `valid_at` = the sync date.
2,229 signals for one workspace sat inside a 9-day window while the calls they
came from spanned two and a half years, so every recency/trend question over
customer voice silently answered about the sync, not the conversation.

Three properties, one per failure mode:
  • a dated record is its OWN document (batching destroys attribution),
  • its transcript id + meeting date reach provenance,
  • valid_at is the MEETING time while transaction_at stays ingest time.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.kg_ingest import runner
from app.kg_ingest.types import RawRecord


def _meeting(i: int, when: str | None = "2026-02-14T16:25:00+00:00") -> RawRecord:
    return RawRecord(
        provider="fireflies", kind="meeting", external_id=f"ff-{i}",
        title=f"Call {i}", text=f"summary: customer {i} wants SSO",
        timestamp=when,
    )


def _task(i: int) -> RawRecord:
    return RawRecord(
        provider="clickup", kind="task", external_id=f"t{i}",
        title=f"Task {i}", text=f"body {i}",
    )


@pytest.fixture(autouse=True)
def _no_ledger(monkeypatch):
    """Ledger out of the way — these tests are about attribution, not cost."""
    monkeypatch.setattr(runner, "seen_hashes", lambda *a, **k: set())
    monkeypatch.setattr(runner, "record_hashes", lambda *a, **k: None)


@pytest.fixture
def extracted(monkeypatch):
    """Capture every extract_document kwarg set."""
    calls: list[dict] = []

    def fake_extract(facade, enterprise_id, **kwargs):
        calls.append(kwargs)
        return {"signals": 1, "themes": 0, "skipped": 0}

    monkeypatch.setattr(runner, "extract_document", fake_extract)
    return calls


def test_each_meeting_is_its_own_document(extracted):
    runner.sync_provider(None, "ent-A", "fireflies", token="k",
                         records=[_meeting(1), _meeting(2), _meeting(3)])
    assert len(extracted) == 3, "meetings must not share a document"
    assert [c["doc_name"] for c in extracted] == [
        "fireflies:ff-1", "fireflies:ff-2", "fireflies:ff-3"
    ]


def test_meeting_id_and_date_reach_provenance(extracted):
    runner.sync_provider(None, "ent-A", "fireflies", token="k",
                         records=[_meeting(7)])
    prov = extracted[0]["provenance_extra"]
    assert prov["external_id"] == "ff-7"
    assert prov["occurred_at"].startswith("2026-02-14")


def test_valid_at_is_the_meeting_time_not_now(extracted):
    runner.sync_provider(None, "ent-A", "fireflies", token="k",
                         records=[_meeting(1)])
    valid_at = extracted[0]["valid_at"]
    assert valid_at == datetime(2026, 2, 14, 16, 25, tzinfo=timezone.utc)


def test_undated_meeting_degrades_to_no_event_time(extracted):
    """A missing/unparseable date must not fail the sync — valid_at falls back
    to the extractor's now(), i.e. the pre-change behaviour."""
    runner.sync_provider(None, "ent-A", "fireflies", token="k",
                         records=[_meeting(1, when=None),
                                  _meeting(2, when="not-a-date")])
    assert len(extracted) == 2
    assert all(c["valid_at"] is None for c in extracted)
    assert all("occurred_at" not in (c["provenance_extra"] or {})
               for c in extracted)


def test_naive_timestamp_is_treated_as_utc(extracted):
    runner.sync_provider(None, "ent-A", "fireflies", token="k",
                         records=[_meeting(1, when="2026-02-14T16:25:00")])
    assert extracted[0]["valid_at"].tzinfo is not None


def test_batched_providers_are_unchanged(extracted):
    """Only dated-event providers go per-record; ClickUp still batches and
    still carries no event time."""
    runner.sync_provider(None, "ent-A", "clickup", token="k",
                         records=[_task(1), _task(2), _task(3)])
    assert len(extracted) == 1, "non-event providers must still batch"
    assert extracted[0]["doc_name"] == "clickup-sync-batch-0"
    assert extracted[0]["valid_at"] is None
