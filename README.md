# How to run this build in Claude Code

1. **Create the repo** and drop all of these files (`CLAUDE.md` +
   `PHASE_1...5`) into the root before starting any session. Claude Code
   auto-loads `CLAUDE.md` at the start of every session in this directory,
   so the architecture and guardrails are always in context without you
   re-pasting them.

2. **Set your model to Fable 5** in Claude Code's model picker for these
   sessions — this project is exactly the long-horizon, multi-step,
   tool-heavy work it's built for. If the picker falls back to Opus 4.8
   unexpectedly, that's currently a known post-relaunch access wrinkle,
   not a sign anything's wrong with the repo — Opus 4.8 is a fine fallback
   for a session if it happens.

3. **Run one phase at a time**, in order:
   ```
   claude "Follow PHASE_1_FOUNDATION.md"
   ```
   Review the diff, run it against your own test ad accounts, and confirm
   the Definition of Done before moving to the next phase. Don't chain
   multiple phase files into one session — each one ends with an
   intentional stop-and-review point, especially before Phase 2 starts
   writing to live accounts.

4. **Use subagents for parallel work within a phase** if Claude Code
   proposes splitting the Meta and Google integration work — that's a
   reasonable place to delegate, since the two platforms are independent
   backend integrations behind one UI.

5. **After each phase**, confirm `CLAUDE.md`'s Current Status checklist
   was actually updated and a commit was made before starting the next
   phase file in a fresh session.

## Order

`CLAUDE.md` (context, always loaded) →
`PHASE_1_FOUNDATION.md` →
`PHASE_2_CORE_MANAGEMENT.md` →
`PHASE_3_ADVANCED_METRICS.md` →
`PHASE_4_CUSTOMIZABLE_UI.md` →
`PHASE_5_CONVERSION_TRACKING.md` →
`PHASE_6_SALESCALE_CRM.md`
