/**
 * Shared primitives (DESIGN.md §4). Every export that existed before the
 * revamp keeps its name and props — unmigrated views compile unchanged;
 * new capabilities are additive (sizes, busy, tones, error slots, …).
 */

import {
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { ArrowDownRight, ArrowUpRight } from "./icons";
import "./ui.css";

// --- Button (§4.1) ---

export type ButtonVariant =
  | "default"
  | "primary"
  | "ghost"
  | "danger"
  | "danger-outline"
  | "link";

export type ButtonSize = "sm" | "md" | "lg";

export function Button({
  variant = "default",
  size = "md",
  block = false,
  busy = false,
  className = "",
  type = "button",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
  /** Shows a centered spinner, hides the label (width locked), blocks input. */
  busy?: boolean;
}) {
  const cls = [
    "btn",
    `btn--${variant}`,
    size !== "md" ? `btn--${size}` : "",
    block ? "btn--block" : "",
    busy ? "btn--busy" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} className={cls} aria-busy={busy || undefined} {...rest}>
      {children}
    </button>
  );
}

// --- GlassCard (§4.2) ---

/**
 * Migration insurance: renders SOLID `.card` chrome. Glass is reserved for
 * floating layers (Dialog, Toast, palette, topbar-on-scroll) — data cards sit
 * on solid `--card`. The `heavy` prop is accepted for backward compatibility
 * and intentionally has no visual effect anymore.
 */
export function GlassCard({
  heavy = false,
  className = "",
  children,
}: {
  heavy?: boolean;
  className?: string;
  children: ReactNode;
}) {
  void heavy;
  return <div className={`card ${className}`.trim()}>{children}</div>;
}

// --- Badge (§4.7) ---

export type BadgeTone = "ok" | "warn" | "danger" | "info" | "neutral" | "accent";

const CANONICAL_TONES = new Set<string>([
  "ok",
  "warn",
  "danger",
  "info",
  "neutral",
  "accent",
]);

/**
 * Canonical API-status → tone mapping (§4.7). Use everywhere a raw platform
 * or app status renders as a badge.
 */
export function toneForStatus(status: string): BadgeTone {
  const s = status.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (CANONICAL_TONES.has(s)) return s as BadgeTone;
  switch (s) {
    case "active":
    case "enabled":
    case "connected":
    case "running":
    case "sent":
    case "won":
    case "executed":
    case "success":
    case "engaged":
    case "valid": // Phase 12 email verification verdict
      return "ok";
    case "paused":
    case "stale":
    case "expiring":
    case "risky": // Phase 12: deliverable but reputation-hazardous
      return "warn";
    case "error":
    case "rejected":
    case "disapproved":
    case "failed":
    case "lost":
    case "suspended":
    case "invalid": // Phase 12: verified undeliverable
      return "danger";
    case "pending":
    case "in_review":
    case "queued":
    case "syncing":
    case "coming_soon":
      return "info";
    default:
      if (s.startsWith("disconnected") && s.includes("error")) return "danger";
      // DRAFT, ARCHIVED, REMOVED, not-connected, disconnected, unknown
      return "neutral";
  }
}

/**
 * Tones are the §4.7 union; any other string (a raw API status, or the legacy
 * "" / "pending" / "error" values) is normalized through toneForStatus so
 * existing call sites keep working and keep the canonical colors.
 */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: BadgeTone | (string & {});
  children: ReactNode;
}) {
  const t: BadgeTone = CANONICAL_TONES.has(tone)
    ? (tone as BadgeTone)
    : toneForStatus(tone);
  return <span className={`badge badge--${t}`}>{children}</span>;
}

// --- Platform chip (§4.8) ---

/**
 * Neutral, label-based platform identity — never brand or chart colors.
 * Names come from the platform registry (`GET /api/platforms`).
 */
export function PlatformChip({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  const initial = (name.trim().charAt(0) || "?").toUpperCase();
  return (
    <span className={`chip ${className}`.trim()}>
      <span className="chip-disc" aria-hidden="true">
        {initial}
      </span>
      <span>{name}</span>
    </span>
  );
}

// --- Field (§4.6) ---

export function Field({
  label,
  optional = false,
  description,
  error,
  children,
}: {
  label: ReactNode;
  optional?: boolean;
  /** Helper text under the label. */
  description?: ReactNode;
  /** Error text (container is aria-live so SRs announce validation). */
  error?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label} {optional && <em className="opt">optional</em>}
      </span>
      {description && <span className="field-desc">{description}</span>}
      {children}
      <span className="field-error" aria-live="polite">
        {error}
      </span>
    </label>
  );
}

// --- Skeleton (§4.12) ---

export function Skeleton({
  width,
  height = "1em",
  className = "",
}: {
  width?: string | number;
  height?: string | number;
  className?: string;
}) {
  return (
    <span
      className={`skel ${className}`.trim()}
      style={{ width, height }}
      aria-hidden="true"
    />
  );
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skel-text" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skel" />
      ))}
    </div>
  );
}

// --- EmptyState (§4.11) ---

