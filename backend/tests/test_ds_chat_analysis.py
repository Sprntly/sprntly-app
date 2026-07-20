"""Chat "analyze my data" command — intent detection + the DS-engine answer path.

No network/LLM/DB: the engine itself is deterministic pure-Python (pandas/scipy)
and the dataset lookup + decision log are patched in the chat_analysis
namespace. The e2e test plants a strong flag→goal effect in a synthetic users
table and asserts the engine surfaces it as a MEASURED finding in the markdown
answer — exercising the real vendored battery end to end.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import app.ds.chat_analysis as ca
from app.skill_router import is_data_analysis_request


# ── intent detection ─────────────────────────────────────────────────────────

def test_is_data_analysis_request_positive():
    for q in [
        "analyze my data",
        "can you analyze our product usage data?",
        "run an analysis on the uploaded CSVs",
        "what does our data show?",
        "dig into the analytics we uploaded",
        "any patterns in our dataset?",
        "run a data science analysis",
        "explore our usage data for insights",
    ]:
        assert is_data_analysis_request(q), q


def test_is_data_analysis_request_negative():
    for q in [
        # other skills' territory
        "generate a PRD for onboarding",
        "prioritize these features",
        "summarize the customer calls from last week",
        "create tickets for the export feature",
        # qualitative corpora → synthesis/VoC skills, even with a data-noun
        "analyze the survey data",
        "analyze customer feedback data",
        "synthesize the interview data",
        "what did we learn from the call transcripts?",
        # generic questions that merely mention numbers
        "what's our churn rate?",
        "how many users do we have?",
    ]:
        assert not is_data_analysis_request(q), q


# ── answer path ──────────────────────────────────────────────────────────────

COMPANY = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Point chat_analysis at a temp dataset raw/ dir for COMPANY."""
    raw = tmp_path / "acme" / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(
        "app.db.companies.slug_for_company_id", lambda cid: "acme"
    )
    monkeypatch.setattr("app.datasets.raw_path", lambda slug: tmp_path / slug / "raw")
    # Decision log is fire-and-forget infra; keep the test hermetic.
    monkeypatch.setattr(ca, "_log_run", lambda *a, **k: None)
    return raw


def _plant_users_csv(raw: Path, n: int = 2000) -> None:
    """Users table with one strong planted effect: used_export → retained."""
    rng = np.random.default_rng(7)
    users = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "plan": rng.choice(["free", "pro", "team"], n, p=[0.6, 0.3, 0.1]),
            "used_export": rng.random(n) < 0.4,
            "country": rng.choice(["US", "DE", "BR", "IN"], n),
        }
    )
    users["retained"] = rng.random(n) < (0.25 + 0.35 * users["used_export"].to_numpy())
    users.to_csv(raw / "users.csv", index=False)


def test_answer_surfaces_planted_effect(workspace):
    _plant_users_csv(workspace)
    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")
    assert out["_skill"] == "ds-agent"
    assert "Measured findings" in out["answer"]
    assert "used_export" in out["answer"]
    assert "retained" in out["answer"]
    # cohort-as-code + replication evidence must be carried into the chat reply
    assert "cohort:" in out["answer"]
    assert out["key_points"], "measured findings should populate key_points"
    assert out["confidence"] >= 0.9
    # Ask-payload contract (routes/ask.py passes extras through verbatim)
    for field in ("answer", "key_points", "citations", "confidence", "unanswered"):
        assert field in out


def test_answer_no_dataset_dir(monkeypatch):
    monkeypatch.setattr("app.db.companies.slug_for_company_id", lambda cid: None)
    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")
    assert "don't see any uploaded data" in out["answer"]
    assert out["confidence"] == 0.0


def test_answer_no_tabular_files(workspace):
    (workspace / "notes.pdf").write_bytes(b"%PDF-1.4 not a table")
    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")
    assert "none of your uploaded files are tabular" in out["answer"]
    assert out["confidence"] == 0.0


def test_answer_xlsx_sheets_are_staged(workspace):
    _plant_users_csv(workspace)
    # move the CSV into a workbook to prove the xlsx→csv staging path works
    df = pd.read_csv(workspace / "users.csv")
    (workspace / "users.csv").unlink()
    df.to_excel(workspace / "users.xlsx", index=False, sheet_name="users")
    out = ca.answer(enterprise_id=COMPANY, question="analyze the uploaded data")
    assert "used_export" in out["answer"]


def test_answer_malformed_file_does_not_crash(workspace):
    (workspace / "broken.csv").write_text('a,b\n1,"unterminated\n' * 10)
    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")
    # quarantine-not-crash: the engine (or adapter fallback) must return a
    # well-formed payload either way
    assert isinstance(out["answer"], str) and out["answer"]


def test_qa_agent_routes_to_ds(monkeypatch):
    """qa_agent.answer intercepts a data-analysis ask before generic routing."""
    import app.qa_agent as qa

    sentinel = {"answer": "ds!", "key_points": [], "citations": [],
                "confidence": 0.9, "unanswered": "", "_skill": "ds-agent"}
    monkeypatch.setattr(ca, "answer", lambda **kw: sentinel)
    out = qa.answer(
        enterprise_id=COMPANY,
        question="analyze my product usage data",
        dataset="acme",
    )
    assert out is sentinel


def test_answer_unanalyzable_summary_sheet(workspace):
    """A tiny aggregated summary sheet yields no scans — the reply must say the
    data lacked analyzable structure, not claim 'no effect survived'."""
    (workspace / "summary.csv").write_text(
        "Feature,Adoption Rate,Revenue Impact\n"
        "Dashboard,84%,$1.2M\n"
        "Reports,49%,$680K\n"
    )
    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")
    assert "none had the structure" in out["answer"]
    assert "Measured findings" not in out["answer"]
