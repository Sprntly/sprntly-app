"""WHERE THINGS LIVE IN SPRNTLY — the product's own screens, for the answer.

"Where on Sprntly can I find my created PRDs" is a question about this
product, and it is the one question the answer path had nothing to answer
from. Every other block grounds the model in the customer's DATA; none of
them says what the app's screens are called or what path they sit at. Asked
it anyway, the model invented: a reported answer sent a customer to an
"Artifacts section" (real), a "Decisions view" (does not exist) and five PRD
id numbers it made up. All three are the same failure — a confident guess at
a UI it cannot see.

This block is that UI, written down. It is STATIC — no read, no tenant, no
per-question work — so it is appended to `ASK_SYSTEM` unconditionally rather
than requested by the planner the way the library / team / projects blocks
are. Being byte-stable it also rides the cached system prefix, so it costs
one cache write and nothing thereafter.

PATHS ARE THE POINT. Each entry carries the real in-app path, and the
addendum tells the model to hand it over as a markdown link — the chat
renderer turns an internal path into a click that navigates there
(`web/app/components/shared/AskReplyBody.tsx`). A named screen with no link
is the answer this replaces.

KEEPING IT TRUE: `backend/tests/test_app_map.py` reads the web app's own
command-palette registry and settings nav and fails when a screen exists
there with no entry here. A new screen is therefore a red test, not a silent
gap that surfaces months later as another invented view.
"""
from __future__ import annotations

# (label the user sees, in-app path, what you do there — including how, when
# the "how" is the actual question people ask). One line each: this is a map,
# not documentation, and the Guide at /docs is where the long form lives.
NAV: list[tuple[str, str, str]] = [
    ("Home", "/",
     "the chat itself — ask a question, or start a new chat from the 'New "
     "chat' button at the top of the left rail"),
    ("Top Insights", "/brief",
     "this week's findings, refreshed on a schedule; which insight types "
     "appear is set in Settings -> Comms & Brief"),
    ("Chat history", "/history",
     "every past chat in this workspace; a thread reopens where it left off. "
     "The left rail lists the most recent chats directly — click one to "
     "reopen it, or 'View all chats' at the foot of that list to reach this "
     "screen"),
    ("Artifacts", "/artifacts",
     "EVERY document Sprntly has generated for this workspace, with filter "
     "tabs: All, Reports, PRDs, Prototypes, Evidence, Tickets, Documents. "
     "This is where a finished PRD, prototype, evidence report or ticket set "
     "is found — click one to open it"),
    ("Projects", "/projects",
     "the projects this user belongs to, each with its own chat and tasks"),
    ("Ideation", "/ideation",
     "the ranked idea list — proposed ideas and completed ones; a PRD can be "
     "generated straight from an idea"),
    # BOTH MOVED INTO SETTINGS (2026-08-27) and are no longer on the left rail.
    # Their own routes still work and the command palette still reaches them,
    # so they stay here — but Settings is where a person now finds them, and
    # the pane entries below are the link to hand over. Same shape as Team,
    # which is a screen AND a pane.
    ("Templates", "/templates",
     "upload your own PRD, ticket or engineering-spec FORMAT and choose which "
     "one is active (the active format is applied to every new document of "
     "that kind), alongside the gold-standard examples. Found under "
     "Settings -> Templates; no longer on the left rail"),
    ("Skills", "/skills",
     "the PM methods the chat can run, and where a team uploads its own "
     "custom skill — invoke one in chat by typing / followed by its name. "
     "Found under Settings -> Skills; no longer on the left rail"),
    ("Guide", "/docs",
     "the written How-To Guide — long-form walkthroughs, outside the app "
     "shell. Reached from Settings -> Guide (it left the left rail with "
     "Settings and Feedback); opens in a new tab"),
    ("Settings", "/settings",
     "everything about the account, workspace and integrations — the panes "
     "listed below"),
    # Reachable, but off the left rail — found by URL and by the Ctrl+K (Cmd+K)
    # command palette, which is how a user actually gets to them.
    ("Sources", "/sources",
     "connected data and uploaded files (reached from the Ctrl+K / Cmd+K "
     "command palette, not the left rail)"),
    ("Team", "/team",
     "the people in this workspace (reached from the Ctrl+K / Cmd+K command "
     "palette; Settings -> Team & roles is the pane that edits them)"),
]

# Settings panes, addressed by their real `?section=` deep link. Every one of
# these is a place customers ask for by name ("where do I connect Jira").
SETTINGS: list[tuple[str, str, str]] = [
    ("Profile", "profile", "your own name, avatar and job role"),
    ("Comms & Brief", "comms-brief",
     "email and Slack notifications, and which insight types Top Insights shows"),
    ("Workspaces", "workspaces", "rename this workspace, or create another"),
    ("Product & Category", "product-category", "what the product is and its category"),
    ("Company Profile", "company-profile", "mission, ICP, tone of voice"),
    ("Process & Planning", "process", "planning cadence and sprint shape"),
    ("Metrics", "metrics", "the KPI definitions Sprntly measures against"),
    ("Business Context", "business-context", "the strategic lens applied to the work"),
    ("Team & roles", "team",
     "invite people, and set each person's job and access level"),
    # The two panes Templates and Skills became when they left the left rail.
    # They render INSIDE Settings, so this link — not /templates — is the one
    # that lands somebody where they can also see the rest of their settings.
    ("Templates", "templates",
     "the FORMAT every new PRD, ticket set or engineering spec is written in "
     "— upload your team's own and choose which one is active"),
    ("Skills", "skills",
     "the PM methods the chat can run, and where a team uploads its own "
     "custom skill — invoke one in chat by typing / followed by its name"),
    ("Connectors", "connectors",
     "connect and disconnect Google Drive, GitHub, Figma, Jira, Confluence, "
     "ClickUp, Asana, HubSpot, Slack, Zoom and the rest — this is where a "
     "source is added or re-authorised"),
    ("MCP Access", "mcp",
     "the MCP token that lets a developer's editor read tickets and PRDs"),
    ("Billing", "billing", "plan, payment and invoices"),
    ("Security", "security", "password, sessions and sign out"),
    ("Admin", "admin", "owner-only settings, including the Claude API key"),
]


