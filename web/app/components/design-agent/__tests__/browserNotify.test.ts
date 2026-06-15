// Browser-notification helpers — node-env unit coverage with a stubbed
// `window.Notification` (the repo's vitest env is `node`, no real Notification
// API). Verifies SSR/unsupported safety, the "ask only when undecided" permission
// rule, and the granted-only firing of the ready notification.
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  ensureNotifyPermission,
  fireReadyNotification,
  notificationsSupported,
  notifyPermission,
} from "../browserNotify"

type FakeNotif = {
  permission: NotificationPermission
  requestPermission?: () => Promise<NotificationPermission>
}

function installNotification(n: FakeNotif | undefined): void {
  const w = globalThis as unknown as { window?: { Notification?: unknown } }
  w.window = w.window ?? ({} as { Notification?: unknown })
  if (n === undefined) {
    delete (w.window as { Notification?: unknown }).Notification
  } else {
    ;(w.window as { Notification?: unknown }).Notification = n
  }
}

afterEach(() => {
  installNotification(undefined)
  vi.restoreAllMocks()
})

describe("notificationsSupported / notifyPermission", () => {
  it("reports unsupported when the Notification API is absent", () => {
    installNotification(undefined)
    expect(notificationsSupported()).toBe(false)
    expect(notifyPermission()).toBe("unsupported")
  })

  it("reflects the current permission when supported", () => {
    installNotification({ permission: "granted" })
    expect(notificationsSupported()).toBe(true)
    expect(notifyPermission()).toBe("granted")
  })
})

describe("ensureNotifyPermission", () => {
  it("requests permission only when undecided ('default')", async () => {
    const requestPermission = vi
      .fn<() => Promise<NotificationPermission>>()
      .mockResolvedValue("granted")
    installNotification({ permission: "default", requestPermission })
    const result = await ensureNotifyPermission()
    expect(requestPermission).toHaveBeenCalledOnce()
    expect(result).toBe("granted")
  })

  it("does NOT re-prompt when already granted or denied", async () => {
    const requestPermission = vi.fn<() => Promise<NotificationPermission>>()
    installNotification({ permission: "denied", requestPermission })
    expect(await ensureNotifyPermission()).toBe("denied")
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it("degrades to 'unsupported' off-browser", async () => {
    installNotification(undefined)
    expect(await ensureNotifyPermission()).toBe("unsupported")
  })
})

describe("fireReadyNotification", () => {
  it("constructs a tagged Notification when permission is granted", () => {
    const ctorArgs: Array<[string, NotificationOptions | undefined]> = []
    class FakeCtor {
      onclick: (() => void) | null = null
      static permission: NotificationPermission = "granted"
      constructor(title: string, opts?: NotificationOptions) {
        ctorArgs.push([title, opts])
      }
      close() {}
    }
    installNotification(FakeCtor as unknown as FakeNotif)
    fireReadyNotification({ title: "Prototype ready", body: "Done.", prdId: 487 })
    expect(ctorArgs).toHaveLength(1)
    expect(ctorArgs[0][0]).toBe("Prototype ready")
    expect(ctorArgs[0][1]).toMatchObject({
      body: "Done.",
      tag: "da-prototype-ready-487",
    })
  })

  it("is a no-op when permission is not granted", () => {
    let constructed = 0
    class FakeCtor {
      static permission: NotificationPermission = "default"
      constructor() {
        constructed += 1
      }
      close() {}
    }
    installNotification(FakeCtor as unknown as FakeNotif)
    fireReadyNotification({ title: "x", body: "y", prdId: 1 })
    expect(constructed).toBe(0)
  })

  it("never throws when the Notification API is absent", () => {
    installNotification(undefined)
    expect(() =>
      fireReadyNotification({ title: "x", body: "y", prdId: 1 }),
    ).not.toThrow()
  })
})
