"use client"

import { SettingsSection } from "./SettingsLayout"

/**
 * Security pane — placeholder stub (commit B, 2026-06-01).
 *
 * The sprntly_Design-3 sidebar lists "Security" under Account but does
 * not render a pane for it. We surface a placeholder so the nav entry
 * doesn't dead-end. Real content (active sessions, MFA, SSO, audit log)
 * is a separate follow-on slice.
 */
export function SecuritySettings() {
  return (
    <SettingsSection
      title="Security"
      sub="Active sessions, MFA, and account safety."
    >
      <p className="settings-placeholder">
        Security controls aren&apos;t available yet in this build — MFA, active
        sessions, and SSO configuration are coming in a follow-on slice.
      </p>
    </SettingsSection>
  )
}
