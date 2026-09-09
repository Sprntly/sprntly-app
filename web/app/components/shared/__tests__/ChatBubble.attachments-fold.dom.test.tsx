// @vitest-environment jsdom
//
// A turn's attachment cards fold once there are too many to scan.
//
// Reported at fourteen: the cards filled the thread, pushed the question that
// sent them off screen, and left a wall of filenames with no visible reason
// for them. Four fills one row at every width the thread renders at, and a
// count says the rest better than a block does.
//
// A COUNT, NOT A SCROLLER, on purpose. A horizontal strip hides the same files
// behind a gesture people miss on a trackpad and cannot perform on a keyboard.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ChatBubble } from "../ChatBubble"

const file = (name: string) => ({ name, content: "text" })

function bubbleWith(count: number, onOpenAttachment = vi.fn()) {
  return render(
    <ChatBubble
      turnId="t1"
      agentName="Sprntly"
      user={{
        query: "from the insight from this day, help me increase revenue",
        initials: "FT",
        attachments: Array.from({ length: count }, (_, i) => file(`file_${i}.pdf`)),
        onOpenAttachment,
      }}
    />,
  )
}

afterEach(() => cleanup())

describe("a handful of files", () => {
  it("shows them all, with no control in the way", () => {
    bubbleWith(4)
    expect(screen.getAllByTestId("turn-attachment-chip")).toHaveLength(4)
    expect(screen.queryByTestId("turn-attachment-more")).toBeNull()
  })

  it("shows a single attachment plainly", () => {
    bubbleWith(1)
    expect(screen.getAllByTestId("turn-attachment-chip")).toHaveLength(1)
    expect(screen.queryByTestId("turn-attachment-more")).toBeNull()
  })
})

describe("more files than fit", () => {
  it("shows four and counts the rest", () => {
    bubbleWith(14)
    expect(screen.getAllByTestId("turn-attachment-chip")).toHaveLength(4)
    expect(screen.getByTestId("turn-attachment-more").textContent).toBe("+10 more")
  })

  it("opens them in place", () => {
    bubbleWith(14)
    fireEvent.click(screen.getByTestId("turn-attachment-more"))
    expect(screen.getAllByTestId("turn-attachment-chip")).toHaveLength(14)
  })

  it("folds again — someone who checked one name can put the wall back", () => {
    bubbleWith(14)
    const toggle = () => screen.getByTestId("turn-attachment-more")
    fireEvent.click(toggle())
    expect(toggle().textContent).toBe("Show fewer")

    fireEvent.click(toggle())
    expect(screen.getAllByTestId("turn-attachment-chip")).toHaveLength(4)
    expect(toggle().textContent).toBe("+10 more")
  })

  it("announces its state to a screen reader", () => {
    bubbleWith(14)
    expect(screen.getByTestId("turn-attachment-more").getAttribute("aria-expanded")).toBe("false")
    fireEvent.click(screen.getByTestId("turn-attachment-more"))
    expect(screen.getByTestId("turn-attachment-more").getAttribute("aria-expanded")).toBe("true")
  })

  it("still opens a file that is visible", () => {
    // Folding is a display decision and must not cost the cards their job.
    const onOpen = vi.fn()
    bubbleWith(14, onOpen)
    fireEvent.click(screen.getAllByTestId("turn-attachment-chip")[0])
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ name: "file_0.pdf" }))
  })

  it("counts exactly one over the fold", () => {
    // Five files is one past four — the control has to be worth its own row.
    bubbleWith(5)
    expect(screen.getByTestId("turn-attachment-more").textContent).toBe("+1 more")
  })
})
