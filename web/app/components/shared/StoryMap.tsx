"use client"

import { priorityPill } from "./TicketDetail"
import type { GeneratedStory } from "../../lib/api"

/** A ticket with its original index into the generated set (so a card click
 *  opens the right detail). */
type Indexed = { s: GeneratedStory; i: number }

/** The Jeff-Patton sizing gate, computed on the frontend from the ticket set —
 *  the backend places each ticket (`activity`/`release`); we decide whether the
 *  map is worth showing. Additive, so no batch-level metadata/migration is
 *  needed. The map is built when ≥2 of these fire: more than one user activity,
 *  more than ~12 requirements, more than one release. */
export function storyMapSizing(stories: GeneratedStory[]): {
  build: boolean
  activities: string[]
  releases: string[]
  reason: string
} {
  const placed = stories.filter((s) => (s.activity || "").trim())
  // Distinct activities / releases in first-seen order (the narrative journey).
  const activities = distinct(placed.map((s) => s.activity!.trim()))
  const releases = orderReleases(distinct(placed.map((s) => (s.release || "").trim()).filter(Boolean)))
  const signals =
    Number(activities.length > 1) +
    Number(stories.length > 12) +
    Number(releases.length > 1)
  const build = placed.length > 0 && signals >= 2
  const reason = build
    ? `built — ${activities.length} activities across ${releases.length} release${releases.length !== 1 ? "s" : ""}`
    : `not needed — sized flat (${activities.length || 1} activit${(activities.length || 1) !== 1 ? "ies" : "y"}, ${stories.length} tickets, ${releases.length || 1} release${(releases.length || 1) !== 1 ? "s" : ""})`
  return { build, activities, releases, reason }
}

function distinct(xs: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const x of xs) if (!seen.has(x)) { seen.add(x); out.push(x) }
  return out
}

/** Release order: anything that reads as "Release 1" / "walking skeleton" first,
 *  then by any trailing number, then first-seen. Keeps the walking skeleton on
 *  top (it's the minimal end-to-end slice = Release 1). */
function orderReleases(releases: string[]): string[] {
  const rank = (r: string): number => {
    const low = r.toLowerCase()
    if (low.includes("walking skeleton")) return -1
    const m = low.match(/release\s*(\d+)|\b(\d+)\b/)
    if (m) return Number(m[1] ?? m[2])
    return 999
  }
  return [...releases].sort((a, b) => rank(a) - rank(b))
}

function isWalkingSkeleton(release: string): boolean {
  const low = release.toLowerCase()
  return low.includes("walking skeleton") || /release\s*0*1\b/.test(low)
}

/** Story-map board (Patton): activity backbone left-to-right, tickets placed
 *  under the activity they serve, grouped into release-slice bands with Release
 *  1 (the walking skeleton) on top. Renders over the SAME tickets as the list —
 *  it never invents cards. */
export function StoryMap({ stories, onOpen }: {
  stories: GeneratedStory[]
  onOpen: (index: number) => void
}) {
  const indexed: Indexed[] = stories.map((s, i) => ({ s, i }))
  const { activities, releases } = storyMapSizing(stories)
  // Tickets with no explicit release fall into an "Unscheduled" band at the end.
  const bands = releases.length ? releases : [""]
  const unplaced = indexed.filter((x) => !(x.s.release || "").trim() && (x.s.activity || "").trim())

  const cell = (activity: string, release: string): Indexed[] =>
    indexed.filter(
      (x) => (x.s.activity || "").trim() === activity && (x.s.release || "").trim() === release,
    )

  return (
    <div className="tkv2-map">
      <div className="tkv2-map-grid" style={{ gridTemplateColumns: `repeat(${activities.length}, minmax(190px, 1fr))` }}>
        {/* Backbone row — the user's narrative journey, left to right */}
        {activities.map((a, i) => (
          <div key={`act-${i}`} className={`tkv2-act${i === activities.length - 1 ? " tkv2-act--last" : ""}`}>
            <span className="n">{`A${i + 1}`}</span>
            <span className="t">{a}</span>
          </div>
        ))}

        {/* Release bands — each spans the full backbone width, then a row of cells */}
        {bands.map((release, ri) => (
          <div key={`band-${ri}`} className="tkv2-map-band" style={{ gridColumn: `1 / -1` }}>
            <div className={`tkv2-rel${isWalkingSkeleton(release) ? " tkv2-rel--skel" : ""}`}>
              {release || "Unscheduled"}{isWalkingSkeleton(release) ? " · walking skeleton" : ""}
            </div>
            <div className="tkv2-map-row" style={{ gridTemplateColumns: `repeat(${activities.length}, minmax(190px, 1fr))` }}>
              {activities.map((a, ci) => (
                <div key={`cell-${ri}-${ci}`} className="tkv2-map-cell">
                  {cell(a, release).map(({ s, i }) => (
                    <MapCard key={i} story={s} index={i} skeleton={isWalkingSkeleton(release)} onOpen={onOpen} />
                  ))}
                </div>
              ))}
            </div>
          </div>
        ))}

        {/* Tickets placed on the backbone but with no release */}
        {unplaced.length > 0 && bands.length === 1 && bands[0] !== "" ? (
          <div className="tkv2-map-band" style={{ gridColumn: "1 / -1" }}>
            <div className="tkv2-rel">Unscheduled</div>
            <div className="tkv2-map-row" style={{ gridTemplateColumns: `repeat(${activities.length}, minmax(190px, 1fr))` }}>
              {activities.map((a, ci) => (
                <div key={`u-${ci}`} className="tkv2-map-cell">
                  {unplaced.filter((x) => (x.s.activity || "").trim() === a).map(({ s, i }) => (
                    <MapCard key={i} story={s} index={i} skeleton={false} onOpen={onOpen} />
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** A compact ticket card on the map — deep-links to the same detail as the list. */
function MapCard({ story, index, skeleton, onOpen }: {
  story: GeneratedStory; index: number; skeleton: boolean; onOpen: (i: number) => void
}) {
  const pill = priorityPill(story.priority)
  const acCount = story.acceptance_criteria.length
  return (
    <button type="button" className={`tkv2-map-card${skeleton ? " tkv2-map-card--skel" : ""}`} onClick={() => onOpen(index)}>
      <span className="tkv2-key">{`T-${index + 1}`}</span>
      <div className="tkv2-map-card-title">{story.title}</div>
      <div className="tkv2-map-card-row">
        <span className={`tkv2-pill tkv2-pill--${pill.variant}`}>{pill.label}</span>
        {acCount > 0 ? <span className="tkv2-acchip">{acCount} AC</span> : null}
      </div>
    </button>
  )
}
