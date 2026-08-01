// @vitest-environment jsdom
//
// Six-box one-time-code entry. The value is a compact digit string and each
// box renders value[i], so the behaviours worth pinning are the ones that
// keep those two in sync: forward auto-advance, backspace stepping back,
// pasting the whole code from anywhere in the row, and non-digits never
// landing in the value at all.
import * as React from "react"
import { useState } from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { OtpInput } from "../OtpInput"

afterEach(cleanup)

/** Mirrors how the routes use it: parent owns the value. */
function Harness({
  onComplete,
  initial = "",
}: {
  onComplete?: (code: string) => void
  initial?: string
}) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <OtpInput value={value} onChange={setValue} onComplete={onComplete} />
      <output data-testid="value">{value}</output>
    </>
  )
}

function boxes(): HTMLInputElement[] {
  return screen.getAllByRole("textbox") as HTMLInputElement[]
}

function currentValue(): string {
  return screen.getByTestId("value").textContent ?? ""
}

describe("OtpInput", () => {
  it("renders six labelled boxes inside a labelled group", () => {
    render(<Harness />)
    expect(boxes()).toHaveLength(6)
    expect(screen.getByRole("group", { name: "Verification code" })).toBeTruthy()
    expect(screen.getByLabelText("Digit 1 of 6")).toBeTruthy()
    expect(screen.getByLabelText("Digit 6 of 6")).toBeTruthy()
  })

  it("hints one-time-code autofill on the first box only", () => {
    render(<Harness />)
    expect(boxes()[0].getAttribute("autocomplete")).toBe("one-time-code")
    expect(boxes()[1].getAttribute("autocomplete")).toBe("off")
  })

  it("advances focus as digits are typed", () => {
    render(<Harness />)
    fireEvent.change(boxes()[0], { target: { value: "4" } })
    expect(currentValue()).toBe("4")
    expect(document.activeElement).toBe(boxes()[1])

    fireEvent.change(boxes()[1], { target: { value: "8" } })
    expect(currentValue()).toBe("48")
    expect(document.activeElement).toBe(boxes()[2])
  })

  it("ignores non-digit input", () => {
    render(<Harness />)
    fireEvent.change(boxes()[0], { target: { value: "a" } })
    expect(currentValue()).toBe("")
    fireEvent.change(boxes()[0], { target: { value: "-" } })
    expect(currentValue()).toBe("")
  })

  it("spills a multi-character autofill across the row", () => {
    render(<Harness />)
    // Password managers / iOS OTP autofill drop the whole code into box 0.
    fireEvent.change(boxes()[0], { target: { value: "483920" } })
    expect(currentValue()).toBe("483920")
    expect(boxes()[5].value).toBe("0")
  })

  it("fills from the start when the code is pasted into a later box", () => {
    render(<Harness />)
    fireEvent.paste(boxes()[3], {
      clipboardData: { getData: () => "483920" },
    })
    expect(currentValue()).toBe("483920")
    expect(boxes()[0].value).toBe("4")
  })

  it("strips separators out of a pasted code", () => {
    render(<Harness />)
    fireEvent.paste(boxes()[0], {
      clipboardData: { getData: () => "483 920" },
    })
    expect(currentValue()).toBe("483920")
  })

  it("caps the value at six digits", () => {
    render(<Harness />)
    fireEvent.paste(boxes()[0], {
      clipboardData: { getData: () => "4839201234" },
    })
    expect(currentValue()).toBe("483920")
  })

  it("clears the focused digit on backspace, then steps back on the next one", () => {
    render(<Harness initial="4839" />)
    const all = boxes()
    all[3].focus()
    fireEvent.keyDown(all[3], { key: "Backspace" })
    expect(currentValue()).toBe("483")
    expect(document.activeElement).toBe(all[3])

    fireEvent.keyDown(all[3], { key: "Backspace" })
    expect(currentValue()).toBe("48")
    expect(document.activeElement).toBe(boxes()[2])
  })

  it("moves focus with the arrow keys", () => {
    render(<Harness initial="483920" />)
    const all = boxes()
    all[2].focus()
    fireEvent.keyDown(all[2], { key: "ArrowRight" })
    expect(document.activeElement).toBe(boxes()[3])
    fireEvent.keyDown(boxes()[3], { key: "ArrowLeft" })
    expect(document.activeElement).toBe(boxes()[2])
  })

  it("fires onComplete once when the last digit lands", () => {
    const onComplete = vi.fn()
    render(<Harness onComplete={onComplete} />)
    fireEvent.paste(boxes()[0], {
      clipboardData: { getData: () => "483920" },
    })
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith("483920")
  })

  it("re-fires onComplete after the code is edited and completed again", () => {
    const onComplete = vi.fn()
    render(<Harness onComplete={onComplete} />)
    fireEvent.paste(boxes()[0], { clipboardData: { getData: () => "483920" } })
    expect(onComplete).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(boxes()[5], { key: "Backspace" })
    fireEvent.change(boxes()[5], { target: { value: "1" } })
    expect(currentValue()).toBe("483921")
    expect(onComplete).toHaveBeenCalledTimes(2)
    expect(onComplete).toHaveBeenLastCalledWith("483921")
  })

  it("disables every box when disabled", () => {
    render(<OtpInput value="" onChange={() => {}} disabled />)
    for (const box of boxes()) expect(box.disabled).toBe(true)
  })

  it("marks the row invalid so the boxes can paint the error state", () => {
    const { container } = render(<OtpInput value="483" onChange={() => {}} invalid />)
    expect(container.querySelector(".otp-row-invalid")).toBeTruthy()
  })
})
