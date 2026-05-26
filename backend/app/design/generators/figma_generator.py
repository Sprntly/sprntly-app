"""Scenario A — Figma-connected prototype generation.

Spec source: Design_Agent_Spec.docx §3.A (Figma-connected scenario),
§6 (design-token extraction).

This module produces the JSON skeleton that the (forthcoming) Next.js
codegen pipeline consumes. Today it extracts top-level pages + frames
from a Figma file via `app.connectors.figma_oauth.fetch_file`. Real
Next.js route mapping + component generation is Jide's follow-up.

The function takes an `access_token_provider` callable rather than the
Figma access token directly so the lifecycle layer can defer the token
decrypt + refresh dance until the generator actually needs it (and so
tests can swap in a constant string without mocking the connector
storage).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def generate_from_figma(
    file_key: str,
    prd_content: dict[str, Any],
    access_token_provider: Optional[Callable[[], str]] = None,
    fetch_file: Optional[Callable[[str, str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build the prototype skeleton from a Figma file.

    Args:
        file_key: Figma file key (the part after /file/ in the URL).
        prd_content: The parent PRD payload — used as steering context
            by the real generator. Today we just stash a summary in meta.
        access_token_provider: Callable returning a Figma OAuth access
            token. Resolved lazily so callers can defer the decrypt.
            Pass None to skip the live Figma call (returns a placeholder
            skeleton); the lifecycle layer does this when running in
            test mode without a Figma connection.
        fetch_file: Override the Figma client (defaults to
            `app.connectors.figma_oauth.fetch_file`). Test seam.

    Returns:
        A JSON skeleton with pages/components/style/meta keys. Always
        well-formed even when the Figma API call fails — we log and fall
        back to a minimal skeleton so the prototype can still progress
        to ITERATING and accumulate comments.
    """
    skeleton: dict[str, Any] = {
        "pages": [],
        "components": [],
        "style": {"colors": [], "fonts": []},
        "meta": {
            "scenario": "figma",
            "source": f"figma:{file_key}",
            "prd_summary": _prd_summary(prd_content),
            "generator_version": "poc-0.1",
        },
    }

    if access_token_provider is None:
        # POC mode: lifecycle didn't supply a token (Figma not connected
        # in a test env, or scenario validation only). Return a labelled
        # placeholder so downstream code keeps working.
        skeleton["meta"]["placeholder"] = True
        skeleton["pages"] = [
            {"id": "page-stub", "name": "Stub Page", "frames": []},
        ]
        return skeleton

    if fetch_file is None:
        # Lazy-import to keep the design package independent of the
        # connectors layer at import time (otherwise test bootstrap
        # gets noisy when Figma env vars aren't set).
        from app.connectors.figma_oauth import fetch_file as _ff
        fetch_file = _ff

    try:
        access_token = access_token_provider()
        figma_payload = fetch_file(access_token, file_key)
    except Exception:
        # Generator failure must not poison the prototype row. Log,
        # mark the skeleton as degraded, return.
        logger.exception("Figma file fetch failed for key=%s", file_key)
        skeleton["meta"]["degraded"] = True
        skeleton["meta"]["error"] = "figma_fetch_failed"
        return skeleton

    skeleton["pages"] = _extract_pages(figma_payload)
    skeleton["meta"]["figma_file_name"] = figma_payload.get("name")
    skeleton["meta"]["figma_last_modified"] = figma_payload.get("lastModified")
    return skeleton


def _extract_pages(figma_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull top-level pages + their direct child frame names.

    The full Figma node tree is deep; the spec only needs page → frame
    structure at this layer (the real codegen mines deeper). Anything
    unexpected → empty list, never crash.
    """
    document = figma_payload.get("document") or {}
    children = document.get("children") or []
    pages: list[dict[str, Any]] = []
    for page in children:
        if not isinstance(page, dict):
            continue
        if page.get("type") != "CANVAS":
            # Figma calls top-level pages CANVAS. Anything else is unexpected.
            continue
        frames = []
        for frame in page.get("children", []) or []:
            if not isinstance(frame, dict):
                continue
            # Top-level frames are FRAME, COMPONENT, COMPONENT_SET.
            if frame.get("type") not in ("FRAME", "COMPONENT", "COMPONENT_SET"):
                continue
            frames.append({
                "id": frame.get("id"),
                "name": frame.get("name"),
                "type": frame.get("type"),
            })
        pages.append({
            "id": page.get("id"),
            "name": page.get("name"),
            "frames": frames,
        })
    return pages


def _prd_summary(prd_content: dict[str, Any]) -> str:
    """A short string the meta block can carry — title + first 200 chars
    of the PRD body. Never raise on unexpected shape."""
    if not isinstance(prd_content, dict):
        return ""
    title = str(prd_content.get("title") or "")
    body = str(prd_content.get("payload_md") or prd_content.get("body") or "")
    return f"{title}: {body[:200]}".strip(": ").strip()


__all__ = ["generate_from_figma"]
