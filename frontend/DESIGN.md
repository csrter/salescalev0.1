<!-- IMPLEMENTATION ERRATA (read first — supersedes conflicting spec lines below)
1. TAILWIND: the Codex/Tailwind experiment referenced throughout was REVERTED before
   implementation (preserved on branch codex-ui-attempt). The working tree is the
   original vanilla-CSS codebase: dashboard.tsx is NOT utility-styled, theme.css has
   no tailwind import, there are no tailwind packages. Treat every "remove tailwind"
   instruction as already satisfied; just don't reintroduce it.
2. ICONS: deviation from "zero new runtime deps" — lucide-react IS installed in
   frontend/package.json (user signal from their own attempt). All icons come from
   lucide-react at size 18 (16 in dense contexts), strokeWidth 1.75, replacing the
   hand-rolled ICON_PATHS record in App.tsx. Icons inherit currentColor. This is the
   ONLY new dependency; everything else stays hand-rolled.
3. CASCADE SHIM: during migration App.css stays alive for unmigrated views but must
   never beat new styles. Commit 1 wraps it: App.tsx imports it via
   `import "./legacy.css"` where legacy.css contains `@import "./App.css" layer(legacy);`
   — layered rules lose to all unlayered rules regardless of specificity, so the old
   global `button`/`section` element selectors can't fight the new primitives.
   theme.css must no longer be @imported by App.css (main.tsx imports it directly).
4. VIEW MIGRATION RULE: per-view agents NEVER edit App.css. New prefixed classes
   simply stop matching old rules; the final serial sweep deletes App.css whole.
5. The "this box lacks Node" note is stale: Node 24 is installed; always run
   `npm run build` in frontend/ to verify.
-->

# Salescale UI Revamp — Final Design Spec (v2, "Deep Cobalt")

**Direction:** Deep Cobalt — Refined Evolution (winner, 2 of 3 judges), with grafts adopted from Ledger (density toggle, keyboard Arrange mode, change-receipt detail, grep gate, `--chart-prior`, live status tick, visible keyboard chrome) and Aurora (Cancel-first confirm focus, branding live preview, `--ink-on-field` tokens, gradient hero number, `.card--hero` masthead, aurora scoped to selling surfaces).

This document is the single source of truth. Parallel implementation agents execute per-view against it without further design decisions. Where this spec and existing CSS disagree, this spec wins. Where this spec and a **hard constraint** (frozen `BRAND_VAR_MAP` names, staged-write confirm flow, dashboard layout persistence shape, hash/query auth flows, zero new npm runtime deps) could ever be read to disagree, the constraint wins.

---

## 1. Design philosophy

The navy/cobalt identity is right for this product and stays: dark, authoritative chrome framing bright, quiet work surfaces — serious about money, calm about data. What the current UI lacks is discipline, not direction. The revamp enforces **one editorial rule**:

> **Glass is reserved for layers that float.** Topbar-on-scroll, drawers, dialogs, the command palette, and toasts may be translucent. Anything that *holds data* — cards, tables, KPI tiles, kanban, charts — sits on solid `--card` with a hairline border and a restrained shadow ramp.

That single rule restores meaning to depth, restores legibility to data, and unblocks dark mode. Everything brand-colored derives from the six frozen white-label vars via `color-mix()`, so a tenant that sets only `primary` still gets coherent strong/soft/ghost/text variants for free (verified against `theme.ts` `BRAND_VAR_MAP`: partial overrides leave the other vars at stylesheet defaults — so the defaults must derive, and now they do). Complexity budget goes to information design — real breadcrumbs, keyboard-operable tables and trees, crosshair charts, a density toggle — not to decoration. The one expressive register (aurora fields, gradient hero number, hero mastheads) is confined to selling surfaces: login, client-detail masthead, branding preview, empty states, and future billing.

Non-negotiables inherited unchanged: React 19 + Vite + TS, hand-rolled CSS, **zero new runtime deps**, `light-dark()` + `:root[data-theme]` theming, hash/query auth flows, the `useManage().stage` → confirmation modal guardrail, the `{type,w,h}[]` dashboard layout round-trip, the validated `--chart-1..6` palette, system/Inter font stack (no webfonts). **Tailwind is removed entirely** (constraint 1): the `@import "tailwindcss"` in theme.css, the `@reference`/`@apply` usage in App.css, and the utility classNames in `dashboard.tsx` / `components/ui.tsx` are all migrated to this spec's tokens and classes, and the tailwind packages are dropped from `package.json`.

---

## 2. theme.css — complete replacement (this exact file ships)

