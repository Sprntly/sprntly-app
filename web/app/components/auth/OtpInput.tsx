// Six-box one-time-code entry, shared by the signup-confirmation and
// password-reset scenes. Presentational: the owning route holds the value.
//
// One <input> per digit rather than a single masked field — it matches the
// emailed code's shape, gives per-digit focus affordance, and lets browsers
// autofill from the "one-time-code" hint. The value is always the source of
// truth (a plain string of up to `length` digits); the boxes just render
// value[i], so a paste that fills all six re-renders every box from one
// onChange.
import { useCallback, useEffect, useRef } from "react"

export type OtpInputProps = {
  value: string
  onChange: (next: string) => void
  /** Fired when the last digit lands, so the route can auto-submit. */
  onComplete?: (code: string) => void
  length?: number
  disabled?: boolean
  autoFocus?: boolean
  /** Labels the group for screen readers. */
  ariaLabel?: string
  /** Set when the code was rejected — paints the boxes in the error state. */
  invalid?: boolean
  idPrefix?: string
}

const DIGITS_ONLY = /\D/g

export function OtpInput({
  value,
  onChange,
  onComplete,
  length = 6,
  disabled = false,
  autoFocus = false,
  ariaLabel = "Verification code",
  invalid = false,
  idPrefix = "otp",
}: OtpInputProps) {
  const boxes = useRef<Array<HTMLInputElement | null>>([])
  // onComplete must not re-fire on unrelated re-renders (a parent state change
  // while the code is already full), so remember what we last announced.
  const announced = useRef<string | null>(null)

  const focusBox = useCallback((index: number) => {
    const el = boxes.current[Math.max(0, Math.min(length - 1, index))]
    el?.focus()
    el?.select()
  }, [length])

  const commit = useCallback(
    (next: string) => {
      const clean = next.replace(DIGITS_ONLY, "").slice(0, length)
      onChange(clean)
      return clean
    },
    [length, onChange],
  )

  useEffect(() => {
    if (value.length < length) {
      announced.current = null
      return
    }
    if (announced.current === value) return
    announced.current = value
    onComplete?.(value)
  }, [value, length, onComplete])

  useEffect(() => {
    if (autoFocus) focusBox(0)
    // Mount-only: refocusing on every value change would fight the per-key
    // advance below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleChange(index: number, raw: string) {
    const typed = raw.replace(DIGITS_ONLY, "")
    if (!typed) return
    // Typing into a filled box replaces that digit; a multi-char burst
    // (autofill, fast paste into one box) spills forward from here.
    const chars = value.split("")
    for (let i = 0; i < typed.length && index + i < length; i += 1) {
      chars[index + i] = typed[i]
    }
    commit(chars.join("").slice(0, length))
    focusBox(Math.min(index + typed.length, length - 1))
  }

  function handleKeyDown(index: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace") {
      e.preventDefault()
      const chars = value.split("")
      if (chars[index]) {
        // Clear the current box but stay put, so a second Backspace steps back.
        chars[index] = ""
        commit(chars.join(""))
        return
      }
      if (index > 0) {
        chars[index - 1] = ""
        commit(chars.join(""))
        focusBox(index - 1)
      }
      return
    }
    if (e.key === "ArrowLeft" && index > 0) {
      e.preventDefault()
      focusBox(index - 1)
      return
    }
    if (e.key === "ArrowRight" && index < length - 1) {
      e.preventDefault()
      focusBox(index + 1)
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text").replace(DIGITS_ONLY, "")
    if (!pasted) return
    // Pasting the whole code anywhere in the row fills from the start — users
    // copy "483920" out of the email and drop it wherever the caret happens
    // to be.
    e.preventDefault()
    const next = commit(pasted)
    focusBox(next.length >= length ? length - 1 : next.length)
  }

  return (
    <div
      className={`otp-row${invalid ? " otp-row-invalid" : ""}`}
      role="group"
      aria-label={ariaLabel}
    >
      {Array.from({ length }, (_, i) => (
        <input
          key={i}
          id={`${idPrefix}-${i}`}
          ref={(el) => {
            boxes.current[i] = el
          }}
          className="otp-box"
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={length}
          // Only the first box carries the hint: browsers fill the whole code
          // into it, and handleChange spills the rest across the row.
          autoComplete={i === 0 ? "one-time-code" : "off"}
          aria-label={`Digit ${i + 1} of ${length}`}
          value={value[i] ?? ""}
          disabled={disabled}
          onChange={(e) => handleChange(i, e.target.value)}
          onKeyDown={(e) => handleKeyDown(i, e)}
          onPaste={handlePaste}
          onFocus={(e) => e.target.select()}
        />
      ))}
    </div>
  )
}
