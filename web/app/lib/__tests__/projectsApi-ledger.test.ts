// The three delegation-ledger `projectsApi` methods — pins the exact
// method + URL + body each one sends. Model: app/lib/__tests__/usageApi.test.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { projectsApi, setActiveWorkspaceId } from "../api"

type MockResponse = {
  ok: boolean
  status: number
  text: () => Promise<string>
}

function jsonResponse(status: number, body: unknown): MockResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }
}

describe("projectsApi ledger methods", () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    setActiveWorkspaceId(null)
  })

  afterEach(() => {
    setActiveWorkspaceId(null)
    vi.unstubAllGlobals()
  })

  function lastCall(): [string, RequestInit] {
    const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    return [call[0] as string, call[1] as RequestInit]
  }

  it("emitDelegationEvent POSTs the event+note body to the events route", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { delegation_id: 7, status: "accepted" }))
    await projectsApi.emitDelegationEvent(3, 7, "accepted", "on it")

    const [url, init] = lastCall()
    expect(url).toContain("/v1/projects/3/delegations/7/events")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({ event: "accepted", note: "on it" })
  })

  it("emitDelegationEvent omits note when not given (still sends the key as undefined)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { delegation_id: 7, status: "cancelled" }))
    await projectsApi.emitDelegationEvent(3, 7, "cancelled")

    const [, init] = lastCall()
    const body = JSON.parse(init.body as string)
    expect(body.event).toBe("cancelled")
    expect(body.note).toBeUndefined()
  })

  it("ledger GETs the view-scoped delegations route", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, []))
    await projectsApi.ledger(3, "assigned_to_me")

    const [url, init] = lastCall()
    expect(url).toContain("/v1/projects/3/delegations?view=assigned_to_me")
    expect(init.method).toBe("GET")
  })

  it("ledger carries the waiting_on view distinctly", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, []))
    await projectsApi.ledger(3, "waiting_on")

    const [url] = lastCall()
    expect(url).toContain("view=waiting_on")
  })

  it("ledgerCounts GETs the counts route", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { assigned_to_me_open: 1, waiting_on_open: 2 }),
    )
    await projectsApi.ledgerCounts(3)

    const [url, init] = lastCall()
    expect(url).toContain("/v1/projects/3/delegations/counts")
    expect(init.method).toBe("GET")
  })
})
