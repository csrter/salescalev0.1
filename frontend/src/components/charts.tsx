/**
 * charts.tsx (§4.13) — hand-rolled SVG charts: ChartFrame + LineChart +
 * BarChart + Sparkline. Chart surface is always --card (inside a .card).
 *
 * Craft rules baked in: horizontal-only solid hairline gridlines
 * (--chart-grid), ≤5 clean y-ticks, --text-2xs tabular-nums axis text,
 * series colors --chart-1..6 by stable series order (never reassigned on
 * filter; >6 series render --ink-faint — aggregate into "Other" upstream),
 * --chart-prior for previous-period series, 2px round-cap lines with r=4
 * end dots ringed 2px --chart-surface, bars ≤24px with 4px radius on the
 * data end only and 2px surface gaps, area fills at --chart-area-opacity,
 * legend only for ≥2 series (keys mirror the mark), crosshair + ONE
 * all-series tooltip (React text nodes only — never innerHTML), ←/→
 * keyboard crosshair with aria-live announcements, refetch holds the
 * previous render at 0.5 opacity. No dual axes — index to 100 or use small
 * multiples instead. "View as table" is the consumer's job: render the same
 * labels/series through DataTable.
 *
 * Inline styles/attributes carry dynamic geometry only; all colors are
 * var(--token) strings.
 */

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import "./ui.css";

export interface ChartSeries {
  name: string;
  data: (number | null)[];
}

export interface LegendItem {
  name: string;
  color: string;
  kind: "line" | "rect";
}

/** Stable series color by order — never reassigned on filter changes. */
export function seriesColor(i: number): string {
  return i < 6 ? `var(--chart-${i + 1})` : "var(--ink-faint)";
}

const MARGIN = { top: 12, right: 16, bottom: 24 };
const MAX_BAR = 24;
const GAP = 2; // surface gap between touching marks
const CHAR_W = 7; // approx --text-2xs digit width for left-margin sizing

