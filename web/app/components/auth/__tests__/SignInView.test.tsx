// View tests for the v4 sign-in scene (design page 01). Node-env SSR via
// renderToStaticMarkup (no jsdom), asserting the key structural elements the
// design defines: serif heading with brand-green italic accent word, the
// "or continue with" divider, and the Google SSO button.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { SignInView, type SignInViewProps } from "../SignInView"

function noop() {}

function render(override: Partial<SignInViewProps> = {}): string {
  const defaults: SignInViewProps = {
    email: "",
    password: "",
    showPassword: false,
    submitting: false,
    error: null,
    forgotMode: false,
    lockoutMs: 0,
    termsHref: "/terms",
    privacyHref: "/privacy",
    onEmailChange: noop,
    onPasswordChange: noop,
    onToggleShowPassword: noop,
    onSubmit: noop,
    onGoogle: noop,
    onEnterForgot: noop,
    onExitForgot: noop,
  }
  return renderToStaticMarkup(<SignInView {...defaults} {...override} />)
}

describe("SignInView (v4)", () => {
  it("renders the v4 auth shell + card structure", () => {
    const html = render()
    expect(html).toContain("auth-shell")
    expect(html).toContain("auth-card")
    expect(html).toContain("auth-logo")
  })

  it("renders the serif heading with brand-green italic accent word", () => {
    const html = render()
    expect(html).toContain('class="auth-h"')
    expect(html).toContain("<em>back.</em>")
  })

  it("renders email + password fields", () => {
    const html = render()
    expect(html).toContain('id="email"')
    expect(html).toContain('id="password"')
  })

  it("renders the SSO divider and Google button", () => {
    const html = render()
    expect(html).toContain("or continue with")
    expect(html).toContain("sso-row")
    expect(html).toContain("Google")
  })

  it("offers forgot-password and create-account links", () => {
    const html = render()
    expect(html).toContain("Forgot?")
    expect(html).toContain("Create an account")
  })

  it("hides password field and SSO in forgot mode", () => {
    const html = render({ forgotMode: true })
    expect(html).not.toContain('id="password"')
    expect(html).not.toContain("or continue with")
    expect(html).toContain("Send reset link")
  })

  it("surfaces the lockout banner", () => {
    const html = render({ lockoutMs: 120000 })
    expect(html).toContain("Too many attempts")
  })
})
