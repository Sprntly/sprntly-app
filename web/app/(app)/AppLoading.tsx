"use client"

import { SprntlyThinkingMark } from "../components/shared/SprntlyMark"

/**
 * The full-page loading shell: a white field and the spinning mark, no words.
 *
 * It is the HAND-OFF from the server splash (`app/layout.tsx`), which paints
 * the identical mark into the first frame before any bundle arrives. Both used
 * to sit under the word "Loading…", so a reload read as three separate things
 * happening — a logo with a caption, then the logo vanishing and the caption
 * staying, then the app. Same mark on both sides of the hand-off means one
 * animation runs from the first frame until the app is on screen.
 *
 * Its own module rather than an export of `AuthGate`, which is where it lived:
 * importing it from there pulled the entire auth-screen tree (share gate →
 * entry gate → not-authorized copy) into every consumer, for a spinner.
 *
 * The splash inlines its own copy of this SVG on purpose — that frame renders
 * before React exists, which is the whole reason it is inline. Keep the two in
 * step; the geometry is identical.
 */
export function AppLoading() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#FFFFFF",
        color: "#111111",
      }}
    >
      <SprntlyThinkingMark size={56} />
    </div>
  )
}
