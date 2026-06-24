"use client"

import { RoadmapDocScreen } from "../../components/screens/app/RoadmapDocScreen"

// Read-only `roadmapdoc` artifact view — renders the company's uploaded roadmap
// (GET /v1/company/roadmap-doc) in the design's clean word-doc layout. Wired the
// same way as the other artifact routes: a thin route page over a screen
// component.
export default function RoadmapPage() {
  return <RoadmapDocScreen />
}
