"""Regression: structured LLM output nested under a lone {"response": {...}}.

Some models (observed on the non-streamed Opus path) wrap the ENTIRE structured
object under a single "response" key — cued by the submit_response tool name —
even though the tool's input_schema is flat. call_json now unwraps that envelope
so callers read their real fields. This silently emptied every regenerated
weekly brief (insights read off the top level → []).
"""
from __future__ import annotations

from app.llm import _unwrap_response_envelope

_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"summary_headline": {"type": "string"}, "insights": {"type": "array"}},
    "required": ["summary_headline", "insights"],
}


def test_unwraps_lone_response_envelope():
    wrapped = {"response": {"summary_headline": "hi", "insights": [{"title": "x"}]}}
    out = _unwrap_response_envelope(wrapped, _BRIEF_SCHEMA)
    assert out == {"summary_headline": "hi", "insights": [{"title": "x"}]}
    assert out["insights"] == [{"title": "x"}]


def test_passes_through_flat_object_unchanged():
    flat = {"summary_headline": "hi", "insights": [{"title": "x"}]}
    assert _unwrap_response_envelope(flat, _BRIEF_SCHEMA) is flat


def test_does_not_unwrap_when_schema_declares_response():
    schema = {"type": "object", "properties": {"response": {"type": "object"}},
              "required": ["response"]}
    payload = {"response": {"answer": "42"}}
    # "response" is a legitimate top-level field here — leave it alone.
    assert _unwrap_response_envelope(payload, schema) is payload


def test_does_not_unwrap_non_dict_inner():
    payload = {"response": "just a string"}
    assert _unwrap_response_envelope(payload, _BRIEF_SCHEMA) is payload


def test_does_not_unwrap_multi_key_object():
    payload = {"response": {"a": 1}, "other": 2}
    assert _unwrap_response_envelope(payload, _BRIEF_SCHEMA) is payload


def test_tolerates_none_schema():
    wrapped = {"response": {"x": 1}}
    assert _unwrap_response_envelope(wrapped, None) == {"x": 1}
