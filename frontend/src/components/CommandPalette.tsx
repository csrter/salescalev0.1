/**
 * Cmd+K command palette: fuzzy navigation to tabs, clients and actions.
 *
 * The caller composes the command list — this component never decides what a
 * user may do. Role-gated destinations must be filtered out by the caller
 * (App.tsx builds commands from the same role-filtered nav list the sidebar
 * renders), so the palette can't become a side door around nav gating.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import "./ui.css";

export interface Command {
  id: string;
  title: string;
  section?: string;
  /** Extra text the fuzzy filter matches against (not displayed). */
  keywords?: string;
  hint?: string;
  run: () => void;
}

/** Subsequence fuzzy score: higher is better, null = no match. */
function fuzzyScore(query: string, text: string): number | null {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  if (!q) return 0;
  let score = 0;
  let ti = 0;
  let streak = 0;
  for (const ch of q) {
    if (ch === " ") continue;
    const found = t.indexOf(ch, ti);
    if (found === -1) return null;
    streak = found === ti ? streak + 1 : 1;
    score += streak + (found === 0 || t[found - 1] === " " ? 3 : 0);
    ti = found + 1;
  }
  return score;
}

export function CommandPalette({
  commands,
  loadDynamic,
}: {
  commands: Command[];
  /** Called each time the palette opens — e.g. fetch clients to jump to. */
  loadDynamic?: () => Promise<Command[]>;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [dynamic, setDynamic] = useState<Command[]>([]);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    // Lets UI affordances (the sidebar search button) open the palette too.
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("cmdk:open", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("cmdk:open", onOpen);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    if (loadDynamic) {
      let stale = false;
      loadDynamic()
        .then((cmds) => {
          if (!stale) setDynamic(cmds);
        })
        .catch(() => {});
      return () => {
        stale = true;
      };
    }
  }, [open, loadDynamic]);

  const close = useCallback(() => setOpen(false), []);

  if (!open) return null;

  const all = [...commands, ...dynamic];
  const matches = all
    .map((c) => ({
      c,
      score: fuzzyScore(query, `${c.title} ${c.keywords ?? ""}`),
    }))
    .filter((m): m is { c: Command; score: number } => m.score !== null)
    .sort((a, b) => b.score - a.score)
    .map((m) => m.c);

  const run = (cmd: Command) => {
    close();
    cmd.run();
  };

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && matches[active]) {
      e.preventDefault();
      run(matches[active]);
    }
  };

  // Group consecutive results by section for the headers.
  let lastSection: string | undefined;

  return (
    <div className="cmdk-backdrop" onClick={close}>
      <div
        className="cmdk"
        role="dialog"
        aria-label="Command palette"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          autoFocus
          placeholder="Jump to a client, page or action…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onInputKey}
        />
        <div className="cmdk-list" ref={listRef}>
          {matches.length === 0 && (
            <div className="cmdk-empty">No matches for “{query}”</div>
          )}
          {matches.map((c, i) => {
            const header =
              c.section && c.section !== lastSection ? c.section : null;
            lastSection = c.section;
            return (
              <div key={c.id}>
                {header && <div className="cmdk-section">{header}</div>}
                <button
                  type="button"
                  className={`cmdk-item ${i === active ? "active" : ""}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => run(c)}
                  ref={(el) => {
                    if (i === active)
                      el?.scrollIntoView({ block: "nearest" });
                  }}
                >
                  <span>{c.title}</span>
                  {c.hint && <span className="cmdk-hint">{c.hint}</span>}
                </button>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> navigate
          </span>
          <span>
            <kbd>↵</kbd> open
          </span>
          <span>
            <kbd>esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