```css
/* ==========================================================================
   theme.css — Salescale design tokens. THE ONLY FILE that may contain
   literal colors, font sizes, radii, durations, or z-index numbers.
   (CI grep gate enforces this — see the CSS architecture doc section.)

   Theming mechanism (FROZEN): light-dark() pairs on :root with
   color-scheme, narrowed by :root[data-theme] which theme.ts stamps on
   <html>. Do not change this mechanism.

   White-label contract (FROZEN): theme.ts BRAND_VAR_MAP writes these vars
   inline on <html> at runtime (inline style beats everything below):
       primary        → --accent, --brand-blue
       primary_strong → --accent-strong
       primary_soft   → --accent-soft
       header_start   → --header-start
       header_end     → --header-end
   These six names must keep existing and keep meaning what they mean.
   Every brand-colored thing in the UI derives from them (color-mix
   allowed). No other file may introduce a brand hex.

   Derivation rule: --accent-strong / --accent-soft stylesheet DEFAULTS are
   re-derived from var(--accent) (see @supports block at the bottom), so a
   tenant that sets only `primary` still gets coherent variants. A tenant
   that sets all keys wins via inline styles, as before.

   Browser floor: same as the existing light-dark() mechanism —
   Chromium ≥123 (Electron shell qualifies) / Safari ≥17.5 / Firefox ≥120.
   All such browsers also support color-mix(in oklab, …).
   ========================================================================== */

:root {
  color-scheme: light dark;

  /* ---- typography (px for Electron determinism) ---------------------- */
  --font-sans: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

  --text-2xs: 11px;   /* table meta, axis ticks, kbd, overlines */
  --text-xs: 12px;    /* badges, captions, breadcrumb, deltas */
  --text-sm: 13px;    /* secondary UI, table cells, buttons */
  --text-md: 14px;    /* default UI: inputs, nav, controls */
  --text-base: 15px;  /* body copy */
  --text-lg: 17px;    /* card titles */
  --text-xl: 20px;    /* section headings */
  --text-2xl: 26px;   /* page titles */
  --text-3xl: 34px;   /* KPI values */
  --text-hero: 48px;  /* ONE hero figure per view; proportional figures */

  --leading-tight: 1.2;
  --leading-normal: 1.5;
  --weight-normal: 400;
  --weight-medium: 500;
  --weight-semibold: 600;
  --weight-bold: 700;
  --tracking-tight: -0.02em;
  --tracking-caps: 0.06em;

  /* ---- spacing (4px base) --------------------------------------------- */
  --space-1: 4px;  --space-2: 8px;   --space-3: 12px; --space-4: 16px;
  --space-5: 20px; --space-6: 24px;  --space-8: 32px; --space-10: 40px;
  --space-12: 48px; --space-16: 64px;

  /* ---- motion (ALL durations in the app come from these) -------------- */
  --dur-fast: 0.14s;     /* hovers, focus, toggles, chevrons */
  --dur-med: 0.25s;      /* drawers, dialogs, nav pill, tabs */
  --dur-slow: 0.45s;     /* entrances, skeleton shimmer base */
  --dur-ambient: 40s;    /* login aurora drift — decorative only */
  --ease-out: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-spring: cubic-bezier(0.3, 1.25, 0.4, 1); /* nav pill slide only */

  /* ---- navy scale: CHROME ONLY (sidebar, auth aside). NEVER text ink -- */
  --navy-950: #0a1022;
  --navy-900: #0e1a3c;
  --navy-800: #142457;
  --navy-700: #1c3178;
  --brand-navy: #0f2147;

  /* ---- brand primitives (FROZEN names — see contract above) ----------- */
  --brand-blue: #2b62e0;
  --accent: light-dark(#4f46e5, #7c78f0);
  /* literal fallbacks; re-derived from --accent in the @supports block */
  --accent-strong: light-dark(#4338ca, #938ff5);
  --accent-soft: light-dark(#e7e8fb, #262d55);

  /* ---- derived brand helpers (never tenant-written; always computed) -- */
  --accent-ghost: color-mix(in srgb, var(--accent) 8%, transparent);   /* hover washes, drag ghosts */
  --accent-line: color-mix(in srgb, var(--accent) 38%, transparent);   /* selected borders, drop targets */
  --accent-ink: light-dark(color-mix(in oklab, var(--accent) 78%, black),
                           color-mix(in oklab, var(--accent) 72%, white)); /* accent-colored TEXT — always ink-mixed so any tenant hue stays readable */
  --ink-on-accent: #ffffff;

  /* legacy aliases — keep until the final migration sweep, then delete */
  --cobalt: var(--accent);
  --cobalt-strong: var(--accent-strong);
  --cobalt-soft: var(--accent-soft);
  --amber: #d97706; /* deprecated: use --warn or --chart-3 */

  /* ---- surfaces -------------------------------------------------------- */
  --bg: light-dark(#f4f5fa, #0a0d18);
  --bg-grad-1: light-dark(#ecedfb, #0c1024);
  --bg-grad-2: light-dark(#f7f4fb, #0a0d18);
  --card: light-dark(#ffffff, #151a2e);          /* data surfaces + chart surface */
  --surface: light-dark(#f8f9fd, #11162a);        /* inset wells: table headers, kanban lanes, input bg */
  --surface-raised: light-dark(#ffffff, #1a2038); /* menus, popovers, tooltips */
  --border: light-dark(#e7e9f2, #262d47);
  --border-strong: light-dark(#d3d7e5, #343c5c);

  /* ---- glass (FLOATING LAYERS ONLY: topbar-on-scroll, drawer, dialog,
          palette, toast — never on data cards) --------------------------- */
  --glass-bg: light-dark(rgba(255, 255, 255, 0.66), rgba(21, 26, 46, 0.6));
  --glass-bg-heavy: light-dark(rgba(255, 255, 255, 0.85), rgba(21, 26, 46, 0.82));
  --glass-border: light-dark(rgba(255, 255, 255, 0.6), rgba(255, 255, 255, 0.08));
  --glass-blur: 14px;
  --glass-highlight: inset 0 1px 0 light-dark(rgba(255, 255, 255, 0.7), rgba(255, 255, 255, 0.06));
  --scrim: light-dark(rgba(14, 19, 42, 0.42), rgba(4, 6, 14, 0.6));

  /* ---- ink (AA on --bg/--card/glass, both modes) ----------------------- */
  --ink: light-dark(#111530, #e9ecfa);
  --ink-soft: light-dark(#565e7d, #a2abca);
  --ink-faint: light-dark(#8b93ad, #6d769a);

  /* ---- ink & washes ON dark brand fields (sidebar, auth aside, hero
          gradients) — these fields are dark in BOTH modes ----------------- */
  --ink-on-field: #eef1fb;
  --ink-on-field-soft: rgba(238, 241, 251, 0.72);
  --field-hover: rgba(255, 255, 255, 0.06);
  --field-border: rgba(255, 255, 255, 0.08);
  --field-kbd-bg: rgba(255, 255, 255, 0.08);

  /* ---- status (identity fixed — never tenant-brandable) ---------------- */
  --ok: light-dark(#0e7a55, #34c793);
  --ok-soft: light-dark(#d8f3e8, #113729);
  --warn: light-dark(#b45309, #e8a33d);
  --warn-soft: light-dark(#fdeed7, #3a2b12);
  --danger: light-dark(#bf2334, #f07683);
  --danger-soft: light-dark(#fbe1e4, #3a1720);
  --info: light-dark(#0369a1, #67b7e8);
  --info-soft: light-dark(#dbeffb, #10293b);

  /* ---- chrome (FROZEN names --header-start/--header-end) --------------- */
  --header-start: light-dark(#10152e, #0a0e1e);
  --header-end: light-dark(#0b0f21, #070a14);
  --sidebar-bg: linear-gradient(180deg, var(--header-start), var(--header-end));
  --sidebar-ink: #c8cee6;
  --sidebar-ink-faint: #838db1;
  --sidebar-w: 248px;
  --sidebar-w-collapsed: 68px;
  --topbar-h: 60px;

  /* ---- expressive register (login, hero mastheads, empty states, billing;
          all accent-derived so white-labeling propagates) ----------------- */
  --aurora-field:
    radial-gradient(58% 62% at 18% 12%, color-mix(in srgb, var(--accent) 34%, transparent), transparent 70%),
    radial-gradient(46% 52% at 85% 28%, color-mix(in srgb, var(--accent-strong) 22%, transparent), transparent 72%),
    radial-gradient(70% 70% at 55% 95%, color-mix(in srgb, var(--brand-blue) 18%, transparent), transparent 75%),
    linear-gradient(160deg, var(--header-start), var(--header-end));
  --aurora-wash:
    radial-gradient(50% 55% at 12% 0%, color-mix(in srgb, var(--accent) 9%, transparent), transparent 70%),
    radial-gradient(45% 50% at 92% 15%, color-mix(in srgb, var(--accent-strong) 6%, transparent), transparent 72%);
  --grad-hero-ink: linear-gradient(115deg, var(--accent-ink),
    color-mix(in oklab, var(--accent) 45%, var(--ink)));

  /* ---- kbd / shortcut chips -------------------------------------------- */
  --kbd-bg: var(--surface);
  --kbd-border: var(--border-strong);
  --kbd-ink: var(--ink-soft);

  /* ---- density (44px comfortable; [data-density="dense"] → 36px) ------- */
  --row-h: 44px;

  /* ---- radii ramp ------------------------------------------------------ */
  --radius-xs: 6px;    /* badges, kbd, chips */
  --radius-sm: 10px;   /* buttons, inputs, nav items, kanban cards */
  --radius-md: 12px;   /* KPI tiles, popovers, toasts */
  --radius-lg: 16px;   /* cards, dialogs, drawers */
  --radius-full: 999px;
  --radius: var(--radius-lg); /* legacy alias */

  /* ---- elevation ramp -------------------------------------------------- */
  --shadow-xs: 0 1px 2px light-dark(rgba(16, 21, 48, 0.05), rgba(0, 0, 0, 0.3));
  --shadow-sm: 0 1px 2px light-dark(rgba(16, 21, 48, 0.06), rgba(0, 0, 0, 0.4)),
               0 1px 3px light-dark(rgba(16, 21, 48, 0.07), rgba(0, 0, 0, 0.4));
  --shadow-md: 0 8px 26px light-dark(rgba(16, 21, 48, 0.09), rgba(0, 0, 0, 0.45));
  --shadow-pop: 0 4px 12px light-dark(rgba(16, 21, 48, 0.10), rgba(0, 0, 0, 0.5)),
                0 12px 32px light-dark(rgba(16, 21, 48, 0.10), rgba(0, 0, 0, 0.45));
  --shadow-lg: 0 24px 64px light-dark(rgba(16, 21, 48, 0.18), rgba(0, 0, 0, 0.55));

  /* ---- focus (one visible ring app-wide; double halo reads on any
          surface). Usage: :focus-visible { outline: none;
          box-shadow: var(--focus-ring); }  On dark brand fields use
          --focus-ring-field instead. ------------------------------------- */
  --focus-ring: 0 0 0 2px light-dark(#ffffff, #0a0d18), 0 0 0 4px var(--accent-ink);
  --focus-ring-field: 0 0 0 2px var(--header-end), 0 0 0 4px var(--ink-on-field);
  --ring: 3px solid var(--accent-soft); /* legacy alias — delete in final sweep */

  /* ---- z-index ramp (no literal z-index anywhere else) ----------------- */
  --z-sticky: 10;    /* sticky table headers, filter rows */
  --z-shell: 20;     /* sidebar rail */
  --z-topbar: 30;
  --z-dropdown: 60;  /* menus, popovers, chart tooltips */
  --z-drawer: 70;
  --z-dialog: 80;
  --z-palette: 90;
  --z-toast: 100;
  --z-tooltip: 110;

  /* ---- charts (VALIDATED palette — verbatim, deliberately NOT derived
          from --accent: tenant rebrands must not repaint series identity.
          Chart surface is --card.) ---------------------------------------- */
  --chart-1: light-dark(#4f46e5, #7c78f0);
  --chart-2: #0d9488;
  --chart-3: #d97706;
  --chart-4: light-dark(#be185d, #db2777);
  --chart-5: #0284c7;
  --chart-6: #65a30d;
  --chart-grid: light-dark(#eef0f6, #20263e);  /* hairline, one step off --card */
  --chart-axis-ink: var(--ink-faint);
  --chart-prior: light-dark(#c9cbd6, #3a3d4a); /* previous-period / pace / sparkline history */
  --chart-area-opacity: 0.1;
  --chart-surface: var(--card);                /* gap + end-dot ring color */
}

:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"]  { color-scheme: dark; }

/* Density: stamped on <html> by the topbar toggle (persisted per user).
   Client-role sessions never get the attribute. */
:root[data-density="dense"] { --row-h: 36px; }

/* Derived defaults for the two frozen-name variant tokens: a tenant that
   sets only `primary` (inline --accent) gets coherent strong/soft for free.
   Guarded as belt-and-braces for older evergreens; the literals above are
   the fallback. Inline tenant styles still beat both. */
@supports (color: color-mix(in oklab, red 50%, white)) {
  :root {
    --accent-strong: light-dark(
      color-mix(in oklab, var(--accent), black 14%),
      color-mix(in oklab, var(--accent), white 14%));
    --accent-soft: light-dark(
      color-mix(in oklab, var(--accent) 13%, white),
      color-mix(in oklab, var(--accent) 26%, #151a2e));
  }
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --dur-fast: 0s;
    --dur-med: 0s;
    --dur-slow: 0s;
    --dur-ambient: 0s;
  }
}
```

