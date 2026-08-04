"""Tests for the Marvin puller's capability resolution and distillation.

Marvin publishes no schema for its MCP tools, so the puller discovers them at
sync time by scoring `tools/list` output and reading each tool's own
`inputSchema` for argument names. That heuristic is the single most
failure-prone part of this connector, so these tests exercise it against
several plausible naming conventions — including ones Marvin does not use
today — to prove a vendor rename doesn't break the pull.

The other contract under test is data minimization: Marvin holds full
interview transcripts, and the puller must ingest only the analysis layer.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.mcp_client import McpError
from app.kg_ingest.pullers import marvin
from app.kg_ingest.pullers.marvin import ToolSpec, resolve_capabilities


# ─────────────────────── capability resolution ───────────────────────


def _tool(name: str, description: str = "", properties: dict | None = None,
          required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


def test_resolves_marvins_documented_capabilities():
    tools = [
        _tool("search_customer_knowledge", "Search interviews, surveys, studies"),
        _tool("list_projects", "List all projects"),
        _tool("list_files", "List files in a project"),
        _tool("get_file_content", "Show file content and summary"),
        _tool("ask_ai", "Run an Ask AI query"),
    ]
    caps = resolve_capabilities(tools)
    assert caps["search"].name == "search_customer_knowledge"
    assert caps["list_projects"].name == "list_projects"
    assert caps["list_files"].name == "list_files"
    assert caps["get_file"].name == "get_file_content"
    # Ask AI is deliberately NOT a pull capability — it is nondeterministic and
    # bills the customer's Marvin account.
    assert "ask" not in caps


@pytest.mark.parametrize(
    "names,expected",
    [
        # Verb-first naming
        (["projects_list", "files_list", "file_get"], "files_list"),
        # Marvin-ish prose naming
        (["browse_all_projects", "browse_all_files", "read_file"], "browse_all_files"),
        # Terse naming
        (["projects", "files_list", "file_summary"], "files_list"),
    ],
)
def test_resolution_survives_plausible_renames(names, expected):
    caps = resolve_capabilities([_tool(n) for n in names])
    assert caps["list_files"].name == expected


def test_each_capability_keeps_only_its_first_claimant():
    """Several search variants must not produce several pulls."""
    caps = resolve_capabilities([
        _tool("search_notes"), _tool("search_files"), _tool("search_projects"),
    ])
    assert caps["search"].name == "search_notes"


def test_a_tool_claims_at_most_one_capability():
    """`get_file` is checked before `list_files`, so a detail tool must not
    also register as the lister."""
    caps = resolve_capabilities([_tool("get_file_content"), _tool("list_files")])
    assert caps["get_file"].name == "get_file_content"
    assert caps["list_files"].name == "list_files"


def test_a_prose_description_cannot_steal_a_capability_from_a_name():
    """"List files… returns file details" must not claim the DETAIL slot —
    the puller would then fetch each file with a tool that returns a page."""
    caps = resolve_capabilities([
        _tool("list_files", "List files in a project. Returns file details."),
        _tool("show_file", "Show one file"),
    ])
    assert caps["list_files"].name == "list_files"
    assert caps["get_file"].name == "show_file"


def test_descriptions_still_resolve_a_capability_no_name_claims():
    """A fuzzy match beats no match when the name alone says nothing."""
    caps = resolve_capabilities([
        _tool("marvin_repository", "List all projects in the workspace"),
    ])
    assert caps["list_projects"].name == "marvin_repository"


def test_unrecognised_tools_are_ignored():
    caps = resolve_capabilities([
        _tool("create_widget"), _tool("delete_everything"), _tool("ping"),
    ])
    assert caps == {}


def test_malformed_tool_entries_do_not_crash_resolution():
    caps = resolve_capabilities([
        {"description": "nameless"}, {"name": ""}, _tool("list_projects"),
    ])
    assert set(caps) == {"list_projects"}


# ─────────────────────────── ToolSpec ───────────────────────────


def test_param_prefers_an_exact_match_over_a_substring_one():
    spec = ToolSpec("search", {"properties": {"query_language": {}, "query": {}}})
    assert spec.param("query") == "query"


def test_param_falls_back_to_a_substring_match():
    spec = ToolSpec("search", {"properties": {"search_query": {}}})
    assert spec.param("query") == "search_query"


def test_param_is_case_insensitive_and_returns_the_original_key():
    spec = ToolSpec("list", {"properties": {"ProjectId": {}}})
    assert spec.param("projectid") == "ProjectId"


def test_param_returns_none_when_nothing_matches():
    assert ToolSpec("x", {"properties": {"foo": {}}}).param("query") is None


def test_param_does_not_substring_match_very_short_candidates():
    """"id" inside "projectId" is a coincidence — acting on it would send a
    file id where a project id was wanted."""
    spec = ToolSpec("get_file", {"properties": {"projectId": {}}})
    assert spec.param("file_id", "file", "id") is None
    # An exact short name is still honoured.
    assert ToolSpec("get", {"properties": {"id": {}}}).param("file_id", "id") == "id"


def test_unfillable_required_reports_arguments_we_cannot_supply():
    spec = ToolSpec("get", {"properties": {}, "required": ["file_id", "workspace"]})
    assert spec.unfillable_required({"file_id": "1"}) == ["workspace"]


# ─────────────────────────── distillation ───────────────────────────


def test_distill_text_takes_analysis_fields_and_ignores_transcripts():
    """The no-raw-dump contract: transcript-bearing fields must never be read."""
    record = {
        "title": "Onboarding interview 3",
        "summary": "Users abandon at the workspace-creation step.",
        "key_points": ["Setup felt long", "Unclear why a workspace is needed"],
        "transcript": "SPEAKER 1: so I clicked the button and…" * 500,
        "raw_text": "verbatim capture" * 500,
    }
    text = marvin._distill_text(record)
    assert "workspace-creation step" in text
    assert "- Setup felt long" in text
    assert "SPEAKER 1" not in text
    assert "verbatim capture" not in text


def test_distill_text_is_capped():
    record = {"summary": "x" * 99_999}
    assert len(marvin._distill_text(record)) == marvin._MAX_TEXT


def test_field_lookup_is_insensitive_to_casing_convention():
    """Marvin's casing is undocumented — camelCase and snake_case must both
    resolve, or a rename to `fileId` would silently drop every record."""
    assert marvin._first_str({"fileId": "f-1"}, marvin._ID_FIELDS) == "f-1"
    assert marvin._first_str({"file_id": "f-1"}, marvin._ID_FIELDS) == "f-1"
    assert marvin._first_str({"Name": "Study A"}, marvin._TITLE_FIELDS) == "Study A"
    assert marvin._first_str({}, marvin._TITLE_FIELDS) == ""
    # Numeric ids are coerced; a numeric title is not (it would be nonsense).
    assert marvin._first_str({"id": 42}, marvin._ID_FIELDS) == "42"
    assert marvin._first_str({"title": 42}, marvin._TITLE_FIELDS) == ""


def test_distill_text_reads_camel_case_analysis_fields():
    text = marvin._distill_text({"keyPoints": ["Setup felt long"]})
    assert text == "- Setup felt long"


def test_properties_drop_empty_values():
    props = marvin._properties({"project": "Onboarding", "tags": [], "url": ""})
    assert props == {"project": "Onboarding"}


# ─────────────────────────── pull() ───────────────────────────


class _FakeSession:
    """Stand-in MCP session driven by a {tool_name: result} script."""

    def __init__(self, tools: list[dict], results: dict):
        self._tools = tools
        self._results = results
        self.calls: list[tuple[str, dict]] = []
        self.server_info = {"name": "Marvin"}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = self._results.get(name)
        if callable(result):
            return result(arguments)
        if result is None:
            raise McpError(f"no script for {name}")
        return result


def _structured(rows: list[dict]) -> dict:
    return {"structuredContent": rows}


CREDENTIAL = json.dumps({
    "access_token": "at-1", "mcp_url": "https://mcp.heymarvin.com",
})


def _run(session: _FakeSession) -> list:
    with patch(
        "app.kg_ingest.pullers.marvin.McpSession", return_value=session
    ):
        return list(marvin.pull(CREDENTIAL))


def test_pull_emits_projects_then_distilled_files():
    tools = [
        _tool("list_projects", "List projects",
              {"limit": {"type": "integer"}}),
        _tool("list_files", "List files",
              {"project_id": {"type": "string"}, "limit": {"type": "integer"}},
              required=["project_id"]),
        _tool("get_file_content", "Show file content",
              {"file_id": {"type": "string"}}, required=["file_id"]),
    ]
    results = {
        "list_projects": _structured([
            {"id": "p1", "name": "Onboarding research",
             "description": "Why do new teams stall?", "updated_at": "2026-07-01"},
        ]),
        "list_files": _structured([{"id": "f1", "name": "Interview — Dana"}]),
        "get_file_content": _structured([{
            "id": "f1",
            "summary": "Dana could not find where to invite teammates.",
            "transcript": "DANA: uhh, so…" * 400,
            "url": "https://app.heymarvin.com/f/1",
        }]),
    }
    records = _run(_FakeSession(tools, results))

    kinds = [r.kind for r in records]
    assert kinds == ["project", "research_file"]

    project = records[0]
    assert project.external_id == "project:p1"
    assert "Why do new teams stall?" in project.text
    assert project.timestamp == "2026-07-01"

    research_file = records[1]
    assert research_file.external_id == "file:f1"
    assert research_file.title == "Interview — Dana"
    assert "invite teammates" in research_file.text
    assert "DANA:" not in research_file.text, "transcript must not be ingested"
    assert research_file.properties["url"] == "https://app.heymarvin.com/f/1"
    assert research_file.properties["project"] == "Onboarding research"
    assert research_file.provider == "marvin"


def test_pull_scopes_the_file_listing_to_each_project():
    tools = [
        _tool("list_projects"),
        _tool("list_files", properties={"project_id": {}}),
        _tool("get_file_content", properties={"file_id": {}}),
    ]
    session = _FakeSession(tools, {
        "list_projects": _structured([
            {"id": "p1", "name": "A"}, {"id": "p2", "name": "B"},
        ]),
        "list_files": lambda args: _structured(
            [{"id": f"f-{args['project_id']}", "name": "Study",
              "summary": "finding"}]
        ),
        "get_file_content": _structured([]),
    })
    records = _run(session)

    scoped = [args for name, args in session.calls if name == "list_files"]
    assert [a["project_id"] for a in scoped] == ["p1", "p2"]
    assert {r.external_id for r in records if r.kind == "research_file"} == {
        "file:f-p1", "file:f-p2",
    }


def test_pull_lists_files_once_when_the_tool_takes_no_project():
    tools = [_tool("list_projects"), _tool("list_files")]
    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"},
                                      {"id": "p2", "name": "B"}]),
        "list_files": _structured([{"id": "f1", "name": "S", "summary": "x"}]),
    })
    _run(session)
    assert sum(1 for name, _ in session.calls if name == "list_files") == 1


def test_pull_deduplicates_files_seen_under_several_projects():
    tools = [_tool("list_projects"), _tool("list_files", properties={"project_id": {}})]
    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"},
                                      {"id": "p2", "name": "B"}]),
        "list_files": _structured([{"id": "shared", "name": "S", "summary": "x"}]),
    })
    files = [r for r in _run(session) if r.kind == "research_file"]
    assert len(files) == 1


def test_pull_falls_back_to_prose_when_the_detail_tool_returns_text():
    tools = [
        _tool("list_projects"), _tool("list_files"),
        _tool("get_file_content", properties={"file_id": {}}),
    ]
    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"}]),
        "list_files": _structured([{"id": "f1", "name": "Study"}]),
        "get_file_content": {
            "content": [{"type": "text", "text": "Summary: pricing confusion."}],
        },
    })
    files = [r for r in _run(session) if r.kind == "research_file"]
    assert files[0].text == "Summary: pricing confusion."


def test_pull_skips_a_detail_tool_whose_required_args_are_unknowable():
    """Calling blind would just burn a round trip on a guaranteed 400."""
    tools = [
        _tool("list_projects"), _tool("list_files"),
        _tool("get_file_content", properties={"wat": {}}, required=["wat"]),
    ]
    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"}]),
        "list_files": _structured([{"id": "f1", "name": "S", "summary": "listed"}]),
    })
    files = [r for r in _run(session) if r.kind == "research_file"]
    assert not any(name == "get_file_content" for name, _ in session.calls)
    assert files[0].text == "listed"


def test_pull_survives_a_failing_project_without_losing_the_rest():
    tools = [_tool("list_projects"), _tool("list_files", properties={"project_id": {}})]

    def flaky(args):
        if args["project_id"] == "p1":
            raise McpError("upstream hiccup")
        return _structured([{"id": "f2", "name": "S", "summary": "kept"}])

    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"},
                                      {"id": "p2", "name": "B"}]),
        "list_files": flaky,
    })
    files = [r for r in _run(session) if r.kind == "research_file"]
    assert [f.external_id for f in files] == ["file:f2"]


def test_pull_reraises_auth_failures_instead_of_reporting_an_empty_sync():
    tools = [_tool("list_projects"), _tool("list_files")]

    def unauthorized(_args):
        raise McpError("token expired", status_code=401)

    session = _FakeSession(tools, {"list_projects": unauthorized})
    with pytest.raises(McpError) as excinfo:
        _run(session)
    assert excinfo.value.status_code == 401


def test_pull_fails_loudly_when_mcp_exposes_no_listing_tool():
    """What a workspace with the admin MCP toggle off looks like."""
    session = _FakeSession([_tool("ask_ai", "Run an Ask AI query")], {})
    with pytest.raises(McpError, match="no project or file listing tool"):
        _run(session)


def test_pull_respects_the_file_cap():
    tools = [_tool("list_projects"), _tool("list_files", properties={"project_id": {}})]
    many_projects = [{"id": f"p{i}", "name": f"P{i}"} for i in range(40)]
    session = _FakeSession(tools, {
        "list_projects": _structured(many_projects),
        "list_files": lambda args: _structured([
            {"id": f"{args['project_id']}-{i}", "name": "S", "summary": "x"}
            for i in range(20)
        ]),
    })
    files = [r for r in _run(session) if r.kind == "research_file"]
    assert len(files) <= marvin._MAX_FILES


def test_pull_requests_a_full_page_when_the_tool_offers_paging():
    tools = [
        _tool("list_projects", properties={"limit": {"type": "integer"}}),
        _tool("list_files", properties={"page_size": {"type": "integer"}}),
    ]
    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"}]),
        "list_files": _structured([]),
    })
    _run(session)
    by_tool = dict(session.calls)
    assert by_tool["list_projects"]["limit"] == marvin._PAGE_SIZE
    assert by_tool["list_files"]["page_size"] == marvin._PAGE_SIZE


# ─────────────────────────── paging ───────────────────────────
#
# A one-page pull is how a connector reports a workspace smaller than it is, and
# nothing downstream can detect it: the KG receives the first hundred research
# files and has no way to know a 300-file repository was cut at page one. These
# pin the three bounds that replace it — the item cap, the page ceiling, and the
# repeat-cursor guard — plus the argument-resolution trap that would send a page
# token where a row count belongs.


def _project_page(rows: list[dict], cursor: str | None = None) -> dict:
    """A listing wrapped in an envelope, the way a paging server returns one."""
    envelope: dict = {"results": rows}
    if cursor:
        envelope["nextCursor"] = cursor
    return {"structuredContent": envelope}


def _calls_to(session: _FakeSession, tool: str) -> list[dict]:
    return [args for name, args in session.calls if name == tool]


def test_paging_resolution_never_mistakes_a_page_size_for_a_page_number():
    """THE resolution trap. `ToolSpec.param` falls back to substring matching
    and "page" is a substring of "page_size", so a tool exposing only a row
    count would have it resolved as its page NUMBER — and the second request
    would ask for 2 rows instead of the next hundred."""
    paging = marvin._resolve_paging(ToolSpec("list", {"properties": {"page_size": {}}}))
    assert paging.size_param == "page_size"
    assert paging.cursor_param is None
    assert paging.style == ""


def test_paging_resolution_reads_each_convention_for_what_it_means():
    def paging(properties):
        return marvin._resolve_paging(ToolSpec("list", {"properties": properties}))

    assert paging({"limit": {}, "cursor": {}}).style == "cursor"
    assert paging({"limit": {}, "starting_after": {}}).cursor_param == "starting_after"
    assert paging({"limit": {}, "offset": {}}).style == "offset"
    assert paging({"page_size": {}, "page": {}}).style == "page"
    # A cursor is server-driven, so it needs no page size. Offset and page
    # numbers advance BLIND — a short page is the only end-of-data signal, and
    # it cannot be recognised without knowing how long a full page is.
    assert paging({"cursor": {}}).style == "cursor"
    assert paging({"offset": {}}).style == ""
    assert paging({}).style == ""


def test_pull_follows_a_cursor_until_the_server_stops_offering_one():
    tools = [
        _tool("list_projects", properties={"limit": {}, "cursor": {}}),
        _tool("list_files"),
    ]
    pages = {
        None: _project_page([{"id": "p1", "name": "A"}], cursor="c2"),
        "c2": _project_page([{"id": "p2", "name": "B"}], cursor="c3"),
        "c3": _project_page([{"id": "p3", "name": "C"}]),
    }
    session = _FakeSession(tools, {
        "list_projects": lambda args: pages[args.get("cursor")],
        "list_files": _structured([]),
    })
    records = _run(session)

    assert [r.external_id for r in records] == [
        "project:p1", "project:p2", "project:p3",
    ]
    assert [a.get("cursor") for a in _calls_to(session, "list_projects")] == [
        None, "c2", "c3",
    ]
    # Every page still asks for a full one.
    assert {a["limit"] for a in _calls_to(session, "list_projects")} == {
        marvin._PAGE_SIZE,
    }


def test_pull_stops_at_the_page_ceiling_when_a_cursor_never_ends():
    """A server that always offers a next page would otherwise spin the sync
    thread forever — the same failure `mcp_client.list_tools` bounds."""
    tools = [
        _tool("list_projects", properties={"limit": {}, "cursor": {}}),
        _tool("list_files"),
    ]
    served = {"n": 0}

    def endless(_args):
        served["n"] += 1
        return _project_page(
            [{"id": f"p{served['n']}", "name": "P"}], cursor=f"c{served['n']}",
        )

    session = _FakeSession(tools, {
        "list_projects": endless, "list_files": _structured([]),
    })
    records = _run(session)
    assert served["n"] == marvin._MAX_PAGES
    assert len(records) == marvin._MAX_PAGES


def test_pull_stops_when_a_server_repeats_the_same_cursor():
    """The other shape of forever: a cursor that never advances. Without the
    repeat guard this re-reads page one until the page ceiling."""
    tools = [
        _tool("list_projects", properties={"limit": {}, "cursor": {}}),
        _tool("list_files"),
    ]
    session = _FakeSession(tools, {
        "list_projects": lambda _a: _project_page(
            [{"id": "p1", "name": "A"}], cursor="stuck",
        ),
        "list_files": _structured([]),
    })
    records = _run(session)
    assert len(_calls_to(session, "list_projects")) == 2
    assert [r.external_id for r in records] == ["project:p1"]


def test_the_item_cap_beats_the_page_ceiling(monkeypatch):
    """The caps are the product bound; the page ceiling is only the runaway
    guard. Whichever binds first must be the item cap."""
    monkeypatch.setattr(marvin, "_MAX_PROJECTS", 3)
    tools = [
        _tool("list_projects", properties={"limit": {}, "cursor": {}}),
        _tool("list_files"),
    ]
    served = {"n": 0}

    def two_per_page(_args):
        served["n"] += 1
        return _project_page(
            [{"id": f"p{served['n']}-{i}", "name": "P"} for i in range(2)],
            cursor=f"c{served['n']}",
        )

    session = _FakeSession(tools, {
        "list_projects": two_per_page, "list_files": _structured([]),
    })
    records = _run(session)
    assert served["n"] == 2                    # stopped as soon as 3 was passed
    assert len([r for r in records if r.kind == "project"]) == 3


def test_pull_follows_an_offset_until_a_short_page_ends_the_listing(monkeypatch):
    monkeypatch.setattr(marvin, "_PAGE_SIZE", 2)
    tools = [
        _tool("list_projects", properties={"limit": {}, "offset": {}}),
        _tool("list_files"),
    ]

    def by_offset(args):
        offset = args.get("offset") or 0
        count = 2 if offset == 0 else 1     # second page is short → exhausted
        return _structured(
            [{"id": f"p{offset + i}", "name": "P"} for i in range(count)]
        )

    session = _FakeSession(tools, {
        "list_projects": by_offset, "list_files": _structured([]),
    })
    records = _run(session)
    assert [a.get("offset") for a in _calls_to(session, "list_projects")] == [None, 2]
    assert [r.external_id for r in records] == [
        "project:p0", "project:p1", "project:p2",
    ]


def test_files_page_within_each_project_scope():
    """Paging has to run per scope, not once: a workspace whose files are listed
    per project pages inside every project."""
    tools = [
        _tool("list_projects"),
        _tool("list_files", properties={"project_id": {}, "limit": {}, "cursor": {}}),
    ]

    def files(args):
        project = args["project_id"]
        if args.get("cursor"):
            return _project_page([
                {"id": f"{project}-b", "name": "S", "summary": "second page"},
            ])
        return _project_page(
            [{"id": f"{project}-a", "name": "S", "summary": "first page"}],
            cursor=f"{project}-next",
        )

    session = _FakeSession(tools, {
        "list_projects": _structured([{"id": "p1", "name": "A"},
                                      {"id": "p2", "name": "B"}]),
        "list_files": files,
    })
    ids = {r.external_id for r in _run(session) if r.kind == "research_file"}
    assert ids == {"file:p1-a", "file:p1-b", "file:p2-a", "file:p2-b"}


def test_a_tool_serving_both_listings_does_not_double_every_record():
    """A server may expose ONE listing tool — `list_projects_and_files` claims
    `list_files` by name and `list_projects` by description, so the same rows
    arrive down both paths. Both roles must keep working (nothing is dropped)
    without the repository appearing twice in the graph."""
    tools = [_tool("list_projects_and_files", "List all projects and files")]
    session = _FakeSession(tools, {
        "list_projects_and_files": _structured([
            {"id": "p1", "name": "Onboarding research", "summary": "why teams stall"},
            {"id": "f1", "name": "Interview — Dana", "summary": "could not invite"},
        ]),
    })
    caps = resolve_capabilities(tools)
    assert caps["list_files"].name == caps["list_projects"].name

    records = _run(session)
    assert [r.external_id for r in records] == ["project:p1", "project:f1"]
    assert len({r.external_id for r in records}) == len(records)


def test_pull_skips_records_with_no_id_or_no_analysis():
    tools = [_tool("list_projects"), _tool("list_files")]
    session = _FakeSession(tools, {
        "list_projects": _structured([{"name": "no id"}, {"id": "p1", "name": "A"}]),
        # f2 has an id and a title but nothing analytical to extract.
        "list_files": _structured([
            {"id": "f1", "name": "Kept", "summary": "a finding"},
            {"id": "f2", "name": "Empty"},
        ]),
    })
    records = _run(session)
    assert [r.external_id for r in records] == ["project:p1", "file:f1"]
