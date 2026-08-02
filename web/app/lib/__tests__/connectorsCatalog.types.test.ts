// Connector TYPES: every catalog entry is classified, labels exist for every
// type in use, and the ticket-sync tracker list derives from types ∩
// backend-implemented — the sync button follows the catalog instead of
// hardcoded provider ids. Mirrors backend/app/connectors/catalog.py.
// Cardinality is a product decision (2026-07-30): connectors may carry
// MULTIPLE types when the tool genuinely is more than one thing (Slack is
// communication + customer-voice); every entry carries at least one.
import { describe, expect, it } from "vitest"

import {
  CONNECTOR_CATALOG,
  CONNECTOR_TYPE_LABELS,
  connectorTypes,
  connectorsWithType,
  ticketSyncTrackers,
} from "../connectorsCatalog"

describe("connector types", () => {
  it("every catalog connector carries at least one type", () => {
    for (const item of CONNECTOR_CATALOG.flatMap((c) => c.items)) {
      expect(
        item.types?.length ?? 0,
        `${item.id} must carry at least 1 type`,
      ).toBeGreaterThanOrEqual(1)
    }
  })

  it("every type in use has a human label", () => {
    for (const item of CONNECTOR_CATALOG.flatMap((c) => c.items)) {
      for (const t of item.types ?? []) {
        expect(CONNECTOR_TYPE_LABELS[t], `${item.id}: unlabeled type ${t}`).toBeTruthy()
      }
    }
    expect(CONNECTOR_TYPE_LABELS["task-management"]).toBe("Task management")
  })

  it("classifies task-management tools vs communication tools", () => {
    expect(connectorTypes("clickup")).toEqual(["task-management"])
    expect(connectorTypes("jira")).toEqual(["task-management"])
    expect(connectorTypes("hubspot")).toEqual(["crm"])
    expect(connectorTypes("unknown-tool")).toEqual([])
    const trackerIds = connectorsWithType("task-management").map((i) => i.id)
    expect(trackerIds).toEqual(expect.arrayContaining(["jira", "clickup", "linear", "asana"]))
    expect(trackerIds).not.toContain("slack")
  })

  it("Slack is dual-typed communication + customer-voice (multi-type)", () => {
    expect(connectorTypes("slack")).toEqual(["communication", "customer-voice"])
    // It answers to BOTH type queries, and appears ONCE in each despite
    // rendering a card on two category shelves.
    const commsIds = connectorsWithType("communication").map((i) => i.id)
    const voiceIds = connectorsWithType("customer-voice").map((i) => i.id)
    expect(commsIds.filter((id) => id === "slack")).toEqual(["slack"])
    expect(voiceIds.filter((id) => id === "slack")).toEqual(["slack"])
  })

  it("ticketSyncTrackers = task-management type ∩ backend-implemented", () => {
    const trackers = ticketSyncTrackers()
    expect(trackers).toEqual([
      { id: "jira", label: "Jira" },
      { id: "clickup", label: "ClickUp" },
      { id: "asana", label: "Asana" },
    ])
    // Linear is a task-management tool by TYPE but not sync-implemented yet —
    // it must not reach the sync button until the backend engine supports it.
    expect(trackers.map((t) => t.id)).not.toContain("linear")
  })
})
