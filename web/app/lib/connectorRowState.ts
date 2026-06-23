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
   * True when an active connection's stored token has failed the scheduled
   * health probe (connection.health === "disconnected"). The row stays
   * "active" (it's still configured) but the UI flags it so the user knows
   * to reconnect without opening the drawer. Always false for off rows.
   */
  disconnected: boolean
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
    // The scheduled connector health monitor flips this to "disconnected" when
    // the stored token stops validating. The connection is still configured
    // (status stays "active"), so we keep the row Active+Configure but flag it
    // so the list shows "Disconnected — reconnect" without opening the drawer.
    const isDisconnected = connection.health === "disconnected"
    if (isDisconnected) {
      return {
        status: "active",
        actionLabel: "Configure",
        canClick: true,
        disconnected: true,
        statsString: "Disconnected — reconnect",
      }
    }
    const label =
      connection.account_label ?? connection.google_email ?? null
    return {
      status: "active",
      actionLabel: "Configure",
      canClick: true,
      disconnected: false,
      statsString: label && label.trim() ? label : "Connected",
    }
  }

  // Not connected (or connection in a non-active state like "error").
  // "Connect" is clickable if EITHER an OAuth backend exists OR the
  // provider uses API-key auth (commit J — Fireflies). Both surface as
  // "Connect" in the UI; the click handler in ConnectorsSettings picks
  // the right flow (OAuth redirect vs API-key modal) based on authType.
  if (item.oauth || item.authType === "apikey") {
    return {
      status: "off",
      actionLabel: "Connect",
      canClick: true,
      disconnected: false,
      statsString: "Not connected",
    }
  }

  return {
    status: "off",
    actionLabel: "Coming soon",
    canClick: false,
    disconnected: false,
    statsString: "Not connected",
  }
}
