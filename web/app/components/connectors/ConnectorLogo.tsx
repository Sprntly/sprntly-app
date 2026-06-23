/**
 * Shared connector logo tile.
 *
 * Renders a connector's real full-color brand logo (bundled locally under
 * `web/public/connectors/<id>.svg`) on a white tile, with the single-letter
 * brand glyph as the fallback if the SVG can't load. Connectors without a
 * bundled logo render the letter glyph on their brand-color background.
 *
 * One component drives every surface (Settings → Connectors, the onboarding
 * grid, the connect modal, and the Configure drawer) so the logos stay
 * identical everywhere. Each caller passes its own `className` (`.logo`,
 * `.conn-logo`, `.drawer-icon`, …) so the existing per-surface sizing CSS
 * still applies — this component only owns the inner img/letter markup and
 * the white-tile-vs-brand-color background decision.
 */
import type { ConnectorItemRow } from "../../types/content"

// All fields optional: connectorLetter() degrades gracefully when any are
// absent, and callers pass partial shapes (e.g. just a name) in tests.
type LogoItem = Partial<
  Pick<ConnectorItemRow, "logoSvg" | "logoColor" | "logoText" | "logo" | "name">
>

/** The single-letter fallback glyph for a connector. */
export function connectorLetter(item: LogoItem): string {
  return item.logoText ?? item.logo ?? item.name?.charAt(0) ?? "?"
}

export function ConnectorLogo({
  item,
  className,
  /** Logo image size as a percentage of the tile (default 70%). */
  imgScale = "70%",
}: {
  item: LogoItem
  className?: string
  imgScale?: string
}) {
  const letter = connectorLetter(item)

  if (item.logoSvg) {
    return (
      <span
        className={className}
        style={{
          background: "#fff",
          border: "1px solid #E5E7EB",
          color: item.logoColor ?? "#444",
          position: "relative",
        }}
      >
        {/* Letter is the fallback; the real brand logo overlays it and is
            hidden if the image fails to load. */}
        <span aria-hidden>{letter}</span>
        <img
          src={item.logoSvg}
          alt=""
          aria-hidden
          loading="lazy"
          style={{
            position: "absolute",
            inset: 0,
            margin: "auto",
            width: imgScale,
            height: imgScale,
            objectFit: "contain",
          }}
          onError={(e) => {
            e.currentTarget.style.display = "none"
          }}
        />
      </span>
    )
  }

  return (
    <span
      className={className}
      style={{ background: item.logoColor ?? "#444" }}
      aria-hidden
    >
      {letter}
    </span>
  )
}
