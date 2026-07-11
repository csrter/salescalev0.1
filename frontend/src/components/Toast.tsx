/**
 * Toast notifications (§4.10) — the ONE feedback pattern. Mount
 * <ToastProvider> once at the app root; anywhere below it:
 *   const toast = useToast(); toast("Saved", "ok").
 *
 * The live region is permanently mounted (SRs only announce changes inside a
 * pre-existing region). Tones: "ok" | "error" | "info" (unchanged public
 * vocabulary); "error" renders the danger tone with role="alert".
 * Auto-dismiss 5s, paused while hovered/focused; close buttons; max 3.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { X } from "./icons";
import "./ui.css";

export type ToastTone = "ok" | "error" | "info";

export type ToastFn = (message: string, tone?: ToastTone) => void;

const ToastCtx = createContext<ToastFn>(() => {});

const AUTO_DISMISS_MS = 5000;
const MAX_TOASTS = 3;

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const toneClass: Record<ToastTone, string> = {
  ok: "toast--ok",
  error: "toast--danger",
  info: "toast--info",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const clearTimer = useCallback((id: number) => {
    const t = timers.current.get(id);
    if (t != null) {
      window.clearTimeout(t);
      timers.current.delete(id);
    }
  }, []);

  const dismiss = useCallback(
    (id: number) => {
      clearTimer(id);
      setToasts((prev) => prev.filter((t) => t.id !== id));
    },
    [clearTimer],
  );

  const arm = useCallback(
    (id: number) => {
      clearTimer(id);
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS),
      );
    },
    [clearTimer, dismiss],
  );

  const push = useCallback<ToastFn>(
    (message, tone = "info") => {
      const id = nextId.current++;
      setToasts((prev) => {
        const next = [...prev, { id, message, tone }];
        // Cap the stack: drop the oldest (and their timers).
        const dropped = next.slice(0, Math.max(0, next.length - MAX_TOASTS));
        dropped.forEach((t) => clearTimer(t.id));
        return next.slice(-MAX_TOASTS);
      });
      arm(id);
    },
    [arm, clearTimer],
  );

  // Clear all pending timers on unmount (logout/route teardown).
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((t) => window.clearTimeout(t));
      map.clear();
    };
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      {/* Permanently mounted live region. */}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast ${toneClass[t.tone]}`}
            role={t.tone === "error" ? "alert" : undefined}
            onMouseEnter={() => clearTimer(t.id)}
            onMouseLeave={() => arm(t.id)}
            onFocus={() => clearTimer(t.id)}
            onBlur={() => arm(t.id)}
          >
            <span className="toast-msg">{t.message}</span>
            <button
              type="button"
              className="toast-close"
              aria-label="Dismiss notification"
              onClick={() => dismiss(t.id)}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast(): ToastFn {
  return useContext(ToastCtx);
}
