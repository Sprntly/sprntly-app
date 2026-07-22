import type { Metadata } from "next"
import { DocsHome } from "./DocsHome"

export const metadata: Metadata = {
  title: "Documentation · Sprntly",
  description:
    "Sprntly product documentation — guides for going from signals to PRD to prototype to build.",
}

export default function DocsIndexPage() {
  return <DocsHome />
}
