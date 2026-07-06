"""Tests for the KG-chain end-to-end fixes:

  1. Long-output skills (e.g. prd-author) stream on a raised read timeout via
     the gateway, accumulating the streamed text into the same return value,
     and don't trip the default per-request timeout on a slow response.
  2. KpiTree tolerates a legacy bare-string north_star (coerce → {metric}) and
     None/garbage shapes (→ a safe default) instead of raising ValidationError,
     keeping the goal-fit path working for legacy companies.
  3. warm_synthesis_drilldowns runs from a no-loop (startup worker thread)
     context without "no running event loop" and still fans out the warming.

Network is never hit: the Anthropic client / gateway / warm runners are stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# =====================================================================
# BUG 1 — gateway streaming + raised timeout for long-output skills
# =====================================================================

def _usage():
    return SimpleNamespace(
        input_tokens=120, output_tokens=5200,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )


def _text_msg(text: str):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=_usage(), stop_reason="end_turn",
    )


class _StreamCtx:
    """Mimic the SDK's `with client.messages.stream(...) as s:` context."""

    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class _RecordingClient:
    """Records create/stream calls; create() can simulate a slow non-stream
    response that would trip a short read timeout."""

    def __init__(self, *, text="PART A\n---\nPART B", create_raises=None):
        self.create_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.create_calls.append(kw)
                if create_raises is not None:
                    raise create_raises
                return _text_msg(text)

            def stream(self, **kw):
                outer.stream_calls.append(kw)
                return _StreamCtx(_text_msg(text))

        self.messages = _Messages()


@pytest.fixture
def patch_client(monkeypatch):
    from app import llm

    def _install(client):
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return client

    return _install


@pytest.fixture
def patch_skill(monkeypatch):
    """Stub get_skill so the gateway can bind a 'prd-author' method block
    without needing on-disk skill assets."""
    spec = SimpleNamespace(
        id="prd-author", content_hash="abc123",
        method="METHOD BODY", modules={}, templates={},
        references={}, assets={},
    )
    import app.graph.gateway as gw
    monkeypatch.setattr(gw, "get_skill", lambda name: spec)
    return spec


def test_long_output_skill_uses_stream_path(isolated_settings, patch_client, patch_skill):
    from app.graph import gateway

    client = patch_client(_RecordingClient(text="HUMAN PRD\n---\nIMPL SPEC"))
    res = gateway.llm_call(
        enterprise_id="acme", agent="prd", purpose="generate_prd",
        prompt_version="prd-author-v1", system="sys", input="make a prd",
        skill="prd-author", log=False,
    )
    # Streamed, not the plain create path.
    assert client.stream_calls and not client.create_calls
    # Accumulated streamed text is returned unchanged.
    assert res.output == "HUMAN PRD\n---\nIMPL SPEC"
    assert res.output_tokens == 5200


def test_long_output_skill_uses_raised_timeout(isolated_settings, patch_client, patch_skill):
    from app.graph import gateway
    from app import llm as llm_mod

    client = patch_client(_RecordingClient())
    gateway.llm_call(
        enterprise_id="acme", agent="prd", purpose="generate_prd",
        prompt_version="prd-author-v1", system="sys", input="x",
        skill="prd-author", log=False,
    )
    assert client.stream_calls[0]["timeout"] == llm_mod.LONG_REQUEST_TIMEOUT_S
    assert llm_mod.LONG_REQUEST_TIMEOUT_S >= 600.0


def test_non_long_skill_keeps_non_stream_path(isolated_settings, patch_client, patch_skill):
    """A non-long skill (md output) still goes through create(), no stream,
    no timeout override — behavior unchanged for existing callers."""
    from app.graph import gateway

    patch_skill.id = "some-other-skill"
    client = patch_client(_RecordingClient(text="ok"))
    res = gateway.llm_call(
        enterprise_id="acme", agent="x", purpose="p",
        prompt_version="v1", system="sys", input="x",
        skill="some-other-skill", log=False,
    )
    assert client.create_calls and not client.stream_calls
    assert "timeout" not in client.create_calls[0]
    assert res.output == "ok"


def test_no_skill_keeps_non_stream_path(isolated_settings, patch_client):
    from app.graph import gateway

    client = patch_client(_RecordingClient(text="plain"))
    gateway.llm_call(
        enterprise_id="acme", agent="x", purpose="p",
        prompt_version="v1", system="sys", input="x", log=False,
    )
    assert client.create_calls and not client.stream_calls
    assert "timeout" not in client.create_calls[0]