function fmtDefault(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(1)}K`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

/** ≤5 clean rounded y-ticks from 0 to a nice max. */
function niceTicks(max: number, count = 5): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const step0 = max / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const norm = step0 / mag;
  const step =
    (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) *
    mag;
  const ticks: number[] = [];
  for (let v = 0; v <= max - step * 0.001; v += step) ticks.push(v);
  ticks.push(ticks.length ? ticks[ticks.length - 1] + step : step);
  return ticks;
}

function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      setW(entries[0].contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

/** Indices for ≤6 x-axis labels, always including first and last. */
function xTickIndices(n: number): number[] {
  if (n <= 6) return Array.from({ length: n }, (_, i) => i);
  const step = Math.ceil((n - 1) / 5);
  const out: number[] = [];
  for (let i = 0; i < n - 1; i += step) out.push(i);
  out.push(n - 1);
  return out;
}

/**
 * Shared chart chrome: legend (≥2 series only), focusable plot with
 * role="img" summary, keyboard crosshair relay, aria-live announcements,
 * refetch opacity hold. Children receive the measured width.
 */
export function ChartFrame({
  legend,
  refetching = false,
  ariaLabel,
  announcement = "",
  onKeyNav,
  className = "",
  children,
}: {
  legend?: LegendItem[];
  refetching?: boolean;
  ariaLabel: string;
  /** aria-live text for the focused X ("May 12: Meta $412, Google $233"). */
  announcement?: string;
  /** Return true if the key was handled (←/→/Home/End crosshair moves). */
  onKeyNav?: (key: string) => boolean;
  className?: string;
  children: (width: number) => ReactNode;
}) {
  const [ref, width] = useWidth();
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (onKeyNav?.(e.key)) e.preventDefault();
  };
  return (
    <div className={className || undefined}>
      {legend && legend.length >= 2 && (
        <div className="chart-legend">
          {legend.map((l) => (
            <span key={l.name} className="chart-legend-item">
              <svg width={14} height={10} aria-hidden="true">
                {l.kind === "line" ? (
                  <line
                    x1={0}
                    y1={5}
                    x2={14}
                    y2={5}
                    stroke={l.color}
                    strokeWidth={2}
                    strokeLinecap="round"
                  />
                ) : (
                  <rect x={2} y={0} width={10} height={10} rx={2} fill={l.color} />
                )}
              </svg>
              <span>{l.name}</span>
            </span>
          ))}
        </div>
      )}
      <div
        ref={ref}
        className={`chart-frame ${refetching ? "chart-frame--refetching" : ""}`.trim()}
        tabIndex={0}
        role="img"
        aria-label={ariaLabel}
        onKeyDown={onKeyDown}
      >
        {width > 0 && children(width)}
      </div>
      <div className="visually-hidden" aria-live="polite">
        {announcement}
      </div>
    </div>
  );
}

/** One tooltip listing every series at the crosshair X. Text nodes only. */
function ChartTooltip({
  x,
  width,
  label,
  rows,
}: {
  x: number;
  width: number;
  label: string;
  rows: { name: string; color: string; value: string }[];
}) {
  return (
    <div
      className="chart-tooltip"
      style={{
        left: clamp(x, 56, Math.max(56, width - 56)),
        top: 4,
        transform: "translateX(-50%)",
      }}
    >
      <div className="chart-tooltip-label">{label}</div>
      {rows.map((r) => (
        <div key={r.name} className="chart-tooltip-row">
          <span className="chart-tooltip-key" style={{ background: r.color }} />
          <span className="chart-tooltip-value">{r.value}</span>
          <span>{r.name}</span>
        </div>
      ))}
    </div>
  );
}

function useCrosshair(n: number) {
  const [idx, setIdx] = useState<number | null>(null);
  const onKeyNav = (key: string): boolean => {
    if (n === 0) return false;
    if (key === "ArrowRight") {
      setIdx((i) => clamp((i ?? -1) + 1, 0, n - 1));
      return true;
    }
    if (key === "ArrowLeft") {
      setIdx((i) => clamp((i ?? n) - 1, 0, n - 1));
      return true;
    }
    if (key === "Home") {
      setIdx(0);
      return true;
    }
    if (key === "End") {
      setIdx(n - 1);
      return true;
    }
    if (key === "Escape") {
      setIdx(null);
      return true;
    }
    return false;
  };
  return { idx, setIdx, onKeyNav };
}

function announcementFor(
  label: string | undefined,
  series: ChartSeries[],
  idx: number | null,
  fmt: (v: number) => string,
): string {
  if (idx == null || label == null) return "";
  const parts = series
    .map((s) => {
      const v = s.data[idx];
      return v == null ? null : `${s.name} ${fmt(v)}`;
    })
    .filter(Boolean);
  return `${label}: ${parts.join(", ")}`;
}

// --- LineChart ---

export function LineChart({
  labels,
  series,
  prior,
  height = 240,
  area = false,
  formatValue = fmtDefault,
  ariaLabel,
  refetching = false,
}: {
  labels: string[];
  series: ChartSeries[];
  /** Previous-period / pace comparison — rendered in --chart-prior. */
  prior?: ChartSeries;
  height?: number;
  /** Area wash under each line at --chart-area-opacity. */
  area?: boolean;
  formatValue?: (v: number) => string;
  ariaLabel?: string;
  refetching?: boolean;
}) {
  const n = labels.length;
  const { idx, setIdx, onKeyNav } = useCrosshair(n);

  const all = prior ? [...series, prior] : series;
  const maxV = Math.max(
    1e-9,
    ...all.flatMap((s) => s.data.filter((v): v is number => v != null)),
  );
  const ticks = niceTicks(maxV);
  const yMax = ticks[ticks.length - 1];

  const label =
    ariaLabel ??
    `Line chart: ${series.map((s) => s.name).join(", ")} over ${n} points`;

  const legend: LegendItem[] = series.map((s, i) => ({
    name: s.name,
    color: seriesColor(i),
    kind: "line",
  }));
  if (prior) legend.push({ name: prior.name, color: "var(--chart-prior)", kind: "line" });

  return (
    <ChartFrame
      legend={legend}
      refetching={refetching}
      ariaLabel={label}
      announcement={announcementFor(idx != null ? labels[idx] : undefined, all, idx, formatValue)}
      onKeyNav={onKeyNav}
    >
      {(width) => {
        const left = Math.max(...ticks.map((t) => formatValue(t).length)) * CHAR_W + 10;
        const plotW = Math.max(10, width - left - MARGIN.right);
        const plotH = Math.max(10, height - MARGIN.top - MARGIN.bottom);
        const x = (i: number) =>
          left + (n <= 1 ? plotW / 2 : (plotW * i) / (n - 1));
        const y = (v: number) => MARGIN.top + plotH - (v / yMax) * plotH;

        const linePath = (data: (number | null)[]): string => {
          let d = "";
          data.forEach((v, i) => {
            if (v == null) return;
            d += `${d ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
          });
          return d;
        };
        const areaPath = (data: (number | null)[]): string => {
          const pts = data
            .map((v, i) => (v == null ? null : ([x(i), y(v)] as const)))
            .filter((p): p is readonly [number, number] => p != null);
          if (pts.length < 2) return "";
          const base = y(0);
          return (
            `M${pts[0][0].toFixed(1)},${base.toFixed(1)}` +
            pts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("") +
            `L${pts[pts.length - 1][0].toFixed(1)},${base.toFixed(1)}Z`
          );
        };
        const lastIdx = (data: (number | null)[]): number => {
          for (let i = data.length - 1; i >= 0; i--) if (data[i] != null) return i;
          return -1;
        };

        return (
          <>
            <svg
              className="chart-plot"
              width={width}
              height={height}
              role="presentation"
              focusable="false"
            >
              {/* horizontal gridlines only — 1px solid, never dashed */}
              {ticks.map((t) => (
                <g key={t}>
                  <line
                    x1={left}
                    x2={left + plotW}
                    y1={y(t)}
                    y2={y(t)}
                    stroke="var(--chart-grid)"
                    strokeWidth={1}
                  />
                  <text
                    className="chart-axis"
                    x={left - 6}
                    y={y(t) + 3}
                    textAnchor="end"
                  >
                    {formatValue(t)}
                  </text>
                </g>
              ))}
              {xTickIndices(n).map((i) => (
                <text
                  key={i}
                  className="chart-axis"
                  x={x(i)}
                  y={height - 6}
                  textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
                >
                  {labels[i]}
                </text>
              ))}

              {prior && (
                <path
                  d={linePath(prior.data)}
                  fill="none"
                  stroke="var(--chart-prior)"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}
              {series.map((s, si) => {
                const color = seriesColor(si);
                const li = lastIdx(s.data);
                return (
                  <g key={s.name}>
                    {area && (
                      <path
                        d={areaPath(s.data)}
                        fill={color}
                        style={{ fillOpacity: "var(--chart-area-opacity)" }}
                      />
                    )}
                    <path
                      d={linePath(s.data)}
                      fill="none"
                      stroke={color}
                      strokeWidth={2}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    {li >= 0 && s.data[li] != null && (
                      <circle
                        cx={x(li)}
                        cy={y(s.data[li] as number)}
                        r={4}
                        fill={color}
                        stroke="var(--chart-surface)"
                        strokeWidth={2}
                      />
                    )}
                  </g>
                );
              })}

              {/* crosshair snapping to nearest X */}
              {idx != null && (
                <line
                  x1={x(idx)}
                  x2={x(idx)}
                  y1={MARGIN.top}
                  y2={MARGIN.top + plotH}
                  stroke="var(--border-strong)"
                  strokeWidth={1}
                />
              )}

              {/* pointer capture across the whole plot (hit area ≥ marks) */}
              <rect
                x={left}
                y={MARGIN.top}
                width={plotW}
                height={plotH}
                fill="transparent"
                onPointerMove={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  const t = r.width <= 0 ? 0 : (e.clientX - r.left) / r.width;
                  setIdx(clamp(Math.round(t * (n - 1)), 0, Math.max(0, n - 1)));
                }}
                onPointerLeave={() => setIdx(null)}
              />
            </svg>
            {idx != null && (
              <ChartTooltip
                x={x(idx)}
                width={width}
                label={labels[idx]}
                rows={all
                  .map((s, si) => {
                    const v = s.data[idx];
                    if (v == null) return null;
                    return {
                      name: s.name,
                      color:
                        prior && si === all.length - 1
                          ? "var(--chart-prior)"
                          : seriesColor(si),
                      value: formatValue(v),
                    };
                  })
                  .filter((r): r is NonNullable<typeof r> => r != null)}
              />
            )}
          </>
        );
      }}
    </ChartFrame>
  );
}

