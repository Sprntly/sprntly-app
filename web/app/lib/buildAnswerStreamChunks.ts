/**
 * Builds cumulative markdown slices so each step is valid-ish for react-markdown
 * (avoids splitting mid-paragraph when possible; falls back to lines / sentences / words).
 */
export function buildAnswerStreamChunks(answer: string): string[] {
  const t = answer.replace(/\r\n/g, "\n").trimEnd()
  if (!t) return [""]

  const paragraphs = t.split(/\n{2,}/)
  if (paragraphs.length > 1) {
    return paragraphs.map((_, i) => paragraphs.slice(0, i + 1).join("\n\n"))
  }

  const block = paragraphs[0] ?? ""
  const lines = block.split("\n")
  if (lines.length > 1) {
    return lines.map((_, i) => lines.slice(0, i + 1).join("\n"))
  }

  const line = lines[0] ?? ""
  if (line.length <= 220) return [line]

  const sentences = line.split(/(?<=[.!?…])\s+/)
  if (sentences.length > 1) {
    return sentences.map((_, i) => sentences.slice(0, i + 1).join(" "))
  }

  const words = line.split(/(\s+)/)
  const step = 12
  const out: string[] = []
  let acc = ""
  let n = 0
  for (const tok of words) {
    acc += tok
    if (tok.trim()) n++
    if (n > 0 && n % step === 0) out.push(acc)
  }
  if (out.length === 0 || out[out.length - 1] !== line) out.push(line)
  return dedupeTail(out)
}

function dedupeTail(chunks: string[]): string[] {
  const r: string[] = []
  for (const c of chunks) {
    if (r.length === 0 || r[r.length - 1] !== c) r.push(c)
  }
  return r
}
