// View tests for the reset-password scene. SSR via renderToStaticMarkup
// (no jsdom), per project convention.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ResetPasswordView, type ResetPasswordViewProps } from "../ResetPasswordView"

function noop() {}

function render(override: Partial<ResetPasswordViewProps> = {}): string {
  const defaults: ResetPasswordViewProps = {
    mode: "form",
    newPassword: "",
    confirmPassword: "",
    showPassword: false,
    submitting: false,
    error: null,
    onNewPasswordChange: noop,
    onConfirmPasswordChange: noop,
    onToggleShowPassword: noop,
    onSubmit: noop,
  }
  return renderToStaticMarkup(<ResetPasswordView {...defaults} {...override} />)
}

describe("ResetPasswordView", () => {
  it("renders the auth shell + card structure", () => {
    const html = render()
    expect(html).toContain("auth-shell")
    expect(html).toContain("auth-card")
  })

  it("renders both password fields and a submit button", () => {
    const html = render()
    expect(html).toContain('id="new-password"')
    expect(html).toContain('id="confirm-password"')
    expect(html).toMatch(/<button[^>]*type="submit"/)
  })

  it("surfaces an inline error when error is set", () => {
    const html = render({ error: "Passwords do not match." })
    expect(html).toContain("Passwords do not match.")
  })

  it("reports the reset and heads for the app when mode is 'done'", () => {
    // `done` is not a stop any more: the password is saved, the session is
    // live, and the page is already navigating. It says so, keeps a link as
    // the escape from a stuck redirect, and never asks anyone to sign in.
    const html = render({ mode: "done" })
    expect(html).toMatch(/password (was |is )?(updated|saved)|new password is set/i)
    expect(html).toMatch(/taking you to sprntly/i)
    expect(html).toMatch(/href="\/?"/)
    expect(html).not.toMatch(/sign in/i)
  })

  it("shows a 'no active recovery session' state when mode is 'no-session'", () => {
    const html = render({ mode: "no-session" })
    expect(html).toMatch(/expired|invalid|sign in again/i)
    expect(html).toMatch(/href="\/sign-in"/)
  })

  describe("code mode", () => {
    const codeProps: Partial<ResetPasswordViewProps> = {
      mode: "code",
      email: "sarah@meridian.health",
      code: "",
      resendCooldown: 0,
      canResend: true,
      onCodeChange: noop,
      onCodeSubmit: noop,
      onResend: noop,
    }

    it("renders six code boxes and the address the code went to", () => {
      const html = render(codeProps)
      expect((html.match(/class="otp-box"/g) ?? []).length).toBe(6)
      expect(html).toContain("sarah@meridian.health")
    })

    it("asks for a code rather than a link", () => {
      const html = render(codeProps)
      expect(html).toMatch(/6-digit/i)
      expect(html).not.toMatch(/reset link/i)
    })

    it("gates the verify submit until all six digits are entered", () => {
      const disabledSubmit =
        /<button[^>]*disabled[^>]*type="submit"|<button[^>]*type="submit"[^>]*disabled/
      expect(render({ ...codeProps, code: "4839" })).toMatch(disabledSubmit)
      expect(render({ ...codeProps, code: "483920" })).not.toMatch(disabledSubmit)
    })

    it("offers a resend with its cooldown", () => {
      expect(render(codeProps)).toContain("Resend code")
      const html = render({ ...codeProps, resendCooldown: 42, canResend: false })
      expect(html).toContain("(42s)")
    })

    it("flags the boxes invalid when the code was rejected", () => {
      const html = render({ ...codeProps, error: "That code isn't right." })
      expect(html).toContain("otp-row-invalid")
      expect(html).toContain('role="alert"')
    })
  })

  it("disables the submit button while submitting", () => {
    const html = render({ submitting: true })
    expect(html).toMatch(/<button[^>]*disabled[^>]*type="submit"|<button[^>]*type="submit"[^>]*disabled/)
  })
})