Notes for implementers:

- The old `@import "tailwindcss";` line is **gone** — removing it is part of landing this file (see §3, Tailwind removal).
- `--text-*` names are kept but move from rem to px at near-identical sizes; `--radius-sm` tightens 11→10px. Existing consumers keep working.
- `--ok/--warn/--danger` upgrade from single literals to light-dark pairs (dark-mode legibility); same names.
- Never use `--navy-*` or `--brand-navy` as text color. Headings are `--ink`.

---

## 3. CSS architecture

### 3.1 File layout (replaces the App.css + Tailwind mix)

```
frontend/src/
  theme.css                 ← §2, tokens ONLY. The only file with literal colors/sizes/durations/z-index.
  styles/
    base.css                ← reset & element defaults: body backdrop (the two --bg-grad radials over --bg),
                              font stack, headings (--ink, --tracking-tight), links (--accent-ink),
                              ::selection (--accent-soft bg / --ink), global
                              :focus-visible { outline: none; box-shadow: var(--focus-ring); },
                              .visually-hidden, scrollbar styling, `img { max-width: 100% }`.
    shell.css               ← .app grid, .sidebar*, .topbar*, .crumb*, .content, arrange-mode chrome (§5).
    auth.css                ← .auth-* login/verify/reset/MFA screens + aurora field (§5.4).
    views/
      dashboard.css         ← .dash-*
      crm.css               ← .crm-*
      outreach.css          ← .or-*
      clients.css           ← .cl-*   (client grid + client detail + account tree host)
      admin.css             ← .adm-*
      security.css          ← .sec-*
      settings.css          ← .set-*  (branding + account/billing)
      manage.css            ← .mg-*   (manage + google + creatives + integrations)
  components/
    ui.css                  ← ALL shared primitives (§4). Imported by ui.tsx/DataTable.tsx/Toast.tsx/
                              CommandPalette.tsx/Dialog.tsx as today.
    ui.tsx, DataTable.tsx, Toast.tsx, CommandPalette.tsx, Dialog.tsx (new), charts.tsx (new)
```

**Imports:** `main.tsx` imports `theme.css` then `styles/base.css` (replacing `index.css`, whose reset folds into base.css). `App.tsx` imports `styles/shell.css` and `styles/auth.css`. Each view module imports its own `styles/views/*.css`. Component files import `components/ui.css`. All selectors are single-class, view-prefixed or primitive names — import order never matters. `App.css` shrinks during migration and is **deleted** in the final sweep together with the legacy aliases (`--cobalt*`, `--ring`, `--amber`).