// --- BarChart ---

export function BarChart({
  labels,
  series,
  stacked = false,
  height = 240,
  formatValue = fmtDefault,
  ariaLabel,
  refetching = false,
}: {
  labels: string[];
  series: ChartSeries[];
  stacked?: boolean;
  height?: number;
  formatValue?: (v: number) => string;
  ariaLabel?: string;
  refetching?: boolean;
}) {
  const n = labels.length;
  const m = series.length;
  const { idx, setIdx, onKeyNav } = useCrosshair(n);

  const totals = labels.map((_, i) =>
    stacked
      ? series.reduce((sum, s) => sum + (s.data[i] ?? 0), 0)
      : Math.max(0, ...series.map((s) => s.data[i] ?? 0)),
  );
  const maxV = Math.max(1e-9, ...totals);
  const ticks = niceTicks(maxV);
  const yMax = ticks[ticks.length - 1];

  const label =
    ariaLabel ??
    `Bar chart: ${series.map((s) => s.name).join(", ")} across ${n} categories`;

  return (
    <ChartFrame
      legend={series.map((s, i) => ({
        name: s.name,
        color: seriesColor(i),
        kind: "rect",
      }))}
      refetching={refetching}
      ariaLabel={label}
      announcement={announcementFor(idx != null ? labels[idx] : undefined, series, idx, formatValue)}
      onKeyNav={onKeyNav}
    >
      {(width) => {
        const left = Math.max(...ticks.map((t) => formatValue(t).length)) * CHAR_W + 10;
        const plotW = Math.max(10, width - left - MARGIN.right);
        const plotH = Math.max(10, height - MARGIN.top - MARGIN.bottom);
        const bandW = plotW / Math.max(1, n);
        const baseY = MARGIN.top + plotH;
        const hOf = (v: number) => (v / yMax) * plotH;

        // Bars ≤24px thick; 2px surface gaps between touching bars.
        const groupCount = stacked ? 1 : m;
        const barW = Math.max(
          2,
          Math.min(MAX_BAR, (bandW * 0.72 - (groupCount - 1) * GAP) / groupCount),
        );
        const groupW = groupCount * barW + (groupCount - 1) * GAP;

        // 4px radius on the data end only, square at the zero baseline.
        const barPath = (bx: number, by: number, h: number): string => {
          const r = Math.min(4, barW / 2, h);
          const x1 = bx + barW;
          return (
            `M${bx.toFixed(1)},${(by + h).toFixed(1)}` +
            `L${bx.toFixed(1)},${(by + r).toFixed(1)}` +
            `Q${bx.toFixed(1)},${by.toFixed(1)} ${(bx + r).toFixed(1)},${by.toFixed(1)}` +
            `L${(x1 - r).toFixed(1)},${by.toFixed(1)}` +
            `Q${x1.toFixed(1)},${by.toFixed(1)} ${x1.toFixed(1)},${(by + r).toFixed(1)}` +
            `L${x1.toFixed(1)},${(by + h).toFixed(1)}Z`
          );
        };

        return (
          <>
            <svg
              className="chart-plot"
              width={width}
              height={height}
              role="presentation"
              focusable="false"
            >
              {ticks.map((t) => (
                <g key={t}>
                  <line
                    x1={left}
                    x2={left + plotW}
                    y1={baseY - hOf(t)}
                    y2={baseY - hOf(t)}
                    stroke="var(--chart-grid)"
                    strokeWidth={1}
                  />
                  <text
                    className="chart-axis"
                    x={left - 6}
                    y={baseY - hOf(t) + 3}
                    textAnchor="end"
                  >
                    {formatValue(t)}
                  </text>
                </g>
              ))}
              {xTickIndices(n).map((i) => (
                <text
                  key={i}
                  className="chart-axis"
                  x={left + bandW * i + bandW / 2}
                  y={height - 6}
                  textAnchor="middle"
                >
                  {labels[i]}
                </text>
              ))}

              {labels.map((_, i) => {
                const cx = left + bandW * i + bandW / 2;
                const hot = idx === i;
                if (stacked) {
                  const bx = cx - barW / 2;
                  let acc = 0;
                  const segs = series
                    .map((s, si) => ({ si, v: s.data[i] ?? 0 }))
                    .filter((s) => s.v > 0);
                  return (
                    <g key={i} className={hot ? "chart-bar chart-bar--hot" : "chart-bar"}>
                      {segs.map((seg, k) => {
                        const h0 = hOf(seg.v);
                        const yTop = baseY - hOf(acc) - h0;
                        acc += seg.v;
                        const isTop = k === segs.length - 1;
                        // 2px surface gap cut from the top of lower segments
                        const gap = isTop ? 0 : GAP;
                        const h = Math.max(0, h0 - gap);
                        if (h <= 0) return null;
                        return isTop ? (
                          <path
                            key={seg.si}
                            d={barPath(bx, yTop, h)}
                            fill={seriesColor(seg.si)}
                          />
                        ) : (
                          <rect
                            key={seg.si}
                            x={bx}
                            y={yTop + gap}
                            width={barW}
                            height={h}
                            fill={seriesColor(seg.si)}
                          />
                        );
                      })}
                    </g>
                  );
                }
                const gx = cx - groupW / 2;
                return (
                  <g key={i} className={hot ? "chart-bar chart-bar--hot" : "chart-bar"}>
                    {series.map((s, si) => {
                      const v = s.data[i];
                      if (v == null || v <= 0) return null;
                      const h = hOf(v);
                      return (
                        <path
                          key={s.name}
                          d={barPath(gx + si * (barW + GAP), baseY - h, h)}
                          fill={seriesColor(si)}
                        />
                      );
                    })}
                  </g>
                );
              })}

              {/* full column bands as hit targets (≥24px) */}
              {labels.map((_, i) => (
                <rect
                  key={i}
                  x={left + bandW * i}
                  y={MARGIN.top}
                  width={bandW}
                  height={plotH}
                  fill="transparent"
                  onPointerEnter={() => setIdx(i)}
                  onPointerLeave={() => setIdx(null)}
                />
              ))}
            </svg>
            {idx != null && (
              <ChartTooltip
                x={left + bandW * idx + bandW / 2}
                width={width}
                label={labels[idx]}
                rows={series
                  .map((s, si) => {
                    const v = s.data[idx];
                    if (v == null) return null;
                    return {
                      name: s.name,
                      color: seriesColor(si),
                      value: formatValue(v),
                    };
                  })
                  .filter((r): r is NonNullable<typeof r> => r != null)}
              />
            )}
          </>
        );
      }}
    </ChartFrame>
  );
}

