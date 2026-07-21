import { useEffect, useRef, useState } from "react";

/**
 * A dismissible install nudge for the mobile PWA, in two flavors:
 * - iOS Safari has no install prompt API, so the nudge coaches the manual
 *   path (Share → Add to Home Screen).
 * - Chromium Android fires `beforeinstallprompt`; we stash the event and the
 *   nudge gets a real "Install" button that triggers the native dialog.
 * "Not now" snoozes for 14 days (the old one-shot dismissal buried the
 * feature forever); nothing renders on desktop widths, when already
 * installed, or under Electron.
 */
const DISMISS_KEY = "pwa_install_hint_dismissed";
const SNOOZE_DAYS = 14;

function isIosSafari(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  const isIos = /iphone|ipad|ipod/i.test(ua) ||
    // iPadOS 13+ reports as Mac; disambiguate by touch support.
    (/macintosh/i.test(ua) && "ontouchend" in document);
  const isSafari = /safari/i.test(ua) && !/crios|fxios|edgios|opios/i.test(ua);
  return isIos && isSafari;
}

function isStandalone(): boolean {
  return (
    (window.navigator as unknown as { standalone?: boolean }).standalone === true ||
    window.matchMedia?.("(display-mode: standalone)").matches === true
  );
}

function isNarrow(): boolean {
  return window.matchMedia?.("(max-width: 760px)").matches === true;
}

function snoozed(): boolean {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    if (!raw) return false;
    // Legacy value "1" parses as epoch-adjacent and correctly reads as
    // expired — old permanent dismissals become eligible again.
    const at = Number(raw);
    if (!Number.isFinite(at)) return false;
    return Date.now() - at < SNOOZE_DAYS * 24 * 60 * 60 * 1000;
  } catch {
    return false;
  }
}

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/** The iOS Share glyph — an up-arrow out of a tray. */
function ShareGlyph() {
  return (
    <svg width="16" height="18" viewBox="0 0 16 18" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path
        d="M8 1.5v9M8 1.5 5 4.5M8 1.5l3 3"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M3.5 7.5H2.5v8.5h11V7.5h-1"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function InstallHint() {
  const [show, setShow] = useState(false);
  const [canPrompt, setCanPrompt] = useState(false);
  const bipEvent = useRef<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    if (snoozed() || isStandalone()) return;

    // Android/Chromium path: the browser tells us installability. Stash the
    // event (preventDefault suppresses Chrome's mini-infobar) and surface
    // our own nudge with a real Install button.
    const onBip = (e: Event) => {
      if (!isNarrow()) return;
      e.preventDefault();
      bipEvent.current = e as BeforeInstallPromptEvent;
      setCanPrompt(true);
      setShow(true);
    };
    window.addEventListener("beforeinstallprompt", onBip);

    // iOS path: no event exists, so show after a short delay (never during
    // first paint / login).
    let t: ReturnType<typeof setTimeout> | undefined;
    if (isIosSafari()) {
      t = setTimeout(() => setShow(true), 1200);
    }
    return () => {
      window.removeEventListener("beforeinstallprompt", onBip);
      if (t) clearTimeout(t);
    };
  }, []);

  if (!show) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      /* ignore */
    }
    setShow(false);
  };

  const install = async () => {
    const ev = bipEvent.current;
    if (!ev) return;
    await ev.prompt();
    const choice = await ev.userChoice.catch(() => null);
    bipEvent.current = null;
    if (choice?.outcome === "dismissed") dismiss();
    else setShow(false);
  };

  return (
    <div
      role="dialog"
      aria-label="Install app"
      style={{
        position: "fixed",
        left: "max(12px, env(safe-area-inset-left))",
        right: "max(12px, env(safe-area-inset-right))",
        bottom: "calc(12px + env(safe-area-inset-bottom))",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "12px 14px",
        borderRadius: "12px",
        background: "var(--surface-raised, #fff)",
        border: "1px solid var(--border-strong, #d3d7e5)",
        boxShadow: "0 8px 30px rgba(15, 33, 71, 0.22)",
        color: "var(--ink, #111530)",
        font: "inherit",
      }}
    >
      {!canPrompt && <ShareGlyph />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: "14px", fontWeight: 600, lineHeight: 1.3 }}>
          Add to your Home Screen
        </div>
        <div style={{ fontSize: "12.5px", color: "var(--ink-soft, #565e7d)", marginTop: "2px", lineHeight: 1.35 }}>
          {canPrompt
            ? "Install this as an app — full screen, one tap from your results."
            : "Tap the Share button, then “Add to Home Screen” to use this like an app."}
        </div>
      </div>
      {canPrompt && (
        <button
          onClick={install}
          style={{
            flexShrink: 0,
            border: "none",
            borderRadius: "6px",
            background: "var(--accent, #2b62e0)",
            color: "var(--ink-on-accent, #fff)",
            fontSize: "13px",
            fontWeight: 600,
            padding: "8px 14px",
            cursor: "pointer",
          }}
        >
          Install
        </button>
      )}
      <button
        onClick={dismiss}
        aria-label="Not now"
        style={{
          flexShrink: 0,
          border: "none",
          background: "transparent",
          color: "var(--ink-faint, #8b93ad)",
          fontSize: "20px",
          lineHeight: 1,
          padding: "4px 6px",
          cursor: "pointer",
        }}
      >
        ×
      </button>
    </div>
  );
}