**Tailwind removal (part of the tokens commit):** delete `@import "tailwindcss"` (theme.css) and `@reference "tailwindcss"` + every `@apply` (App.css); rewrite the utility classNames in `dashboard.tsx` and `components/ui.tsx` onto spec classes; remove tailwind packages from `package.json`. No `@apply`, no utility classes anywhere after migration.

### 3.2 Naming conventions

- Kebab-case classes. Primitives are unprefixed (`.btn`, `.card`, `.kpi`, `.table`, `.dialog`, `.badge`, `.tabs`, `.seg`, `.field`, `.toast`, `.alert`, `.empty`, `.skel`, `.tree`, `.kanban`, `.chip`, `.kbd`, `.crumb`). Modifiers use `--` (`.btn--primary`, `.card--interactive`). Sub-parts use `-` (`.card-header`, `.kpi-delta`).
- View classes carry the view prefix (`.dash-grid`, `.crm-lanes`, `.or-inbox`).

### 3.3 What belongs where

- **`components/ui.css`** — anything used by ≥2 views, and every primitive in §4, including all its states. If you're about to write a button/table/badge/dialog style in a view file, stop: it belongs here.
- **`styles/views/*.css`** — layout and composition only: grids, column widths, view-specific spacing, one-off arrangement of primitives. A view file may **never** restyle a primitive's internals (no `.crm-x .btn { background: … }`), never use element selectors outside its own prefixed block, never use `!important` (the outreach `!important` patch layer is deleted), never declare colors.
- **Inline `style={{}}` in TSX** — allowed only for *dynamic geometry* (widget grid spans, SVG coordinates, drag transforms, computed chart dims). Never colors, fonts, spacing constants, radii, shadows. The ~41 static inline style objects in `outreach.tsx` migrate to `.or-*` classes.

### 3.4 The grep gate (CI + pre-commit; runs from repo root)

All three commands must return empty:

```sh
# 1. Literal sizes/durations only in theme.css
git grep -nE '(font-size|border-radius|transition|animation|z-index)[^;:]*:[^;]*[0-9]' \
  -- 'frontend/src/**/*.css' ':!frontend/src/theme.css'

# 2. No color literals outside theme.css (tsx allowlist: branding.tsx default
#    swatch DATA constants; qrcode #000000/#ffffff functional colors in account.tsx)
git grep -nE '#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklab\(|light-dark\(' \
  -- 'frontend/src/**/*.css' 'frontend/src/**/*.tsx' \
  ':!frontend/src/theme.css' ':!frontend/src/branding.tsx' ':!frontend/src/account.tsx'

# 3. Tailwind is gone
git grep -nE '@apply|@reference|tailwind' -- 'frontend/src/**/*.css' 'frontend/src/**/*.tsx' 'frontend/package.json'
```

### 3.5 Migration sequencing (for parallel agents)

1. **Commit 1 (serial, blocking):** theme.css (§2) + base.css + the `.card`/`.btn`/focus-ring rules in ui.css + grep gate in CI. Legacy aliases keep unmigrated views rendering.
2. **Commit 2 (serial):** shell.css + auth.css (§5), Dialog + Toast + charts primitives (§4).
3. **Then per-view, parallel:** one view per commit per §7. Migration insurance: `GlassCard` is re-implemented to render solid `.card` chrome by default — any unmigrated data-card usage degrades to the target rule (data on solid surfaces), not to the old noise. Glass visuals exist only on the five floating layers.
4. **Final sweep (serial):** delete App.css, legacy aliases, `assets/hero.png`, dead classes.

---

## 4. Component specs (`components/ui.css` + ui.tsx unless noted)

Global interaction rules: every interactive element shows `var(--focus-ring)` on `:focus-visible` (`var(--focus-ring-field)` on dark brand fields); every transition uses `--dur-*`/`--ease-*` tokens (reduced motion zeroes them); disabled = `opacity: .5; pointer-events: none;`.

### 4.1 Button — `.btn` (existing `Button`, variants unchanged: `default | primary | ghost | danger | link`)

- **Sizes:** `.btn--sm` 28px / `--text-xs`; default 34px / `--text-sm`; `.btn--lg` 40px / `--text-md` (auth only). Padding-inline 12/14/18px. `--radius-sm`, `--weight-medium`, icon gap `--space-2` (16px icons).
- **primary:** bg `--accent`, ink `--ink-on-accent`, hover bg `--accent-strong`. **No glow shadows.**
- **default:** bg `--card`, 1px `--border-strong`, ink `--ink`, hover bg `--surface`.
- **ghost:** transparent, ink `--ink-soft`; hover bg `--accent-ghost`, ink `--accent-ink`.
- **danger:** bg `--danger`, ink white — destructive confirms only. Row-level destructive actions use default styling with `--danger` ink/border (`.btn--danger-outline`).
- **link:** ink `--accent-ink`, underline on hover, no box.
- **States:** hover per variant (`background var(--dur-fast)`); active: no transform; focus ring; disabled as global; **busy:** label `opacity: 0`, centered 14px border-spinner, width locked, `aria-busy="true"`.
- **A11y:** real `<button>`; icon-only buttons are 34×34 with `aria-label`.

### 4.2 Card — `.card`, `.card--interactive`, `.card--hero`; `GlassCard`

- `.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-xs); padding: var(--space-6); }` — defining this heals `security.tsx`'s chrome-less `.card` usages.
- `.card-header`: title `--text-lg`/`--weight-semibold`/`--ink` (never navy), optional sub `--text-sm`/`--ink-soft`, actions right-aligned.
- `.card--interactive`: hover `translateY(-1px)` + `--shadow-md` over `--dur-fast` (transform+shadow only; no transform under reduced motion since durations are 0s — also gate the translate behind the token by transitioning it).
- `.card--hero` (expressive register — client-detail masthead, billing header ONLY): `background: var(--aurora-wash), var(--card)`, `--radius-lg`, `--space-8` padding.
- **GlassCard:** renders `.card` chrome by default (migration insurance). Glass visuals (`--glass-bg-heavy` + `backdrop-filter: blur(var(--glass-blur))` + `--glass-border` + `--glass-highlight`) apply only via the floating-layer components (Dialog, Drawer, Toast, palette, topbar). Never stack glass on glass.

### 4.3 KPI stat tile — `.kpi`, `.kpi--hero` (ONE tile system; replaces all ad-hoc stat markups)

- Base = `.card` at `--space-5` padding, `--radius-md`.
- Label: `--text-sm`/`--ink-soft`, sentence case, no colon. Value: `--text-3xl`/`--weight-semibold`/`--ink`, **proportional figures (never `tabular-nums` at display size)**, compact notation ($4.2M, 12.9K).
- Delta badge: `--text-xs`, signed, named period ("vs prev 30d"), pill on `--ok-soft`/`--danger-soft` with `--ok`/`--danger` ink; **color = direction × up-is-good flag** (spend up may be red) — pass `upIsGood` per metric.
- Optional 12-point sparkline 120×40: history stroke `--chart-prior` 1.5px, current-period segment `--chart-1` 2px, round caps, no axes.
- `.kpi--hero` — **exactly one per view**: value `--text-hero` with `background: var(--grad-hero-ink); -webkit-background-clip: text; background-clip: text; color: transparent;` wrapped in `@supports (background-clip: text)` with fallback `color: var(--ink)`.
- Grid: `repeat(auto-fit, minmax(200px, 1fr))`, gap `--space-4`. Skeleton twin ships with it.
- **A11y:** tile is a `<div role="group" aria-label="{label}: {value}, {delta} vs previous period">`; sparkline `aria-hidden`.