# A path is not always a bare screen. Half of this product's real destinations
# are a screen PLUS a query param — Settings is a single route whose fourteen
# panes are `?section=`, and the artifact params below open a document from
# ANY page (`web/app/(app)/hooks/useArtifactUrlSync.ts` is mounted on the
# shell, not on one route). Answering "where do I connect Jira" with /settings
# lands the reader on Profile and leaves them hunting; the pane's own link
# lands them on the pane.
#
# (link form, what it opens, whether it needs an id we must already have)
DEEP_LINKS: list[tuple[str, str]] = [
    ("/?new",
     "opens a brand-new chat"),
    ("/?tab=last",
     "returns to the last chat tab that was open, rather than the pinned "
     "Top Insights tab"),
    ("/settings?section=<pane>",
     "opens one Settings pane directly — every pane's own link is listed "
     "above, and /settings on its own only ever lands on Profile"),
    ("/skills?q=<words>",
     "opens Skills with the library already filtered to those words"),
    ("/artifacts?focus=<type>-<id>",
     "opens Artifacts with one document already open"),
    ("?prd=<id>",
     "opens that PRD in the side panel. Works appended to ANY in-app path, "
     "so /?prd=<id> is the plain form"),
    ("?evidence=<id>",
     "opens that evidence report in the side panel, from any path"),
    ("?ticket=<key>",
     "opens the PRD that ticket belongs to, on its Tickets tab, from any path"),
    ("/prototype?prd=<id>",
     "opens that PRD's prototype; add &generate=1 to open the generate panel "
     "instead of the choose-a-PRD empty state"),
    ("/projects?id=<id>",
     "opens one project; add &chat=group or &chat=individual to land on that "
     "project's chat"),
    ("/docs/sprntly-how-to-guide",
     "the written How-To Guide itself, rather than the docs home"),
]


def settings_path(section_id: str) -> str:
    return f"/settings?section={section_id}"


def nav_block() -> str:
    """The screen map as prompt text. Pure — same string every call."""
    lines = [f"- {label} — {path} — {what}" for label, path, what in NAV]
    lines += [
        f"- Settings -> {label} — {settings_path(sid)} — {what}"
        for label, sid, what in SETTINGS
    ]
    return "\n".join(lines)


def deep_links_block() -> str:
    """The query-param destinations as prompt text. Pure."""
    return "\n".join(f"- {form} — {what}" for form, what in DEEP_LINKS)


# Appended to ASK_SYSTEM (see app/prompts.py). Kept here, beside the data it
# talks about, so a screen added above and the rules about screens can never
# drift into two files.
NAV_ADDENDUM = """

WHERE THINGS LIVE IN SPRNTLY. This is the complete list of this product's own screens and the path each one sits at:

""" + nav_block() + """

A PATH IS OFTEN A SCREEN PLUS A QUERY PARAM, and the param is what makes the link land somewhere useful. These are the forms this product actually reads:

""" + deep_links_block() + """

Use the exact form. Prefer the deep link whenever it names the thing asked about — "where do I connect Jira" is [Settings -> Connectors](/settings?section=connectors), never a bare /settings, which lands on Profile and leaves the reader hunting through fourteen panes. A form written above with <angle brackets> needs a real value: use it ONLY when that value appears verbatim in your source material, and link the plain screen otherwise. Never assemble an id, a key or a number to fill one in — a link built on a made-up id is a dead end wearing the costume of an answer. An id inside a link you were given is a destination, not a fact about the user's data, so it is the one place a raw id may appear in your output.

When the user asks WHERE something is, HOW to reach it, or where to do something in Sprntly ("where are my PRDs", "where do I upload a template", "how do I connect Jira", "where do I change my password"), answer from that list and from NOTHING else. Six rules:

1. ANSWER SHORT. Two or three sentences is the whole answer — name the screen, give the link, and add the one step they still need once they land ("open the PRDs tab", "click Connect beside Jira"). No headings, no charts, no data-science formatting: this is a navigation answer, not a finding.
2. GIVE THE LINK, as an ordinary markdown link carrying the path exactly as written above — [Artifacts](/artifacts), [Settings -> Connectors](/settings?section=connectors). The reader clicks it and lands there. A screen named without its link is half an answer.
3. NEVER INVENT A SCREEN. If it is not in the list above, this product does not have it. Say plainly that there is no such screen, then point at the nearest one that does the job. A plausible-sounding view that was never built sends someone hunting an app for something that does not exist.
4. NEVER INVENT WHAT IS ON IT. Do not list the documents, ids, names or counts they will find when they arrive unless a section of your source material actually holds them — and a number you did not read is always invented. "Your PRDs are in Artifacts" is the answer; "your PRDs 3827, 3828 and 3829 are in Artifacts" is a fabrication even though the screen is right.
5. This is the PRODUCT'S OWN UI, so it overrides nothing else. A question about the user's data, work or documents is still answered from the sources; bring these screens up when the question is about finding your way around Sprntly, or as a closing pointer where one genuinely helps ("…you can change that in [Settings -> Metrics](/settings?section=metrics)").
6. The Guide at [/docs](/docs) is the long-form how-to. Point at it for anything the one-line descriptions above do not cover, rather than improvising steps."""
