/**
 * Lightweight toast notifications. Mount <ToastProvider> once at the app
 * root; anywhere below it, const toast = useToast(); toast("Saved", "ok").
 */

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import "./ui.css";

export type ToastTone = "ok" | "error" | "info";

type ToastFn = (message: string, tone?: ToastTone) => void;

const ToastCtx = createContext<ToastFn>(() => {});

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const push = useCallback<ToastFn>((message, tone = "info") => {
    const id = nextId.current++;
    setToasts((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(
      () => setToasts((prev) => prev.filter((t) => t.id !== id)),
      4000
    );
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      {toasts.length > 0 && (
        <div className="toasts" role="status" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.tone}`}>
              {t.message}
            </div>
          ))}
        </div>
      )}
    </ToastCtx.Provider>
  );
}

export function useToast(): ToastFn {
  return useContext(ToastCtx);
}
