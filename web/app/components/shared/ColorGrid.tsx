"use client"

import { COLOR_SWATCHES } from "../../(app)/artifacts/doc/editorSchema"

/**
 * The colour picker both toolbars open: a grid of swatches, a way back to the
 * document's own colour, and a native picker for anything not on the grid.
 *
 * It replaced a five-entry dropdown. A list works for fonts, where the options
 * ARE the vocabulary, and fails for colour, where what someone wants is a point
 * in a space — naming five of them mostly tells people the one they want is
 * unreachable. The grid is the shape everyone has already learned from every
 * other document tool, and `<input type="color">` covers the rest natively:
 * every platform already ships a colour picker, and shipping a second one in
 * JavaScript would be a worse picker that also has to be maintained.
 *
 * Every value it can emit is a hex literal, which is what the server's
 * `color` / `background-color` allowlist keeps — see editorSchema's note on
 * the two ends of that contract.
 */
export function ColorGrid({ onPick, clearLabel, testId }: {
  /** Called with a hex, or `""` for "back to the default". */
  onPick: (value: string) => void
  /** What the reset row says — "Default" reads right for text, "None" for a
   *  highlight, which is not a colour the text HAS but one it sits on. */
  clearLabel: string
  testId: string
}) {
  return (
    <div className="prd-colorgrid" data-testid={testId}>
      <button
        type="button"
        className="prd-colorgrid-clear"
        data-testid={`${testId}-clear`}
        // Same reason as every control in these bars: the selection is the
        // argument, and it does not survive the focus move a plain mousedown
        // causes.
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => onPick("")}
      >
        {clearLabel}
      </button>

      {COLOR_SWATCHES.map((row, i) => (
        <div className="prd-colorgrid-row" key={i}>
          {row.map((swatch) => (
            <button
              key={swatch.value}
              type="button"
              className="prd-colorgrid-swatch"
              // The NAME, not the hex: "Cornflower light" is what a person can
              // act on, and it is all a screen reader has to go on.
              title={swatch.label}
              aria-label={swatch.label}
              data-testid={`${testId}-${swatch.value.slice(1).toLowerCase()}`}
              style={{ background: swatch.value }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onPick(swatch.value)}
            />
          ))}
        </div>
      ))}

      <label className="prd-colorgrid-custom">
        Custom
        {/* `onInput` as well as `onChange`: a native picker fires `input` while
            the user drags and `change` on commit, and browsers disagree about
            which one a keyboard-driven pick sends. Applying on both means the
            colour follows the drag, which is what the control looks like it
            promises. */}
        <input
          type="color"
          data-testid={`${testId}-custom`}
          onInput={(e) => onPick((e.target as HTMLInputElement).value)}
          onChange={(e) => onPick(e.target.value)}
        />
      </label>
    </div>
  )
}
