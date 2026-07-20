// Part A — deletes the OS browser-notification path entirely. The in-app
// toast (persisted via notificationStore + the shell replay) covers in-app
// navigation; the backend Slack notifier covers the away/closed-tab case
// strictly better than an unsolicited browser permission prompt does.
import { existsSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { afterEach, describe, expect, it, vi } from "vitest"
import { runGenerateFlow } from "../DesignAgentDrawer"

const here = dirname(fileURLToPath(import.meta.url))
const COMPONENT_PATH = join(here, "..", "GenerationLoadingScreen.tsx")
const DRAWER_PATH = join(here, "..", "DesignAgentDrawer.tsx")
const BROWSER_NOTIFY_PATH = join(here, "..", "browserNotify.ts")
const BROWSER_NOTIFY_TEST_PATH = join(here, "browserNotify.test.ts")

const testGlobal = globalThis as unknown as {
  window?: {
    Notification: {
      permission: string
      requestPermission: ReturnType<typeof vi.fn>
    }
  }
}

afterEach(() => {
  testGlobal.window = undefined
  vi.restoreAllMocks()
})

describe("Part A — browser notification path removed", () => {
  it("test_browsernotify_module_deleted — the module and its test no longer exist on disk (AC1)", () => {
    expect(existsSync(BROWSER_NOTIFY_PATH)).toBe(false)
    expect(existsSync(BROWSER_NOTIFY_TEST_PATH)).toBe(false)
  })

  it("test_run_generate_flow_never_calls_ensure_notify_permission — no Notification call during a full run (AC2, AC3)", async () => {
    const requestPermission = vi.fn().mockResolvedValue("granted")
    testGlobal.window = {
      Notification: { permission: "default", requestPermission },
    }
    const genResult = Promise.resolve({
      ok: true as const,
      prototype: {} as never,
    })

    await runGenerateFlow({
      params: {
        prd_id: 1,
        target_platform: "desktop",
        instructions: "",
        figma_file_key: null,
      },
      generate: vi
        .fn()
        .mockResolvedValue({ prototype_id: 1, status: "generating" }),
      runGeneration: vi.fn().mockReturnValue(genResult),
      onOpenChange: vi.fn(),
      showToast: vi.fn(),
      setSubmitting: vi.fn(),
      notifyOnReady: true,
    })
    await genResult
    await Promise.resolve()

    expect(requestPermission).not.toHaveBeenCalled()
  })

  it("DesignAgentDrawer.tsx imports nothing from ./browserNotify and calls neither removed function (AC2)", () => {
    const src = readFileSync(DRAWER_PATH, "utf8")
    expect(src).not.toMatch(/from ["']\.\/browserNotify["']/)
    expect(src).not.toContain("ensureNotifyPermission")
    expect(src).not.toContain("fireReadyNotification")
  })

  it("test_generation_loading_screen_source_has_no_notification_reference — no Notification/ensureNotifyPermission string (AC4)", () => {
    const src = readFileSync(COMPONENT_PATH, "utf8")
    expect(src).not.toContain("Notification")
    expect(src).not.toContain("ensureNotifyPermission")
  })
})
