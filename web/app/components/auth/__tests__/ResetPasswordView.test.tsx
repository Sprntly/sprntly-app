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

  it("shows a success state with a link to the app when mode is 'done'", () => {
    const html = render({ mode: "done" })
    expect(html).toMatch(/password (was |is )?updated|new password is set/i)
    expect(html).toMatch(/href="\/?"/)
  })

  it("shows a 'no active recovery session' state when mode is 'no-session'", () => {
    const html = render({ mode: "no-session" })
    expect(html).toMatch(/expired|invalid|sign in again/i)
    expect(html).toMatch(/href="\/sign-in"/)
  })

  it("disables the submit button while submitting", () => {
    const html = render({ submitting: true })
    expect(html).toMatch(/<button[^>]*disabled[^>]*type="submit"|<button[^>]*type="submit"[^>]*disabled/)
  })
})
