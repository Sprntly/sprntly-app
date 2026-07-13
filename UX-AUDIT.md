# UX Audit — Sprntly Web App

> Full-system UI/UX review of `web/`, compiled 2026-07-13.
> Items are numbered for reference — e.g. "fix 1, 4 and 7".
> Ranked by user impact: 🔴 Critical → 🟠 High → 🟡 Medium → ⚪ Polish.

---

## 🔴 Critical — data loss or silently broken features

### 1. Unsaved settings edits vanish on a single click
No pane warns when you switch settings sections (or sign out) with unsaved changes. The panes track `dirty` for the Save button, but nothing consumes it on navigation — `setSection` just `router.replace`s and the pane unmounts.
- `web/app/components/screens/app/SettingsScreen.tsx:212` (`setSection`), sign-out at `:216-226`

### 2. PRD autosave lies on failure
The debounced autosave's `catch` still sets the status to "Saved". A failed save shows the same green state as a successful one — silent data loss. The manual save path correctly toasts "Save failed", so the two paths contradict each other.
- `web/app/components/shared/PrdPanelContent.tsx:186`

### 3. Team screen is decorative (dead controls)
On the Team **screen** (not Team settings): the role `<select>` is uncontrolled with no `onChange` — changing a member's role does nothing; "Remove", "Resend invite", and "Revoke" have no `onClick` at all. An admin believes they changed permissions; nothing happened. Security-relevant silent failure.
- `web/app/components/screens/app/TeamScreen.tsx:57-60` (role select), `:70, :94, :97` (dead buttons)

### 4. Deleting a chat / template is one un-confirmed click
History's tiny `×` permanently deletes a conversation with no confirm, no undo, no toast. Template removal is the same. Sources *does* use `window.confirm`, so the app is inconsistently unsafe about the same class of action.
- `web/app/components/screens/app/ChatsScreen.tsx` (delete button + `handleDelete`)
- `web/app/components/screens/app/TemplatesScreen.tsx:270-282`

### 5. Locked-out users can't request a password reset
The sign-in lockout disables the submit button in forgot-password mode too (`disabled={submitting || lockoutMs > 0}`), and `onForgot` never consults lockout. The moment someone fails sign-in enough to lock out — exactly when they need a reset — the "Send reset link" button is disabled with no explanation. Hard dead-end.
- `web/app/components/auth/SignInView.tsx:89`, `web/app/sign-in/page.tsx:68`

### 6. Failed onboarding upload still advances
If every uploaded file fails to ingest, the render keys off `!uploadResult`, so the Upload button disappears and "Generate first brief →" appears. The user is pushed to generate a brief on an empty corpus with no way to retry the upload.
- `web/app/onboard/page.tsx:139-149`, `:285-298`

---

## 🟠 High — controls that lie or dead-end

### 7. Brief composer's Attach / Voice buttons are inert
On the brief, Attach shows a "File attachments aren't wired up yet" toast; the visually identical Attach on the chat composer opens a real file picker (PRD import). Voice is a toast too. Twin surfaces, one working affordance, one dead.
- `web/app/components/shared/BriefChat.tsx:1147-1151` vs `web/app/components/screens/app/ChatScreen.tsx:2040`

