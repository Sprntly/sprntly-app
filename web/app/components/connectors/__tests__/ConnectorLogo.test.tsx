// View tests for the shared connector logo tile.
// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ConnectorLogo, connectorLetter } from "../ConnectorLogo"
import type { ConnectorItemRow } from "../../../types/content"

const SLACK: ConnectorItemRow = {
  id: "slack",
  name: "Slack",
  logo: "S",
  logoText: "S",
  logoColor: "#4A154B",
  logoSvg: "/connectors/slack.svg",
  oauth: true,
}

const FIREFLIES: ConnectorItemRow = {
  id: "fireflies",
  name: "Fireflies",
  logo: "F",
  logoText: "F",
  logoColor: "#FFAD33",
  oauth: false,
  authType: "apikey",
}

function render(item: ConnectorItemRow, className = "logo"): string {
  return renderToStaticMarkup(
    React.createElement(ConnectorLogo, { item, className }),
  )
}

describe("ConnectorLogo", () => {
  it("renders the bundled SVG on a white tile when logoSvg is set", () => {
    const html = render(SLACK)
    expect(html).toContain('src="/connectors/slack.svg"')
    expect(html).toContain("background:#fff")
    // Brand-color letter sits behind as the load fallback.
    expect(html).toContain("color:#4A154B")
    expect(html).toContain(">S<")
    // Keeps the caller's CSS hook for sizing.
    expect(html).toContain('class="logo"')
  })

  it("falls back to the brand-color letter glyph when there is no logoSvg", () => {
    const html = render(FIREFLIES)
    expect(html).not.toContain("<img")
    expect(html).toContain("background:#FFAD33")
    expect(html).toContain(">F<")
  })

  it("connectorLetter prefers logoText, then logo, then the name initial", () => {
    expect(connectorLetter({ logoText: "S", logo: "x", name: "Slack" })).toBe("S")
    expect(connectorLetter({ logo: "G", name: "GitHub" })).toBe("G")
    expect(connectorLetter({ name: "Notion" })).toBe("N")
  })
})
