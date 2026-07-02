// @vitest-environment node
//
// renderInline underscore-emphasis fix: PRD bodies are full of identifiers with
// intra-word underscores (source tokens like `customer_voice`/`deal_blocker`,
// `pm_manual`, `signal_id`). The old pattern treated `_..._` as italic anywhere,
// so every `[Source: …]` citation rendered as broken italics. Underscore
// emphasis now fires only at word boundaries (GitHub-flavored markdown);
// asterisk emphasis is unaffected.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, it, expect } from "vitest"

// inline-md.tsx uses the classic JSX runtime (no `import React`); expose it
// globally so its <span>/<em>/<strong> render (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { renderInline } from "../inline-md"

const html = (s: string) =>
  renderToStaticMarkup(React.createElement(React.Fragment, null, renderInline(s)))

describe("renderInline — underscores only italicize at word boundaries", () => {
  it("does NOT italicize intra-word underscores in source-type tokens", () => {
    const out = html("[Source: customer_voice/deal_blocker] Orchard Labs churned")
    expect(out).not.toContain("<em>")
    expect(out).toContain("customer_voice/deal_blocker")
  })

  it("leaves signal_id / pm_manual style identifiers literal", () => {
    expect(html("signal_id 642141b5 · pm_manual/bug · revenue/deal_blocker"))
      .not.toContain("<em>")
  })

  it("STILL italicizes underscore emphasis at word boundaries", () => {
    // renderInline wraps inner content in <span>, so <em><span>…</span></em>.
    expect(html("this is _important_ now")).toContain("<em><span>important</span></em>")
  })

  it("still renders **bold** and *italic* via asterisks", () => {
    const out = html("**big** and *small*")
    expect(out).toContain("<strong><span>big</span></strong>")
    expect(out).toContain("<em><span>small</span></em>")
  })

  it("still supports __bold__ at word boundaries", () => {
    expect(html("a __strong__ point")).toContain("<strong><span>strong</span></strong>")
  })
})
