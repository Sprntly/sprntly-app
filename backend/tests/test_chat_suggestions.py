"""app.chat_suggestions — the abstention contract, then the generation.

The acceptance criterion for this feature is NEGATIVE: when Sprntly does not
know what to suggest it must suggest nothing. So the bulk of this suite proves
that suggestions get DROPPED, and only a couple of cases prove that a
well-grounded one survives.

Covered:
  - pre-call abstention (feature off, no exchange yet, empty/failed answer) —
    and that these paths never reach the model at all
  - the anchor gate: a suggestion whose claimed grounding is not in the
    conversation is dropped, however confident it sounds
  - confidence floor, filler denylist, length band, de-duplication (including
    against what the user already asked), cap of three
  - fail CLOSED: gateway error, non-dict output, malformed items → []
  - the prompt actually carries the thread + the open-PRD line
  - the route: ownership gating, `[]` as an ordinary 200, foreign
    conversation_id → silence not another tenant's turns
"""
from __future__ import annotations

import app.chat_suggestions as cs
import app.routes.chat as chat_route
from app.db.client import require_client


class _FakeResult:
    def __init__(self, output):
        self.output = output


def _patch_llm(monkeypatch, output, calls=None):
    def _fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if isinstance(output, Exception):
            raise output
        return _FakeResult(output)

    monkeypatch.setattr(cs, "llm_call", _fake)


# A thread with real, quotable content — the anchors below are copied from it.
_THREAD = [
    {"role": "user", "content": "What are the top complaints about checkout?"},
    {
        "role": "assistant",
        "content": (
            "Three themes dominate: promo codes drop the session (23 tickets), "
            "the address form rejects valid postcodes, and Apple Pay is missing "
            "on mobile. Promo codes are the largest by volume."
        ),
    },
]


def _suggestion(prompt, anchor, confidence=0.9):
    return {"prompt": prompt, "anchor": anchor, "confidence": confidence}


# ── Pre-call abstention: silence that costs nothing ──────────────────────────

def test_feature_off_returns_empty_without_calling_the_model(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"suggestions": [_suggestion(
        "Break the promo code issue into tickets", "promo codes drop the session")]},
        calls)
    monkeypatch.setenv("CHAT_SUGGESTIONS_ENABLED", "false")

    assert cs.suggest_next_prompts("ent-1", _THREAD) == []
    assert calls == []  # the kill switch is a cost switch: no spend at all


def test_kill_switch_accepts_the_usual_falsey_spellings(monkeypatch):
    for value in ("0", "false", "FALSE", "no", "off", " Off "):
        monkeypatch.setenv("CHAT_SUGGESTIONS_ENABLED", value)
        assert cs.enabled() is False, value
    for value in ("", "1", "true", "yes", "anything-else"):
        monkeypatch.setenv("CHAT_SUGGESTIONS_ENABLED", value)
        assert cs.enabled() is True, value
    monkeypatch.delenv("CHAT_SUGGESTIONS_ENABLED", raising=False)
    assert cs.enabled() is True  # DEFAULT ON


def test_no_answered_turn_yet_abstains_without_calling_the_model(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"suggestions": []}, calls)

    assert cs.suggest_next_prompts("ent-1", []) == []
    assert cs.suggest_next_prompts(
        "ent-1", [{"role": "user", "content": "hello?"}]
    ) == []
    assert calls == []


def test_empty_or_failed_answer_abstains_without_calling_the_model(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"suggestions": [_suggestion(
        "Show me the promo code tickets", "promo codes drop the session")]}, calls)

    for last in ("", "   ", "Sorry.", "I don't know."):
        history = [
            {"role": "user", "content": "What are the top complaints about checkout?"},
            {"role": "assistant", "content": last},
        ]
        assert cs.suggest_next_prompts("ent-1", history) == [], last
    assert calls == []


# ── The anchor gate ──────────────────────────────────────────────────────────

def test_untethered_suggestion_is_dropped_however_confident(monkeypatch):
    """The headline case. The model returns a fluent, on-topic-sounding
    suggestion at confidence 0.99 whose anchor was never said. It must not
    reach the user."""
    _patch_llm(monkeypatch, {"suggestions": [
        _suggestion("Compare our refund rate to last quarter",
                    "refund rate trends", confidence=0.99),
    ]})
    assert cs.suggest_next_prompts("ent-1", _THREAD) == []


def test_anchor_present_in_the_thread_survives(monkeypatch):
    _patch_llm(monkeypatch, {"suggestions": [
        _suggestion("Break the promo code session bug into tickets",
                    "promo codes drop the session"),
    ]})
    assert cs.suggest_next_prompts("ent-1", _THREAD) == [
        "Break the promo code session bug into tickets"
    ]


