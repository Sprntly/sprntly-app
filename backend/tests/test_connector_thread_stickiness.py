"""A live-source thread must survive its own follow-up.

Reported 2026-08-03, three turns, reproduced verbatim below:

  1. "give me all information on confluence"     → the wiki listing (correct)
  2. 'tell me more about "ChoisBits Decision Making"'
                                                 → "no tracker is connected yet.
                                                    Connect Jira or ClickUp"
  3. "so check confluence"                       → the page (correct again)

Turn 2 is the bug. One of the pages listed in turn 1 is Atlassian's stock
product-requirements template, whose body says "Add Jira work item links or
other relevant project links here". _JIRA_THREAD_MARKER matches a bare "jira"
anywhere in a recent turn, so that quoted word made the thread a tracker
thread; `more` then matched _TRACKER_DETAIL, and is_jira_lookup — which runs
before every connector interception — claimed the follow-up.

Same class as the VoC-report bug _last_answer_was_report was written for, and
it slipped past that guard because a connector answer is markdown, not HTML.
"""
from __future__ import annotations

from app.skill_router import is_connector_lookup, is_jira_lookup

# The turn-1 answer, trimmed but keeping the two things that matter: it is a
# Confluence answer, and it quotes a template that names Jira.
_WIKI_LISTING = """Here is a full overview of everything in your Confluence workspace:

## Workspace Overview
- 1 Space: Software Development (key: SD)
- 8 Pages/Posts in total (7 pages + 1 blog post)

## Pages
1. ChoisBits Decision Making — Last Updated: 2026-08-03 | Version: 2
   Content: Describes what a "Bit" is on the ChoisBits platform.
2. Product Requirements — Last Updated: 2026-08-03 | Version: 1
   Content: Objective, Success metrics, Milestones, Quick links —
   "Work tracker: Add Jira work item links or other relevant project links here"
"""

_HISTORY = [
    {"role": "user", "content": "give me all information on confluence"},
    {"role": "assistant", "content": _WIKI_LISTING},
]

_FOLLOWUP = 'tell me more about "ChoisBits Decision Making"'


def test_the_followup_is_not_stolen_by_the_tracker():
    assert is_jira_lookup(_FOLLOWUP, _HISTORY) is False


def test_the_followup_stays_on_the_confluence_thread():
    assert is_connector_lookup(_FOLLOWUP, _HISTORY) == {"confluence"}


def test_a_quoted_tracker_word_alone_never_opens_a_tracker_thread():
    """The precise mechanism: a wiki answer quoting a template that says
    "Jira" is a document, not a tracker thread."""
    for followup in [
        "tell me more about that page",
        "what else does it say?",
        "give me the details",
        "expand on the second one",
    ]:
        assert is_jira_lookup(followup, _HISTORY) is False, followup


def test_a_real_tracker_thread_still_goes_sticky():
    """The guard must not cost the behaviour it is narrowing. An issue key is
    vocabulary a quoted wiki page does not produce."""
    history = [
        {"role": "user", "content": "get me the open tickets"},
        {"role": "assistant", "content": "KAN-1038 Checkout fails on retry — In Progress"},
    ]
    assert is_jira_lookup("tell me more about it", history) is True


def test_a_tracker_answer_mentioning_another_source_still_goes_sticky():
    """A tracker answer that happens to name Slack is still a tracker answer —
    the issue key carries it."""
    history = [
        {"role": "user", "content": "get me the open tickets"},
        {"role": "assistant", "content": (
            "KAN-1038 Checkout fails on retry. Discussed in slack #product-eng."
        )},
    ]
    assert is_jira_lookup("who is it assigned to?", history) is True


def test_the_trackers_own_clarifying_question_still_goes_sticky():
    """"Which Jira project?" names no OTHER source, so the bare-word marker
    still applies to it — this is the case the narrowing must not break.

    The user turn is deliberately not a tracker lookup of its own, so the
    assistant's bare "jira" is the only thing that can carry the thread.
    """
    history = [
        {"role": "user", "content": "can you help me with the checkout work?"},
        {"role": "assistant", "content": "Which Jira project should I look in?"},
    ]
    assert is_jira_lookup("give me the details", history) is True


def test_a_confluence_thread_about_a_genuine_ticket_still_reaches_the_tracker():
    """Naming a tracker in the FOLLOW-UP is a pivot back to it, and the
    stateless trigger owns that regardless of what the thread was about."""
    assert is_jira_lookup("get me the open tickets in jira", _HISTORY) is True
