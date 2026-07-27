// @vitest-environment jsdom
//
// Public-feedback report rows in the Artifacts list: badge/icon/source line,
// the Reports filter chip, and row click → onOpen. Mirrors ArtifactsView.test.tsx.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ArtifactsView } from "../ArtifactsScreen"
import type { ArtifactItem } from "../../../../lib/api"

const REPORT: ArtifactItem = {
  type: "report",
  id: 7,
  title: "Public feedback · July 2026",
  status: "ready",
  created_at: new Date().toISOString(),
  source: { question: "what are people saying about us online?" },
  open: { report_id: 7 },
}
const PRD: ArtifactItem = {
  type: "prd",
  id: 1,
  title: "Handoff Threshold PRD",
  status: "ready",
  created_at: new Date().toISOString(),
  source: { brief_id: 10, week_label: "Week of May 20", insight_index: 0 },
  open: { brief_id: 10, insight_index: 0, prd_id: 1 },
}

const noop = () => {}
type Props = React.ComponentProps<typeof ArtifactsView>

function markup(override: Partial<Props> = {}): string {
  const defaults: Props = {
    items: [REPORT, PRD],
    filter: "all",
    loading: false,
    onFilterChange: noop,
    onOpen: noop,
  }
  return renderToStaticMarkup(<ArtifactsView {...defaults} {...override} />)
}

afterEach(cleanup)

describe("ArtifactsView — public-feedback report rows", () => {
  it("renders the report row with badge, title, and chat source line", () => {
    const html = markup()
    expect(html).toContain("Public feedback · July 2026")
    expect(html).toContain("REPORT")
    expect(html).toContain("from chat")
    expect(html).toContain('data-artifact-type="report"')
  })

  it("offers a Reports filter chip that narrows to reports only", () => {
    expect(markup()).toContain('data-filter="report"')
    const filtered = markup({ filter: "report" })
    expect(filtered).toContain("Public feedback · July 2026")
    expect(filtered).not.toContain("Handoff Threshold PRD")
  })

  it("row click calls onOpen with the report item", () => {
    const onOpen = vi.fn()
    const { container } = render(
      <ArtifactsView items={[REPORT]} filter="all" loading={false}
                     onFilterChange={noop} onOpen={onOpen} />,
    )
    const row = container.querySelector('[data-artifact-type="report"]') as HTMLElement
    fireEvent.click(row)
    expect(onOpen).toHaveBeenCalledWith(REPORT)
  })
})
