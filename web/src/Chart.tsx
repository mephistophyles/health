// Charts are inline SVG on purpose: seven people-sized datasets do not justify a
// charting library, and this way light and dark themes come from the same CSS tokens.
// Under three points there is nothing to draw, so the data is listed instead (PRD 9).
import type { ChartSeries } from './api'
import { Empty, shortDay } from './components'

type LineSpec = { key: string; label: string; secondary?: boolean }
type BarSpec = { key: string; label: string; ghost?: boolean }

const WIDTH = 320
const HEIGHT = 140
const PAD = { top: 10, right: 8, bottom: 20, left: 30 }

function numbers(points: any[], keys: string[]): number[] {
  return points
    .flatMap((point) => keys.map((key) => point[key]))
    .map(Number)
    .filter((value) => Number.isFinite(value))
}

function scale(values: number[], goal?: number | null) {
  const all = goal != null && Number.isFinite(goal) ? [...values, goal] : values
  if (!all.length) return { min: 0, max: 1 }
  let min = Math.min(...all)
  let max = Math.max(...all)
  if (min === max) {
    min -= 1
    max += 1
  }
  const margin = (max - min) * 0.1
  return { min: min - margin, max: max + margin }
}

function DataTable({ points, columns }: { points: any[]; columns: { key: string; label: string }[] }) {
  if (!points.length) return <Empty>Nothing recorded yet.</Empty>
  return (
    <table>
      <thead>
        <tr>
          <th>Date</th>
          {columns.map((column) => (
            <th key={column.key}>{column.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {points.map((point, index) => (
          <tr key={index}>
            <td>{point.date ? shortDay(point.date) : '—'}</td>
            {columns.map((column) => (
              <td key={column.key}>{point[column.key] ?? '—'}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function LineChart({
  series,
  lines,
  goal,
  unit = '',
}: {
  series: ChartSeries
  lines: LineSpec[]
  goal?: number | null
  unit?: string
}) {
  const points = series.points ?? []
  if (!series.enough_for_chart) return <DataTable points={points} columns={lines} />

  const values = numbers(points, lines.map((line) => line.key))
  const { min, max } = scale(values, goal)
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const x = (index: number) =>
    PAD.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth)
  const y = (value: number) => PAD.top + plotHeight - ((value - min) / (max - min)) * plotHeight

  return (
    <>
      <svg className="chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
           aria-label={lines.map((line) => line.label).join(', ')}>
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line className="grid" x1={PAD.left} x2={WIDTH - PAD.right}
                  y1={PAD.top + plotHeight * fraction} y2={PAD.top + plotHeight * fraction} />
            <text className="axis" x={PAD.left - 4} y={PAD.top + plotHeight * fraction + 3}
                  textAnchor="end">
              {(max - (max - min) * fraction).toFixed(max - min < 5 ? 1 : 0)}
            </text>
          </g>
        ))}
        {goal != null && Number.isFinite(goal) && (
          <line className="goal" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(goal)} y2={y(goal)} />
        )}
        {lines.map((line) => {
          const path = points
            .map((point, index) => {
              const value = Number(point[line.key])
              return Number.isFinite(value) ? `${index === 0 ? 'M' : 'L'}${x(index)},${y(value)}` : ''
            })
            .filter(Boolean)
            .join(' ')
          return <path key={line.key} className={`line ${line.secondary ? 'secondary' : ''}`} d={path} />
        })}
        {points.map((point, index) => {
          const value = Number(point[lines[0].key])
          return Number.isFinite(value) ? (
            <circle key={index} className="dot" cx={x(index)} cy={y(value)} r={2} />
          ) : null
        })}
        <text className="axis" x={PAD.left} y={HEIGHT - 6}>
          {points[0]?.date ? shortDay(points[0].date) : ''}
        </text>
        <text className="axis" x={WIDTH - PAD.right} y={HEIGHT - 6} textAnchor="end">
          {points.at(-1)?.date ? shortDay(points.at(-1).date) : ''}
        </text>
      </svg>
      <div className="legend">
        {lines.map((line) => (
          <span key={line.key}>
            <span className="swatch"
                  style={{ background: line.secondary ? 'var(--muted)' : 'var(--accent)' }} />
            {line.label}
          </span>
        ))}
        {goal != null && Number.isFinite(goal) && (
          <span>
            <span className="swatch" style={{ background: 'var(--warn)' }} />
            Goal {goal}{unit}
          </span>
        )}
      </div>
    </>
  )
}

export function BarChart({ series, bars }: { series: ChartSeries; bars: BarSpec[] }) {
  const points = series.points ?? []
  if (!series.enough_for_chart) return <DataTable points={points} columns={bars} />

  const values = numbers(points, bars.map((bar) => bar.key))
  const max = Math.max(1, ...values)
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const groupWidth = plotWidth / points.length
  const barWidth = Math.max(3, (groupWidth - 4) / bars.length)

  return (
    <>
      <svg className="chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
           aria-label={bars.map((bar) => bar.label).join(' versus ')}>
        <line className="grid" x1={PAD.left} x2={WIDTH - PAD.right}
              y1={PAD.top + plotHeight} y2={PAD.top + plotHeight} />
        <text className="axis" x={PAD.left - 4} y={PAD.top + 6} textAnchor="end">{max}</text>
        {points.map((point, index) =>
          bars.map((bar, barIndex) => {
            const value = Number(point[bar.key]) || 0
            const height = (value / max) * plotHeight
            return (
              <rect
                key={`${index}-${bar.key}`}
                className={`bar ${bar.ghost ? 'ghost' : ''}`}
                x={PAD.left + index * groupWidth + 2 + barIndex * barWidth}
                y={PAD.top + plotHeight - height}
                width={barWidth - 1}
                height={height}
              />
            )
          }),
        )}
        <text className="axis" x={PAD.left} y={HEIGHT - 6}>
          {points[0]?.date ? shortDay(points[0].date) : ''}
        </text>
        <text className="axis" x={WIDTH - PAD.right} y={HEIGHT - 6} textAnchor="end">
          {points.at(-1)?.date ? shortDay(points.at(-1).date) : ''}
        </text>
      </svg>
      <div className="legend">
        {bars.map((bar) => (
          <span key={bar.key}>
            <span className="swatch"
                  style={{ background: bar.ghost ? 'var(--border)' : 'var(--accent)' }} />
            {bar.label}
          </span>
        ))}
      </div>
    </>
  )
}

/** Points carrying a `slug` are several people on one chart, one line each. */
export function GroupedLines({
  series,
  valueKey,
  label,
}: {
  series: ChartSeries
  valueKey: string
  label: string
}) {
  const points = series.points ?? []
  const groups = Array.from(new Set(points.map((point: any) => point.name ?? point.slug)))
  if (!groups.length) return <Empty>Nothing recorded yet.</Empty>

  const dates = Array.from(new Set(points.map((point: any) => point.date))).sort()
  if (dates.length < 3) {
    return (
      <table>
        <thead>
          <tr>
            <th>Period</th>
            {groups.map((group) => (
              <th key={String(group)}>{String(group)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dates.map((date) => (
            <tr key={String(date)}>
              <td>{shortDay(String(date))}</td>
              {groups.map((group) => {
                const match = points.find(
                  (point: any) => point.date === date && (point.name ?? point.slug) === group,
                )
                return <td key={String(group)}>{match?.[valueKey] ?? '—'}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  const values = points.map((point: any) => Number(point[valueKey])).filter(Number.isFinite)
  const { min, max } = scale(values)
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom
  const x = (index: number) => PAD.left + (index / (dates.length - 1)) * plotWidth
  const y = (value: number) => PAD.top + plotHeight - ((value - min) / (max - min)) * plotHeight
  const shades = ['var(--accent)', 'var(--warn)', 'var(--muted)', 'var(--danger)']

  return (
    <>
      <svg className="chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={label}>
        {[0, 0.5, 1].map((fraction) => (
          <line key={fraction} className="grid" x1={PAD.left} x2={WIDTH - PAD.right}
                y1={PAD.top + plotHeight * fraction} y2={PAD.top + plotHeight * fraction} />
        ))}
        <text className="axis" x={PAD.left - 4} y={PAD.top + 3} textAnchor="end">{max.toFixed(1)}</text>
        <text className="axis" x={PAD.left - 4} y={PAD.top + plotHeight} textAnchor="end">
          {min.toFixed(1)}
        </text>
        {groups.map((group, groupIndex) => {
          const path = dates
            .map((date, index) => {
              const match = points.find(
                (point: any) => point.date === date && (point.name ?? point.slug) === group,
              )
              const value = Number(match?.[valueKey])
              return Number.isFinite(value) ? `${index === 0 ? 'M' : 'L'}${x(index)},${y(value)}` : ''
            })
            .filter(Boolean)
            .join(' ')
          return (
            <path key={String(group)} className="line" d={path}
                  style={{ stroke: shades[groupIndex % shades.length] }} />
          )
        })}
      </svg>
      <div className="legend">
        {groups.map((group, index) => (
          <span key={String(group)}>
            <span className="swatch" style={{ background: shades[index % shades.length] }} />
            {String(group)}
          </span>
        ))}
      </div>
    </>
  )
}
