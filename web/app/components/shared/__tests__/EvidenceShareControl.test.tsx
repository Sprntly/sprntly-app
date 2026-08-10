// @vitest-environment jsdom
//
// EvidenceShareControl: reads a pre-existing canonical token off
// content.evidenceShareToken and renders the link inline — never mints on
// open. Mirrors the PRD ShareMenu's ContentPanel.share-canonical.dom.test.tsx
// harness, scoped to this standalone leaf component.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const mintMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { mint: (...a: unknown[]) => mintMock(...a) },
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { EvidenceShareControl } from "../EvidenceShareControl"

const writeTextMock = vi.fn(async () => {})

beforeEach(() => {
  vi.stubGlobal("navigator", {
    ...navigator,
    clipboard: { writeText: writeTextMock },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe("EvidenceShareControl", () => {
  it("test_share_control_renders_inline_token_url — AC13", () => {
    contentMock.value = { evidenceShareToken: "canon-ev-tok-1" }
    render(<EvidenceShareControl />)
    fireEvent.click(screen.getByRole("button", { name: "Copy evidence link" }))
    const expected = `${window.location.origin}/?share=canon-ev-tok-1`
    expect(screen.getByText(expected).tagName.toLowerCase()).toBe("code")
  })

  it("test_copy_writes_url_and_flips_to_copied — AC13", async () => {
    contentMock.value = { evidenceShareToken: "canon-ev-tok-2" }
    render(<EvidenceShareControl />)
    fireEvent.click(screen.getByRole("button", { name: "Copy evidence link" }))
    const expected = `${window.location.origin}/?share=canon-ev-tok-2`
    fireEvent.click(screen.getByRole("button", { name: "Copy" }))
    await waitFor(() => expect(writeTextMock).toHaveBeenCalledWith(expected))
    expect(screen.getByRole("button", { name: "Copied!" })).not.toBeNull()
  })

  it("test_opening_share_control_never_mints — AC14 (RED if mint-on-open is reintroduced)", async () => {
    contentMock.value = { evidenceShareToken: "canon-ev-tok-3" }
    render(<EvidenceShareControl />)
    fireEvent.click(screen.getByRole("button", { name: "Copy evidence link" }))
    fireEvent.click(screen.getByRole("button", { name: "Copy" }))
    await waitFor(() => expect(writeTextMock).toHaveBeenCalled())
    expect(mintMock).not.toHaveBeenCalled()
  })

  it("test_share_control_disabled_without_token_never_mints — AC15", () => {
    contentMock.value = { evidenceShareToken: null }
    render(<EvidenceShareControl />)
    fireEvent.click(screen.getByRole("button", { name: "Copy evidence link" }))
    expect(screen.queryByText(/\/\?share=/)).toBeNull()
    const preparing = screen.getByRole("button", { name: /preparing link/i })
    expect(preparing).toHaveProperty("disabled", true)
    fireEvent.click(preparing)
    expect(writeTextMock).not.toHaveBeenCalled()
    expect(mintMock).not.toHaveBeenCalled()
  })
})

// Note: test_artifacts_evidence_open_threads_share_token (AC16) — the
// ArtifactsScreen evidence-open-path threading test the ticket's Unit Tests
// section lists under this file's heading — lives instead in
// ArtifactsScreen.evidence-share-token.dom.test.tsx, colocated with every
// other ArtifactsScreen test (components/screens/app/__tests__/). Keeping it
// here would require a SECOND, conflicting `vi.mock` registration for
// context/ContentContext (this file's is `{ content: ...}`-shaped for the
// leaf component; ArtifactsScreen's own harness is `{ setContent }`-shaped)
// in the same module — vi.mock is file-scoped, not describe-scoped, so the
// two would collide. Deliberate placement, not a scope change: the test
// name/behaviour/AC-mapping is unchanged.
