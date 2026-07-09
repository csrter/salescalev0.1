/**
 * One sortable data table shared by CRM, campaign and admin views, so every
 * list in the product sorts, hovers and loads the same way.
 */

import { useMemo, useState, type ReactNode } from "react";
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

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  loading = false,
  emptyMessage = "Nothing here yet.",
  initialSort,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  loading?: boolean;
  emptyMessage?: ReactNode;
  /** Column key (prefix with "-" for descending), e.g. "-created_at". */
  initialSort?: string;
}) {
  const [sort, setSort] = useState<string | null>(initialSort ?? null);

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

  const toggleSort = (key: string) =>
    setSort((cur) => (cur === key ? `-${key}` : cur === `-${key}` ? null : key));

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort === c.key || sort === `-${c.key}`;
              return (
                <th
                  key={c.key}
                  className={[
                    c.sortValue ? "sortable" : "",
                    c.align === "right" ? "align-right" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  aria-sort={
                    active
                      ? sort === c.key
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                  onClick={c.sortValue ? () => toggleSort(c.key) : undefined}
                >
                  {c.header}
                  {active && (
                    <span className="sort-arrow" aria-hidden="true">
                      {sort === c.key ? "▲" : "▼"}
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 4 }, (_, i) => (
                <tr key={`s${i}`}>
                  {columns.map((c) => (
                    <td key={c.key}>
                      <Skeleton height="0.85em" />
                    </td>
                  ))}
                </tr>
              ))
            : sorted.map((row) => {
                const k = rowKey(row);
                return (
                  <tr
                    key={k}
                    className={[
                      onRowClick ? "clickable" : "",
                      selectedKey === k ? "selected" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
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
                {emptyMessage}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
