// @vitest-environment jsdom
//
// Unit tests for useGeneratePrototype(), the shared generate/view-prototype
// state machine. Injects designAgentApi/router/navigation/workspace via
// vi.mock (mirrors the adjacent useGenerationNotify.test.tsx precedent) so the
// hook's own branching is exercised without a real backend.

import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { PrototypeRecord } from "../../../lib/api"
import { prototypePath } from "../../../lib/routes"
import { reasonCopy } from "../GenerationErrorBanner"
import {
  useGeneratePrototype,
  type UseGeneratePrototypeOptions,
  type UseGeneratePrototypeResult,
} from "../useGeneratePrototype"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast }),
}))

const refresh = vi.fn(async () => {})
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace: { id: "ws-1", design_source: null }, refresh }),
}))

const updateWorkspace = vi.fn(async (..._args: unknown[]) => {})
vi.mock("../../../lib/onboarding/store", () => ({
  updateWorkspace: (...args: unknown[]) => updateWorkspace(...args),
}))

const getByPrd = vi.fn()
const deleteProto = vi.fn(async (..._args: unknown[]) => {})
vi.mock("../../../lib/api", () => ({
  designAgentApi: {
    getByPrd: (...args: [number]) => getByPrd(...args),
    delete: (...args: unknown[]) => deleteProto(...args),
  },
}))

function readyRow(id: number): PrototypeRecord {
  return {
    id,
    status: "ready",
    bundle_url: `https://example.com/${id}.js`,
    error: null,
  } as PrototypeRecord
}

/** Minimal host: mounts the hook and hands its live result to the test via a
 *  callback invoked on every render (no @testing-library/react-hooks in this
 *  repo — this is the same shape the adjacent useGenerationNotify tests use
 *  for a bare CustomEvent-driven hook; here we additionally surface the
 *  return value itself). */
function Host({
  prdId,
  options,
  onResult,
}: {
  prdId: number | null
  options?: UseGeneratePrototypeOptions
  onResult: (r: UseGeneratePrototypeResult) => void
}) {
  const result = useGeneratePrototype(prdId, options)
  onResult(result)
  return null
}

beforeEach(() => {
  getByPrd.mockReset()
  deleteProto.mockClear()
  push.mockClear()
  showToast.mockClear()
  updateWorkspace.mockClear()
  refresh.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("useGeneratePrototype — existence check", () => {
  it("fetches exactly once per prdId value, not once per render", async () => {
    getByPrd.mockResolvedValue(null)
    let latest!: UseGeneratePrototypeResult
    const { rerender } = render(
      <Host prdId={1} onResult={(r) => (latest = r)} />,
    )
    await act(async () => {})
    expect(getByPrd).toHaveBeenCalledTimes(1)
    expect(getByPrd).toHaveBeenCalledWith(1)

    // Re-render with the SAME prdId — must not re-fetch.
    rerender(<Host prdId={1} onResult={(r) => (latest = r)} />)
    await act(async () => {})
    expect(getByPrd).toHaveBeenCalledTimes(1)
    expect(latest.cta).toBe("generate")
  })

  it("never fetches when skipExistenceCheck is true; cta is always generate", async () => {
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={1}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})
    expect(getByPrd).not.toHaveBeenCalled()
    expect(latest.existing).toBeNull()
    expect(latest.cta).toBe("generate")

    await act(async () => {
      await latest.handleCtaClick()
    })
    expect(latest.generateModalProps.open).toBe(true)
    expect(push).not.toHaveBeenCalled()
  })
})

