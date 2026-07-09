/**
 * Small shared primitives. These wrap the existing global styles (App.css)
 * where they exist — e.g. Button renders the same .primary/.ghost classes the
 * codebase already uses — so screens can adopt them incrementally without a
 * big-bang restyle.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./ui.css";

// --- Button ---

export type ButtonVariant = "default" | "primary" | "ghost" | "danger" | "link";

export function Button({
  variant = "default",
  block = false,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  block?: boolean;
}) {
  const cls = [
    variant === "default" ? "" : variant,
    block ? "block" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return <button className={cls || undefined} {...rest} />;
}

// --- GlassCard ---

export function GlassCard({
  heavy = false,
  className = "",
  children,
}: {
  heavy?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`glass-card ${heavy ? "heavy" : ""} ${className}`.trim()}>
      {children}
    </div>
  );
}

// --- Badge ---

/** Tones map onto the existing .badge status classes in App.css. */
export function Badge({
  tone = "",
  children,
}: {
  tone?: string;
  children: ReactNode;
}) {
  return <span className={`badge ${tone}`.trim()}>{children}</span>;
}

// --- Field ---

export function Field({
  label,
  optional = false,
  children,
}: {
  label: ReactNode;
  optional?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>
        {label} {optional && <em className="opt">optional</em>}
      </span>
      {children}
    </label>
  );
}

// --- Skeleton ---

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
      className={`skeleton ${className}`.trim()}
      style={{ display: "block", width, height }}
      aria-hidden="true"
    />
  );
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton-text" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton" />
      ))}
    </div>
  );
}

// --- EmptyState ---

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