def test_anchor_match_forgives_case_and_punctuation_only():
    text = cs._conversation_text(_THREAD)
    # Faithful quote with different casing/punctuation — kept.
    assert cs.filter_suggestions(
        {"suggestions": [_suggestion("Draft a PRD for Apple Pay on mobile",
                                     "Apple Pay is missing, on mobile!")]},
        text,
    ) == ["Draft a PRD for Apple Pay on mobile"]
    # A paraphrase is NOT a quote — dropped.
    assert cs.filter_suggestions(
        {"suggestions": [_suggestion("Draft a PRD for Apple Pay on mobile",
                                     "Apple Pay unsupported on handsets")]},
        text,
    ) == []


def test_trivially_short_anchor_cannot_buy_a_pass():
    text = cs._conversation_text(_THREAD)
    assert cs.filter_suggestions(
        {"suggestions": [_suggestion("Draft a PRD for the address form", "the")]},
        text,
    ) == []


# ── The remaining deterministic gates ────────────────────────────────────────

def test_low_confidence_is_dropped():
    text = cs._conversation_text(_THREAD)
    assert cs.filter_suggestions(
        {"suggestions": [_suggestion("Break the promo code bug into tickets",
                                     "promo codes drop the session",
                                     confidence=0.4)]},
        text,
    ) == []


def test_generic_filler_is_dropped_even_with_a_real_anchor():
    """Filler is the specific failure the requirement names — and it is worse
    when the model dresses it in a valid anchor."""
    text = cs._conversation_text(_THREAD)
    for filler in (
        "Tell me more about this",
        "Can you elaborate?",
        "What else?",
        "What are the next steps?",
        "Summarize this",
        "Any other thoughts?",
        "Show me more about that",
    ):
        assert cs.filter_suggestions(
            {"suggestions": [_suggestion(filler, "promo codes drop the session")]},
            text,
        ) == [], filler


def test_length_band_rejects_one_word_and_paragraph_suggestions():
    text = cs._conversation_text(_THREAD)
    assert cs.filter_suggestions(
        {"suggestions": [_suggestion("Yes", "promo codes drop the session")]}, text
    ) == []
    assert cs.filter_suggestions(
        {"suggestions": [_suggestion("Draft a PRD " + "x" * 200,
                                     "promo codes drop the session")]},
        text,
    ) == []


def test_duplicates_and_already_asked_questions_are_dropped():
    text = cs._conversation_text(_THREAD)
    asked = cs._user_questions(_THREAD)
    out = cs.filter_suggestions(
        {"suggestions": [
            _suggestion("Break the promo code bug into tickets",
                        "promo codes drop the session"),
            _suggestion("break the PROMO CODE bug into tickets!",
                        "promo codes drop the session"),
            _suggestion("What are the top complaints about checkout?",
                        "top complaints about checkout"),
        ]},
        text,
        asked,
    )
    assert out == ["Break the promo code bug into tickets"]


def test_capped_at_three():
    text = cs._conversation_text(_THREAD)
    out = cs.filter_suggestions(
        {"suggestions": [
            _suggestion(f"Draft a PRD for issue number {n}",
                        "promo codes drop the session")
            for n in range(6)
        ]},
        text,
    )
    assert len(out) == cs.MAX_SUGGESTIONS == 3


# ── Fail closed ──────────────────────────────────────────────────────────────

def test_gateway_error_returns_empty(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("gateway down"))
    assert cs.suggest_next_prompts("ent-1", _THREAD) == []


def test_malformed_output_returns_empty(monkeypatch):
    for output in (
        None,
        "not a dict",
        {},
        {"suggestions": "nope"},
        {"suggestions": [None, 7, "text"]},
        {"suggestions": [{"prompt": "Break the promo bug into tickets"}]},
        {"suggestions": [{"prompt": "Break the promo bug into tickets",
                          "anchor": "promo codes drop the session",
                          "confidence": "high"}]},
    ):
        _patch_llm(monkeypatch, output)
        assert cs.suggest_next_prompts("ent-1", _THREAD) == [], output


def test_model_returning_an_empty_list_is_an_ordinary_result(monkeypatch):
    _patch_llm(monkeypatch, {"suggestions": []})
    assert cs.suggest_next_prompts("ent-1", _THREAD) == []


# ── Prompt assembly ──────────────────────────────────────────────────────────

