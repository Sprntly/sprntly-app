// @vitest-environment jsdom
//
// The MCP Access install guide: a "Guide to install" button opens a modal of
// per-client connection instructions (Claude Code / claude.ai / Claude
// Desktop / ChatGPT / Cursor), each with a copyable command or config carrying
// the connector URL — the real one-time URL right after a token is minted, a
// YOUR_TOKEN placeholder otherwise. These tests render the pure view.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// The component compiles against the classic JSX runtime in tests — global
// React must exist before the import below evaluates.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { McpSettingsView, type McpSettingsViewProps } from "../McpSettings"

afterEach(cleanup)

const noop = () => {}

const baseProps: McpSettingsViewProps = {
  tokens: [],
  loading: false,
  error: null,
  newName: "",
  newRole: "developer",
  creating: false,
  justCreated: null,
  copiedAck: false,
  onNewNameChange: noop,
  onNewRoleChange: noop,
  onCreate: (e) => e.preventDefault(),
  onDismissCreated: noop,
  onCopiedAckChange: noop,
  onRevoke: noop,
  revokingId: null,
}

describe("MCP install guide", () => {
  it("opens from 'Guide to install' with the Claude Code command (placeholder token)", () => {
    render(React.createElement(McpSettingsView, baseProps))

    fireEvent.click(screen.getByRole("button", { name: /guide to connect to mcp/i }))
    const dialog = screen.getByRole("dialog", { name: /install guide/i })
    expect(dialog).toBeTruthy()

    // Claude Code is the default tab; its terminal command carries the URL.
    const cmd = dialog.querySelector("pre")?.textContent || ""
    expect(cmd).toContain("claude mcp add --transport http sprntly")
    expect(cmd).toContain("/mcp?token=YOUR_TOKEN")
    // No real token in hand → the placeholder note explains where to get one.
    expect(screen.getByText(/only displayed once/i)).toBeTruthy()
  })

  it("switching clients swaps the instructions (Cursor shows the mcp.json snippet)", () => {
    render(React.createElement(McpSettingsView, baseProps))
    fireEvent.click(screen.getByRole("button", { name: /guide to connect to mcp/i }))

    fireEvent.click(screen.getByRole("tab", { name: "Cursor" }))
    const dialog = screen.getByRole("dialog", { name: /install guide/i })
    const snippet = dialog.querySelector("pre")?.textContent || ""
    expect(snippet).toContain('"mcpServers"')
    expect(snippet).toContain('"sprntly"')
    // Named in both the steps and the block label.
    expect(screen.getAllByText(/\.cursor\/mcp\.json/i).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole("tab", { name: "claude.ai" }))
    expect(screen.getByText(/add custom connector/i)).toBeTruthy()
  })

  it("uses the real one-time connector URL right after a token is created", () => {
    render(
      React.createElement(McpSettingsView, {
        ...baseProps,
        justCreated: {
          id: "t1", name: "Claude Desktop", token: "sk-live-abc123",
        } as McpSettingsViewProps["justCreated"],
      }),
    )

    // The success banner offers a direct path into the guide.
    fireEvent.click(screen.getByRole("button", { name: /how to connect/i }))
    const dialog = screen.getByRole("dialog", { name: /install guide/i })
    const cmd = dialog.querySelector("pre")?.textContent || ""
    expect(cmd).toContain("/mcp?token=sk-live-abc123")
    // Real token in hand → no placeholder note.
    expect(screen.queryByText(/only displayed once/i)).toBeNull()
  })

  it("closes via the close button", () => {
    render(React.createElement(McpSettingsView, baseProps))
    fireEvent.click(screen.getByRole("button", { name: /guide to connect to mcp/i }))
    fireEvent.click(screen.getByRole("button", { name: /close guide/i }))
    expect(screen.queryByRole("dialog", { name: /install guide/i })).toBeNull()
  })
})
