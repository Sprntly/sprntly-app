/**
 * Per-row derivation for the generate-prototype modal's design-source rows.
 *
 * The modal renders a compact "connected vs not" row for each provider
 * (Figma, GitHub) from the live connection list (`connectorsApi.list()`). It
 * only has provider strings + the live `ConnectionSummary`, so it does NOT use
 * the Settings-grid row helper (which needs a static catalog row and returns a
 * 3-action shape). This is the small 2-state mapping that backs those rows.
 *
 * No React, no IO — pure function, node-env unit-testable.
 */
import type { ConnectionSummary } from "./api"

export type GenerateConnectorRowState = {
  /** True only when the connection is present and active. */
  connected: boolean
  /** The account label to show next to "Connected", when present + non-blank. */
  accountLabel: string | null
}

export function getGenerateConnectorRowState(
  connection: ConnectionSummary | undefined,
): GenerateConnectorRowState {
  const connected = connection?.status === "active"
  const label = connection?.account_label
  return {
    connected,
    accountLabel: connected && label && label.trim() ? label : null,
  }
}
