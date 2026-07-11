/**
 * Dialog (§4.5) — the ONE modal primitive: portal, scrim, glass panel,
 * role=dialog/aria-modal, focus trap (sentinel loop), Escape, focus return.
 *
 * ConfirmDialog is the Change-Receipt variant — the ONLY rendering of the
 * staged-write confirmation (useManage().stage). Compliance-critical focus
 * rules: initial focus on Cancel, confirm disabled until the receipt rows
 * have rendered, scrim-click close OFF.
 */

import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, X } from "./icons";
import { PlatformChip } from "./ui";
import "./ui.css";

export type DialogSize = "sm" | "md" | "lg";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({
  open,
  onClose,
  title,
  size = "md",
  children,
  footer,
  closeOnScrim = true,
  initialFocus,
  className = "",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  size?: DialogSize;
  children: ReactNode;
  footer?: ReactNode;
  /** Scrim-click closes by default; ConfirmDialog turns this off. */
  closeOnScrim?: boolean;
  /** Element to focus on open (defaults to the panel itself). */
  initialFocus?: RefObject<HTMLElement | null>;
  className?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  // Focus in on open, restore to the invoker on close; lock body scroll.
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    const target = initialFocus?.current ?? panelRef.current;
    target?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
      restoreRef.current?.focus?.();
    };
    // initialFocus is a ref — reading .current in the effect is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const focusables = (): HTMLElement[] =>
    Array.from(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);

  const focusFirst = () => (focusables()[0] ?? panelRef.current)?.focus();
  const focusLast = () =>
    (focusables().at(-1) ?? panelRef.current)?.focus();

  return createPortal(
    <div
      className="dialog-scrim"
      onMouseDown={
        closeOnScrim
          ? (e) => {
              if (e.target === e.currentTarget) onClose();
            }
          : undefined
      }
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      {/* focus trap sentinels */}
      <span tabIndex={0} className="visually-hidden" onFocus={focusLast} />
      <div
        ref={panelRef}
        className={`dialog dialog--${size} ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="dialog-head">
          <h2 className="dialog-title" id={titleId}>
            {title}
          </h2>
          <button
            type="button"
            className="btn btn--ghost dialog-close"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer className="dialog-foot">{footer}</footer>}
      </div>
      <span tabIndex={0} className="visually-hidden" onFocus={focusFirst} />
    </div>,
    document.body,
  );
}

// --- Change Receipt (confirm variant) ---

export interface ReceiptDelta {
  /** Pre-formatted, e.g. "(+$70, +46.7%)". */
  text: string;
  /** Colored by direction: up = ok, down = danger. */
  tone: "ok" | "danger";
}

export interface ReceiptRow {
  key?: string;
  client?: string;
  campaign?: string;
  field: string;
  /** Platform display name → neutral chip (§4.8). */
  platform?: string;
  oldValue: ReactNode;
  newValue: ReactNode;
  /** For money/budget changes: absolute + % delta. See formatMoneyDelta. */
  delta?: ReceiptDelta | null;
}

/** Builds the "(+$70, +46.7%)" receipt delta for a money change. */
export function formatMoneyDelta(
  oldValue: number,
  newValue: number,
  currency = "$",
): ReceiptDelta {
  const abs = newValue - oldValue;
  const sign = abs >= 0 ? "+" : "−";
  const absText = `${sign}${currency}${Math.abs(abs).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}`;
  const pctText =
    oldValue !== 0
      ? `, ${sign}${Math.abs((abs / oldValue) * 100).toFixed(1)}%`
      : "";
  return {
    text: `(${absText}${pctText})`,
    tone: abs >= 0 ? "ok" : "danger",
  };
}

/**
 * The staged-write confirmation. Flow logic (useManage().stage) is the
 * caller's; this renders it. tone="danger" when any change pauses spend or
 * reduces budget.
 */
export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  rows,
  title = "Review changes",
  tone = "warn",
  confirmLabel,
  cancelLabel = "Keep staging",
  busy = false,
  children,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  rows: ReceiptRow[];
  title?: ReactNode;
  tone?: "warn" | "danger";
  confirmLabel?: ReactNode;
  cancelLabel?: ReactNode;
  busy?: boolean;
  /** Optional extra content rendered below the receipt. */
  children?: ReactNode;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Confirm stays disabled until the receipt list has actually rendered.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!open) {
      setReady(false);
      return;
    }
    if (rows.length > 0) setReady(true);
  }, [open, rows.length]);

  const n = rows.length;
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={
        <>
          <span className="dialog-warn-icon" aria-hidden="true">
            <AlertTriangle size={18} />
          </span>
          {title}
        </>
      }
      size="md"
      closeOnScrim={false}
      initialFocus={cancelRef}
      className={`dialog--confirm ${tone === "danger" ? "dialog--danger" : ""}`.trim()}
      footer={
        <>
          <button
            type="button"
            ref={cancelRef}
            className="btn btn--ghost"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${tone === "danger" ? "btn--danger" : "btn--primary"} ${
              busy ? "btn--busy" : ""
            }`.trim()}
            disabled={!ready || busy}
            aria-busy={busy || undefined}
            onClick={onConfirm}
          >
            {confirmLabel ??
              `Apply ${n} change${n === 1 ? "" : "s"} to live accounts`}
          </button>
        </>
      }
    >
      <ul className="receipt">
        {rows.map((r, i) => (
          <li key={r.key ?? i} className="receipt-row">
            <div className="receipt-meta">
              {r.platform && <PlatformChip name={r.platform} />}
              <span>
                {[r.client, r.campaign, r.field].filter(Boolean).join(" · ")}
              </span>
            </div>
            <div className="receipt-change">
              <del className="receipt-old">{r.oldValue}</del>
              <span className="receipt-arrow" aria-hidden="true">
                {"→"}
              </span>
              <ins className="receipt-new">{r.newValue}</ins>
              {r.delta && (
                <span className={`receipt-delta receipt-delta--${r.delta.tone}`}>
                  {r.delta.text}
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
      {children}
    </Dialog>
  );
}
