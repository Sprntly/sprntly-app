"""Tests for the global error envelope in app.main.

Covers the Workstreet black-box assessment's WS-06: GET
/v1/design-agent/by-token/token/bundle/asset_path returned a bare
`text/plain` 500 because `prototypes.share_token` is a `uuid` column and a
non-UUID probe value made PostgREST raise before the route's own (careful,
404-for-everything) deny logic ever ran.

The load-bearing assertion is `test_malformed_identifier_is_404_not_500`. A
malformed identifier and an unknown one MUST be indistinguishable: if 22P02
returned a 500 while an unknown-but-well-formed token returned a 404, the
status code itself would tell an attacker when they had guessed the token
FORMAT right, which is the enumeration oracle the by-token route is written to
deny.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from postgrest.exceptions import APIError
from starlette.testclient import TestClient

from app.main import (
    _is_invalid_identifier,
    postgrest_error_handler,
    unhandled_error_handler,
)


class _PostgrestError(APIError):
    """A real APIError subclass built field-by-field.

    Subclassing (not mirroring) is what makes Starlette route it to the
    registered `APIError` handler, which is the wiring under test. Bypassing
    `APIError.__init__` keeps the test independent of that constructor's dict
    shape, which is a pinned-library detail and not what we are asserting.
    """

    def __init__(self, code: str | None, message: str):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        # APIError's own __str__/__repr__ read these, and _is_invalid_identifier
        # falls back to str(exc) when message is empty. A stand-in missing them
        # would blow up inside the handler rather than in the code under test.
        self.hint = None
        self.details = None


def _build() -> TestClient:
    """A minimal app carrying the same two handlers app.main registers."""
    app = FastAPI()
    app.add_exception_handler(APIError, postgrest_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/bad-uuid")
    def _bad_uuid():
        raise _PostgrestError("22P02", 'invalid input syntax for type uuid: "token"')

    @app.get("/untyped")
    def _untyped():
        raise _PostgrestError(None, 'invalid input syntax for type uuid: "token"')

    @app.get("/duplicate")
    def _duplicate():
        raise _PostgrestError("23505", "duplicate key value violates unique constraint")

    @app.get("/boom")
    def _boom():
        raise ValueError("something internal broke")

    # raise_server_exceptions=False: Starlette's ServerErrorMiddleware re-raises
    # after sending the response so Sentry's ASGI layer still sees it, which
    # would otherwise surface here instead of the response we want to assert.
    return TestClient(app, raise_server_exceptions=False)


def test_malformed_identifier_is_404_not_500():
    """WS-06. The probe value that produced the bare 500 now reads as absent."""
    r = _build().get("/bad-uuid")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}


def test_malformed_identifier_is_recognised_without_a_typed_code():
    """The same failure arriving as a message only still resolves to 404."""
    assert _build().get("/untyped").status_code == 404


def test_other_postgrest_errors_stay_500():
    """22P02 is special-cased; a real DB fault must not be laundered into a 404
    that hides a broken write from monitoring."""
    r = _build().get("/duplicate")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal Server Error"}


def test_unhandled_exception_returns_structured_json_not_bare_text():
    """The other half of WS-06: no response body may be a bare error string."""
    r = _build().get("/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Internal Server Error"}


def test_internal_detail_never_reaches_the_client():
    """The exception message goes to the log, never into the response."""
    assert "something internal broke" not in _build().get("/boom").text


@pytest.mark.parametrize(
    "code,message,expected",
    [
        ("22P02", "invalid input syntax for type uuid", True),
        (None, "22P02", True),
        ("23505", "duplicate key value", False),
        ("57014", "canceling statement due to statement timeout", False),
    ],
)
def test_is_invalid_identifier(code, message, expected):
    assert _is_invalid_identifier(_PostgrestError(code, message)) is expected


def test_handlers_are_registered_on_the_real_app():
    """The unit tests above run against a stand-in app, so this is what catches
    the handlers being dropped from app.main itself."""
    import app.main as main_mod

    assert main_mod.app.exception_handlers[APIError] is postgrest_error_handler
    assert main_mod.app.exception_handlers[Exception] is unhandled_error_handler