### 4.4 DataTable — `.table` (extend existing `components/DataTable.tsx`; keep its props, add `refetching?`, `density` from context, `caption`)

- Wrapper: `.card` at padding 0, `overflow: auto`.
- Header: sticky (`--z-sticky`), bg `--surface`, `--text-2xs` caps `--tracking-caps` `--ink-faint`; sortable headers are real `<button>`s filling the `th`, `aria-sort` on the `th`, 12px arrow in `--accent-ink` when active.
- Rows: height `var(--row-h)` (44px comfortable / 36px dense via `data-density`), `--text-sm` `--ink`, 1px bottom `--border`, no zebra. Hover bg `--accent-ghost`. Selected: bg `--accent-soft` wash + `box-shadow: inset 2px 0 var(--accent)`.
- Numeric columns (`align: "right"`): right-aligned, `font-variant-numeric: tabular-nums`, formatted currency/percent.
- **Keyboard:** container `role="grid"`; roving tabindex on rows; ↑/↓ move active row, Enter opens (`onRowClick`), Home/End jump; active row shows inset `--focus-ring`. Row count and selection changes announced via a visually-hidden `aria-live="polite"` region.
- **States:** `loading` → 6 skeleton rows matching column widths; empty → `EmptyState` slot; `refetching` → previous rows held at `opacity: .5` (never a skeleton flash).
- Every chart's "View as table" twin renders through this component.

### 4.5 Dialog — `.dialog` (NEW `components/Dialog.tsx`; replaces every hand-rolled modal)

- Portal to `body`. Scrim `--scrim` at `--z-dialog`. Panel: `--glass-bg-heavy` + `backdrop-filter: blur(var(--glass-blur))` + 1px `--glass-border` + `--glass-highlight` + `--shadow-lg`, `--radius-lg`; max-width 480/640/840 (`sm|md|lg`).
- **Semantics (required):** `role="dialog"`, `aria-modal="true"`, `aria-labelledby={titleId}`; focus moves into the panel on open and is trapped (sentinel loop); Escape closes; focus returns to the invoker on close; scrim-click close configurable (OFF for confirm variant).
- Entrance: `@starting-style` opacity 0 / translateY(8px) → settle over `--dur-med` (instant under reduced motion; degrades to instant-show without `@starting-style`).
- Header `--text-lg`/`--weight-semibold` + 32px ghost close button (`aria-label="Close"`). Footer buttons right-aligned.

**Confirm variant — the Change Receipt (`.dialog--confirm`), the ONLY rendering of `useManage().stage`'s confirmation. Flow logic untouched; never bypassed.**

- Panel gets a 3px left border `--warn` (`--danger` when any change pauses spend or reduces budget) + warn icon.
- Body = the receipt: one row per staged change — `"{Client} · {Campaign} · {Field}"` with a neutral **platform chip** (§4.8) per row; values in `--font-mono`: old value struck-through `--ink-faint` → new value `--weight-semibold` `--ink`, plus **absolute and % delta** for budget changes (`$150 → $220 (+$70, +46.7%)`), delta colored `--ok`/`--danger` by direction.
- Footer: `[ghost "Keep staging"] [primary/danger "Apply N changes to live accounts"]`.
- **Focus rules (compliance-critical):** initial focus goes to **Cancel**, never the confirm button; the confirm button is `disabled` until the receipt list has rendered; Enter never activates confirm unless it is the explicitly focused element; scrim click does not close; result announced via the toast live region.

### 4.6 Field / Input / Select — `.field`, `.input`, `.select`, `.textarea` (extend existing `Field`)

