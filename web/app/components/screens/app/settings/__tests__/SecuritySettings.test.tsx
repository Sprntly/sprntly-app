// View tests for the Security pane (C3 — adds the password-change form).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { SecuritySettingsView } from "../SecuritySettings"

function noop() {}

function render(
  override: Partial<React.ComponentProps<typeof SecuritySettingsView>> = {},
): string {
  return renderToStaticMarkup(
    <SecuritySettingsView
      newPassword=""
      confirmPassword=""
      saving={false}
      error={null}
      message={null}
      onNewPasswordChange={noop}
      onConfirmPasswordChange={noop}
      onSubmit={noop}
      {...override}
    />,
  )
}

describe("SecuritySettingsView", () => {
  it("renders a Change password section with both fields and a submit button", () => {
    const html = render()
    expect(html).toMatch(/change password|password/i)
    expect(html).toMatch(/new password/i)
    expect(html).toMatch(/confirm/i)
    expect(html).toMatch(/<button[^>]*type="submit"/)
  })

  it("disables the submit button when no new password is entered", () => {
    const html = render({ newPassword: "" })
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("enables the submit button when a new password is entered", () => {
    const html = render({ newPassword: "Hunter22!" })
    expect(html).not.toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  })

  it("surfaces an inline error", () => {
    const html = render({ error: "Password too short." })
    expect(html).toContain("Password too short.")
  })

  it("surfaces a success message", () => {
    const html = render({ message: "Password updated successfully." })
    expect(html).toContain("Password updated successfully.")
  })
})
