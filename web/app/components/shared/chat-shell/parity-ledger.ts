/**
 * The checked-in composer/capability opt-out ledger — the durability data
 * file `ChatShell.composer-parity-guard.test.ts` (§F) reads to silence a
 * confirmed by-design residual. Adding a future opt-out is ONE array entry
 * with a concrete reason + a neutral `owner` tag — never a DBD id/name. A
 * project surface that diverges from main's composer/capability set WITHOUT
 * an entry here fails the guard (fail-closed).
 */
import type { ChatSurfaceKind } from "./types"

export type ParityOptOut = {
  /** A `ChatComposer` prop name (`composer.<propName>`) or a
   *  `PROJECT_CAPABILITY_MANIFEST` entry (`clarify.structured`,
   *  `overlays.artifactPanel`, …). */
  capability: string
  surface: ChatSurfaceKind
  /** Why this surface legitimately diverges from main — never blank. */
  reason: string
  /** A neutral surface-team tag, never a DBD id/name. */
  owner: string
}

export const PARITY_OPT_OUTS: ParityOptOut[] = [
  {
    capability: "composer.stop",
    surface: "project_group",
    reason:
      "Group replies are backgrounded/fire-and-forget multi-party (capabilities.streaming=false) — no cancel/stop affordance.",
    owner: "projects-chat",
  },
  {
    capability: "clarify.structured",
    surface: "project_group",
    reason:
      "Multi-party backgrounded generation has no synchronous per-user surface for a structured clarify card.",
    owner: "projects-chat",
  },
  {
    capability: "tabs.multiConversation",
    surface: "project_private",
    reason: "Projects are one durable conversation per (project, caller); no tab strip / multi-conversation history.",
    owner: "projects-chat",
  },
  {
    capability: "tabs.multiConversation",
    surface: "project_group",
    reason: "Projects are one durable conversation per (project, caller); no tab strip / multi-conversation history.",
    owner: "projects-chat",
  },
  {
    capability: "overlays.artifactPanel",
    surface: "project_private",
    reason: "The artifact panel is main's tab spawn/reuse machinery; project surfaces have no tab host to open into.",
    owner: "projects-chat",
  },
  {
    capability: "overlays.artifactPanel",
    surface: "project_group",
    reason: "The artifact panel is main's tab spawn/reuse machinery; project surfaces have no tab host to open into.",
    owner: "projects-chat",
  },
  {
    capability: "composer.draftPersistence",
    surface: "project_private",
    reason: "Shell-owned useState draft, lost on unmount (minor); not persisted across mount.",
    owner: "projects-chat",
  },
  {
    capability: "composer.draftPersistence",
    surface: "project_group",
    reason: "Shell-owned useState draft, lost on unmount (minor); not persisted across mount.",
    owner: "projects-chat",
  },
]
