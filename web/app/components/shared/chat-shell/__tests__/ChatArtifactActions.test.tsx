// @vitest-environment jsdom
//
// Proves the two artifact-action rows still render their exact markup and fire
// their CTAs after the verbatim move out of ChatScreen into the shared
// chat-shell home. GeneratePrototypeCTA is mocked to its render-prop contract so
// this suite exercises ChatArtifactActions' own wiring (not the prototype hook).
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../design-agent/GeneratePrototypeCTA", () => ({
  GeneratePrototypeCTA: ({
    render: renderTrigger,
  }: {
    render: (state: { onClick: () => void; cta: string; label: string }) => unknown
  }) => renderTrigger({ onClick: () => {}, cta: "ready", label: "Generate Prototype" }),
}))

import { ChatArtifactActions, ChatTicketSetActions } from "../ChatArtifactActions"

afterEach(cleanup)

describe("ChatArtifactActions / ChatTicketSetActions — moved verbatim (AC5)", () => {
  it("test_ChatArtifactActions_renders_ctas_and_fires", () => {
    const onViewEvidence = vi.fn()
    const onOpenPrd = vi.fn()
    const onViewPrototype = vi.fn()
    const { getByText, getByTestId, container } = render(
      <ChatArtifactActions
        evidenceExists
        prdExists
        prdWaiting={false}
        prdGenerating={false}
        onViewEvidence={onViewEvidence}
        onOpenPrd={onOpenPrd}
        prototypePrdId={42}
        prototypeReady
        onViewPrototype={onViewPrototype}
      />,
    )

    // Unchanged markup: the shared `bc-actions` wrapper + two `bc-action-btn`s.
    expect(container.querySelector(".bc-actions")).toBeTruthy()
    expect(container.querySelectorAll(".bc-action-btn").length).toBe(2)

    // First button: evidence present → "View Evidence", fires onViewEvidence.
    fireEvent.click(getByText("View Evidence"))
    expect(onViewEvidence).toHaveBeenCalledTimes(1)
    expect(onOpenPrd).not.toHaveBeenCalled()

    // Prototype button: prototypeReady + a prd id → "View Prototype", fires
    // onViewPrototype (the ready branch of the render-prop onClick).
    fireEvent.click(getByTestId("chat-prototype-cta"))
    expect(onViewPrototype).toHaveBeenCalledTimes(1)
  })

  it("ChatArtifactActions shows Generate PRD when no evidence and no prd", () => {
    const onOpenPrd = vi.fn()
    const { getByText } = render(
      <ChatArtifactActions
        evidenceExists={false}
        prdExists={false}
        prdWaiting={false}
        prdGenerating={false}
        onViewEvidence={() => {}}
        onOpenPrd={onOpenPrd}
        prototypePrdId={null}
        prototypeReady={false}
        onViewPrototype={() => {}}
      />,
    )
    fireEvent.click(getByText("Generate PRD"))
    expect(onOpenPrd).toHaveBeenCalledTimes(1)
  })

  it("test_ChatTicketSetActions_renders_and_fires", () => {
    const onClick = vi.fn()
    const { getByTestId, rerender } = render(
      <ChatTicketSetActions state="ready" onClick={onClick} />,
    )
    const btn = getByTestId("chat-ticket-set-cta")
    expect(btn.textContent).toBe("View Tickets")
    fireEvent.click(btn)
    expect(onClick).toHaveBeenCalledTimes(1)

    // running → labelled + disabled (the run owns the button).
    rerender(<ChatTicketSetActions state="running" onClick={onClick} />)
    const running = getByTestId("chat-ticket-set-cta") as HTMLButtonElement
    expect(running.textContent).toBe("Writing tickets…")
    expect(running.disabled).toBe(true)

    // failed → offers the re-run.
    rerender(<ChatTicketSetActions state="failed" onClick={onClick} />)
    expect(getByTestId("chat-ticket-set-cta").textContent).toBe("Retry tickets")
  })
})