describe("useGeneratePrototype — retrieval / round-trip", () => {
  it("resolves cta to view with a ready bundle_url row", async () => {
    getByPrd.mockResolvedValue(readyRow(5))
    let latest!: UseGeneratePrototypeResult
    render(<Host prdId={5} onResult={(r) => (latest = r)} />)
    await act(async () => {})
    expect(latest.cta).toBe("view")
    expect(latest.ctaLabel).toBe("View Prototype")
  })

  it("does not adopt a generating row — cta stays generate", async () => {
    getByPrd.mockResolvedValue({
      id: 2,
      status: "generating",
      bundle_url: null,
      error: null,
    } as PrototypeRecord)
    let latest!: UseGeneratePrototypeResult
    render(<Host prdId={2} onResult={(r) => (latest = r)} />)
    await act(async () => {})
    expect(latest.cta).toBe("generate")
    expect(latest.existing).toBeNull()
  })

  it("degrades to generate with no thrown error when getByPrd rejects", async () => {
    getByPrd.mockRejectedValue(new Error("network"))
    let latest!: UseGeneratePrototypeResult
    render(<Host prdId={3} onResult={(r) => (latest = r)} />)
    await act(async () => {})
    expect(latest.existing).toBeNull()
    expect(latest.cta).toBe("generate")
  })
})

describe("useGeneratePrototype — handleCtaClick view re-verify", () => {
  it("resets existing and toasts when the re-verify finds no ready row", async () => {
    getByPrd.mockResolvedValueOnce(readyRow(9)).mockResolvedValueOnce(null)
    let latest!: UseGeneratePrototypeResult
    render(<Host prdId={9} onResult={(r) => (latest = r)} />)
    await act(async () => {})
    expect(latest.cta).toBe("view")

    await act(async () => {
      await latest.handleCtaClick()
    })
    expect(latest.existing).toBeNull()
    expect(showToast).toHaveBeenCalledWith(
      "Prototype unavailable",
      "The prototype was removed. Generate a new one.",
    )
    expect(push).not.toHaveBeenCalled()
  })

  it("navigates exactly once when the re-verify succeeds", async () => {
    getByPrd.mockResolvedValueOnce(readyRow(10)).mockResolvedValueOnce(readyRow(10))
    let latest!: UseGeneratePrototypeResult
    render(<Host prdId={10} onResult={(r) => (latest = r)} />)
    await act(async () => {})
    expect(latest.cta).toBe("view")

    await act(async () => {
      await latest.handleCtaClick()
    })
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith(prototypePath(10))
  })
})

