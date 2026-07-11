/**
 * Runtime theming: (a) the user's light/dark preference, persisted and
 * applied as data-theme on <html> (theme.css narrows color-scheme, which
 * flips every light-dark() token at once), and (b) tenant white-label
 * branding fetched from /api/branding/resolve and mapped onto CSS variables.
 *
 * Security note: logo_url / favicon_url land in <img src> / <link href>.
 * The backend only accepts http(s) (schemas.BrandingIn), and safeBrandUrl()
 * re-checks here so a compromised or stale API response still can't
 * introduce a javascript:/data: scheme client-side.
 */

import { useSyncExternalStore } from "react";
import { api } from "./api";

// --- light/dark preference ---

export type ThemePref = "light" | "dark" | "system";

const THEME_KEY = "theme";

function readPref(): ThemePref {
  const v = localStorage.getItem(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

let prefListeners: Array<() => void> = [];

export function getThemePref(): ThemePref {
  return readPref();
}

export function setThemePref(pref: ThemePref): void {
  if (pref === "system") localStorage.removeItem(THEME_KEY);
  else localStorage.setItem(THEME_KEY, pref);
  applyThemePref();
  prefListeners.forEach((l) => l());
}

function applyThemePref(): void {
  const pref = readPref();
  if (pref === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = pref;
}

/** The user's theme preference + setter, for a toggle control. */
export function useTheme(): { pref: ThemePref; setPref: (p: ThemePref) => void } {
  const pref = useSyncExternalStore(
    (cb) => {
      prefListeners.push(cb);
      return () => {
        prefListeners = prefListeners.filter((l) => l !== cb);
      };
    },
    readPref
  );
  return { pref, setPref: setThemePref };
}

// --- row density (topbar toggle; DESIGN.md §5.2) ---
// Stamped as data-density on <html>; theme.css maps it onto --row-h.
// Client-role sessions never get the attribute: App calls applyDensity(false)
// for them, which clears it regardless of the stored preference.

export type DensityPref = "comfortable" | "dense";

const DENSITY_KEY = "density";

let densityListeners: Array<() => void> = [];
let densityAllowed = false;

function readDensity(): DensityPref {
  return localStorage.getItem(DENSITY_KEY) === "dense" ? "dense" : "comfortable";
}

function stampDensity(): void {
  const root = document.documentElement;
  if (densityAllowed && readDensity() === "dense") root.dataset.density = "dense";
  else delete root.dataset.density;
}

export function getDensityPref(): DensityPref {
  return readDensity();
}

export function setDensityPref(pref: DensityPref): void {
  if (pref === "dense") localStorage.setItem(DENSITY_KEY, "dense");
  else localStorage.removeItem(DENSITY_KEY);
  stampDensity();
  densityListeners.forEach((l) => l());
}

/** Gate density by session role: true for team sessions, false for
 * client-role sessions and logged-out screens (clears the attribute). */
export function applyDensity(allowed: boolean): void {
  densityAllowed = allowed;
  stampDensity();
}

/** The user's density preference + setter, for the topbar Segmented. */
export function useDensity(): {
  pref: DensityPref;
  setPref: (p: DensityPref) => void;
} {
  const pref = useSyncExternalStore(
    (cb) => {
      densityListeners.push(cb);
      return () => {
        densityListeners = densityListeners.filter((l) => l !== cb);
      };
    },
    readDensity
  );
  return { pref, setPref: setDensityPref };
}

// --- tenant branding ---

export interface PublicBranding {
  product_name: string;
  logo_url: string | null;
  favicon_url: string | null;
  colors: Record<string, string>;
  apply_to_team: boolean;
  is_custom: boolean;
}

export const DEFAULT_BRANDING: PublicBranding = {
  product_name: "Salescale",
  logo_url: null,
  favicon_url: null,
  colors: {},
  apply_to_team: false,
  is_custom: false,
};

/** Backend color keys → the CSS variables they override (theme.css). A brand
 * color is mode-invariant, so setting the var inline on <html> deliberately
 * wins over both light-dark() values. */
const BRAND_VAR_MAP: Record<string, string[]> = {
  primary: ["--accent", "--brand-blue"],
  primary_strong: ["--accent-strong"],
  primary_soft: ["--accent-soft"],
  header_start: ["--header-start"],
  header_end: ["--header-end"],
};

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/** Only http(s) URLs may reach an <img src>/<link href>. */
export function safeBrandUrl(url: string | null | undefined): string | null {
  if (url && (url.startsWith("https://") || url.startsWith("http://"))) return url;
  return null;
}

let branding: PublicBranding = DEFAULT_BRANDING;
let brandingListeners: Array<() => void> = [];

/** The effective branding for this host (neutral default until resolved). */
export function useBranding(): PublicBranding {
  return useSyncExternalStore(
    (cb) => {
      brandingListeners.push(cb);
      return () => {
        brandingListeners = brandingListeners.filter((l) => l !== cb);
      };
    },
    () => branding
  );
}

function applyBrandingVars(b: PublicBranding): void {
  const root = document.documentElement;
  for (const vars of Object.values(BRAND_VAR_MAP)) {
    vars.forEach((v) => root.style.removeProperty(v));
  }
  for (const [key, value] of Object.entries(b.colors || {})) {
    if (!HEX_RE.test(value)) continue;
    (BRAND_VAR_MAP[key] ?? []).forEach((v) => root.style.setProperty(v, value));
  }
  document.title = b.product_name;
  const favicon = safeBrandUrl(b.favicon_url);
  const link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
  if (link) {
    if (!link.dataset.defaultHref) link.dataset.defaultHref = link.href;
    link.href = favicon ?? link.dataset.defaultHref;
  }
}

/** Fetch this host's public branding and apply it. Safe to call again (e.g.
 * after the Branding settings page saves) — listeners re-render. */
export async function refreshBranding(): Promise<void> {
  try {
    const b = await api<PublicBranding>(
      `/api/branding/resolve?host=${encodeURIComponent(window.location.host)}`
    );
    branding = { ...DEFAULT_BRANDING, ...b };
  } catch {
    branding = DEFAULT_BRANDING; // neutral default — never block the app on branding
  }
  applyBrandingVars(branding);
  brandingListeners.forEach((l) => l());
}

/** Boot step — call once before first render (main.tsx). */
export function initTheme(): void {
  applyThemePref();
  void refreshBranding();
}
