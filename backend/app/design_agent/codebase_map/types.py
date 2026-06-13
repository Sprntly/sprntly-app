"""Source-agnostic data shapes for the screen graph and shell of a connected repo.

This module defines the intermediate shape that sits between the per-source
MAP phase and any downstream consumer that reasons about a repo's navigation
structure. Each MAP source (filesystem route reader, typed registry probe)
reduces its raw observations into a single ``MapResult``: a bag of screen
nodes, navigation edges, unresolved edges, and the app shell.

``MapResult`` decides nothing. It carries structure plus provenance only;
downstream consumers (locate, design-kit recreation) are the sole resolvers.
This module is pure data — no fetching, no AST/static analysis, no model
calls, no I/O. Every field carries a deterministic default so a bare instance
is a valid, honestly-empty baseline.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Posture = Literal["CLEAN", "PARTIAL"]
# CLEAN  = a typed screen registry or route-table was found; node completeness
#          is certifiable and the unresolved edge tail is type-bounded.
# PARTIAL = filesystem-route fallback only; route skeleton and literal edges
#          harvested, but node completeness is NOT certifiable and the
#          unresolved edge tail is unbounded.

EdgeKind = Literal["literal", "path_builder", "registry", "dynamic", "external"]
# literal      = navigate("/team")            — target fully resolved
# path_builder = navigate(`/team/${id}`)       — target resolved to a route template
# registry     = goTo(ScreenId.Team)           — resolved via the detected registry
# dynamic      = goTo(variable) / href={prop}  — target NOT statically resolvable
# external     = href to an off-app URL        — intentionally not a screen edge

LogoRenderKind = Literal["img_src", "inline_svg", "imported_asset", "text", "absent"]
# how the shell renders its brand mark:
# img_src        = <img src="/logo.svg">        — carry: copy the referenced file
# inline_svg     = <svg>…</svg> in the shell    — carry: reproduce the markup
# imported_asset = import logo from "./logo.svg" — carry: copy file + keep the import
# text           = a text wordmark / letter badge (no asset)
# absent         = no brand mark detected


class ScreenNode(BaseModel):
    """One screen in the customer app's screen graph."""

    route: str = ""
    # url path the screen mounts at ("/team", "/settings/members"),
    # or a synthetic id for a query-param route-state node
    entry_component: str = ""
    # the component name that renders the screen (e.g. "TeamScreen")
    file: str = ""
    # repo-relative path of the entry component
    composed_components: list[str] = Field(default_factory=list)
    # direct child component names the entry component renders
    is_route_state: bool = False
    # True when the node is a query-param route-state of another route
    # (e.g. "/inbox?view=archived"), not a distinct path
    kind: Literal["route", "section", "shell"] = "route"
    # discriminates a routed screen ("route") from an in-page section
    # ("section") or the app shell ("shell"); today only "route" is emitted
    id: str = ""
    # stable per-node key. Defaults to the route when left empty (see the
    # validator below) — for a routed screen the route IS the stable key.
    # Section/shell nodes pass an explicit non-route id, which is preserved.

    @model_validator(mode="after")
    def _default_id_to_route(self) -> "ScreenNode":
        """Fall the stable id back to the route when no explicit id was given."""
        if not self.id:
            self.id = self.route
        return self


class NavEdge(BaseModel):
    """A statically-resolved navigation transition between two screens."""

    from_route: str = ""
    # source screen route (empty when the call-site is shell-global)
    to_route: str = ""
    # resolved destination route ("" when unresolved — see UnresolvedEdge)
    kind: EdgeKind = "literal"
    resolved: bool = True
    # False means a matching UnresolvedEdge is also emitted on MapResult
    call_site: str = ""
    # "file:line" identifier of the navigation call


class UnresolvedEdge(BaseModel):
    """A navigation call-site whose destination could not be statically resolved.

    The bounded worklist a human (or PM) labels if completeness matters.
    """

    from_route: str = ""
    call_site: str = ""
    # "file:line"
    reason: str = ""
    # plain-English description: "dynamic target", "prop-href indirection", etc.


class LogoAsset(BaseModel):
    """How the shell renders its brand mark — the single biggest recognizability lever."""

    render_kind: LogoRenderKind = "absent"
    asset_ref: str = ""
    # for img_src: the src path; imported_asset: the import source path;
    # inline_svg: "" (markup carried separately at recreate time);
    # text: the wordmark/letter text
    alt_text: str = ""
    # alt / aria-label when present


class NavItem(BaseModel):
    """One entry in the shell's primary navigation."""

    label: str = ""
    order: int = 0
    # 0-based position in the rendered nav
    icon: str = ""
    # icon component / name when statically detectable ("" = none)
    route: str = ""
    # destination route when the nav item links to a known screen


class ShellModel(BaseModel):
    """The app shell (sidebar / topbar) — recreated in full per the locked shell-scope decision."""

    brand: str = ""
    # the brand / product name text rendered in the shell
    nav_items: list[NavItem] = Field(default_factory=list)
    collapse_model: str = ""
    # "" (none) | "collapsible" | "static" — how the shell collapses
    logo: LogoAsset = Field(default_factory=LogoAsset)


class MapResult(BaseModel):
    """The complete deterministic map of a connected repo at one commit."""

    repo: str = ""
    # "org/repo"
    commit_sha: str = ""
    # the resolved commit the map was built against
    posture: Posture = "PARTIAL"
    # honest default: PARTIAL until a registry proves CLEAN
    nodes: list[ScreenNode] = Field(default_factory=list)
    edges: list[NavEdge] = Field(default_factory=list)
    shell: ShellModel = Field(default_factory=ShellModel)
    unresolved: list[UnresolvedEdge] = Field(default_factory=list)
