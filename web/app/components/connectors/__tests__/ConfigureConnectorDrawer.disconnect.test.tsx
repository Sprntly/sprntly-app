// Which endpoint the Disconnect button actually calls, per provider.
//
// `callDisconnect` is a hand-written switch that THROWS "not implemented" for
// any provider it does not know. Everything else about a connector is
// data-driven off CONNECTOR_CATALOG — a new row renders, connects, probes and
// syncs without touching that file — so this is the one place a newly shipped
// connector silently breaks, and it breaks at the worst possible moment: a user
// trying to revoke access to their own data.
//
// The last assertion is therefore the point of the file: EVERY connectable
// catalog row must have a branch, so the next connector cannot ship without one.
import { describe, expect, it, vi } from "vitest"

const { deleteMock } = vi.hoisted(() => ({ deleteMock: vi.fn() }))

vi.mock("../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/api")>()
  return {
    ...actual,
    connectorsApi: {
      ...actual.connectorsApi,
      // Record the URL each provider's disconnect resolves to, rather than
      // stubbing them one by one — that way a branch wired to the WRONG
      // provider's function is caught too, not just a missing one.
      disconnectGoogleMeet: () => deleteMock("/v1/connectors/google-meet"),
      disconnectZoom: () => deleteMock("/v1/connectors/zoom"),
      disconnectGoogleDrive: () => deleteMock("/v1/connectors/google-drive"),
    },
  }
})

import { callDisconnect } from "../ConfigureConnectorDrawer"
import {
  CONNECTOR_IDS_CONNECTABLE,
  UPLOADS_PROVIDER_ID,
} from "../../../lib/connectorsCatalog"

describe("callDisconnect", () => {
  it("sends Google Meet to its own endpoint and does not throw", async () => {
    deleteMock.mockClear()
    await expect(callDisconnect("google_meet")).resolves.toBeUndefined()
    expect(deleteMock).toHaveBeenCalledWith("/v1/connectors/google-meet")
  })

  it("does not confuse Google Meet with the Google Drive connector", async () => {
    // They share a Cloud project and an OAuth client on the backend, so a
    // crossed branch here would revoke the wrong grant — and the connectors
    // screen would show the untouched one as still connected.
    deleteMock.mockClear()
    await callDisconnect("google_drive")
    expect(deleteMock).toHaveBeenCalledWith("/v1/connectors/google-drive")
    deleteMock.mockClear()
    await callDisconnect("zoom")
    expect(deleteMock).toHaveBeenCalledWith("/v1/connectors/zoom")
  })

  it("throws for a provider it has never heard of", async () => {
    await expect(callDisconnect("not-a-connector")).rejects.toThrow(
      /not implemented/i,
    )
  })

  it("has a branch for every connectable connector in the catalog", async () => {
    // `uploads` is connectable but is the user's own document corpus rather
    // than a third-party grant; it is handled and included here for that
    // reason. Any OTHER id reaching the throw is a shipped-broken Disconnect.
    const missing: string[] = []
    for (const id of CONNECTOR_IDS_CONNECTABLE) {
      try {
        await callDisconnect(id)
      } catch (e) {
        if (String(e).includes("not implemented")) missing.push(id)
      }
    }
    expect(missing).toEqual([])
    expect(CONNECTOR_IDS_CONNECTABLE.has("google_meet")).toBe(true)
    expect(CONNECTOR_IDS_CONNECTABLE.has(UPLOADS_PROVIDER_ID)).toBe(true)
  })
})
