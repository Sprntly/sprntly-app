// View tests for the v4 sign-up scenes (design pages 02 + 03). Node-env SSR.
// Asserts the step indicator ("1 of 2" / "2 of 2"), password strength meter on
// step 1, and the name + role fields on the about-you step.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  SignUpStep1View,
  SignUpStep2View,
  V4_ROLES,
  type SignUpStep1ViewProps,
  type SignUpStep2ViewProps,
} from "../SignUpView"

function noop() {}

function renderStep1(override: Partial<SignUpStep1ViewProps> = {}): string {
  const defaults: SignUpStep1ViewProps = {
    email: "",
    password: "",
    showPassword: false,
    error: null,
    termsHref: "/terms",
    privacyHref: "/privacy",
    onEmailChange: noop,
    onPasswordChange: noop,
    onToggleShowPassword: noop,
    onSubmit: noop,
    onGoogle: noop,
  }
  return renderToStaticMarkup(<SignUpStep1View {...defaults} {...override} />)
}

function renderStep2(override: Partial<SignUpStep2ViewProps> = {}): string {
  const defaults: SignUpStep2ViewProps = {
    email: "sarah@meridian.health",
    firstName: "",
    lastName: "",
    role: "Product Manager",
    submitting: false,
    error: null,
    onFirstNameChange: noop,
    onLastNameChange: noop,
    onRoleChange: noop,
    onSubmit: noop,
    onBack: noop,
  }
  return renderToStaticMarkup(<SignUpStep2View {...defaults} {...override} />)
}

describe("SignUpStep1View (v4 page 02)", () => {
  it("shows the '1 of 2' step indicator", () => {
    expect(renderStep1()).toContain("1 of 2")
  })

  it("renders serif heading with italic accent word", () => {
    const html = renderStep1()
    expect(html).toContain('class="auth-h"')
    expect(html).toContain("<em>account.</em>")
  })

  it("renders the password strength meter when a password is present", () => {
    const html = renderStep1({ password: "Abcdef1!ghij" })
    expect(html).toContain("pwd-strength")
    expect(html).toContain("pwd-bar")
  })

  it("omits the strength meter for an empty password", () => {
    expect(renderStep1({ password: "" })).not.toContain("pwd-strength")
  })

  it("renders the terms line and Google SSO", () => {
    const html = renderStep1()
    expect(html).toContain("Terms")
    expect(html).toContain("Privacy Policy")
    expect(html).toContain("Sign up with Google")
  })
})

describe("SignUpStep2View (v4 page 03 — about you)", () => {
  it("shows the '2 of 2' step indicator", () => {
    expect(renderStep2()).toContain("2 of 2")
  })

  it("renders the account-created welcome banner with the email", () => {
    const html = renderStep2()
    expect(html).toContain("welcome-banner")
    expect(html).toContain("sarah@meridian.health")
  })

  it("renders first/last name fields and a role select", () => {
    const html = renderStep2()
    expect(html).toContain('id="firstName"')
    expect(html).toContain('id="lastName"')
    expect(html).toContain("auth-role-select")
  })

  it("renders every v4 role option", () => {
    const html = renderStep2()
    for (const role of V4_ROLES) {
      expect(html).toContain(role)
    }
  })

  it("renders the 'Who are you?' serif heading", () => {
    expect(renderStep2()).toContain("<em>you?</em>")
  })
})
