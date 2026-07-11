/**
 * DataTable (§4.4) — one sortable, keyboard-operable table shared by CRM,
 * campaign, outreach and admin views. Wrapper is a padding-0 .card; header is
 * sticky on --surface; row height follows --row-h (density toggle stamps
 * data-density on <html>, the token does the rest).
 *
 * Keyboard (when rows are clickable): the table is a grid with roving
 * tabindex rows — ↑/↓ move, Home/End jump, Enter/Space opens. Sort headers
 * are real <button>s with aria-sort on the th. Selection and row-count
 * changes are announced through a visually-hidden live region.
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { ArrowDown, ArrowUp } from "./icons";
import { Skeleton } from "./ui";
import "./ui.css";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Provide to make the column sortable. */
  sortValue?: (row: T) => string | number | null | undefined;
  align?: "left" | "right";
}

const SKELETON_ROWS = 6;

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  loading = false,
  refetching = false,
  emptyMessage = "Nothing here yet.",
  empty,
  caption,
  initialSort,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  loading?: boolean;
  /** Holds the previous rows at half opacity — never a skeleton flash. */
  refetching?: boolean;
  emptyMessage?: ReactNode;
  /** Rich empty slot (e.g. an <EmptyState/>); wins over emptyMessage. */
  empty?: ReactNode;
  /** Accessible table caption (visually hidden). */
  caption?: ReactNode;
  /** Column key (prefix with "-" for descending), e.g. "-created_at". */
  initialSort?: string;
}) {
  const [sort, setSort] = useState<string | null>(initialSort ?? null);
  const [activeIdx, setActiveIdx] = useState(0);
  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const desc = sort.startsWith("-");
    const key = desc ? sort.slice(1) : sort;
    const col = columns.find((c) => c.key === key);
    if (!col?.sortValue) return rows;
    const sv = col.sortValue;
    return [...rows].sort((a, b) => {
      const va = sv(a);
      const vb = sv(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1; // nulls last regardless of direction
      if (vb == null) return -1;
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb), undefined, { numeric: true });
      return desc ? -cmp : cmp;
    });
  }, [rows, sort, columns]);

  // Keep the roving index valid as the row set changes.
  useEffect(() => {
    setActiveIdx((i) => Math.min(i, Math.max(0, sorted.length - 1)));
  }, [sorted.length]);

  const toggleSort = (key: string) =>
    setSort((cur) => (cur === key ? `-${key}` : cur === `-${key}` ? null : key));

  const interactive = Boolean(onRowClick);

  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, i: number) => {
    let next: number | null = null;
    if (e.key === "ArrowDown") next = Math.min(i + 1, sorted.length - 1);
    else if (e.key === "ArrowUp") next = Math.max(i - 1, 0);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = sorted.length - 1;
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onRowClick?.(sorted[i]);
      return;
    } else return;
    e.preventDefault();
    setActiveIdx(next);
    rowRefs.current[next]?.focus();
  };

  const selectedIdx =
    selectedKey != null ? sorted.findIndex((r) => rowKey(r) === selectedKey) : -1;

  return (
    <div className={`table-wrap ${refetching ? "table-refetching" : ""}`.trim()}>
      <table className="table" role={interactive ? "grid" : undefined}>
        {caption && <caption className="visually-hidden">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort === c.key || sort === `-${c.key}`;
              const asc = sort === c.key;
              return (
                <th
                  key={c.key}
                  className={c.align === "right" ? "align-right" : undefined}
                  aria-sort={active ? (asc ? "ascending" : "descending") : undefined}
                >
                  {c.sortValue ? (
                    <button
                      type="button"
                      className="table-sort"
                      onClick={() => toggleSort(c.key)}
                    >
                      {c.header}
                      {active && (
                        <span className="table-sort-arrow" aria-hidden="true">
                          {asc ? (
                            <ArrowUp size={12} />
                          ) : (
                            <ArrowDown size={12} />
                          )}
                        </span>
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: SKELETON_ROWS }, (_, i) => (
                <tr key={`s${i}`} aria-hidden="true">
                  {columns.map((c) => (
                    <td key={c.key}>
                      <Skeleton height="0.85em" />
                    </td>
                  ))}
                </tr>
              ))
            : sorted.map((row, i) => {
                const k = rowKey(row);
                return (
                  <tr
                    key={k}
                    ref={(el) => {
                      rowRefs.current[i] = el;
                    }}
                    className={[
                      interactive ? "clickable" : "",
                      selectedKey === k ? "selected" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    tabIndex={interactive ? (i === activeIdx ? 0 : -1) : undefined}
                    aria-selected={
                      interactive && selectedKey != null
                        ? selectedKey === k
                        : undefined
                    }
                    onClick={interactive ? () => onRowClick?.(row) : undefined}
                    onKeyDown={interactive ? (e) => onRowKey(e, i) : undefined}
                    onFocus={interactive ? () => setActiveIdx(i) : undefined}
                  >
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={c.align === "right" ? "align-right" : undefined}
                      >
                        {c.render(row)}
                      </td>
                    ))}
                  </tr>
                );
              })}
          {!loading && sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="muted">
                {empty ?? emptyMessage}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {/* Row-count / selection announcements for screen readers. */}
      <div className="visually-hidden" aria-live="polite">
        {loading
          ? "Loading rows"
          : `${sorted.length} row${sorted.length === 1 ? "" : "s"}` +
            (selectedIdx >= 0
              ? `, row ${selectedIdx + 1} selected`
              : "")}
      </div>
    </div>
  );
}
