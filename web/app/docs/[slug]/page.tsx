import type { Metadata } from "next"
import { DOCS, getDoc } from "../content"
import { DocsShell } from "../DocsShell"

// Static export: prebuild one HTML page per registered doc.
export function generateStaticParams() {
  return DOCS.map((doc) => ({ slug: doc.slug }))
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>
}): Promise<Metadata> {
  const { slug } = await params
  const doc = getDoc(slug)
  if (!doc) return { title: "Documentation · Sprntly" }
  return {
    title: `${doc.title} · Sprntly Docs`,
    description: doc.description,
  }
}

export default async function DocPage({
  params,
}: {
  params: Promise<{ slug: string }>
}) {
  const { slug } = await params
  return <DocsShell slug={slug} />
}
