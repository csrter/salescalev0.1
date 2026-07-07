# How to run this build in Claude Code

1. **Create the repo** and drop all of these files (`CLAUDE.md`,
   `PLATFORMS.md`, and `PHASE_1...10`) into the root before starting any
   session. Claude Code auto-loads `CLAUDE.md` at the start of every
   session in this directory, so the architecture and guardrails are
   always in context without you re-pasting them.

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
`PLATFORMS.md` (reference, consulted in Phases 1, 2, 3, 5, 7) →
`PHASE_1_FOUNDATION.md` →
`PHASE_2_CORE_MANAGEMENT.md` →
`PHASE_3_ADVANCED_METRICS.md` →
`PHASE_4_CUSTOMIZABLE_UI.md` →
`PHASE_5_CONVERSION_TRACKING.md` →
`PHASE_6_SALESCALE_CRM.md` →
`PHASE_7_ADDITIONAL_PLATFORMS.md` →
`PHASE_8_BILLING_ONBOARDING.md` →
`PHASE_9_WHITELABEL_AI_INSIGHTS.md` →
`PHASE_10_HEALTH_TRUST.md`

Note: Phase 1 now includes the Organization tenancy model that Phase 8's
billing/signup flow depends on — Phase 8 is sequenced last to match the
existing numbering, but it's really "the layer that makes the Phase 1
tenancy model self-service," not a late add-on. Phase 9 depends on Phase 4
(dashboard), Phase 3 (metrics), and Phase 6 (CRM) already existing, since
white-labeling rebrands those surfaces and AI insights are grounded in
their data — **it does not depend on Phase 7 or Phase 8**, and can be run
directly after Phase 6 if that's a better order for you. Phase 9 itself
notes where its tasks would normally tie into Phase 8's tier model and
gives a lighter-weight version to build instead if Phase 8 isn't done yet.
**Phase 10 has the same droppable property** — its real dependencies are
Phase 1, 2, 3, and 6, not Phase 7, 8, or 9 — so you can run Phase 9 and
Phase 10 in either order, or interleave them with Phase 7/8, whatever
fits your priorities.

---

## Getting Started with Docker

This project is fully containerized with Docker, which makes it easy to run on any machine, including both Intel and Apple Silicon Macs.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop) installed on your machine.

### Running the Application

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/csrter/salescalev0.1.git
    cd salescalev0.1
    ```

2.  **Set up your environment variables:**
    - Make a copy of the `.env.example` file and name it `.env`.
    - Fill in the required Supabase credentials in the new `.env` file.

3.  **Build and run the application:**
    ```bash
    docker-compose up --build
    ```
    This command will build the Docker images for both the frontend and backend services and start the application.

4.  **Access the application:**
    - The **frontend** will be available at [http://localhost:8080](http://localhost:8080).
    - The **backend** API will be available at [http://localhost:8000](http://localhost:8000).

### Building the Desktop App (.dmg)

To package the application into a distributable `.dmg` file for macOS, run the main build script from the root of the project:

```bash
./build-macos.sh
```

This script will handle everything: it will install all dependencies for both the backend and the Electron app, package the Python server into an executable, and build the final `.dmg` installer.

When it's finished, you will find the installer in the `electron-app/dist` directory.
