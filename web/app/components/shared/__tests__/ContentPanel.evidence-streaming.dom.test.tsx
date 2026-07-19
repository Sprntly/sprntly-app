// @vitest-environment jsdom
//
// Live streaming preview in the Evidence tab — mirrors
// PrdPanelContent.streaming.dom.test.tsx: while evidence generation is in
// flight AND partial HTML has arrived (content.evidencePartialHtml), the tab
// renders the growing document in a sandboxed read-only iframe with a slim
// "Generating…" indicator — not the full-pane skeleton. With no partial yet,
// the skeleton still shows; once generation is over a stale partial must not
// resurrect the preview.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// ContentPanel has module-level JSX (the TABS array), so global React must exist
// before the import below evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

// The real PrdPanelContent fetches the latest PRD on mount — stub it so the
// Evidence tab under test stays hermetic.
vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: "evidence",
    openContentPanel: vi.fn(),
    closeContentPanel: vi.fn(),
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { ContentPanel } from "../ContentPanel"

function renderWith(content: Record<string, unknown>) {
  contentMock.value = {
    prd: null,
    prdMeta: null,
    detail: null,
    evidence: null,
    evidenceGenerating: false,
    evidencePartialHtml: null,
    ...content,
  }
  return render(React.createElement(ContentPanel))
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ContentPanel Evidence tab — live streaming preview", () => {
  it("renders the partial HTML in a sandboxed iframe with the slim indicator (no skeleton)", async () => {
    renderWith({
      evidenceGenerating: true,
      evidencePartialHtml: "<!doctype html><html><body><h1>Draft evidence</h1>",
    })
    await waitFor(() => {})

    // Slim pulsing indicator, not the big "Generating evidence…" skeleton.
    expect(screen.getByTestId("evidence-streaming")).toBeTruthy()
    expect(screen.queryByText("Generating evidence…")).toBeNull()

    // The preview iframe is sandboxed (no script execution) and read-only.
    const iframe = screen.getByTestId("evidence-streaming-preview") as HTMLIFrameElement
    expect(iframe.getAttribute("sandbox")).toBe("allow-same-origin")
  })

  it("keeps the full-pane skeleton while generating with no partial yet", async () => {
    renderWith({ evidenceGenerating: true, evidencePartialHtml: null })
    await waitFor(() => {})

    expect(screen.getByText("Generating evidence…")).toBeTruthy()
    expect(screen.queryByTestId("evidence-streaming")).toBeNull()
  })

  it("ignores a stale partial once generation is over (no preview)", async () => {
    // Terminal paths clear evidencePartialHtml, but even a missed clear must
    // not resurrect the preview once evidenceGenerating is off.
    renderWith({ evidenceGenerating: false, evidencePartialHtml: "<!doctype html><p>old</p>" })
    await waitFor(() => {})

    expect(screen.queryByTestId("evidence-streaming")).toBeNull()
    expect(screen.queryByTestId("evidence-streaming-preview")).toBeNull()
  })

  it("renders the finished evidence document once it lands (preview gone)", async () => {
    renderWith({
      evidenceGenerating: false,
      evidencePartialHtml: null,
      evidence: { title: "E", metaLine: "", sections: [], html: "<!doctype html><h1>Final</h1>" },
    })
    await waitFor(() => {})

    expect(screen.queryByTestId("evidence-streaming")).toBeNull()
    // The v3 HTML brief iframe renders the authoritative document.
    expect(screen.getByTitle("Evidence brief")).toBeTruthy()
  })
})