export function EmptyState({
  icon,
  title,
  children,
  action,
  hero = false,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  /** Large view-level empties get the aurora wash (expressive register). */
  hero?: boolean;
}) {
  return (
    <div className={hero ? "empty empty--hero" : "empty"}>
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

// --- KPI stat tile (§4.3) ---

/**
 * The ONE stat-tile system. `delta` is a percent change vs a named period;
 * color = direction × `upIsGood` (spend up may be red). Value should be
 * pre-formatted compact ($4.2M, 12.9K) — proportional figures, never
 * tabular-nums at display size. At most one `hero` tile per view.
 */
export function Kpi({
  label,
  value,
  delta,
  deltaLabel = "vs prev period",
  upIsGood = true,
  hero = false,
  sparkline,
}: {
  label: string;
  value: string | number;
  delta?: number | null;
  deltaLabel?: string;
  upIsGood?: boolean;
  hero?: boolean;
  sparkline?: ReactNode;
}) {
  const dir = delta == null ? null : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const good = dir === "up" ? upIsGood : dir === "down" ? !upIsGood : null;
  const deltaText =
    delta == null
      ? null
      : `${delta > 0 ? "+" : ""}${Math.abs(delta) >= 100 ? Math.round(delta) : delta.toFixed(1)}%`;
  const aria =
    `${label}: ${value}` + (deltaText ? `, ${deltaText} ${deltaLabel}` : "");
  return (
    <div className={hero ? "kpi kpi--hero" : "kpi"} role="group" aria-label={aria}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {deltaText && (
        <span
          className={`kpi-delta ${
            good == null ? "" : good ? "kpi-delta--good" : "kpi-delta--bad"
          }`.trim()}
        >
          {dir === "up" && <ArrowUpRight size={12} aria-hidden="true" />}
          {dir === "down" && <ArrowDownRight size={12} aria-hidden="true" />}
          {deltaText} {deltaLabel}
        </span>
      )}
      {sparkline && (
        <div className="kpi-spark" aria-hidden="true">
          {sparkline}
        </div>
      )}
    </div>
  );
}

export function KpiGrid({ children }: { children: ReactNode }) {
  return <div className="kpi-grid">{children}</div>;
}

/** Structure-matching skeleton twin for a KPI tile (§4.3). */
export function KpiSkeleton() {
  return (
    <div className="kpi" aria-hidden="true">
      <Skeleton width="45%" height="0.85em" />
      <Skeleton width="70%" height="1.6em" />
      <Skeleton width="55%" height="0.8em" />
    </div>
  );
}

// --- Tabs & Segmented (§4.9) ---

export interface TabItem {
  id: string;
  label: ReactNode;
}

function moveRoving(
  e: KeyboardEvent<HTMLElement>,
  count: number,
  current: number,
): number | null {
  switch (e.key) {
    case "ArrowRight":
      return (current + 1) % count;
    case "ArrowLeft":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/** View tabs (role=tablist, ←/→ roving tabindex, automatic activation). */
export function Tabs({
  tabs,
  active,
  onChange,
  ariaLabel,
  className = "",
}: {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  ariaLabel?: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const activeIdx = Math.max(
    0,
    tabs.findIndex((t) => t.id === active),
  );
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const next = moveRoving(e, tabs.length, activeIdx);
    if (next == null) return;
    e.preventDefault();
    onChange(tabs[next].id);
    refs.current[next]?.focus();
  };
  return (
    <div className={`tabs ${className}`.trim()} role="tablist" aria-label={ariaLabel}>
      {tabs.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          className="tab"
          aria-selected={t.id === active}
          tabIndex={i === activeIdx ? 0 : -1}
          onClick={() => onChange(t.id)}
          onKeyDown={onKey}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Value picker (role=radiogroup): density, chart/table twin, date presets. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className = "",
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const activeIdx = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const next = moveRoving(e, options.length, activeIdx);
    if (next == null) return;
    e.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  };
  return (
    <div
      className={`seg ${className}`.trim()}
      role="radiogroup"
      aria-label={ariaLabel}
    >
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          className="seg-item"
          aria-checked={o.value === value}
          tabIndex={i === activeIdx ? 0 : -1}
          onClick={() => onChange(o.value)}
          onKeyDown={onKey}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// --- Alert (§4.10, inline) ---

export type AlertTone = "ok" | "warn" | "danger" | "info";

export function Alert({
  tone = "info",
  title,
  children,
  className = "",
}: {
  tone?: AlertTone;
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`alert alert--${tone} ${className}`.trim()}
      role={tone === "danger" ? "alert" : "status"}
    >
      <div>
        {title && <div className="alert-title">{title}</div>}
        {children}
      </div>
    </div>
  );
}

// --- Kbd chip (§4.16) ---

/** Shortcut chip. aria-hidden — the accessible name lives on the control. */
export function Kbd({
  children,
  onField = false,
}: {
  children: ReactNode;
  /** Set on dark brand fields (sidebar, auth aside). */
  onField?: boolean;
}) {
  return (
    <kbd className={onField ? "kbd kbd--field" : "kbd"} aria-hidden="true">
      {children}
    </kbd>
  );
}

// --- misc shared helpers ---

/** Stable id helper for label/control wiring in composite fields. */
export function useFieldIds(): { id: string; errorId: string; descId: string } {
  const base = useId();
  return { id: base, errorId: `${base}-err`, descId: `${base}-desc` };
}
