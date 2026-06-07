// View tests for the v4 verify-email scene (design page 04). Node-env SSR.
// Asserts the verify icon treatment, the email shown in mono, the
// "I've verified — continue" CTA, and the resend button with its cooldown.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { VerifyEmailView, type VerifyEmailViewProps } from "../VerifyEmailView"

function noop() {}

function render(override: Partial<VerifyEmailViewProps> = {}): string {
  const defaults: VerifyEmailViewProps = {
    email: "sarah@meridian.health",
    message: null,
    resendCooldown: 0,
    canResend: true,
    onContinue: noop,
    onResend: noop,
  }
  return renderToStaticMarkup(<VerifyEmailView {...defaults} {...override} />)
}

describe("VerifyEmailView (v4 page 04)", () => {
  it("renders the verify icon treatment", () => {
    expect(render()).toContain("verify-icon")
  })

  it("renders the serif heading with italic accent word", () => {
    const html = render()
    expect(html).toContain('class="auth-h"')
    expect(html).toContain("<em>inbox.</em>")
  })

  it("renders the email in the mono pill", () => {
    const html = render()
    expect(html).toContain("verify-email")
    expect(html).toContain("sarah@meridian.health")
  })

  it("renders the continue CTA", () => {
    expect(render()).toContain("I&#x27;ve verified — continue")
  })

  it("renders the resend button", () => {
    expect(render()).toContain("Resend email")
  })

  it("shows the cooldown countdown on the resend button", () => {
    const html = render({ resendCooldown: 42, canResend: false })
    expect(html).toContain("(42s)")
    expect(html).toContain("disabled")
  })

  it("renders the spam note", () => {
    expect(render()).toContain("spam-note")
  })

  it("surfaces a status message when present", () => {
    expect(render({ message: "Verification email sent." })).toContain(
      "Verification email sent.",
    )
  })
})
