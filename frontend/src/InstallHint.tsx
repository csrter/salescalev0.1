import { useEffect, useState } from "react";

/**
 * A one-time, dismissible nudge shown only to iOS Safari visitors who haven't
 * installed the app yet. iOS has no automatic install prompt (unlike Android),
 * so the home-screen install has to be pointed out: Share → Add to Home Screen.
 * Renders nothing anywhere else (desktop, Android, already-installed, Electron).
 */
const DISMISS_KEY = "pwa_install_hint_dismissed";

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

  useEffect(() => {
    try {
      if (localStorage.getItem(DISMISS_KEY)) return;
    } catch {
      /* private mode — just show it */
    }
    if (isIosSafari() && !isStandalone()) {
      // Small delay so it doesn't slam in during first paint / login.
      const t = setTimeout(() => setShow(true), 1200);
      return () => clearTimeout(t);
    }
  }, []);

  if (!show) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
    setShow(false);
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
      <ShareGlyph />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: "14px", fontWeight: 600, lineHeight: 1.3 }}>
          Add to your Home Screen
        </div>
        <div style={{ fontSize: "12.5px", color: "var(--ink-soft, #565e7d)", marginTop: "2px", lineHeight: 1.35 }}>
          Tap the Share button, then “Add to Home Screen” to use this like an app.
        </div>
      </div>
      <button
        onClick={dismiss}
        aria-label="Dismiss"
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
