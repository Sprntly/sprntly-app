"""The screen map stays true to the app, or CI goes red.

`app/app_map.py` is the only thing standing between "where are my PRDs" and
an invented answer, and a map is worth exactly as much as its accuracy. The
failure it would drift into is silent: someone ships a new screen, nobody
adds it here, and six months later the chat confidently tells a customer that
screen does not exist.

So the map is checked against the WEB APP'S OWN registries rather than
against a copy of them — the command palette's page list
(`web/app/lib/search/registry.ts`) and the settings nav
(`.../settings/SettingsLayout.tsx`), which are the two places a new
destination is already required to be declared for it to be reachable at all.
Reading them as text (rather than parsing TypeScript) is deliberate: the
regexes match the literal shape those files have used since they were
written, and a shape change that outruns them fails loudly here, which is the
point.

This mirrors `web/app/lib/__tests__/pipeline-contract.test.ts`, which reads
backend templates from the web suite for the same reason in the other
direction.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import app_map

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "web" / "app" / "lib" / "search" / "registry.ts"
SETTINGS_LAYOUT = (
    REPO / "web" / "app" / "components" / "screens" / "app" / "settings"
    / "SettingsLayout.tsx"
)


def _mapped_paths() -> set[str]:
    """Every path the map hands out, screens and settings panes alike."""
    return {path for _, path, _ in app_map.NAV} | {
        app_map.settings_path(sid) for _, sid, _ in app_map.SETTINGS
    }


def test_every_palette_page_is_on_the_map():
    """A page the command palette can reach must be a page the chat can name."""
    if not REGISTRY.exists():  # pragma: no cover — web/ absent (backend-only checkout)
        pytest.skip(f"{REGISTRY} not present")
    urls = set(re.findall(r'url:\s*"(/[^"]*)"', REGISTRY.read_text(encoding="utf-8")))
    # The settings panes come from SETTINGS_NAV at runtime, not from literals
    # in this file, so the only `/settings?...` url here would be the bare one.
    pages = {u for u in urls if "?" not in u}
    assert pages, "registry.ts parsed to zero pages — the file's shape changed"
    missing = sorted(pages - _mapped_paths())
    assert not missing, (
        "these screens exist in the app but not in app_map.NAV: "
        f"{missing}. Add each one (label, path, what you do there) so the chat "
        "can point a customer at it instead of inventing a screen."
    )


def test_every_settings_pane_is_on_the_map():
    """Same rule for the `?section=` panes — the deep link is the answer."""
    if not SETTINGS_LAYOUT.exists():  # pragma: no cover
        pytest.skip(f"{SETTINGS_LAYOUT} not present")
    src = SETTINGS_LAYOUT.read_text(encoding="utf-8")
    # A row carrying an `href` is a door OUT of Settings, not a pane: today
    # that is Guide, which opens the public docs site. It has no `?section=`
    # to link to, so it is named in NAV (with where it now lives) rather than
    # in SETTINGS, and demanding a pane entry for it would be demanding a link
    # that lands on Profile.
    ids = {
        sid
        for sid, tail in re.findall(
            r'\{\s*id:\s*"([\w-]+)"\s*,\s*label:\s*"[^"]*"\s*,\s*available:\s*true([^}]*)\}',
            src,
        )
        if "href:" not in tail
    }
    assert ids, "SettingsLayout.tsx parsed to zero panes — the file's shape changed"
    mapped = {sid for _, sid, _ in app_map.SETTINGS}
    missing = sorted(ids - mapped)
    assert not missing, (
        "these Settings panes are in the app's nav but not in app_map.SETTINGS: "
        f"{missing}. /settings alone lands on Profile, so a pane with no entry "
        "cannot be linked to."
    )


def test_map_paths_are_unique_and_absolute():
    paths = [p for _, p, _ in app_map.NAV]
    assert len(paths) == len(set(paths)), "duplicate path in app_map.NAV"
    assert all(p.startswith("/") for p in paths), "a map path must be in-app"
    sids = [s for _, s, _ in app_map.SETTINGS]
    assert len(sids) == len(set(sids)), "duplicate section id in app_map.SETTINGS"


def test_addendum_carries_every_path_and_reaches_the_answer():
    """The map is only grounding if it is actually in the prompt."""
    from app.prompts import ASK_SYSTEM

    for path in _mapped_paths():
        assert path in app_map.NAV_ADDENDUM, f"{path} missing from the addendum"
    for form, _ in app_map.DEEP_LINKS:
        assert form in app_map.NAV_ADDENDUM, f"{form} missing from the addendum"
    assert app_map.NAV_ADDENDUM in ASK_SYSTEM