- `.field`: real `<label htmlFor>` `--text-sm`/`--weight-medium`/`--ink`; optional "(optional)" flag `--ink-faint`; description `--text-xs`/`--ink-soft`; error slot `--text-xs`/`--danger`.
- Placeholders are demoted to example text — **no placeholder-only forms anywhere** (CRM's forms migrate).
- Inputs: 36px (40px in auth), bg `--surface`, 1px `--border-strong`, `--radius-sm`, `--text-md`; focus: border-color `--accent` + `--focus-ring`. Selects/textareas/date inputs match. Checkbox/radio: `accent-color: var(--accent)`.
- **Error state:** input `aria-invalid="true"` + `aria-describedby={errorId}`; error text container is `aria-live="polite"`. Validate on blur; on submit, focus the first invalid field.

### 4.7 Badge — `.badge` + tone map

- 20px, `--radius-full`, `--text-xs`/`--weight-medium`, padding 4px 10px, optional 6px dot. Text always names the state — never color-only.
- Tones: `ok` `--ok-soft`bg/`--ok`ink · `warn` · `danger` · `info` (same pattern) · `neutral` `--surface`bg/`--ink-soft`ink · `accent` `--accent-soft`bg/`--accent-ink`ink.
- **API-status mapping (canonical, used everywhere a status renders):**
  - `ACTIVE`, `ENABLED`, `connected`, `running`, `sent`, `won` → **ok**
  - `PAUSED`, `stale`, `expiring` → **warn**
  - `ERROR`, `REJECTED`, `DISAPPROVED`, `failed`, `disconnected(error)`, `lost` → **danger**
  - `PENDING`, `IN_REVIEW`, `queued` (incl. outreach sends waiting on the 24h window), `syncing`, `coming soon` → **info**
  - `DRAFT`, `ARCHIVED`, `REMOVED`, not-connected, unknown → **neutral**

### 4.8 Platform chip — `.chip` (registry-driven; kills `meta|google` hardcodes and any `'#888'` fallback)

- Neutral chip: bg `--surface`, 1px `--border`, `--text-xs` `--ink-soft`; 16px monogram disc (platform initial, bg `--surface-raised`, 1px `--border-strong`).
- **No per-platform brand colors and no chart-palette colors** (semantic collision — all judges concur). Platform identity is the label. Names/ids come from `GET /api/platforms`; `api.ts` platform unions widen to registry lookup with a neutral fallback chip for unknown ids.

### 4.9 Tabs & Segmented — `.tabs`, `.seg`

- **Tabs** (views — e.g. outreach's 6): row with `--border` bottom hairline; tab = ghost button `--text-sm`, inactive `--ink-soft`, active `--ink` + 2px underline `--accent` (underline transition `--dur-fast`). `role="tablist"`/`tab`/`tabpanel`, `aria-selected`, ←/→ roving tabindex.
- **Segmented** (value pickers — density, chart/table twin, date presets): track bg `--surface`, `--radius-sm`, inset; active segment bg `--card` + 1px `--border-strong` + `--shadow-xs`. Radiogroup semantics (`role="radiogroup"` + `aria-checked`).

### 4.10 Toast / Alert — `.toast`, `.alert` (restyle existing `Toast.tsx`; the ONE feedback pattern)

- Toast: bottom-right stack (`--z-toast`), 320px, `--glass-bg-heavy` + blur + `--glass-border`, `--radius-md`, `--shadow-pop`, 4px left border in tone color, `--text-sm`. Auto-dismiss 5s, paused on hover/focus; close button; max 3 stacked; entrance via `@starting-style` translateY(8px) (`--dur-med`).
- Container `role="status"` `aria-live="polite"`; error toasts `role="alert"`.
- Inline `.alert`: tone-soft bg, tone ink, `--radius-sm`, `--space-3` padding — form-level errors and page notices.

### 4.11 EmptyState — `.empty` (extend existing)

- Centered, `--space-12` padding; 48px icon disc bg `--accent-soft`, glyph `--accent-ink`; title `--text-lg`/`--weight-semibold`; one-line body `--ink-soft` max 44ch; one primary action.
- Large view-level empties (first-run dashboard, zero clients) add `background: var(--aurora-wash), var(--card)` (expressive register, static).

### 4.12 Skeleton — `.skel` (extend existing `Skeleton`/`SkeletonText`)

- Base `--surface` with a shimmer sweep: gradient of `color-mix(in srgb, var(--ink) 6%, transparent)`, `animation-duration: calc(var(--dur-slow) * 3)` — static two-tone under reduced motion (duration 0s). `aria-hidden="true"`.
- Structure-matching skeleton twins ship for: dashboard widgets, KPI grid, table rows, kanban columns, client cards, tree children, inbox rows.

### 4.13 Charts — `components/charts.tsx` (NEW; hand-rolled SVG only — `LineChart`, `BarChart`, `Sparkline` inside a shared `ChartFrame`)

Surface is always `--card` (inside a `.card`). Per the dataviz craft rules:

- **Grid/axes:** horizontal gridlines only, 1px **solid** `--chart-grid` (never dashed); ≤5 y-ticks at clean rounded numbers; axis text `--text-2xs` `--chart-axis-ink` with `tabular-nums`; no axis domain lines; no chart border.
- **Series colors:** `--chart-1..6` assigned by stable series order — never reassigned on filter changes; >6 categories aggregate into "Other" (`--ink-faint`). Previous-period/pace/deemphasis series use `--chart-prior`. Status colors (`--ok/--warn/--danger`) only for status meaning, never as series identity.
- **Lines:** 2px, round join/cap; end-dots r=4 with 2px `--chart-surface` ring; area fills = series hue at `--chart-area-opacity`. Y-axis may crop (label it); no dual axes ever — index to 100 or small multiples.
- **Bars:** ≤24px thick, 4px radius on the data end only, square at the baseline, baseline at zero; 2px `--chart-surface` gaps between touching/stacked segments (gaps, never strokes); hover lifts via lighten.
- **Legend:** always for ≥2 series, never for 1; keys mirror the mark (line-stroke key for lines, rect for bars); direct labels only at endpoints/extremes, label text in ink tokens, never in the series color.
- **Interaction:** vertical crosshair (1px `--border-strong`) snapping to nearest X; **one** tooltip listing every series at that X — value `--weight-semibold` `--ink` leads, series name `--ink-soft` follows, keyed by 10×2px line-keys; tooltip = `--surface-raised`, `--radius-md`, `--shadow-pop`, `--z-dropdown`, built with `textContent` only (never `innerHTML`). Hit areas ≥24px (full column bands). Bars/cells are their own hit targets.
- **Keyboard:** chart focusable (`tabindex=0`, `role="img"` + `aria-label` summary); ←/→ move the crosshair index with the same tooltip; a visually-hidden `aria-live="polite"` region announces the focused X ("May 12: Meta $412, Google $233").
- **States:** refetch holds the previous render at 0.5 opacity (no skeleton flash); first load = skeleton block. Every chart card has a "View as table" segmented toggle → DataTable twin (tooltips enhance, never gate).
- **Filters:** one filter row (date presets first: 7/30/90/custom) sits once above the charts it scopes — never inside a chart card.

### 4.14 Tree rows — `.tree` (account ▸ campaign ▸ ad group ▸ ad)

- Rows 36px; indent 20px/level with a 1px `--border` rail; replace `▾/▸` text glyphs with one 16px SVG chevron rotating 90° over `--dur-fast`.
- Entity type tag `--text-2xs` caps `--ink-faint` (CMP/ADSET/AD); status `Badge` and spend (right-aligned, `tabular-nums`) at row end. Selected row: `--accent-soft` bg + `--accent-ink` label. Hover `--accent-ghost`.
- **A11y:** `role="tree"`/`treeitem`/`group`, `aria-expanded`, `aria-level`; ↑/↓ traverse, → expand / ← collapse-or-parent, Enter selects, `*` expands siblings. Async children render 2 inline skeleton rows.

### 4.15 Kanban — `.kanban-lane`, `.kanban-card` (CRM)

- Lane: bg `--surface` well, `--radius-md`, header `--text-2xs` caps + count badge. Card: bg `--card`, 1px `--border`, `--radius-sm` wait— use `--radius-md`? No: **`--radius-sm`**, `--shadow-xs`, `--space-3` padding; name `--text-sm`/`--weight-semibold`, value `tabular-nums` `--ink-soft`, source platform chip, stage-age dot (`--warn` when stale).
- Dragging: `--shadow-md` + 2° tilt (tilt transitioned via `--dur-fast` so reduced motion removes it); drop target: dashed 2px `--accent-line` on `--accent-ghost`.
- **Keyboard path (required):** HTML5 DnD stays mouse-only; each card has a menu with "Move to stage ▸" (and `[`/`]` shortcuts while focused); moves announced via the live region. Document that this menu — not DnD parity — is the accessible path.

### 4.16 Kbd chip — `.kbd`

- `--text-2xs` `--font-mono`, bg `--kbd-bg`, 1px `--kbd-border`, ink `--kbd-ink`, `--radius-xs`, 2px 6px padding. On dark brand fields: bg `--field-kbd-bg`, border `--field-border`, ink `--sidebar-ink`. Always `aria-hidden` (the accessible name lives on the control).

---

## 5. Shell spec (`styles/shell.css`, `styles/auth.css`)

### 5.1 Sidebar — `.sidebar`

- Width `var(--sidebar-w)` (248px); collapsed `var(--sidebar-w-collapsed)` (68px), width transition `--dur-med`. Background `var(--sidebar-bg)` (tenant gradient), 1px right border `--field-border`. Sticky full-height at `--z-shell`.
- **Brand block:** 60px tall (aligns with topbar). Tenant logo via `safeBrandUrl()` (max-height 28px) or product_name wordmark in `--ink-on-field` with the logo glyph in `--brand-blue`.
- **Search ghost (palette entry):** a real `<button>` styled as an input ghost — 32px, `--radius-sm`, 1px `--field-border`, text "Search…" in `--sidebar-ink-faint`, right-aligned `.kbd` chip `⌘K`. Opens the existing CommandPalette. Collapsed: icon-only with `aria-label="Search (⌘K)"`.
- **Nav items:** 36px, `--radius-sm`, `--text-sm`/`--weight-medium` `--sidebar-ink`; hand-rolled 18px SVG icons, `stroke: currentColor` 1.75px. Hover bg `--field-hover`. Section heads: `--text-2xs` caps `--tracking-caps` `--sidebar-ink-faint`, collapsible with a rotating chevron (`--dur-fast`).
- **Active state — the sliding pill:** ONE absolutely-positioned element (bg `color-mix(in srgb, var(--accent) 28%, transparent)`, 1px border `color-mix(in srgb, var(--accent) 45%, transparent)`, `--radius-sm`) that translates to the active item over `--dur-med` `--ease-spring` (transform-only; snaps instantly under reduced motion). Active item text `--ink-on-field`. Active link carries `aria-current="page"`. No hardcoded purple anywhere.
- **Footer:** user chip (avatar = initials disc on `--accent-strong`, ink `--ink-on-accent` — no hardcoded avatar hex), theme toggle, logout. Collapsed mode: icons only with `title` + `aria-label`; pill persists.
- Focus on the rail uses `--focus-ring-field`. Role-gating of nav items (team vs client vs superadmin) unchanged.

### 5.2 Topbar — `.topbar`

- 60px (`--topbar-h`), sticky at `--z-topbar`. At scrollTop 0: transparent, borderless. Once content scrolls under (IntersectionObserver on a 1px sentinel toggling `.topbar--stuck`): `--glass-bg-heavy` + `blur(var(--glass-blur))` + 1px bottom `--glass-border` + `--shadow-xs`, transitioned over `--dur-fast`. Degrades to always-stuck styling without IO.
- **Left — real breadcrumb `.crumb`:** `<nav aria-label="Breadcrumb"><ol>`; segments derived from app state (workspace name › view label › client name in ClientDetail › campaign when the tree has a selection). Every ancestor is a real `<button>` (`--text-xs` `--ink-soft`, hover `--ink`) that sets view state back — pure `useState` routing, no URLs, Electron `file://` safe. Separator `›` in `--ink-faint`; current segment `--ink`/`--weight-semibold` with `aria-current="page"`.
- **Right cluster** (gap `--space-2`): ① **live status tick** — a `--text-2xs` `--font-mono` `--ink-faint` `aria-live="polite"` region flipping "Saving…" → "Saved 14:02:31" after any successful write (fed by the shared API mutation wrapper; doubles as the app-wide SR write-feedback channel, complementing toasts); ② **density toggle** — `.seg` comfortable/dense, stamps `data-density` on `<html>`, persisted in localStorage per user, **not rendered for Client-role sessions** (attribute cleared on client login); ③ notifications; ④ help.
- Data views render the standard **filter row** directly below the topbar (date presets first), scoping everything beneath — once per view, never per card.

### 5.3 Content well

- Padding `--space-8` (`--space-5` below 1100px), no max-width (dashboards want width). Background `--bg` with the two existing corner `--bg-grad` radials (in base.css) at reduced strength.

### 5.4 Login / auth (`auth.css`) — the flagship expressive surface

- Split layout. **Left aside (48%, min 420px, hidden <960px):** `background: var(--aurora-field)` — tenant-branded automatically. Optional drift: one `background-position` keyframe, `animation-duration: var(--dur-ambient)` (0s → static under reduced motion; the static field is the design). Content: tenant logo; headline `--text-3xl`/`--weight-bold`/`--ink-on-field` ("Every campaign. Every client. One place." — overridable by branding copy later); three proof points with check glyphs in `--ink-on-field-soft`.
- **Right panel:** solid `--card` (NOT glass — forms need max legibility), 380px, `--radius-lg`, `--shadow-lg`, `--space-10` padding; real labeled Fields, 40px inputs, block primary button; email autofocused; Enter submits. MFA gate, `?verify`, `?reset`, and `#access_token` flows reuse the same panel — **query/hash handling untouched, presentation only.**
- Delete `src/assets/hero.png` (dead asset).

---

## 6. Signature moments (all cheap, all reduced-motion-safe)

1. **The floating-glass rule** — glass only on layers above the page (topbar-stuck, drawer, dialog, palette, toast). Depth regains meaning; ≤2 concurrent blur layers on Electron.
2. **The sliding nav pill** — one transform-animated indicator gliding between nav items (`--dur-med` `--ease-spring`); single GPU-only node; snaps at 0s under reduced motion.
3. **The branded aurora** — login aside (and empty-state wash) computed from `--accent`/`--accent-strong`/`--brand-blue`; every white-label tenant gets a bespoke backdrop from pure CSS gradients; drift is optional garnish gated by `--dur-ambient`.
4. **The Change Receipt** — the staged-write confirm rendered as a warn-edged diff receipt (struck old → bold new, absolute + % delta, platform tag per row, Cancel-first focus). The compliance rule becomes the product's most trust-building moment.
5. **Settle-in entrances** — `@starting-style` opacity/8px-translate on dialogs, toasts, and first-paint cards over `--dur-med`; zero JS; instant under reduced motion; degrades to instant-show.
6. **The gradient hero number** — exactly one `--text-hero` figure per view clipped with `--grad-hero-ink` (`@supports` fallback to solid `--ink`); reads as bespoke brand typography for two tokens.
7. **The live status tick** — "Saving… → Saved 14:02:31" in mono in the topbar; one aria-live region doubling as the SR feedback channel.
8. **Visible keyboard chrome** — ⌘K chip on the sidebar search ghost, kbd chips in the command palette rows; static markup + `--kbd-*` tokens; keyboard-first becomes visible identity.

---

## 7. Per-view direction notes

- **Dashboard (`dashboard.tsx`, `widgets.tsx`)** — **Remove all Tailwind utility classes** (this file is currently slate-utility styled, i.e. dark-only — replace with tokens so light mode works). Layout persistence untouched: same `{type,w,h}[]` PUT `/api/dashboard/layout` round-trip, byte-identical. Widgets become `.card`; drag handle = 6-dot grip on hover (always visible on touch); drop placeholder `--accent-ghost` + dashed `--accent-line`; 12px corner resize chevron. **Keyboard Arrange mode:** an "Arrange" toggle makes widgets focusable — arrows reorder, Shift+arrows resize, Esc exits — persisting through the identical shape. Metric cards → `.kpi` tiles with one `.kpi--hero` (blended spend). Spend-pacing chart rebuilt on `charts.tsx` (crosshair, all-series tooltip, `--chart-prior` pace line, table twin). Global date-preset filter row above the grid.
- **CRM (`crm.tsx`)** — Lead list stays on DataTable (gains keyboard grid, density, tabular-nums money columns). Kanban per §4.15 with the "Move to stage" keyboard menu. The drawer keeps glass (it floats) but gains full Dialog-grade focus semantics (trap, Escape, return focus). Every placeholder-only form → labeled `Field` with error slots.
- **Outreach (`outreach.tsx`)** — Mostly deletion: migrate the ~41 inline style objects to `.or-*` classes and primitives; delete the `!important` patch layer. 6 tabs → `.tabs` per §4.9. Inbox = three-pane with `--row-h` rows and mono timestamps; queued-window sends show the **info** badge; Rep (member) vs Manager gating unchanged.
- **Clients grid + Client detail + tree (`App.tsx`, `styles/views/clients.css`)** — Client cards → `.card--interactive` with accent-derived initials discs and neutral platform chips. Client detail gets a `.card--hero` masthead (client name `--text-2xl`, status badge, connected-platform chips, health strip) — the screenshotable white-label surface. Account tree → §4.14 (SVG chevrons, treeitem keyboard model, inline skeletons). Breadcrumb reflects client/campaign selection.
- **Admin (`admin.tsx`)** — Org list already on DataTable — restyles free. Raw tables → DataTable. Signup chart → `charts.tsx` (single series: no legend, endpoint direct label). Counters → `.kpi`.
- **Security (`security.tsx`)** — Healed largely by the `.card` rule (its chrome-less cards start rendering); then align headings/spacing to tokens and posture counts to `.kpi`; event log on DataTable.
- **Branding (`branding.tsx`)** — Already on primitives; add the **live preview panel**: a mini sidebar + primary button + KPI tile + selected table row + badge, rendered from the *staged* colors (write staged values as inline vars on the preview container only). This is the white-label demo moment and lets tenants self-correct low-contrast accents before saving. Default swatch hexes here are data, not styling (grep-gate allowlisted).
- **Account / billing (`account.tsx`)** — Profile/MFA sections → `.card` + Field; QR block on `--card` with functional `#000/#fff` QR colors (allowlisted). Design-ahead for Phase 8: plan cards on `--aurora-wash`, price `--text-3xl` proportional, current plan ringed with `--accent-line` — expressive register, since it's a selling surface.
- **Manage / Google / Creatives / Integrations (`manage.tsx`, `google.tsx`, `creatives.tsx`, `integrations.tsx`)** — Legacy tables fold into DataTable. Every staged write flows through the **Change Receipt** Dialog (§4.5) — `useManage().stage` wiring identical, presentation only. Platform chips/colors go registry-driven (delete any per-platform color maps and fallbacks). Creative statuses → the Badge tone map. Integrations list renders from `GET /api/platforms` with neutral chips; "coming soon" = info badge; zero per-platform CSS.
- **Global sweep (every view)** — Headings use `--ink` (never `--navy-*`); all literal font sizes/radii/durations/z-indexes → tokens; all hardcoded hexes/rgba → tokens (grep gate enforces); every feedback path → Toast; every modal → Dialog.

---

## 8. Verification checklist (run per-view at migration, and in full before the final sweep)

**Both themes** (toggle via the UI, which stamps `data-theme`; also test `system`):
- [ ] Every migrated view in light and dark: no invisible text (headings, table headers, axis ticks), no washed-out borders, charts legible on `--card` in both.
- [ ] Glass appears ONLY on: stuck topbar, drawer, dialog, palette, toasts. All data cards/tables/tiles are solid `--card`.
- [ ] Focus ring visible on every interactive element in both modes, including on the sidebar (`--focus-ring-field`) and on glass panels.

**Garish-tenant-brand test** (simulate `BRAND_VAR_MAP`: set inline on `<html>` `--accent` and `--brand-blue` to `#84cc16` lime, `--header-start`/`--header-end` to `#f4f4d0`/`#c8c86e`, and delete inline `--accent-strong`/`--accent-soft` to prove derivation):
- [ ] Derived tokens recompute: buttons get coherent hover, `--accent-soft` washes stay subtle, `--accent-ink` text stays readable on `--card` in both modes (spot-check contrast ≥4.5:1).
- [ ] Charts and status colors are **unchanged** (series identity never rebrands).
- [ ] Platform chips remain neutral. Sidebar pill, nav aurora, login field, hero number, focus ring all re-key to lime without any leftover indigo.
- [ ] Branding live preview shows exactly what shipped surfaces show.
- [ ] Known limitation to document (not fix here): white `--ink-on-accent` on a very light tenant accent is the one AA exposure — the backend luminance check is a Phase-9 follow-up.

**Keyboard walk (no mouse, screen reader spot-checks with VoiceOver):**
- [ ] Login: Tab order logical, labels announced, Enter submits; `?verify`/`?reset`/`#access_token` flows still work under Electron `file://` AND hosted web.
- [ ] Sidebar: Tab to search ghost (⌘K announced), arrow through nav, active item `aria-current`; collapsed rail still operable.
- [ ] Breadcrumb buttons navigate view state backward.
- [ ] DataTable: ↑/↓/Home/End/Enter; `aria-sort` toggles; selection announced via live region.
- [ ] Tree: ↑↓←→/Enter/`*` per §4.14 with `aria-expanded`/`aria-level` announced.
- [ ] Kanban: card menu "Move to stage" works and announces the move.
- [ ] Dialog: focus moves in, traps, Escape closes, focus returns to invoker.
- [ ] **Staged-write Change Receipt:** initial focus on Cancel; confirm disabled until rows render; Enter from anywhere else cannot apply; the diff shows old→new with abs+% delta and platform tag; the flow still executes the identical `useManage()` confirm path (no bypass — snapshot-test this).
- [ ] Dashboard Arrange mode: arrows reorder, Shift+arrows resize; resulting PUT body shape `{type,w,h}[]` byte-identical to a drag-produced one.
- [ ] Charts: ←/→ move crosshair, tooltip mirrors, live region announces series values; "View as table" twin reachable.
- [ ] Command palette: opens via ⌘K, arrow navigation, kbd chips visible.
- [ ] Status tick announces "Saved HH:MM:SS" after a write; toasts announce politely (errors assertively).

**Motion & platform:**
- [ ] `prefers-reduced-motion: reduce`: nav pill snaps, entrances instant, shimmer static, aurora static, kanban tilt gone — verify durations all come from zeroed tokens.
- [ ] Electron DMG build AND hosted web: fonts never fetch a network resource; no URL-path routing introduced; `@starting-style`, IntersectionObserver topbar, and `background-clip: text` degrade gracefully.
- [ ] Blur layers on screen simultaneously ≤2 (Electron GPU budget).

**Hygiene:**
- [ ] All three grep-gate commands (§3.4) return empty; `npm run build` in `frontend/` passes (this box lacks Node in some environments — CI covers it).
- [ ] Tailwind fully removed: no `@apply`/`@reference`/utility classes/package remnants.
- [ ] Final sweep only: App.css, `assets/hero.png`, legacy aliases (`--cobalt*`, `--ring`, `--amber`, `--radius`) deleted; grep for their usage returns empty first.