### 8. Advertised keyboard shortcuts don't exist
- The History search box shows a **⌘K** badge; no handler is wired anywhere reachable (the only ⌘K handler is gated on `AI_BAR_SCREENS`, which is an empty array).
- The brief composer shows a **⌘ /** hint, but the brief has no slash-command handling at all.
- `web/app/components/screens/app/ChatsScreen.tsx` (search input), `web/app/types.ts:97` (`AI_BAR_SCREENS = []`)
- `web/app/components/shared/BriefChat.tsx:1157-1160`

### 9. Slash-command dropdown isn't keyboard-driven
Typing `/prd` + Enter submits the literal string `/prd` to the ask agent instead of selecting the skill. No arrow-key highlight, no Enter-to-pick, no Escape-to-close — mouse only.
- `web/app/components/screens/app/ChatScreen.tsx:1388` (`handleComposerKeyDown`), dropdowns at `:1716` / `:2000`

### 10. Backlog's "Ask Sprntly to re-prioritize" bar always fails
A large, primary-looking textarea + send button whose placeholder promises re-prioritization — it always resolves to a "Not yet available / coming soon" toast. Users will repeatedly try it.
- `web/app/components/screens/app/BacklogScreen.tsx:798-806`, `:949-986`

### 11. Error states dead-end with no retry
- Failed chat turns render `bc-error` text only — retype the whole question to retry.
- Tickets **error** state has no action, while the sibling empty-run state offers "Regenerate".
- Evidence load error renders an EmptyPane with no retry.
- Tickets first-generation poll has no timeout/cap — a stuck server job spins the "Breaking into tickets…" spinner forever with no cancel.
- `web/app/components/screens/app/ChatScreen.tsx:1912`; `web/app/components/shared/ContentPanel.tsx:854-861` (tickets error), `:406-412` (evidence error), `:586-599` (poll)

### 12. Destructive actions inconsistently gated
MCP token "Revoke" (instantly breaks live AI clients) and Admin "Remove key" (instantly reroutes ALL company LLM calls off their own key) fire with no confirmation. Team settings' remove-member *does* confirm.
- `web/app/components/screens/app/settings/McpSettings.tsx:386-395`
- `web/app/components/screens/app/settings/AdminSettings.tsx:109-116`, `:181-194`

### 13. Verify-email "Continue" shows a false failure + missing in-flight states
`onContinue` awaits `auth.refresh()` then checks the **pre-refresh** closure value, so genuinely-verified users are told "we haven't seen your verification yet". The button has no loading/disabled state (double-clickable); Resend is only disabled *after* success. Google/OAuth buttons on sign-in/sign-up also have no submitting state.
- `web/app/verify-email/page.tsx:31-41`; `web/app/components/auth/VerifyEmailView.tsx:27-46`
- `web/app/sign-in/page.tsx:87`, `web/app/sign-up/page.tsx:69` (OAuth)

---

## 🟡 Medium — inconsistent mental models

### 14. One settings pane, two save models
- **Business Context**: the lens doc saves from the top bar, but "Company shape" has its own inline Save that isn't part of the bar's dirty state, isn't reverted by Discard, and is always enabled even with zero changes.
- **Comms & Brief**: Slack connect/disconnect/channel-pick persist instantly; the Email digest toggle right below animates "on" but only persists on a bar Save — flip it, leave the pane, it's silently lost.
- `web/app/components/screens/app/settings/BusinessContextSettings.tsx:446-450` vs `:706-716`, dirty calc `:687-692`
- `web/app/components/screens/app/settings/NotificationsSettings.tsx:374-381` vs `:332-362`

### 15. Insight-card CTAs mis-fire during load
- The prototype button has no loading guard: while the brief-prototype map resolves it reads "Generate prototype", and clicking mis-navigates to the empty "No PRD selected" canvas. The PRD button beside it handles this with a "Loading…" disabled state.
- "Generate PRD" can silently open an **empty** PRD panel when no meta resolves — no toast, no generation.
- `web/app/components/screens/app/ChatScreen.tsx:1852-1858`, `:1962-1968`, `:634-637`

### 16. Settings panes flash "Loading…" on every visit
Team, Connectors, MCP, and Business Context re-fetch on every mount. Business Context is worst: two serial fetches (roster for `canEdit`, then the doc) before anything renders. Profile now hydrates instantly from `WorkspaceContext` — the model the rest should follow.
- `web/app/components/screens/app/settings/BusinessContextSettings.tsx:603-638`
- `TeamSettings.tsx:612`, `ConnectorsSettings.tsx:392`, `McpSettings.tsx:429`, `NotificationsSettings.tsx:194`

### 17. Every settings pane shows its title twice
Sticky bar says "Profile" and the serif heading right below says "Profile" again — on the full-bleed panes AND the harmonized legacy panes (bar + their own `set-h`).
- e.g. `ProfileSettings.tsx:283` & `:293`; `TeamSettings.tsx:115`; `ConnectorsSettings.tsx:181`; `McpSettings.tsx:273`

### 18. "Saved" chip can lie after edit-then-revert
Save → edit a field → undo the edit: `dirty` returns to false and the green "Saved" chip reappears, implying the intermediate state was persisted. `saved` should reset on first edit (Business Context already does this; Profile and Comms & Brief don't).
- `web/app/components/screens/app/settings/SettingsLayout.tsx:161-169` (`saved && !dirty`)

### 19. Right-panel header always says "PRD · …"
The panel header is hardcoded to the PRD title even when the Evidence or Tickets tab is active.
- `web/app/components/shared/ContentPanel.tsx:258`

### 20. Silent onboarding failures (no catch)
Several Continue/Skip paths wrap advance+navigate in `try/finally` with no `catch`: on failure the button re-enables and nothing happens — no error, no navigation. Sibling steps (BusinessContext `next`, Strategy `finish`) do surface errors, so behavior is inconsistent.
- `web/app/components/screens/onboarding/Connectors.tsx:259-271`, `ApiKey.tsx:73-83`, `:99-106`, `BusinessContext.tsx:304-313`

### 21. Onboarding niggles
- "Generate" step error offers only Retry — no Back to fix inputs (upload step has Back).
- Workspace step's Back is a bare text link; every other step uses the footer Back button.
- Sign-in error text isn't cleared when toggling into/out of forgot-password mode, so stale errors show against the wrong form.
- `web/app/onboard/page.tsx:303-317`; `Workspace.tsx:112-126`; `web/app/sign-in/page.tsx:154-155`

---

## ⚪ Polish — the app doesn't feel like one product

### 22. Every library screen has a different header
History has the new full-width sticky bar (title · search · New chat). Artifacts has **no title and no search at all** (just a floated "+ Upload PRD"); Sources/Team use `main-header`; Templates uses `tpl-top`; Backlog uses `bl-topbar`. Five patterns across sibling screens; Artifacts looks unfinished next to History.
- `ChatsScreen.tsx:629-674` vs `ArtifactsScreen.tsx:438-471`, `SourcesScreen.tsx:195-219`, `TemplatesScreen.tsx:99-116`, `BacklogScreen.tsx:813-851`

### 23. Four different time formats across sibling lists
History: "Mon 3:45 PM" · Artifacts: "3h ago" · Templates: "May 3, 2026" · Sources: its own relative format. Identical-looking rows express recency four ways.

### 24. Empty states have no call to action
`EmptyPane` can't render a button, so empty states say "start a new chat from the home screen" while the New chat button is on the very screen the user is looking at.
- `web/app/components/shared/EmptyPane.tsx:10-26`

### 25. Smaller items
- Pin toggle silently no-ops on non-persisted (in-memory) chats — `ChatsScreen.tsx` `handlePin` early-returns without feedback.
- History rows are mouse-only (no `role="button"` / `tabIndex` / Enter) while Artifacts rows are keyboard-accessible.
- Backlog "Sync with backlog" toasts "Synced" on a fixed 800ms timer regardless of whether the refetch succeeded — `BacklogScreen.tsx:756-764`.
- PRD editor's "Table" toolbar button has no `onClick` — inert. Insert-link uses `window.prompt`; version "Restore" does a full `window.location.reload()` — `PrdPanelContent.tsx:74-79`, `:358`.
- Brief composer's "Source" button inserts a bare `@` with no picker — `BriefChat.tsx:998`, `:1154`.
- Onboarding shows a pulsing "Saved" pill before anything has been saved — `OnboardingChrome.tsx:63-66`.
- BusinessInfo's Continue looks enabled while the footer says "Pick N more metrics to continue" — `BusinessInfo.tsx:327-328`.
- Sign out is only discoverable inside Settings → Account; the main rail's identity row offers nothing.
- A fresh profile's prefilled browser timezone never reads as dirty, so it can't be saved on its own — `ProfileSettings.tsx` (seed goes into both field and snapshot).
- Non-admins opening Settings → Admin briefly see the admin pane ("Claude API key" + Loading…) before the 403 swaps in the restricted message — `AdminSettings.tsx:62-71`.
- Composer placeholders diverge between chat ("type / for skills") and brief ("try \"generate PRD\"…") — `ChatScreen.tsx:2031` vs `BriefChat.tsx:1136`.
- Artifacts loading skeleton draws circles while prototype rows render 64×48 thumbnails — visible reshape on load; filtering can also hide the row whose panel is open, orphaning the selection — `ArtifactsScreen.tsx:239-252`, `:210`.
- Settings shows two stacked sidebars with no explicit "back to app" affordance — exit relies on noticing the main rail.

---

## Suggested order of attack

1. **1–6** — genuine data-loss / trust bugs; fix before anything cosmetic.
2. **7–13** — controls that lie or dead-end; these make the product feel broken.
3. **14–21** — consistency of save/loading/error models.
4. **22–25** — visual/interaction consistency debt.

**Note (not user-facing):** `FirstBrief.tsx` and the standalone `Metrics.tsx` onboarding picker appear unreachable from the routed flow (`ONBOARDING_STEPS` never references them) — worth confirming they're intended dead code.
