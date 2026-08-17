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
  // ── Render/context divergences the render-inheritance guard tracks ──
  // Group backgrounded/no-stop reuses the existing `composer.stop` entry above
  // (the "streaming" divergence); no-multi-tab reuses the existing
  // `tabs.multiConversation` entries — NEITHER is duplicated here.
  {
    capability: "render.landing",
    surface: "project_private",
    reason: "Project chats are thread-only — there is no home/landing composer state to render (main's landing mode).",
    owner: "projects-chat",
  },
  {
    capability: "render.landing",
    surface: "project_group",
    reason: "Project chats are thread-only — there is no home/landing composer state to render (main's landing mode).",
    owner: "projects-chat",
  },
  {
    capability: "open.destination",
    surface: "project_private",
    reason: "Open-artifact routes to this project's artifacts modal, not main's side panel / tab host (the open-destination seam a later sub-phase unifies).",
    owner: "projects-chat",
  },
  {
    capability: "open.destination",
    surface: "project_group",
    reason: "Open-artifact routes to this project's artifacts modal, not main's side panel / tab host (the open-destination seam a later sub-phase unifies).",
    owner: "projects-chat",
  },
  {
    capability: "respond.gate",
    surface: "project_group",
    reason: "Multi-human group threads run a when-to-respond/trigger gate (mention/solo/continuation/interjection) that main's 1:1 chat has no equivalent for.",
    owner: "projects-chat",
  },
  {
    capability: "context.multiParty",
    surface: "project_group",
    reason: "Group renders a speaker-attributed multi-party transcript (roster, author labels, start-aligned peer turns) with no counterpart in main's single-speaker context.",
    owner: "projects-chat",
  },
  {
    capability: "membership.roster",
    surface: "project_private",
    reason: "Project surfaces are scoped to a project's membership; main chat has no project roster/membership dimension.",
    owner: "projects-chat",
  },
  {
    capability: "membership.roster",
    surface: "project_group",
    reason: "Project surfaces are scoped to a project's membership; main chat has no project roster/membership dimension.",
    owner: "projects-chat",
  },
  {
    capability: "mutation.confirmGate",
    surface: "project_private",
    reason: "Project PRD edits render a confirm/cancel gate before writing; main chat has no such confirmation gate on its edits.",
    owner: "projects-chat",
  },
  {
    capability: "mutation.confirmGate",
    surface: "project_group",
    reason: "Project PRD edits render a confirm/cancel gate before writing; main chat has no such confirmation gate on its edits.",
    owner: "projects-chat",
  },
  {
    capability: "hostOrchestration.clientSide",
    surface: "project_group",
    reason: "Group chat is server-classified — it posts to the backend which classifies/answers (the group qa_agent), so it does NOT consume the client-side host-orchestration services (intent executors, next-prompts, inline classify-envelope cards). This is a sanctioned NON-consumer, not a fork; group's answer parity is delivered backend-side, not through these client hooks.",
    owner: "projects-chat",
  },
  // ── Shell-default affordance opt-outs (the absence-check inversion) ──
  {
    capability: "affordance.nextPrompts",
    surface: "project_group",
    reason:
      "Group is server-classified, backgrounded multi-party (no synchronous per-user settle moment) — no next-prompt strip; parity delivered backend-side, not via this client hook.",
    owner: "projects-chat",
  },
]
