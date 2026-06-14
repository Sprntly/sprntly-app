"""Tests for GithubExtractor.extract_ui_primitives — codebase UI primitive seeding.

Pure unit tests — mocks the GitHub API layer so no network or installation token
is required.  Exercises: empty-when-no-installation, primitive-vs-bespoke
filtering, oversized-file skip, file-count cap, and strict-dirs-only behaviour.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.design_agent.design_system.adapters import GithubExtractor, _GITHUB_MAX_UI_FILES


def _make_listing(*names: str, directory: str = "src/components/ui") -> list[dict]:
    return [
        {"type": "file", "path": f"{directory}/{n}", "name": n}
        for n in names
    ]


def test_extract_ui_primitives_installation_none_returns_empty():
    extractor = GithubExtractor(installation_id=None)
    assert extractor.extract_ui_primitives("owner/repo") == {}


def test_extract_ui_primitives_mixed_listing_filters_to_primitives():
    extractor = GithubExtractor(installation_id=42)
    # 3 primitive + 2 bespoke — only the 3 primitives should be returned
    listing = _make_listing(
        "button.tsx", "card.tsx", "input.tsx", "dashboard-chart.tsx", "sidebar.tsx"
    )

    def fake_get_contents(repo, path, branch):
        if path == "src/components/ui":
            return listing
        return None

    extractor._github_get_contents = fake_get_contents
    extractor._fetch_text_file = MagicMock(return_value="content")

    result = extractor.extract_ui_primitives("owner/repo")
    assert set(result.keys()) == {
        "src/components/ui/button.tsx",
        "src/components/ui/card.tsx",
        "src/components/ui/input.tsx",
    }
    assert all(v == "content" for v in result.values())


def test_extract_ui_primitives_oversized_file_skipped():
    extractor = GithubExtractor(installation_id=42)
    listing = _make_listing("button.tsx", directory="components/ui")
    extractor._github_get_contents = MagicMock(return_value=listing)
    extractor._fetch_text_file = MagicMock(return_value=None)  # oversized / failed

    result = extractor.extract_ui_primitives("owner/repo")
    assert result == {}


def test_extract_ui_primitives_cap_at_max_files():
    extractor = GithubExtractor(installation_id=42)
    # More primitive files than the cap
    all_hints = [
        "accordion.tsx", "alert.tsx", "avatar.tsx", "badge.tsx", "button.tsx",
        "card.tsx", "checkbox.tsx", "dialog.tsx", "drawer.tsx", "dropdown.tsx",
        "form.tsx", "input.tsx", "menu.tsx", "modal.tsx", "popover.tsx",
    ]
    assert len(all_hints) > _GITHUB_MAX_UI_FILES
    listing = _make_listing(*all_hints, directory="src/components/ui")
    extractor._github_get_contents = MagicMock(return_value=listing)
    extractor._fetch_text_file = MagicMock(return_value="tsx content")

    result = extractor.extract_ui_primitives("owner/repo")
    assert len(result) == _GITHUB_MAX_UI_FILES


def test_extract_ui_primitives_only_strict_ui_dirs():
    extractor = GithubExtractor(installation_id=42)
    listing = _make_listing("button.tsx", directory="components")

    def fake_get_contents(repo, path, branch):
        # Only return a listing for the broader dir, not the strict ones
        if path == "components":
            return listing
        return None

    extractor._github_get_contents = fake_get_contents
    extractor._fetch_text_file = MagicMock(return_value="tsx content")

    result = extractor.extract_ui_primitives("owner/repo")
    assert result == {}


def test_extract_ui_primitives_resolves_under_monorepo_web_prefix():
    """Monorepo regression: primitives under web/app/components/ui must resolve.

    Before frontend-prefix detection was applied to extract_ui_primitives, the
    strict-UI-dir probe was root-relative, so a monorepo whose frontend lives
    under web/ had every probe 404 and the method returned {}. With the fix,
    _detect_frontend_prefix picks 'web/', the listing is probed at
    web/app/components/ui, and the bodies are fetched at their (prefix-inclusive)
    API paths — yielding the extracted primitives keyed canonically.
    """
    extractor = GithubExtractor(installation_id=42)
    # GitHub returns full repo paths, so listing items carry the web/ prefix.
    listing = _make_listing(
        "button.tsx", "card.tsx", directory="web/app/components/ui"
    )

    def fake_get_contents(repo, path, branch):
        # Prefix detection: only web/package.json exists (root has none).
        if path == "web/package.json":
            return {"type": "file", "name": "package.json", "path": "web/package.json"}
        # Strict-UI-dir listing only resolves under the detected web/ prefix.
        if path == "web/app/components/ui":
            return listing
        return None

    extractor._github_get_contents = fake_get_contents
    extractor._fetch_text_file = MagicMock(return_value="content")

    result = extractor.extract_ui_primitives("owner/repo")
    assert set(result.keys()) == {
        "src/components/ui/button.tsx",
        "src/components/ui/card.tsx",
    }
    assert all(v == "content" for v in result.values())
