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
    confirmPassword: "",
    showPassword: false,
    submitting: false,
    googleSubmitting: false,
    error: null,
    termsHref: "/terms",
    privacyHref: "/privacy",
    onEmailChange: noop,
    onPasswordChange: noop,
    onConfirmPasswordChange: noop,
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

  it("has no account-type cards (the company/personal split is retired in v6)", () => {
    const html = renderStep1()
    expect(html).not.toContain("For a company")
    expect(html).not.toContain("For personal use")
    expect(html).not.toContain("auth-acct-card")
  })

  it("labels the email field plainly", () => {
    const html = renderStep1()
    expect(html).toContain("Email")
    expect(html).not.toContain("Work email")
  })

  it("test_sign_up_step1_view_mounts_share_context_strip_when_present", () => {
    const html = renderStep1({
      shareContext: { title: "Q3 Retention PRD", sharerName: "Priya Shah", requiredDomain: "acme.com" },
    })
    expect(html).toContain("share-context-strip")
    expect(html).toContain("Priya Shah")
    expect(html).toContain("acme.com")
  })

  it("test_sign_up_step1_view_unchanged_without_share_context", () => {
    const html = renderStep1()
    expect(html).not.toContain("share-context-strip")
    expect(html).not.toContain("sign-up-domain-hint")
  })

  it("test_sign_up_step1_create_button_shows_busy_state_while_checking", () => {
    const html = renderStep1({ submitting: true })
    expect(html).toContain("Checking…")
    expect(html).toContain("auth-btn-spin")
    expect(html).toContain('aria-busy="true"')
    // Disabled so a second click can't fire another availability check.
    expect(html).toContain("disabled")
  })

  it("test_sign_up_step1_create_button_is_idle_by_default", () => {
    const html = renderStep1()
    expect(html).toContain("Create account")
    expect(html).not.toContain("auth-btn-spin")
    expect(html).not.toContain("Checking…")
  })

  it("test_sign_up_step1_google_button_stays_busy_through_the_redirect", () => {
    const html = renderStep1({ googleSubmitting: true })
    expect(html).toContain("Redirecting…")
    expect(html).toContain("auth-btn-spin")
    expect(html).not.toContain("Sign up with Google")
  })
})

describe("SignUpStep2View (v4 page 03 — about you)", () => {
  it("shows the '2 of 2' step indicator", () => {
    expect(renderStep2()).toContain("2 of 2")
  })

  it("renders the account-created welcome banner with the email", () => {
    const html = renderStep2()
    expect(html).toContain("welcome-banner")
    expect(html).toContain("Account created")
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

  it("test_sign_up_step2_continue_button_shows_spinner_while_creating", () => {
    const html = renderStep2({ submitting: true })
    expect(html).toContain("Creating account…")
    expect(html).toContain("auth-btn-spin")
    expect(html).toContain('aria-busy="true"')
    expect(html).not.toContain(">Continue")
  })

  it("test_sign_up_step2_continue_button_is_idle_by_default", () => {
    const html = renderStep2()
    expect(html).toContain("Continue")
    expect(html).not.toContain("auth-btn-spin")
    expect(html).not.toContain('aria-busy="true"')
  })

  it("no longer renders a priorities textarea (v7 — about-you is name + role only)", () => {
    const html = renderStep2()
    expect(html).not.toContain('id="priorities"')
    expect(html).not.toContain("Your priorities")
    // The three fields the step DOES own are still there.
    expect(html).toContain('id="firstName"')
    expect(html).toContain('id="lastName"')
    expect(html).toContain('id="role"')
  })
})
