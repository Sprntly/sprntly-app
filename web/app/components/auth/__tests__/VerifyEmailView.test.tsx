// View tests for the v4 verify-email scene (design page 04). Node-env SSR.
// Signup confirmation is a typed 6-digit code, so this asserts the code boxes,
// the verify submit and its length gate, and the resend button with its
// cooldown — plus that no emailed link is offered any more.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { VerifyEmailView, type VerifyEmailViewProps } from "../VerifyEmailView"

function noop() {}

function render(override: Partial<VerifyEmailViewProps> = {}): string {
  const defaults: VerifyEmailViewProps = {
    email: "sarah@meridian.health",
    code: "",
    message: null,
    error: null,
    submitting: false,
    resendCooldown: 0,
    canResend: true,
    onCodeChange: noop,
    onSubmit: noop,
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

  it("renders six code boxes", () => {
    const html = render()
    expect(html).toContain("otp-row")
    expect((html.match(/class="otp-box"/g) ?? []).length).toBe(6)
  })

  it("asks for a code rather than a link", () => {
    const html = render()
    expect(html).toMatch(/6-digit/i)
    expect(html).not.toMatch(/verification link/i)
  })

  it("renders the verify submit CTA", () => {
    expect(render()).toContain("Verify email")
  })

  it("gates the submit until all six digits are entered", () => {
    expect(render({ code: "4839" })).toMatch(/<button[^>]*disabled[^>]*type="submit"|<button[^>]*type="submit"[^>]*disabled/)
    expect(render({ code: "483920" })).not.toMatch(
      /<button[^>]*disabled[^>]*type="submit"|<button[^>]*type="submit"[^>]*disabled/,
    )
  })

  it("shows the submitting label while verifying", () => {
    expect(render({ code: "483920", submitting: true })).toContain("Verifying…")
  })

  it("renders the resend button", () => {
    expect(render()).toContain("Resend code")
  })

  it("shows the cooldown countdown on the resend button", () => {
    const html = render({ resendCooldown: 42, canResend: false })
    expect(html).toContain("(42s)")
    expect(html).toContain("disabled")
  })

  it("renders the spam note with the code expiry", () => {
    const html = render()
    expect(html).toContain("spam-note")
    expect(html).toMatch(/Code expires in 1 hour/i)
  })

  it("surfaces a status message when present", () => {
    expect(render({ message: "New code sent." })).toContain("New code sent.")
  })

  it("surfaces a rejected code as an alert and flags the boxes invalid", () => {
    const html = render({ error: "That code isn't right." })
    expect(html).toContain('role="alert"')
    expect(html).toContain("That code isn&#x27;t right.")
    expect(html).toContain("otp-row-invalid")
  })

  it("mounts the share-context strip when shareContext is present", () => {
    const html = render({
      shareContext: { title: "Q3 Retention PRD", sharerName: "Priya Shah" },
    })
    expect(html).toContain('data-testid="share-context-strip"')
    expect(html).toContain("Priya Shah")
  })

  it("renders unchanged (no strip) when shareContext is absent", () => {
    const html = render()
    expect(html).not.toContain('data-testid="share-context-strip"')
  })
})
