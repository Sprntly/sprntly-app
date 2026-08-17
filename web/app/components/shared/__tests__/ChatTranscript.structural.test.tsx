// @vitest-environment node
//
// Structural guard on the turn-render extraction: the three chat shells no
// longer hand-roll the turn wrapper's own DOM — that lives in `ChatBubble`
// alone. A working-tree check only (no historical git comparison — CI's
// checkout shape is not guaranteed to carry the ref a diff would need).
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const here = path.dirname(fileURLToPath(import.meta.url))
const webRoot = path.resolve(here, "../../../..")

const SHELLS = [
  path.join(webRoot, "app/components/screens/app/ChatScreen.tsx"),
  path.join(webRoot, "app/components/screens/app/projects/ProjectPrivateChat.tsx"),
  path.join(webRoot, "app/components/screens/app/projects/ProjectGroupChat.tsx"),
]

// The turn-wrapper class names ChatBubble alone now owns. A shell that still
// wrote one of these itself would be a second, undeclared copy of the leaf.
const OWNED_BY_CHATBUBBLE = ['"bc-user-head"', '"bc-agent-head"', '"bc-user-bubble"', '"bc-agent-body"']

describe("chat shells delegate turn DOM to the shared leaves", () => {
  it.each(SHELLS)("%s does not write ChatBubble's own wrapper classes", (file) => {
    const src = readFileSync(file, "utf8")
    for (const marker of OWNED_BY_CHATBUBBLE) {
      expect(src).not.toContain(marker)
    }
  })
})
