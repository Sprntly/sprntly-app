// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ZoomConfigSlotView,
  lastSyncState,
  mapSyncFailure,
} from "../ZoomConfigSlot"

const noop = () => {}

type Props = React.ComponentProps<typeof ZoomConfigSlotView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    syncState: "done",
    lastSyncPhrase: "2 hours ago",
    failureReason: null,
    meetings: undefined,
    transcripts: undefined,
    authExpired: false,
    canManage: true,
    isReconnecting: false,
    onReconnect: noop,
  }
  return renderToStaticMarkup(
    React.createElement(ZoomConfigSlotView, { ...defaults, ...override }),
  )
}

describe("ZoomConfigSlotView — sync summary", () => {
  it("renders all three rows even when every value is missing", () => {
    // A blank value is not a signal to drop the line. A missing row tells the
    // user nothing and reads as a bug; "Transcripts read: —" tells them we
    // have not counted yet.
    const html = render({
      syncState: "never",
      lastSyncPhrase: null,
      meetings: undefined,
      transcripts: undefined,
    })
    expect(html).toContain("Last sync")
    expect(html).toContain("Meetings found")
    expect(html).toContain("Transcripts read")
    // Both counter VALUES show the placeholder rather than the row vanishing.
    // Counted on the value spans specifically — the "Never — …" copy carries
    // an em dash of its own.
    const values = [...html.matchAll(/conn-zoom-summary-v[^>]*>([^<]*)</g)].map(
      (m) => m[1],
    )
    expect(values.slice(1)).toEqual(["—", "—"])
  })

  it("renders a real zero as 0, not as an em dash", () => {
    // 0 and "never counted" are different facts and must not collapse.
    const html = render({ meetings: 0, transcripts: 0 })
    expect(html).toContain(">0<")
    expect(html).not.toContain("—")
  })

  it("explains a never-synced connection instead of showing a bare Never", () => {
    const html = render({ syncState: "never", lastSyncPhrase: null })
    expect(html).toContain("Never — the first sync starts within a few minutes.")
  })

  it("announces the first sync as running, politely", () => {
    const html = render({ syncState: "running", lastSyncPhrase: null })
    expect(html).toContain("First sync running — backfilling the last 3 months.")
    expect(html).toContain('role="status"')
    expect(html).toContain('aria-live="polite"')
  })

  it("shows a mapped failure reason and says it will retry", () => {
    const html = render({
      syncState: "failed",
      failureReason: "Zoom access expired and needs reconnecting",
    })
    expect(html).toContain("Failed — Zoom access expired and needs reconnecting")
    expect(html).toContain("Sprntly will try again at the next refresh.")
  })

  it("shows the relative phrase for a completed sync", () => {
    expect(render({ syncState: "done", lastSyncPhrase: "2 hours ago" })).toContain(
      "2 hours ago",
    )
  })
})

describe("ZoomConfigSlotView — no-transcripts warning", () => {
  it("fires only when meetings were found and no transcripts read", () => {
    const html = render({ meetings: 12, transcripts: 0 })
    expect(html).toContain("Recordings found, but no transcripts.")
    expect(html).toContain("Sprntly synced 12 meetings from Zoom")
    expect(html).toContain("Audio transcript")
    expect(html).toContain('role="status"')
    expect(html).toContain("conn-zoom-notice--warn")
  })

  it("stays silent when transcripts were read", () => {
    expect(render({ meetings: 12, transcripts: 12 })).not.toContain(
      "Recordings found, but no transcripts.",
    )
  })

  it("stays silent when no meetings were found at all", () => {
    // Nothing recorded this month is a quiet account, not a broken setting.
    expect(render({ meetings: 0, transcripts: 0 })).not.toContain(
      "Recordings found, but no transcripts.",
    )
  })

  it("stays silent when the counters are absent", () => {
    // Never counted is NOT "found nothing" — asserting a Zoom misconfiguration
    // from a sync that has not happened is exactly the confident-false
    // conclusion this surface must not draw.
    expect(
      render({ meetings: undefined, transcripts: undefined }),
    ).not.toContain("Recordings found, but no transcripts.")
    expect(render({ meetings: 5, transcripts: undefined })).not.toContain(
      "Recordings found, but no transcripts.",
    )
  })
})