describe("useGeneratePrototype — onGenDone terminal outcomes", () => {
  it("default path navigates once on success with no onSuccess supplied", async () => {
    getByPrd.mockResolvedValue(null)
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={11}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    await act(async () => {
      latest.generateModalProps.onGenDone({ ok: true, prototype: readyRow(11) })
    })
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith(prototypePath(11))
  })

  it("calls onSuccess instead of navigating when supplied", async () => {
    const onSuccess = vi.fn()
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={12}
        options={{ skipExistenceCheck: true, onSuccess }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    const proto = readyRow(12)
    await act(async () => {
      latest.generateModalProps.onGenDone({ ok: true, prototype: proto })
    })
    expect(onSuccess).toHaveBeenCalledWith(proto)
    expect(push).not.toHaveBeenCalled()
  })

  it("calls neither onSuccess nor push on a failure result", async () => {
    const onSuccess = vi.fn()
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={13}
        options={{ skipExistenceCheck: true, onSuccess }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    await act(async () => {
      latest.generateModalProps.onGenDone({ ok: false, message: "boom" })
    })
    expect(onSuccess).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })
})

describe("useGeneratePrototype — notify-when-ready", () => {
  it("default path dispatches exactly one da:generating event and no da:notify-generation", async () => {
    const generatingEvents: CustomEvent[] = []
    const notifyGenerationEvents: CustomEvent[] = []
    const onGenerating = (e: Event) => generatingEvents.push(e as CustomEvent)
    const onNotifyGeneration = (e: Event) => notifyGenerationEvents.push(e as CustomEvent)
    window.addEventListener("da:generating", onGenerating)
    window.addEventListener("da:notify-generation", onNotifyGeneration)

    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={14}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    // Arm a prototype id via the real onGenStart/onKickoff wiring before notify.
    await act(async () => {
      latest.generateModalProps.onGenStart()
      latest.generateModalProps.onKickoff(42)
    })

    await act(async () => {
      latest.loadingScreenProps.onNotifyWhenReady()
    })

    expect(generatingEvents.length).toBe(1)
    expect(generatingEvents[0].detail).toEqual({ prototypeId: 42 })
    expect(notifyGenerationEvents.length).toBe(0)
    expect(showToast).toHaveBeenCalledWith(
      "Prototype is processing",
      "We'll let you know when it's ready.",
    )
    expect(latest.loadingScreenProps.open).toBe(false)

    window.removeEventListener("da:generating", onGenerating)
    window.removeEventListener("da:notify-generation", onNotifyGeneration)
  })

  it("an onNotifyWhenReady override replaces the default side effects", async () => {
    const onNotifyWhenReady = vi.fn()
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={15}
        options={{ skipExistenceCheck: true, onNotifyWhenReady }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    await act(async () => {
      latest.loadingScreenProps.onNotifyWhenReady()
    })

    expect(onNotifyWhenReady).toHaveBeenCalledTimes(1)
    expect(showToast).not.toHaveBeenCalledWith(
      "Prototype is processing",
      "We'll let you know when it's ready.",
    )
  })
})

describe("useGeneratePrototype — notify-mode completion (handleGenDone)", () => {
  it("shows a persistent success toast with a working Open action and dispatches the done event", async () => {
    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={21}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    // Arm notify mode via the real onNotifyWhenReady wiring (default path).
    await act(async () => {
      latest.generateModalProps.onGenStart()
      latest.generateModalProps.onKickoff(99)
    })
    await act(async () => {
      latest.loadingScreenProps.onNotifyWhenReady()
    })
    showToast.mockClear()

    const proto = readyRow(21)
    await act(async () => {
      latest.generateModalProps.onGenDone({ ok: true, prototype: proto })
    })

    expect(showToast).toHaveBeenCalledTimes(1)
    const [title, sub, action, opts] = showToast.mock.calls[0]
    expect(title).toBe("Prototype ready")
    expect(sub).toBe("Your prototype finished generating.")
    expect(action).toBe("Open")
    expect(opts).toMatchObject({ persist: true })
    expect(typeof opts.onAction).toBe("function")

    // The action button navigates via the hook's own onSuccess-or-navigate
    // pattern (no onSuccess supplied here, so it falls back to router.push).
    opts.onAction()
    expect(push).toHaveBeenCalledWith(prototypePath(21))

    expect(doneEvents.length).toBe(1)
    window.removeEventListener("da:generating-done", onDone)
  })

  it("routes the Open action through a supplied onSuccess instead of navigating", async () => {
    const onSuccess = vi.fn()
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={22}
        options={{ skipExistenceCheck: true, onSuccess }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    await act(async () => {
      latest.generateModalProps.onGenStart()
      latest.generateModalProps.onKickoff(100)
    })
    await act(async () => {
      latest.loadingScreenProps.onNotifyWhenReady()
    })
    showToast.mockClear()

    const proto = readyRow(22)
    await act(async () => {
      latest.generateModalProps.onGenDone({ ok: true, prototype: proto })
    })

    const opts = showToast.mock.calls[0][3]
    opts.onAction()
    expect(onSuccess).toHaveBeenCalledWith(proto)
    expect(push).not.toHaveBeenCalled()
  })

  it("shows a persistent failure toast and dispatches the done event", async () => {
    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={23}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    await act(async () => {
      latest.generateModalProps.onGenStart()
      latest.generateModalProps.onKickoff(101)
    })
    await act(async () => {
      latest.loadingScreenProps.onNotifyWhenReady()
    })
    showToast.mockClear()

    await act(async () => {
      latest.generateModalProps.onGenDone({ ok: false, message: "boom" })
    })

    expect(showToast).toHaveBeenCalledWith(
      "Generation failed",
      reasonCopy("boom"),
      undefined,
      { persist: true },
    )
    expect(doneEvents.length).toBe(1)
    window.removeEventListener("da:generating-done", onDone)
  })
})

describe("useGeneratePrototype — cross-surface generating signal", () => {
  it("ignores the external da:generating event by default", async () => {
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={16}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})
    expect(latest.cta).toBe("generate")

    await act(async () => {
      window.dispatchEvent(new CustomEvent("da:generating", { detail: { prototypeId: 1 } }))
    })
    expect(latest.cta).toBe("generate")
  })

  it("tracks the external event when opted in, and reverts on da:generating-done", async () => {
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={17}
        options={{ skipExistenceCheck: true, listenForCrossSurfaceGenerating: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})
    expect(latest.cta).toBe("generate")

    await act(async () => {
      window.dispatchEvent(new CustomEvent("da:generating", { detail: { prototypeId: 1 } }))
    })
    expect(latest.cta).toBe("generating")
    expect(latest.ctaLabel).toBe("Generating Prototype")

    await act(async () => {
      window.dispatchEvent(new CustomEvent("da:generating-done"))
    })
    expect(latest.cta).toBe("generate")
  })
})

describe("useGeneratePrototype — openGenerateModal", () => {
  it("opens unconditionally, even before the existence check resolves", async () => {
    let resolveGetByPrd!: (v: PrototypeRecord | null) => void
    getByPrd.mockImplementation(
      () => new Promise<PrototypeRecord | null>((resolve) => { resolveGetByPrd = resolve }),
    )
    let latest!: UseGeneratePrototypeResult
    render(<Host prdId={18} onResult={(r) => (latest = r)} />)

    // The existence-check promise is deliberately left unresolved here.
    await act(async () => {
      latest.openGenerateModal()
    })
    expect(latest.generateModalProps.open).toBe(true)

    // Clean up the pending promise so it doesn't leak into another test.
    resolveGetByPrd(null)
    await act(async () => {})
  })

  it("opens unconditionally with skipExistenceCheck true as well", async () => {
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={19}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {
      latest.openGenerateModal()
    })
    expect(latest.generateModalProps.open).toBe(true)
  })
})

describe("useGeneratePrototype — controlled open", () => {
  it("mirrors the caller's open prop and routes through onOpenChange", async () => {
    const onOpenChange = vi.fn()
    let latest!: UseGeneratePrototypeResult
    const { rerender } = render(
      <Host
        prdId={20}
        options={{ skipExistenceCheck: true, open: false, onOpenChange }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})
    expect(latest.generateModalProps.open).toBe(false)

    await act(async () => {
      latest.openGenerateModal()
    })
    expect(onOpenChange).toHaveBeenCalledWith(true)
    // Controlled mode: the hook never flips an internal boolean — it still
    // mirrors the caller-owned `open` prop, unchanged until the caller re-
    // renders with a new value.
    expect(latest.generateModalProps.open).toBe(false)

    rerender(
      <Host
        prdId={20}
        options={{ skipExistenceCheck: true, open: true, onOpenChange }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})
    expect(latest.generateModalProps.open).toBe(true)

    await act(async () => {
      latest.generateModalProps.onClose()
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe("useGeneratePrototype — onCancel wiring (locating-phase Cancel control)", () => {
  it("test_useGeneratePrototype_generateModalProps_onCancel_is_same_reference_as_loadingScreenProps_onCancel", async () => {
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={24}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    expect(latest.generateModalProps.onCancel).toBe(latest.loadingScreenProps.onCancel)
  })

  it("test_useGeneratePrototype_generateModalProps_onCancel_dismisses_overlay_like_loadingScreenProps_onCancel", async () => {
    let latest!: UseGeneratePrototypeResult
    render(
      <Host
        prdId={25}
        options={{ skipExistenceCheck: true }}
        onResult={(r) => (latest = r)}
      />,
    )
    await act(async () => {})

    await act(async () => {
      latest.generateModalProps.onGenStart()
      latest.generateModalProps.onKickoff(200)
    })
    expect(latest.loadingScreenProps.open).toBe(true)

    await act(async () => {
      latest.generateModalProps.onCancel()
    })
    expect(latest.loadingScreenProps.open).toBe(false)
  })
})