def test_prd_author_does_not_raise_on_slow_mock(isolated_settings, patch_client, patch_skill):
    """A slow generation is served via the stream context (which the mock makes
    instant) — the prd-author path returns rather than raising a timeout."""
    from app.graph import gateway

    client = patch_client(_RecordingClient(text="A\n---\nB"))
    res = gateway.llm_call(
        enterprise_id="acme", agent="prd", purpose="generate_prd",
        prompt_version="prd-author-v1", system="sys", input="x",
        skill="prd-author", log=False,
    )
    assert res.output == "A\n---\nB"
    assert client.stream_calls


def test_call_md_stream_returns_accumulated_text(isolated_settings, patch_client):
    """The llm layer's stream path returns the assembled final-message text."""
    from app import llm

    client = patch_client(_RecordingClient(text="streamed body"))
    out = llm.call_md(system="s", user="u", stream=True, timeout=600.0)
    assert out == "streamed body"
    assert client.stream_calls[0]["timeout"] == 600.0
    assert not client.create_calls


def test_call_md_stream_retries_on_transient(isolated_settings, patch_client, monkeypatch):
    """The streamed path is wrapped by the same retry layer."""
    import anthropic
    from app import llm

    monkeypatch.setattr(llm, "_BACKOFF_BASE_S", 0.001)
    state = {"n": 0}
    outer = SimpleNamespace()

    class _Messages:
        def stream(self, **kw):
            state["n"] += 1
            if state["n"] < 2:
                raise anthropic.APIConnectionError(request=None)
            return _StreamCtx(_text_msg("recovered"))

    client = SimpleNamespace(messages=_Messages())
    monkeypatch.setattr(llm, "get_client", lambda: client)
    out = llm.call_md(system="s", user="u", stream=True)
    assert out == "recovered"
    assert state["n"] == 2


def test_prd_runner_completes_via_stream(isolated_settings, monkeypatch):
    """prd_runner._run_sync drives the gateway via the human PRD (prd-author)
    ONLY — it does not chain an implementation-spec call — and completion stores
    the human PRD via complete_prd."""
    import app.prd_runner as pr

    brief = {
        "id": 1, "dataset": "acme",
        "insights": [{"title": "Speed up onboarding"}],
    }
    monkeypatch.setattr(pr, "get_brief_by_id", lambda bid: brief)
    monkeypatch.setattr(pr, "_resolve_grounding",
                        lambda ds, b, i: ("EVIDENCE", None))
    # Part A is now an HTML page built from the skill's HTML template; stub the
    # loader so the test stays hermetic (no skill-file read).
    monkeypatch.setattr(pr, "_load_part_a_template", lambda: "<!DOCTYPE html><html></html>")
    monkeypatch.setattr(pr, "log_agent_decision", lambda **kw: None)

    skills_seen = []

    def fake_llm(**kw):
        skills_seen.append(kw.get("skill"))
        return SimpleNamespace(
            output="HUMAN PRD BODY", model="m",
            prompt_version=(kw["prompt_version"] + "+" + kw["skill"] + "@abc"),
        )

    completed = {}

    def fake_complete(**kw):
        completed.update(kw)

    monkeypatch.setattr(pr, "llm_call", fake_llm)
    monkeypatch.setattr(pr, "complete_prd", fake_complete)

    pr._run_sync(prd_id=7, brief_id=1, insight_index=0)

    # Exactly one call, the human PRD; the implementation-spec is on demand.
    assert skills_seen == ["prd-author"]
    assert completed["md"] == "HUMAN PRD BODY"


# =====================================================================
# BUG 2 — KpiTree north_star coercion
# =====================================================================

def test_kpi_tree_coerces_bare_string_north_star():
    from app.kpi_tree import KpiTree

    t = KpiTree.model_validate({"north_star": "Revenue"})
    assert t.north_star.metric == "Revenue"


def test_kpi_tree_none_north_star_defaults_no_raise():
    from app.kpi_tree import KpiTree

    t = KpiTree.model_validate({"north_star": None})
    assert t.north_star.metric  # non-empty safe default


def test_kpi_tree_garbage_north_star_defaults_no_raise():
    from app.kpi_tree import KpiTree

    for garbage in (123, ["a", "b"], {"metric": ""}, {"metric": "   "}, {}):
        t = KpiTree.model_validate({"north_star": garbage})
        assert t.north_star.metric  # always parses to a usable label


def test_kpi_tree_valid_object_north_star_ignores_legacy_fields():
    from app.kpi_tree import KpiTree

    # Legacy rows carry a numeric current_value on the north star; the model
    # now parses the object and IGNORES the old field (description defaults).
    t = KpiTree.model_validate(
        {"north_star": {"metric": "Weekly Active Users", "current_value": 10}}
    )
    assert t.north_star.metric == "Weekly Active Users"
    assert t.north_star.description == ""
    assert "current_value" not in t.north_star.model_dump()


