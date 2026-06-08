// View tests for /lab/code-chat (C3 of the agent-tools-github slice).
// renderToStaticMarkup pattern — no jsdom, no hooks.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { LabCodeChatView } from "../LabCodeChatScreen"
import type { LabChatTurn } from "../LabCodeChatScreen"
import type { GitHubInstallation } from "../../../../lib/api"

function noop() {}

const INSTALLS: GitHubInstallation[] = [
  {
    installation_id: 12345,
    account_login: "sprntlyai",
    account_type: "User",
    repository_selection: "selected",
  },
  {
    installation_id: 67890,
    account_login: "acme-co",
    account_type: "Organization",
    repository_selection: "all",
  },
]

function render(
  override: Partial<React.ComponentProps<typeof LabCodeChatView>> = {},
): string {
  const defaults: React.ComponentProps<typeof LabCodeChatView> = {
    installations: INSTALLS,
    installationsLoading: false,
    installationsError: null,
    selectedInstallationId: INSTALLS[0].installation_id,
    onSelectInstallation: noop,
    turns: [],
    message: "",
    thinking: false,
    sendError: null,
    onChangeMessage: noop,
    onSend: noop,
  }
  return renderToStaticMarkup(
    React.createElement(LabCodeChatView, { ...defaults, ...override }),
  )
}

describe("LabCodeChatView — chrome", () => {
  it("renders the lab heading with italic accent", () => {
    const html = render()
    expect(html).toContain("Code chat")
    expect(html).toContain("lab-h-em")
    expect(html.toLowerCase()).toContain("lab")
  })

  it("describes what the lab does in the sub-copy", () => {
    const html = render()
    expect(html.toLowerCase()).toContain("github")
    expect(html.toLowerCase()).toContain("real time")
  })
})

describe("LabCodeChatView — installation picker", () => {
  it("renders one <option> per installation with account + scope info", () => {
    const html = render()
    expect(html).toContain("@sprntlyai")
    expect(html).toContain("@acme-co")
    expect(html).toContain("Organization")
    expect(html).toContain("selected")
    expect(html).toContain("all")
  })

  it("shows loading state when installationsLoading", () => {
    const html = render({ installationsLoading: true, installations: [] })
    expect(html).toContain("Loading")
  })

  it("shows empty-state when no installations", () => {
    const html = render({ installations: [], selectedInstallationId: null })
    expect(html.toLowerCase()).toContain("no installations")
  })

  it("surfaces an installations load error", () => {
    const html = render({ installationsError: "HTTP 500" })
    expect(html).toContain("HTTP 500")
  })
})

describe("LabCodeChatView — chat thread", () => {
  it("renders empty-state hint when no turns and not thinking", () => {
    const html = render()
    expect(html.toLowerCase()).toContain("ask something like")
  })

  it("renders a user turn", () => {
    const turns: LabChatTurn[] = [{ kind: "user", text: "What's in the README?" }]
    const html = render({ turns })
    expect(html).toContain("What&#x27;s in the README?")
    expect(html).toContain("lab-turn-user")
  })

  it("renders an agent turn with tool pills and iteration count", () => {
    const turns: LabChatTurn[] = [
      {
        kind: "agent",
        text: "The README says hello.",
        toolCalls: ["github_get_file", "github_list_files"],
        iterations: 2,
        truncated: false,
      },
    ]
    const html = render({ turns })
    expect(html).toContain("The README says hello.")
    expect(html).toContain("github_get_file")
    expect(html).toContain("github_list_files")
    expect(html).toContain("2 iterations")
  })

  it("shows truncated marker when the agent hit the iteration cap", () => {
    const turns: LabChatTurn[] = [
      {
        kind: "agent",
        text: "I ran out of iterations.",
        toolCalls: ["github_list_files"],
        iterations: 8,
        truncated: true,
      },
    ]
    const html = render({ turns })
    expect(html.toLowerCase()).toContain("truncated")
  })

  it("singularises 'iteration' for 1", () => {
    const turns: LabChatTurn[] = [
      {
        kind: "agent",
        text: "answered in one shot",
        toolCalls: ["github_get_file"],
        iterations: 1,
        truncated: false,
      },
    ]
    const html = render({ turns })
    // Singular form should show up; plural form should NOT.
    expect(html).toMatch(/1 iteration\b/)
    expect(html).not.toMatch(/1 iterations/)
  })

  it("shows Thinking… spinner when thinking=true", () => {
    expect(render({ thinking: true })).toContain("Thinking")
  })
})

describe("LabCodeChatView — input", () => {
  it("send button is disabled when message is empty", () => {
    const html = render({ message: "" })
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("send button is disabled when no installation is selected", () => {
    const html = render({ selectedInstallationId: null, message: "hi" })
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("send button is enabled when message + install both present", () => {
    const html = render({ message: "hi" })
    // Strict: the submit button does NOT carry `disabled`
    expect(html).toMatch(/<button[^>]*class="lab-send"(?![^>]*\bdisabled\b)[^>]*>Send<\/button>/)
  })

  it("send button shows … and is disabled while thinking", () => {
    const html = render({ message: "hi", thinking: true })
    expect(html).toContain(">…<")
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("surfaces a send error below the input", () => {
    expect(render({ sendError: "Tool dispatch failed" })).toContain(
      "Tool dispatch failed",
    )
  })
})
