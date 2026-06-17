"""Voice guard: user-facing generation must never expose Sprntly's internal
architecture vocabulary ("corpus", "knowledge graph", "dataset", …) to the PM.

The reader is an end user, not a Sprntly engineer; leaking our plumbing terms
is off-brand and confusing. Two layers are tested here:

1. Every user-facing SYSTEM prompt ends with VOICE_GUARD (the explicit
   never-say-these-words rule), and VOICE_GUARD actually names each banned term.
2. The two literal section headers the model reads — the Ask "connected
   sources" block and the Ask cacheable prefix — carry no jargon to echo.
"""
import re

from app import prompts
from app.graph.retrieval import render_context_section
from app.prd_runner import _SYSTEM as PRD_RUNNER_SYSTEM
from app.synthesis.agent import _SYSTEM as SYNTHESIS_SYSTEM


# Every system prompt whose model OUTPUT is read by the PM. Includes the two
# inline runners (the multi-agent PRD author + the brief synthesis agent), not
# just the centralized prompts.py constants.
USER_FACING_SYSTEM_PROMPTS = {
    "BRIEF_SYSTEM": prompts.BRIEF_SYSTEM,
    "ASK_SYSTEM": prompts.ASK_SYSTEM,
    "PRD_SYSTEM": prompts.PRD_SYSTEM,
    "EVIDENCE_SYSTEM": prompts.EVIDENCE_SYSTEM,
    "EVIDENCE_KG_SYSTEM": prompts.EVIDENCE_KG_SYSTEM,
    "prd_runner._SYSTEM": PRD_RUNNER_SYSTEM,
    "synthesis.agent._SYSTEM": SYNTHESIS_SYSTEM,
}


def test_voice_guard_appended_to_every_user_facing_system_prompt():
    for name, prompt in USER_FACING_SYSTEM_PROMPTS.items():
        assert prompts.VOICE_GUARD in prompt, (
            f"{name} must end with VOICE_GUARD so the model never echoes "
            f"internal vocabulary into user-facing output"
        )


def test_voice_guard_bans_every_term_on_the_deny_list():
    """VOICE_GUARD is the contract; INTERNAL_JARGON is the deny-list. The guard
    must explicitly name each banned term, or the deny-list is just decoration."""
    guard = prompts.VOICE_GUARD.lower()
    for term in prompts.INTERNAL_JARGON:
        # "knowledge-graph" is covered by the guard's "knowledge graph" / "KG".
        needle = "knowledge graph" if term == "knowledge-graph" else term
        assert needle.lower() in guard, f"VOICE_GUARD must name banned term {term!r}"


def test_ask_kg_section_header_has_no_internal_jargon():
    """The Ask connected-sources block is injected verbatim into the prompt and
    is the single most echo-prone string — it must not say "knowledge graph"."""
    bundle = {
        "themes": [{"label": "Checkout drop-off", "score": 0.9}],
        "signals": [
            {
                "source_type": "revenue",
                "kind": "deal_blocker",
                "theme": "Checkout drop-off",
                "content": "ARR at risk",
                "provenance": {"source": "HubSpot"},
            }
        ],
    }
    text = render_context_section(bundle)
    lowered = text.lower()
    assert "knowledge graph" not in lowered
    assert "corpus" not in lowered
    # The replacement header is what we DO want the model to see / echo.
    assert "CONNECTED SOURCES" in text


def test_ask_addendum_does_not_invite_the_leak():
    """The KG addendum used to literally say 'when the corpus and the knowledge
    graph agree, say so' — a direct instruction to leak. It must be neutral."""
    addendum = prompts.ASK_SYSTEM_KG_ADDENDUM.lower()
    assert "corpus" not in addendum
    assert "knowledge graph" not in addendum


def test_ask_user_templates_use_plain_source_header():
    """The literal 'Corpus:' header the model reads is renamed to 'Source
    material:' so a quoted-back header can't expose the term. The `{corpus}`
    format placeholder is fine — it's substituted with real content, never
    shown — so we check the visible prose, not the variable name."""
    for name in ("ASK_USER_TEMPLATE", "ASK_USER_TEMPLATE_QUESTION_ONLY", "ASK_USER_TEMPLATE_WITH_KG"):
        tmpl = getattr(prompts, name)
        visible = re.sub(r"\{+corpus\}+", "", tmpl)  # drop the {corpus} placeholder
        assert not re.search(r"\bcorpus\b", visible, re.IGNORECASE), f"{name} exposes 'corpus' to the reader"
