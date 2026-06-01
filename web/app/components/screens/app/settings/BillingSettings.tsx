"use client"

import { SettingsSection } from "./SettingsLayout"

/**
 * Billing pane — placeholder stub (commit B, 2026-06-01).
 *
 * Per SETTINGS_PAGE_PLAN.md §7 decision 3, this is a "Coming soon"
 * placeholder for the current Settings/Connectors slice. The real
 * Billing pane (plan grid, usage bars, payment method, invoice
 * history — see sprntly_Design-3/Sprntly.html lines 2336+) is a
 * separate follow-on slice that needs Stripe / billing-backend work.
 */
export function BillingSettings() {
  return (
    <SettingsSection
      title="Billing"
      sub="Payment method, usage, and invoices."
    >
      <p className="settings-placeholder">
        Billing isn&apos;t available yet in this build — plan management, usage
        metering, and invoice history are coming in a follow-on slice.
      </p>
    </SettingsSection>
  )
}
