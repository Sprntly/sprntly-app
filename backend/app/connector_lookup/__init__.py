"""Ad-hoc connector lookups — read a connected tool LIVE to answer a chat
question, instead of answering from the periodic KG snapshot.

Layout:
    base.py      the adapter contract (LookupProvider) + the enforced caps
    answer.py    the shared answer loop (sessions → tools → bounded loop → payload)
    registry.py  provider hint → adapter, plus honest copy for what we can't read
    tracker.py   the tracker fast-path (picks the tenant's connected tracker)
    jira.py, clickup.py, slack.py, …   one adapter per connector

Deliberately no `answer` symbol re-exported here: `app.connector_lookup.answer`
is the MODULE, and callers do `from app.connector_lookup import answer as
connector_answer` (or import the submodule they need). Binding the function to
that name here would shadow the module.
"""
from __future__ import annotations

from app.connector_lookup.base import (
    DEFAULT_RESULT_CHARS,
    HTTP_TIMEOUT,
    LookupProvider,
    LookupSession,
    cap_items,
    cap_text,
)

__all__ = [
    "DEFAULT_RESULT_CHARS",
    "HTTP_TIMEOUT",
    "LookupProvider",
    "LookupSession",
    "cap_items",
    "cap_text",
]