def test_load_kpi_tree_legacy_string_degrades_gracefully(isolated_settings):
    """A company row whose stored kpi_tree.north_star is a bare string parses
    (coerced) instead of being dropped — goal-fit stays available."""
    from app.kpi_tree import load_kpi_tree

    sb = isolated_settings["supabase"]
    sb.table("companies").insert({
        "id": "co-legacy", "slug": "legacy", "display_name": "Legacy Co",
        "kpi_tree": {"north_star": "Revenue", "version": 2},
    }).execute()

    tree = load_kpi_tree("co-legacy")
    assert tree is not None
    assert tree.north_star.metric == "Revenue"


def test_classify_theme_fit_tolerant_of_legacy_tree():
    """The goal-fit classifier consumes a coerced legacy tree (renders, no raise)."""
    from app.kpi_tree import KpiTree
    from app.synthesis.scoring import classify_theme_fit

    tree = KpiTree.model_validate({"north_star": "Revenue"})
    # No tree → skip; with a tree it would render — render must not raise.
    text = tree.render_for_prompt()
    assert "Revenue" in text
    # kpi_tree=None short-circuits to neutral "high" without any LLM call.
    assert classify_theme_fit(None, "ent", object(), None) == "high"


# =====================================================================
# BUG 3 — warm_synthesis_drilldowns from a no-loop context
# =====================================================================

def test_warm_drilldowns_no_loop_does_not_raise_and_fans_out(monkeypatch):
    import app.brief_runner as br

    brief = {"id": 5, "insights": [{"title": "A"}, {"title": "B"}]}
    monkeypatch.setattr(br, "get_current_brief", lambda ds: brief)

    fanned = {"called": False, "brief": None, "dataset": None}

    def fake_warm(b, dataset=None):
        fanned["called"] = True
        fanned["brief"] = b
        fanned["dataset"] = dataset

    monkeypatch.setattr(br, "_warm_drilldowns", fake_warm)

    # Called with NO running event loop — must not raise.
    br.warm_synthesis_drilldowns("acme")

    assert fanned["called"] is True
    assert fanned["brief"] == brief
    assert fanned["dataset"] == "acme"


def test_warm_drilldowns_no_loop_drains_scheduled_tasks(monkeypatch):
    """In a no-loop context the fan-out's scheduled asyncio tasks run to
    completion (not abandoned) before the loop tears down."""
    import asyncio
    import app.brief_runner as br

    brief = {"id": 9, "insights": [{"title": "X"}]}
    monkeypatch.setattr(br, "get_current_brief", lambda ds: brief)

    ran = {"count": 0}

    async def _worker():
        ran["count"] += 1

    def fake_warm(b, dataset=None):
        # Schedule real tasks the way _warm_drilldowns does.
        asyncio.create_task(_worker())
        asyncio.create_task(_worker())

    monkeypatch.setattr(br, "_warm_drilldowns", fake_warm)

    br.warm_synthesis_drilldowns("acme")
    assert ran["count"] == 2


def test_warm_drilldowns_no_brief_is_noop(monkeypatch):
    import app.brief_runner as br

    monkeypatch.setattr(br, "get_current_brief", lambda ds: None)
    called = {"v": False}
    monkeypatch.setattr(br, "_warm_drilldowns",
                        lambda *a, **k: called.__setitem__("v", True))
    br.warm_synthesis_drilldowns("acme")
    assert called["v"] is False


def test_warm_drilldowns_with_running_loop_schedules(monkeypatch):
    """When a loop IS running, warming schedules on it (fire-and-forget),
    unchanged behavior."""
    import asyncio
    import app.brief_runner as br

    brief = {"id": 3, "insights": [{"title": "A"}]}
    monkeypatch.setattr(br, "get_current_brief", lambda ds: brief)
    scheduled = {"n": 0}

    def fake_warm(b, dataset=None):
        scheduled["n"] += 1

    monkeypatch.setattr(br, "_warm_drilldowns", fake_warm)

    async def _drive():
        # A running loop exists here.
        br.warm_synthesis_drilldowns("acme")

    asyncio.run(_drive())
    assert scheduled["n"] == 1


def test_warm_drilldowns_error_isolated(monkeypatch):
    """A failure inside warming is swallowed (best-effort)."""
    import app.brief_runner as br

    monkeypatch.setattr(br, "get_current_brief",
                        lambda ds: (_ for _ in ()).throw(RuntimeError("boom")))
    # Must not raise.
    br.warm_synthesis_drilldowns("acme")
