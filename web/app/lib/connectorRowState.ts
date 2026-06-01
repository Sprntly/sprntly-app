/**
 * Per-row derivation for the Settings → Connectors grid (commit D).
 *
 * Takes a static catalog row plus the live connection record (if any)
 * and returns what the UI should display: status pill, action label,
 * and a one-line stats string under the connector name.
 *
 * No React, no IO — pure function with full unit-test coverage.
 */
import type { ConnectionSummary } from "./api"
import type { ConnectorItemRow } from "../types/content"

export type ConnectorRowStatus = "active" | "off"
export type ConnectorRowAction = "Configure" | "Connect" | "Coming soon"

export type ConnectorRowState = {
  status: ConnectorRowStatus
  actionLabel: ConnectorRowAction
  /** False renders the action link disabled. */
  canClick: boolean
  /**
   * Short description shown under the connector name. Today it's just
   * the account label or a fallback; when per-connector telemetry lands
   * in a follow-on slice this is where the rich strings go ("12,847
   * events / day · last sync 2 min ago").
   */
  statsString: string
}

export function getConnectorRowState(
  item: ConnectorItemRow,
  connection: ConnectionSummary | null,
): ConnectorRowState {
  const isActive = connection?.status === "active"

  if (isActive) {
    const label =
      connection.account_label ?? connection.google_email ?? null
    return {
      status: "active",
      actionLabel: "Configure",
      canClick: true,
      statsString: label && label.trim() ? label : "Connected",
    }
  }

  // Not connected (or connection in a non-active state like "error").
  if (item.oauth) {
    return {
      status: "off",
      actionLabel: "Connect",
      canClick: true,
      statsString: "Not connected",
    }
  }

  return {
    status: "off",
    actionLabel: "Coming soon",
    canClick: false,
    statsString: "Not connected",
  }
}
