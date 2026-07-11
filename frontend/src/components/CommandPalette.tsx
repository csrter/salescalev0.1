/**
 * Cmd+K command palette: fuzzy navigation to tabs, clients and actions.
 *
 * The caller composes the command list — this component never decides what a
 * user may do. Role-gated destinations must be filtered out by the caller
 * (App.tsx builds commands from the same role-filtered nav list the sidebar
 * renders), so the palette can't become a side door around nav gating.
 *
 * Contract kept: opens on Cmd/Ctrl+K or the window "cmdk:open" CustomEvent;
 * `commands` + optional `loadDynamic()` per open. A11y: combobox/listbox with
 * aria-activedescendant (focus stays on the input), Escape closes anywhere,
 * body scroll locked while open.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Search } from "./icons";
import { Kbd } from "./ui";
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
  const listId = "cmdk-listbox";

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
    setDynamic([]); // never show a previous session's stale results
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    let stale = false;
    if (loadDynamic) {
      loadDynamic()
        .then((cmds) => {
          if (!stale) setDynamic(cmds);
        })
        .catch(() => {});
    }
    return () => {
      stale = true;
      document.body.style.overflow = prevOverflow;
    };
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

  // Re-clamp when the match list shrinks under the current index.
  const activeIdx = Math.min(active, Math.max(0, matches.length - 1));
  const activeId = matches[activeIdx] ? `cmdk-opt-${matches[activeIdx].id}` : undefined;

  const run = (cmd: Command) => {
    close();
    cmd.run();
  };

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(activeIdx + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(activeIdx - 1, 0));
    } else if (e.key === "Enter" && matches[activeIdx]) {
      e.preventDefault();
      run(matches[activeIdx]);
    }
  };

  // Group consecutive results by section for the headers.
  let lastSection: string | undefined;

  return (
    <div
      className="cmdk-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          close();
        } else if (e.key === "Tab") {
          // Focus stays on the input (aria-activedescendant pattern).
          e.preventDefault();
        }
      }}
    >
      <div
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div className="cmdk-input-row">
          <Search size={16} aria-hidden="true" />
          <input
            autoFocus
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeId}
            aria-label="Search commands"
            placeholder="Jump to a client, page or action…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onInputKey}
          />
        </div>
        <div className="cmdk-list" role="listbox" id={listId} ref={listRef}>
          {matches.length === 0 && (
            <div className="cmdk-empty">No matches for “{query}”</div>
          )}
          {matches.map((c, i) => {
            const header =
              c.section && c.section !== lastSection ? c.section : null;
            lastSection = c.section;
            return (
              <div key={c.id}>
                {header && (
                  <div className="cmdk-section" role="presentation">
                    {header}
                  </div>
                )}
                <div
                  role="option"
                  id={`cmdk-opt-${c.id}`}
                  aria-selected={i === activeIdx}
                  className={`cmdk-item ${i === activeIdx ? "active" : ""}`.trim()}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => run(c)}
                  ref={(el) => {
                    if (i === activeIdx) el?.scrollIntoView({ block: "nearest" });
                  }}
                >
                  <span>{c.title}</span>
                  {c.hint && (
                    <span className="cmdk-hint">
                      <Kbd>{c.hint}</Kbd>
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span>
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> navigate
          </span>
          <span>
            <Kbd>↵</Kbd> open
          </span>
          <span>
            <Kbd>esc</Kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
