"use client"

/**
 * The Sprntly mark, wordmark lockup, and the animated working state — ONE
 * definition each, for the whole app.
 *
 * Geometry is the v2 brand pack's, verbatim (viewBox 0 0 84 84): four solid
 * bars in a pinwheel around a centred square aperture. Mass centroid,
 * bounding-box centre and aperture centre coincide, which is why the mark can
 * be dropped into a circular chip without optical re-centring.
 *
 * Every variant fills with `currentColor` — the pack ships black and white
 * files, but a component that inherits colour covers both and every tinted
 * context (the agent chip's accent green) with one copy. Nothing here sets a
 * colour of its own.
 *
 * NOT an app-icon: these live outside `app-icons.tsx` deliberately. That file
 * is a UI glyph set (chevrons, documents, a generic sparkle) where any icon can
 * be swapped for a better-drawn one. This is the brand, it is drawn to a spec,
 * and it should be found by name when someone goes looking for the logo.
 */

/** The static mark. Square; size is the rendered edge in px. */
export function SprntlyMark({ size = 16, title }: { size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 84 84"
      fill="currentColor"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {title ? <title>{title}</title> : null}
      <rect x="0" y="0" width="60" height="22" />
      <rect x="62" y="0" width="22" height="60" />
      <rect x="24" y="62" width="60" height="22" />
      <rect x="0" y="24" width="22" height="60" />
    </svg>
  )
}

/**
 * The working state: blades close toward the centre while the whole group
 * spins, on one continuous 1.8s cycle with no pause and no reset.
 *
 * This is the animation the marketing site runs (`css-new.css`'s `mark-iris`),
 * reproduced here rather than re-invented — same 1.8s, same 19-unit blade
 * travel, same asymmetric spin curve — so the product and the site are visibly
 * the same product.
 *
 * The viewBox is 126 wide, not 84: the blades travel 19 units inward from a
 * 21-unit inset, so the resting frame is the static mark centred in a box with
 * room for the closing motion. Rendering it at the same `size` as
 * `SprntlyMark` therefore draws a slightly smaller mark, which is correct — it
 * is the same mark with its movement margin included.
 *
 * Keyframes live in `globals.css` under `.spr-*`. Reduced motion is honoured
 * there (the animation is only applied under `prefers-reduced-motion:
 * no-preference`), so this component renders the resting frame — which is the
 * static mark exactly — for anyone who has asked for less movement.
 */
export function SprntlyThinkingMark({ size = 16, title }: { size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 126 126"
      fill="currentColor"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {title ? <title>{title}</title> : null}
      <g className="spr-iris">
        <rect className="spr-blade-top" x="21" y="21" width="60" height="22" />
        <rect className="spr-blade-right" x="83" y="21" width="22" height="60" />
        <rect className="spr-blade-bottom" x="45" y="83" width="60" height="22" />
        <rect className="spr-blade-left" x="21" y="45" width="22" height="60" />
      </g>
    </svg>
  )
}

/**
 * Mark + wordmark. Sized by HEIGHT — the lockup is 340×84, so a caller asking
 * for 20px tall gets ~81px wide, which is how a lockup is specified everywhere
 * it is used.
 *
 * The wordmark outlines are the pack's. Its README flags the typeface as a
 * working stand-in pending a licensed geometric grotesque, so expect this path
 * data to be replaced wholesale one day; the mark beside it is final and
 * independent of that.
 */
export function SprntlyLockup({ height = 20, title = "Sprntly" }: { height?: number; title?: string }) {
  return (
    <svg
      height={height}
      width={(height * 340) / 84}
      viewBox="0 0 340 84"
      fill="currentColor"
      role="img"
      aria-label={title}
    >
      <title>{title}</title>
      <rect x="0" y="0" width="60" height="22" />
      <rect x="62" y="0" width="22" height="60" />
      <rect x="24" y="62" width="60" height="22" />
      <rect x="0" y="24" width="22" height="60" />
      <path
        transform="translate(107.8 66) scale(0.074074 -0.074074)"
        d={WORDMARK_PATH}
      />
    </svg>
  )
}

/** The "sprntly" wordmark outlines, lifted verbatim from the brand pack's
 *  lockup SVG (`svg/lockup/sprntly-lockup-transparent.svg`). One opaque
 *  string on purpose: it is generated geometry with nothing to hand-edit,
 *  and the pack is the source of truth if it ever needs regenerating. */
const WORDMARK_PATH =
  "M167 157Q174 123 195.5 105.0Q217 87 277 87Q326 87 353.5 101.0Q381 115 381 140Q381 156 370.5 166.0Q360 176 334 184L167 236Q166 236 155.0 239.5Q144 243 139.5 245.0Q135 247 123.0 251.5Q111 256 104.5 261.0Q98 266 88.0 273.5Q78 281 72.5 290.0Q67 299 61.0 310.5Q55 322 52.0 337.0Q49 352 49 369Q49 452 108.5 500.5Q168 549 271 549Q380 549 442.0 500.5Q504 452 506 366H371Q370 439 270 439Q233 439 211.0 425.5Q189 412 189 389Q189 373 199.0 364.5Q209 356 238 347L415 296Q521 265 521 160Q521 129 510.0 99.5Q499 70 474.5 41.0Q450 12 401.0 -5.5Q352 -23 285 -23Q36 -23 30 157ZM876.0 549Q978.0 549 1040.0 467.0Q1102.0 385 1102.0 262.0Q1102.0 139 1037.5 57.5Q973.0 -24 876.0 -24Q776.0 -24 726.0 64V-218H586.0V540H726.0V460Q776.0 549 876.0 549ZM844.0 93Q896.0 93 929.0 140.0Q962.0 187 962.0 260Q962.0 337 929.5 384.5Q897.0 432 844.0 432.0Q791.0 432 758.5 385.0Q726.0 338 726.0 262.0Q726.0 186 758.5 139.5Q791.0 93 844.0 93ZM1172.0 540H1312.0V434Q1334.0 489 1374.0 519.0Q1414.0 549 1462.0 549Q1471.0 549 1479.0 548V406Q1454.0 410 1435.0 410Q1312.0 410 1312.0 287V0H1172.0ZM1529.0 540H1669.0V462Q1727.0 549 1831.0 549Q1917.0 549 1964.5 500.0Q2012.0 451 2012.0 362V0H1872.0V333Q1872.0 430 1782.0 430Q1731.0 430 1700.0 401.0Q1669.0 372 1669.0 324V0H1529.0ZM2350.0 529V436H2272.0V142Q2272.0 106 2281.0 94.5Q2290.0 83 2318.0 83Q2329.0 83 2350.0 86V-12Q2316.0 -23 2269.0 -23Q2132.0 -23 2132.0 104V436H2063.0V529H2132.0V674H2272.0V529ZM2550.0 729V0H2410.0V729ZM2791.0 -26V-22L2590.0 540H2744.0L2863.0 147L2975.0 540H3119.0L2897.0 -99Q2897.0 -100 2893.0 -112.5Q2889.0 -125 2886.5 -129.5Q2884.0 -134 2878.5 -146.5Q2873.0 -159 2867.0 -164.5Q2861.0 -170 2851.0 -180.5Q2841.0 -191 2828.5 -196.5Q2816.0 -202 2800.0 -208.0Q2784.0 -214 2763.0 -216.5Q2742.0 -219 2717.0 -219Q2695.0 -219 2667.0 -215V-110Q2689.0 -116 2704.0 -116Q2741.0 -116 2766.0 -90.0Q2791.0 -64 2791.0 -26Z"