describe("ZoomConfigSlotView — auth-expired block", () => {
  it("renders as an alert with a Reconnect button for an admin", () => {
    const html = render({ authExpired: true, canManage: true })
    expect(html).toContain('role="alert"')
    expect(html).toContain("Zoom stopped syncing.")
    expect(html).toContain("Reconnect to pick up where the last sync left off.")
    expect(html).toContain("Reconnect Zoom")
    expect(html).toContain("conn-zoom-notice--danger")
  })

  it("shows Reconnecting… and marks the button busy in flight", () => {
    const html = render({ authExpired: true, isReconnecting: true })
    expect(html).toContain("Reconnecting…")
    expect(html).toContain('aria-busy="true"')
  })

  it("tells a non-admin who to ask, and offers no button", () => {
    const html = render({ authExpired: true, canManage: false })
    expect(html).toContain("Zoom stopped syncing.")
    expect(html).toContain("Ask a workspace admin to reconnect Zoom.")
    expect(html).not.toContain("Reconnect Zoom<")
  })

  it("is absent on a healthy connection", () => {
    expect(render({ authExpired: false })).not.toContain("Zoom stopped syncing.")
  })
})

describe("lastSyncState", () => {
  const NOW = new Date("2026-08-04T12:00:00Z")

  it("is 'never' with no connection at all", () => {
    expect(lastSyncState(null, NOW)).toBe("never")
  })

  it("is 'running' for a connection made minutes ago with no sync yet", () => {
    expect(
      lastSyncState(
        {
          last_sync_at: null,
          last_sync_error: null,
          created_at: "2026-08-04T11:58:00Z",
        },
        NOW,
      ),
    ).toBe("running")
  })

  it("falls back to 'never' once the first-sync grace window has passed", () => {
    // Otherwise a genuinely stuck connector would claim to be busy forever.
    expect(
      lastSyncState(
        {
          last_sync_at: null,
          last_sync_error: null,
          created_at: "2026-08-04T10:00:00Z",
        },
        NOW,
      ),
    ).toBe("never")
  })

  it("is 'done' once a sync has landed", () => {
    expect(
      lastSyncState(
        {
          last_sync_at: "2026-08-04T11:00:00Z",
          last_sync_error: null,
          created_at: "2026-08-01T10:00:00Z",
        },
        NOW,
      ),
    ).toBe("done")
  })

  it("is 'failed' when an error is stamped, even with a prior success", () => {
    expect(
      lastSyncState(
        {
          last_sync_at: "2026-08-04T11:00:00Z",
          last_sync_error: "zoom authorization expired — reconnect required",
          created_at: "2026-08-01T10:00:00Z",
        },
        NOW,
      ),
    ).toBe("failed")
  })
})

describe("mapSyncFailure", () => {
  it("never echoes the stored error string verbatim", () => {
    // The stamped value is whatever the puller or Zoom produced — it can be a
    // stack-shaped fragment, and it is not copy.
    const raw =
      "Traceback: HTTPException(502) Zoom list_recordings failed at 0x7f00"
    expect(mapSyncFailure(raw)).toBe("Sprntly could not reach Zoom")
    expect(mapSyncFailure(raw)).not.toContain("0x7f00")
    expect(mapSyncFailure(raw)).not.toContain("Traceback")
  })

  it("recognises the reconnect and rate-limit shapes", () => {
    expect(
      mapSyncFailure("zoom authorization expired — reconnect required"),
    ).toBe("Zoom access expired and needs reconnecting")
    expect(mapSyncFailure("Zoom list_recordings rate-limited (429)")).toBe(
      "Zoom rate-limited the sync",
    )
  })

  it("handles an empty or missing error", () => {
    expect(mapSyncFailure(null)).toBe("Sprntly could not reach Zoom")
    expect(mapSyncFailure("")).toBe("Sprntly could not reach Zoom")
  })
})
