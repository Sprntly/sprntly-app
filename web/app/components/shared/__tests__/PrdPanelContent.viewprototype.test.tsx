// @vitest-environment jsdom
//
// The PRD panel footer's prototype CTA is BACK: the weekly brief no longer
// offers "Generate prototype" on its finding cards, so the PRD panel footer is
// the canonical home for the affordance — mounted via the shared
// <GeneratePrototypeCTA> (existence check inside the hook decides
// "Generate Prototype" vs "View Prototype"; its behavior is covered by
// GeneratePrototypeCTA.test.tsx + useGeneratePrototype tests, so it is mocked
// here and we assert the WIRING: mounted in the footer, keyed to the open
// PRD). "Send to Claude Code" remains removed from this surface (it lives with
// the Tickets flow; SendToClaudeCode.dom.test.tsx covers the component).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const mocks = vi.hoisted(() => ({
  showToast: vi.fn(),
  setContent: vi.fn(),
}))

let content: Record<string, unknown>

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: mocks.showToast }),
}))

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content, setContent: mocks.setContent }),
}))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))

// The CTA composite (hook + GenerateModal + loading overlay) is exercised by
// its own suites — here it's a lightweight stand-in that records its prdId and
// drives the host's render prop, so these tests assert the panel's wiring.
vi.mock("../../design-agent/GeneratePrototypeCTA", () => ({
  GeneratePrototypeCTA: (props: {
    prdId: number | null
    render: (state: {
      label: string
      onClick: () => void
      disabled: boolean
      cta: string
      existing: null
    }) => React.ReactNode
  }) => (
    <div data-testid="prd-proto-cta-mount" data-prd-id={String(props.prdId)}>
      {props.render({
        label: "Generate Prototype",
        onClick: () => {},
        disabled: false,
        cta: "generate",
        existing: null,
      })}
    </div>
  ),
}))

vi.mock("../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    designAgentApi: {
      listPendingPatches: vi.fn(async () => []),
    },
    multiAgentApi: {
      getQaScenarios: vi.fn(async () => ({ doc: null })),
    },
    prdApi: {
      latest: vi.fn(async () => { throw new ApiError(404, "none") }),
      update: vi.fn(async () => ({})),
      listVersions: vi.fn(async () => []),
      listGenerations: vi.fn(async () => []),
    },
  }
})

import { PrdPanelContent } from "../PrdPanelContent"

const PRD = {
  prd_id: 42,
  title: "Retention PRD",
  metaLine: "From Brief",
  sections: [{ type: "p", text: "Improve retention." }],
  figma_file_key: "fig-file",
}

beforeEach(() => {
  vi.clearAllMocks()
  content = { prd: PRD, prdGenerating: false }
})

afterEach(cleanup)

describe("PrdPanelContent — footer prototype CTA", () => {
  it("mounts the shared prototype CTA in the bottom bar, wired to the open PRD", () => {
    const { container } = render(<PrdPanelContent />)
    const mount = screen.getByTestId("prd-proto-cta-mount")
    // Keyed to THIS PRD's id, not a hardcoded/latest one.
    expect(mount.getAttribute("data-prd-id")).toBe("42")
    // The host renders the trigger via the render prop, inside the bottom bar.
    const btn = screen.getByTestId("prd-footer-prototype-cta")
    expect(btn.textContent).toContain("Generate Prototype")
    expect(btn.closest(".prd-bottom-bar")).not.toBeNull()
    expect(container.querySelector(".prd-bottom-bar")).not.toBeNull()
  })

  it("renders no CTA at all when no PRD is loaded (empty panel)", () => {
    content = { prd: null, prdGenerating: false }
    render(<PrdPanelContent />)
    expect(screen.queryByTestId("prd-proto-cta-mount")).toBeNull()
    expect(screen.queryByTestId("prd-footer-prototype-cta")).toBeNull()
  })

  it("still renders no Send to Claude Code button in the PRD panel", () => {
    render(<PrdPanelContent />)
    expect(screen.queryByTestId("prd-send-claude")).toBeNull()
    expect(screen.queryByText(/Send to Claude Code/i)).toBeNull()
  })

  it("imports the canonical GeneratePrototypeCTA (never a hand-rolled copy) and still no Send to Claude Code", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/PrdPanelContent.tsx"),
      "utf8",
    )
    expect(src).toContain("GeneratePrototypeCTA")
    expect(src).not.toContain("SendToClaudeCode")
    expect(src).not.toContain("ViewPrototypeButton")
    expect(src).not.toMatch(/pid=/)
  })
})
