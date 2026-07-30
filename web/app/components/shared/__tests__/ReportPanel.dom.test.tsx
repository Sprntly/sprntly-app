// @vitest-environment jsdom
//
// Tests for the report viewer drawer (`ReportPanel`) — the surface a captured
// report opens into from the Artifacts list. Presentational, so no network stub
// is needed: the document is passed in.
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ReportPanel } from "../ReportPanel"
import type { ReportDoc } from "../../../lib/api"

const DOC: ReportDoc = {
  id: 4,
  skill: "voice-of-customer-report",
  title: "Voice of Customer Report · Q2",
  question: "what are customers saying?",
  html: "<!DOCTYPE html><html><body><h1>VoC</h1></body></html>",
  created_at: new Date().toISOString(),
  conversation_id: 77,
  prd_id: null,
  share_mode: "private",
  share_token: null,
}

const noop = () => {}

function panel(props: Partial<React.ComponentProps<typeof ReportPanel>> = {}) {
  return render(
    React.createElement(ReportPanel, {
      report: DOC, loading: false, onClose: noop, ...props,
    }),
  )
}

afterEach(cleanup)

describe("ReportPanel", () => {
  it("renders nothing when there is no report and nothing loading", () => {
    const { container } = panel({ report: null, loading: false })
    expect(container.querySelector('[data-testid="report-panel"]')).toBeNull()
  })

  it("opens in a loading state before the document arrives", () => {
    const { container } = panel({ report: null, loading: true })
    // The drawer is present immediately — the click is never silent.
    expect(container.querySelector('[data-testid="report-panel"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="report-panel-loading"]')).not.toBeNull()
    expect(container.querySelector("iframe")).toBeNull()
  })

  it("renders the document in a sandboxed iframe once loaded", () => {
    const { container } = panel()
    const frame = container.querySelector("iframe") as HTMLIFrameElement
    expect(frame).not.toBeNull()
    expect(frame.getAttribute("srcdoc")).toContain("<h1>VoC</h1>")
    // Scripts must never run in a report document.
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin")
    expect(container.querySelector('[data-testid="report-panel-loading"]')).toBeNull()
  })

  it("shows the report title and its humanised kind", () => {
    const { container } = panel()
    const head = container.querySelector('[data-testid="report-panel-title"]')
    expect(head?.textContent).toBe("Voice of Customer Report · Q2")
    expect(container.textContent).toContain("Voice of Customer report")
  })

  it("names what the report is attached to", () => {
    const { container } = panel({
      attachment: { conversationTitle: "Q2 customer themes", prdTitle: "Checkout revamp" },
    })
    const line = container.querySelector('[data-testid="report-panel-attachment"]')
    expect(line?.textContent).toBe("from Q2 customer themes · on PRD Checkout revamp")
  })

  it("omits the attachment line when the report stands alone", () => {
    const { container } = panel({ attachment: {} })
    expect(container.querySelector('[data-testid="report-panel-attachment"]')).toBeNull()
  })

  it("omits the attachment line when the chat was deleted (id but no title)", () => {
    // The viewer never fabricates a label for a resolved-to-nothing attachment.
    const { container } = panel({ attachment: { conversationTitle: null, prdTitle: null } })
    expect(container.querySelector('[data-testid="report-panel-attachment"]')).toBeNull()
  })

  it("closes from the header button and the overlay", () => {
    const onClose = vi.fn()
    const { container } = panel({ onClose })
    fireEvent.click(container.querySelector(".cpanel-close") as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(container.querySelector(".cpanel-overlay") as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