// --- Sparkline ---

/**
 * 12-point KPI sparkline (§4.3): history in --chart-prior 1.5px, the
 * current-period segment in --chart-1 2px, round caps, no axes. Decorative:
 * aria-hidden — the KPI tile's aria-label carries the meaning.
 */
export function Sparkline({
  points,
  split,
  width = 120,
  height = 40,
}: {
  points: number[];
  /** Index where the current period begins (default: last segment). */
  split?: number;
  width?: number;
  height?: number;
}) {
  const n = points.length;
  if (n < 2) return null;
  const cut = clamp(split ?? n - 2, 0, n - 1);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const pad = 3;
  const px = (i: number) => pad + ((width - pad * 2) * i) / (n - 1);
  const py = (v: number) =>
    height - pad - ((height - pad * 2) * (v - min)) / range;
  const path = (from: number, to: number) =>
    points
      .slice(from, to + 1)
      .map(
        (v, k) =>
          `${k === 0 ? "M" : "L"}${px(from + k).toFixed(1)},${py(v).toFixed(1)}`,
      )
      .join("");
  return (
    <svg width={width} height={height} aria-hidden="true" focusable="false">
      {cut > 0 && (
        <path
          d={path(0, cut)}
          fill="none"
          stroke="var(--chart-prior)"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      <path
        d={path(cut, n - 1)}
        fill="none"
        stroke="var(--chart-1)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