def test_prompt_carries_the_thread_and_the_open_prd(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"suggestions": []}, calls)
    cs.suggest_next_prompts("ent-1", _THREAD, prd_id=42, prd_title="Checkout")
    (call,) = calls
    assert call["model"] == "claude-haiku-4-5"  # cheap tier, per-turn cost
    assert call["purpose"] == "chat_suggestions"
    assert 'PRD #42 — "Checkout" is open' in call["input"]
    assert "top complaints about checkout" in call["input"]
    assert "empty list" in call["input"]
    # The schema must let the model say nothing in one token.
    items = call["json_schema"]["properties"]["suggestions"]
    assert "minItems" not in items
    assert items["maxItems"] == cs.MAX_SUGGESTIONS


# ── Route ────────────────────────────────────────────────────────────────────

def _seed_conversation(company_id, user_id, turns=()):
    conv = require_client().table("conversations").insert({
        "company_id": company_id, "user_id": user_id,
        "title": "chat", "query": "chat", "agent_type": "ask",
    }).execute().data[0]
    for role, content in turns:
        require_client().table("conversation_turns").insert(
            {"conversation_id": conv["id"], "role": role, "content": content}
        ).execute()
    return conv["id"]


def _capture(monkeypatch, suggestions):
    seen: dict = {}

    def _suggest(enterprise_id, history=None, *, prd_id=None, prd_title=None):
        seen.update(enterprise_id=enterprise_id, history=history,
                    prd_id=prd_id, prd_title=prd_title)
        return list(suggestions)

    monkeypatch.setattr(chat_route, "suggest_next_prompts", _suggest)
    return seen


def test_route_returns_suggestions_with_history_loaded_server_side(
    tenant_client, monkeypatch
):
    t = tenant_client.make(slug="acme")
    conv_id = _seed_conversation(t.company_id, t.user_id, turns=[
        ("user", "What are the top complaints about checkout?"),
        ("assistant", "Promo codes drop the session, 23 tickets."),
    ])
    seen = _capture(monkeypatch, ["Break the promo code bug into tickets"])

    resp = t.client.post("/v1/chat/suggestions", json={"conversation_id": conv_id})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"suggestions": ["Break the promo code bug into tickets"]}
    assert seen["enterprise_id"] == t.company_id
    assert [row["role"] for row in seen["history"]] == ["user", "assistant"]


def test_route_empty_suggestions_is_an_ordinary_200(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    conv_id = _seed_conversation(t.company_id, t.user_id, turns=[
        ("user", "thanks"), ("assistant", "Anytime.")])
    _capture(monkeypatch, [])

    resp = t.client.post("/v1/chat/suggestions", json={"conversation_id": conv_id})
    assert resp.status_code == 200
    assert resp.json() == {"suggestions": []}


def test_route_foreign_conversation_yields_silence_not_turns(
    tenant_client, monkeypatch
):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="rival")
    foreign_conv = _seed_conversation(other.company_id, other.user_id, turns=[
        ("user", "rival secret roadmap question"),
        ("assistant", "Rival's confidential answer."),
    ])
    seen = _capture(monkeypatch, [])

    resp = t.client.post(
        "/v1/chat/suggestions", json={"conversation_id": foreign_conv}
    )
    assert resp.status_code == 200
    assert resp.json() == {"suggestions": []}
    # The other tenant's turns never reached the generator.
    assert seen["history"] == []


def test_route_foreign_prd_is_404(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    other = tenant_client.make(slug="rival")
    db = isolated_settings["db"]
    brief_id = db.save_brief(
        dataset="rival", week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}],
                 "_schema_version": 1},
        schema_version=1,
    )
    foreign_prd = db.start_prd(
        brief_id=brief_id, insight_index=0, title="Rival doc",
        template_version=1, variant="v3", source="chat", theme_id="chat:seed",
    )
    db.complete_prd(foreign_prd, title="Rival doc", md="<html><body>x</body></html>")
    conv_id = _seed_conversation(t.company_id, t.user_id, turns=[
        ("user", "hi"), ("assistant", "hello")])
    seen = _capture(monkeypatch, [])

    resp = t.client.post(
        "/v1/chat/suggestions",
        json={"conversation_id": conv_id, "prd_id": foreign_prd},
    )
    assert resp.status_code == 404
    assert not seen  # gated before any generation
    assert other  # the owner exists; ownership is the only difference


def test_route_requires_a_conversation(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    seen = _capture(monkeypatch, [])
    assert t.client.post("/v1/chat/suggestions", json={}).status_code == 422
    assert not seen


def test_route_unauthenticated_is_401(unauth_client, monkeypatch):
    seen = _capture(monkeypatch, [])
    resp = unauth_client.post("/v1/chat/suggestions", json={"conversation_id": 1})
    assert resp.status_code == 401
    assert not seen
