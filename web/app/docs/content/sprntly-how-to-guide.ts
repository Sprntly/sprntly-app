import type { Doc } from "./types"

/**
 * Sprntly How-To Guide — hardcoded content (no database).
 * Source: "Sprntly How-To Guide" PDF, Version 1.0, July 2026.
 * To edit the guide, edit the Markdown strings below.
 */
export const sprntlyHowToGuide: Doc = {
  slug: "sprntly-how-to-guide",
  title: "How-To Guide",
  description:
    "From signals to PRD to prototype to build — the step-by-step guide to using Sprntly end to end.",
  category: "Guides",
  version: "1.0",
  updated: "July 2026",
  sections: [
    {
      id: "overview",
      title: "What this guide is for",
      body: `This guide walks you step by step through using Sprntly to move from signals to a PRD, from a PRD to tickets, from tickets to a working prototype, and from there into your codebase.

By the end, you will know how to:

- Turn your weekly brief, or an uploaded file, into a PRD
- Generate and assign tickets that sync to your project management tool
- Generate, edit, and finalize a prototype
- Pull all of that context into your code environment using MCP`,
    },
    {
      id: "who-its-for",
      title: "Who this guide is for",
      body: `| Role | What you will use Sprntly for |
| --- | --- |
| **Product Managers & Founders** | Gather signals, generate PRDs, create and assign tickets |
| **Designers** | Generate prototypes from PRDs and tickets, then iterate on them |
| **Engineers** | Use PRDs, tickets, and prototypes to build, with MCP to bring the context into their AI coding environment |`,
    },
    {
      id: "what-is-sprntly",
      title: "What is Sprntly?",
      body: `Sprntly is an AI product intelligence platform that helps product organizations build products that drive their core business goals.`,
    },
    {
      id: "before-you-start",
      title: "Before you start",
      body: `You will get the most out of Sprntly if you have:

- **Your data sources connected.** This is what powers signals and your weekly brief. To do this, go to **Settings → Connectors**.
- **Your project management tool connected**, so tickets sync automatically. Connect it in **Settings → Connectors**.
- **Your team invited to the workspace.** Invite them in **Settings → Team and Roles**.`,
    },
    {
      id: "prd-and-tickets",
      title: "Section 1 — Creating a PRD and tickets",
      body: `### Step 1: Start your PRD

There are two ways to start. Pick whichever fits your situation.

#### Option A — From your weekly brief (recommended)

1. Go to your **Weekly Brief**, which is the home page. If your team has connected data sources, Sprntly pulls that information in and generates the brief for you automatically.
2. On the weekly brief page, click **View or Generate PRD**.
3. Your PRD opens, already grounded in your team's signals.

#### Option B — From a file you upload

1. Go to your **Weekly Brief** (the home page).
2. Click the **+ icon in the navigation bar** to open a new workspace.
3. In the text field, click **Attach a File** and upload your document.
4. Type a prompt such as: *"Create a PRD based on this document."*
5. Click **Send**.

> **This takes about 2 minutes 30 seconds.** Sprntly reads the attachment, extracts the information, and generates the PRD. Leave the tab open.

### Step 2: Review and edit the PRD

Read through the PRD and edit it directly. Tighten the problem statement, adjust scope, and add anything Sprntly could not have known. Move on when you are happy with it.

### Step 3: Generate tickets

1. In the **Artifacts** section on the right, scroll down to the bottom.
2. At the bottom right, click **Generate Artifact**.

Sprntly breaks the PRD into individual tickets.

### Step 4: Assign and refine tickets

1. Click any individual ticket to open it.
2. Make changes as needed to the title, description, or scope.
3. Assign the ticket to the right person.

> **Person not showing up?** If a team member has not been added to the workspace yet, you can add them through **Settings → Team and Roles**.

### Step 5: Tickets sync to your project management tool

> **This happens automatically.** Once generated, tickets are pushed to your connected project management tool.

If you want to sync manually, click **Push to Project Management** at the top of the page (Jira, Asana, or ClickUp).`,
    },
    {
      id: "prototype",
      title: "Section 2 — Generating and editing a prototype",
      body: `### Step 1: Generate the prototype

1. From your tickets, go to the **Artifacts** section on the right.
2. At the bottom right, click **Generate Prototype**.

> **This can take up to 3 minutes.** Do not click Cancel while it generates.

### Step 2: Open the editable view

When the prototype is ready, it opens in a preview you can click through, but not change.

To edit it, click the **X (close) icon in the top right corner**. This opens the editable view.

### Step 3: Describe the changes you want

1. Use the main chat panel on the left.
2. Describe the change you want in plain language.
3. Click **Submit**. The prototype regenerates with your change.

**Be specific. Vague requests produce vague results.**

- Instead of *"make it better"*, write *"make the Continue button red."*
- Instead of *"fix the checkout"*, write *"when I click Continue, open a pop-up that confirms the order total and has a Pay Now button."*
- Instead of *"clean up the UX"*, write *"move the CTA above the fold and use consistent spacing between the form fields."*

### Step 4: Mark as complete

When the prototype is finished, click **Mark as Complete**. This does two things:

- Locks the prototype so no further changes can be made.
- Syncs it with your project management tool.

### Step 5: Find past PRDs and prototypes

1. Click **Artifacts** in the navigation.
2. Toggle between **PRDs** and **Prototypes**.
3. Click any item to review it or make edits.`,
    },
    {
      id: "mcp",
      title: "Section 3 — Using MCP to build",
      body: `The Sprntly MCP lets engineers pull their tickets, PRDs, prototypes, and research evidence directly into their AI coding environment: Claude Code, Cursor, Claude Desktop, claude.ai, or ChatGPT. Instead of copy-pasting a ticket into a chat, your AI client reads the ticket, and everything behind it, straight from Sprntly. It can also update status, edit descriptions, comment, and link the PR back, so the build stays aligned with the spec.

It works over the Model Context Protocol. You connect once with a token you create in Settings. After that, your AI client just knows about your Sprntly work.

> **One workspace, your access only.** A token is tied to your account and your workspace. It only ever sees your assigned tickets, never a teammate's, never the unassigned pool. There is nothing to configure for scoping. It is automatic.

### Step 1: Create your MCP token

1. Go to **Settings → MCP Access**.
2. Under **New token**, give it a name you will recognize (e.g. "Claude Code" or "Cursor").
3. Pick a role:
   - **Developer (tickets & PRDs)** — the ticket tools, plus the PRDs, prototypes, and evidence behind your tickets. This is the right choice for engineers.
   - **PM (full access)** — everything a developer token has, plus workspace-level surfaces: datasets, the weekly brief, the ranked backlog, and the latest PRD.
4. Click **Create token**.

> **Copy the connector URL now — it is shown only once.** Sprntly displays a single URL that looks like \`https://api.sprntly.ai/mcp?token=…\` with your secret token embedded. Copy it immediately. If you lose it, you cannot recover it — just revoke the token and create a new one.

Existing tokens are listed below the form, showing their role, when they were created, and when they were last used. Click **Revoke** to disable one at any time. Rotate a token this way if it is ever exposed.

### Step 2: Connect your AI client

Every client comes down to giving it that connector URL. In-app, click **Guide to connect to MCP** for the exact steps per client. Replace \`<CONNECTOR_URL>\` with the URL from Step 1.

### Step 3: Start building

Once connected, just talk to your AI client in plain language. Try:

- *"List my Sprntly tickets that are in progress."*
- *"Open ticket \`prd-42-a1b2c3d4e5f6\` and implement it, read the PRD and acceptance criteria first."*
- *"Show me the prototype and the research evidence behind this ticket's PRD before we start."*
- *"I've opened a PR for this ticket, attach the link and move it to In review."*

Your client will call the right Sprntly tools on its own. A good habit: have it read the ticket (and its PRD and evidence) before writing code, and update status and attach the PR when it is done.

#### What tools your AI client can use

You do not call these directly. Your AI client picks them as needed, but knowing what is available helps you prompt well.

Available to every token (the engineer set):

| Tool | What it does |
| --- | --- |
| \`list_tickets\` | Lists the tickets assigned to you, with status, type, priority, and PRD. Optional filters by status or type. |
| \`get_ticket\` | Full detail for one ticket: title, description, acceptance criteria, scope, and what/why context, merged with any edits, plus comments and attachments. This is what your client reads to implement a ticket. |
| \`get_prd\` | The parent PRD behind a ticket, for full product context. |
| \`list_prd_tickets\` | All tickets in one PRD: the full scope across every assignee, not just yours. |
| \`get_prd_prototype\` | The design prototype behind a PRD: its status and viewer links (in-app link always; public share link only if a PM already shared it). |
| \`get_prd_evidence\` | The research evidence behind the PRD: the customer signals explaining why it exists. |
| \`update_ticket_fields\` | Update a ticket's status, priority, title, or sprint. (Assigning people stays in the app.) |
| \`update_ticket_description\` | Replace a ticket's description, and optionally its acceptance criteria. |
| \`add_ticket_comment\` | Comment on a ticket, attributed to you. |
| \`add_ticket_attachment\` | Link a PR or branch to a ticket. |`,
    },
    {
      id: "need-help",
      title: "Need help?",
      body: `- **Send feedback.** Click the feedback icon at the bottom left of the navigation, write what happened, and send. Best for bugs, UX issues, and feature requests.
- **Call for immediate help:** **(201) 852-5211**. Use this when you are blocked and need someone now.`,
    },
  ],
}
