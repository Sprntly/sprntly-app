// Connector TYPES: every catalog entry is classified, labels exist for every
// type in use, and the ticket-sync tracker list derives from types ∩
// backend-implemented — the sync button follows the catalog instead of
// hardcoded provider ids. Mirrors backend/app/connectors/catalog.py.
// Cardinality is a product decision: exactly ONE type per connector for now,
// kept list-shaped so multi-type support later is a data change only.
import { describe, expect, it } from "vitest"

import {
  CONNECTOR_CATALOG,
  CONNECTOR_TYPE_LABELS,
  connectorTypes,
  connectorsWithType,
  ticketSyncTrackers,
} from "../connectorsCatalog"

describe("connector types", () => {
  it("every catalog connector carries exactly one type (for now)", () => {
    for (const item of CONNECTOR_CATALOG.flatMap((c) => c.items)) {
      expect(item.types?.length, `${item.id} must have exactly 1 type`).toBe(1)
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
    expect(connectorTypes("slack")).toEqual(["communication"])
    expect(connectorTypes("hubspot")).toEqual(["crm"])
    expect(connectorTypes("unknown-tool")).toEqual([])
    const trackerIds = connectorsWithType("task-management").map((i) => i.id)
    expect(trackerIds).toEqual(expect.arrayContaining(["jira", "clickup", "linear", "asana"]))
    expect(trackerIds).not.toContain("slack")
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
