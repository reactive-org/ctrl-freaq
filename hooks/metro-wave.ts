// Metro Wave (#156): the board drawn as one SVG document, a pure function with no `$`. A lane per
// board group (plus Integration), a line per row, placed on a time axis from each line's `history`
// (the coordinator prep's fact). Segment placement is the second pure-function exception to "the
// view derives nothing" (epic #136, "Deliberate departures from #116"): every point and its time
// come from the prep, and this module only lays them out.
import type { Board, Envelope, HistoryPoint } from './board-model.ts'

/** CSS pixels per band cell: the markup is `bodyColumns × CELL_PX` wide, and the surface scales it. */
export const CELL_PX = 8

/** The `Svg` element's `source` limit. */
export const SOURCE_LIMIT = 131072

/** One fixed palette, mid-tones that read on light and dark surfaces alike (epic #136 UI decisions). */
export const PALETTE = {
  plan: '#7c3aed', // defined, unused in the alpha: no sightings yet
  workspace: '#0891b2', // defined, unused in the alpha: no workspace-opened point yet
  build: '#2563eb',
  evaluate: '#d97706',
  wait: '#9ca3af',
  station: '#6b7280',
  merged: '#16a34a',
  rework: '#dc2626',
  now: '#ef4444',
  label: '#6b7280',
} as const

type Kind = 'wait' | 'build' | 'evaluate'

type Line = { number: number | null; label: string; points: HistoryPoint[]; lane: number }

const LANE_HEAD = 18
const ROW = 22
const TOP = 16
const BOTTOM = 16
const GUTTER = 8
const DASH = '4 3'

const esc = (text: string): string =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const n1 = (value: number): string => String(Math.round(value * 10) / 10)

const day = (iso: string): string => iso.slice(0, 10)

const cut = (text: string, chars: number): string =>
  text.length <= chars ? text : `${text.slice(0, Math.max(0, chars - 1))}…`

/**
 * The kind of the segment a point opens: a wait after `eligible` or `plan-posted` (planning is never
 * solid without a sighting, and with no workspace-opened point the wait runs to the first commit),
 * or after a `pr-opened` that no commit precedes; build after a commit, a `rework`, or a `pr-opened`
 * once a commit has been seen; evaluate after `evaluate`. `merged` and `closed` open nothing.
 */
export function segmentKind(point: string, commitSeen: boolean): Kind | null {
  switch (point) {
    case 'eligible':
    case 'plan-posted':
      return 'wait'
    case 'pr-opened':
      return commitSeen ? 'build' : 'wait'
    case 'commit':
    case 'rework':
      return 'build'
    case 'evaluate':
      return 'evaluate'
    default:
      return null
  }
}

function linesOf(board: Board, envelope: Envelope): { lines: Line[]; lanes: string[] } {
  const lines: Line[] = []
  const lanes: string[] = []
  const stories = new Map((envelope.epic?.stories ?? []).map(story => [story.number, story]))
  board.groups.forEach(group => {
    const lane = lanes.length
    lanes.push(group.name)
    for (const row of group.rows) {
      const history = envelope.epic ? stories.get(row.number)?.history : envelope.unit?.history
      lines.push({ number: row.number, label: `#${row.number} ${row.title}`, points: history ?? [], lane })
    }
  })
  const integration = envelope.epic?.integration_history ?? []
  if (integration.length > 0) {
    const lane = lanes.length
    lanes.push('Integration')
    lines.push({ number: null, label: 'Integration PR', points: integration, lane })
  }
  return { lines, lanes }
}

// The lines and the time axis, built once per drawing for both the markup and its `alt`: from the
// earliest point to now, or to the last point when the target is terminal. With no parseable time at
// all the axis is the hour after the epoch, so every coordinate stays a number.
type Layout = { lines: Line[]; lanes: string[]; terminal: boolean; t0: number; t1: number }

function layoutOf(board: Board, envelope: Envelope, nowIso: string): Layout {
  const { lines, lanes } = linesOf(board, envelope)
  const terminal = envelope.stage === 'terminal'
  const times = lines.flatMap(line => line.points.map(p => Date.parse(p.at))).filter(t => !Number.isNaN(t))
  const parsedNow = Date.parse(nowIso)
  const now = Number.isNaN(parsedNow) ? (times.length > 0 ? Math.max(...times) : 0) : parsedNow
  const t0 = times.length > 0 ? Math.min(...times) : now
  let t1 = terminal && times.length > 0 ? Math.max(...times) : now
  if (!(t1 > t0)) t1 = t0 + 3600000
  return { lines, lanes, terminal, t0, t1 }
}

function draw(layout: Layout, envelope: Envelope, nowIso: string, columns: number, ticks: boolean): string {
  const { lines, lanes, terminal, t0, t1 } = layout
  const width = Math.max(1, Math.floor(columns)) * CELL_PX
  const labelWidth = Math.min(Math.round(width * 0.3), 240)
  const x0 = labelWidth + GUTTER
  const x1 = Math.max(x0 + 1, width - GUTTER)

  const x = (iso: string): number => {
    const t = Date.parse(iso)
    return x0 + (((Number.isNaN(t) ? t1 : t) - t0) / (t1 - t0)) * (x1 - x0)
  }

  // Each line's row y, lanes stacked with a header each.
  const ys = new Map<Line, number>()
  const laneY: number[] = []
  let y = TOP
  lanes.forEach((_name, lane) => {
    laneY.push(y)
    y += LANE_HEAD
    for (const line of lines.filter(l => l.lane === lane)) {
      ys.set(line, y + ROW / 2)
      y += ROW
    }
  })
  const height = y + BOTTOM
  const chars = Math.max(4, Math.floor(labelWidth / 6.5))

  const out: string[] = []
  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" font-family="sans-serif" font-size="11">`,
  )
  out.push(`<text x="${x0}" y="11" fill="${PALETTE.label}">${esc(day(new Date(t0).toISOString()))}</text>`)
  out.push(
    `<text x="${x1}" y="11" text-anchor="end" fill="${PALETTE.label}">${esc(day(new Date(t1).toISOString()))}</text>`,
  )
  lanes.forEach((name, lane) => {
    out.push(`<text x="0" y="${laneY[lane] + 13}" font-weight="bold" fill="${PALETTE.label}">${esc(cut(name, chars))}</text>`)
  })

  // Where each story's line ends, for the transfer lines: its merged station, else its last x (the
  // track's start for a line with no points, so a wait on it is still drawn).
  const merges = new Map<number, { x: number; y: number }>()
  const ends = new Map<number, { x: number; y: number }>()
  // The paint order, since a later element covers an earlier one: labels and each line's track
  // (segments, ticks, rework loops), then every transfer line, then the stations, merged ones last so
  // the issue's `closed` station seconds later cannot hide the green, then the now-line on top.
  const stations: string[] = []
  const mergedStations: string[] = []

  for (const line of lines) {
    const ly = ys.get(line)!
    out.push(`<text x="0" y="${n1(ly + 4)}" fill="${PALETTE.label}">${esc(cut(line.label, chars))}</text>`)
    const points = line.points.filter(p => !Number.isNaN(Date.parse(p.at)))
    const open = !points.some(p => p.point === 'merged' || p.point === 'closed')
    // Consecutive segments of one kind draw as one run, so a line's markup grows with its kind
    // changes, not its commits.
    const runs: { kind: Kind; from: number; to: number }[] = []
    let commitSeen = false
    for (let i = 0; i < points.length; i++) {
      const p = points[i]
      if (p.point === 'commit') commitSeen = true
      const kind = segmentKind(p.point, commitSeen)
      const last = i === points.length - 1
      const toIso = last ? (open && !terminal ? nowIso : null) : points[i + 1].at
      if (kind === null || toIso === null) continue
      const from = x(p.at)
      const to = x(toIso)
      if (!(to > from)) continue
      const run = runs[runs.length - 1]
      if (run !== undefined && run.kind === kind && run.to === from) run.to = to
      else runs.push({ kind, from, to })
    }
    for (const { kind, from, to } of runs) {
      const color = PALETTE[kind]
      out.push(
        kind === 'wait'
          ? `<line data-kind="wait" x1="${n1(from)}" y1="${n1(ly)}" x2="${n1(to)}" y2="${n1(ly)}" stroke="${color}" stroke-width="2" stroke-dasharray="${DASH}"/>`
          : `<line data-kind="${kind}" x1="${n1(from)}" y1="${n1(ly)}" x2="${n1(to)}" y2="${n1(ly)}" stroke="${color}" stroke-width="4"/>`,
      )
    }
    for (const p of points) {
      const px = x(p.at)
      if (p.point === 'commit') {
        // A commit's station is its tick on the build segment; ticks go first when the source is full.
        if (ticks) {
          out.push(`<line data-point="commit" x1="${n1(px)}" y1="${n1(ly - 5)}" x2="${n1(px)}" y2="${n1(ly + 5)}" stroke="${PALETTE.station}"/>`)
        }
        continue
      }
      if (p.point === 'rework') {
        out.push(
          `<path data-mark="rework" d="M${n1(px)} ${n1(ly)}a6 6 0 1 0 -12 0" fill="none" stroke="${PALETTE.rework}" stroke-width="1.5"/>`,
        )
      }
      const merged = p.point === 'merged'
      ;(merged ? mergedStations : stations).push(
        `<circle data-point="${esc(p.point)}" cx="${n1(px)}" cy="${n1(ly)}" r="3" fill="${merged ? PALETTE.merged : PALETTE.station}"/>`,
      )
      if (p.point === 'merged' && line.number !== null) merges.set(line.number, { x: px, y: ly })
    }
    if (line.number !== null) {
      const endX = points.length === 0 ? x0 : open && !terminal ? x(nowIso) : x(points[points.length - 1].at)
      ends.set(line.number, { x: endX, y: ly })
    }
  }

  // Transfer lines: a story's `eligible` station to each story its `start` edges wait on.
  for (const story of envelope.epic?.stories ?? []) {
    const line = lines.find(l => l.number === story.number)
    const eligible = line?.points.find(p => p.point === 'eligible')
    if (!line || !eligible) continue
    const from = { x: x(eligible.at), y: ys.get(line)! }
    for (const edge of story.start ?? []) {
      const to = merges.get(edge.waits_on) ?? ends.get(edge.waits_on)
      if (!to) continue
      const solid = edge.merged === true
      out.push(
        `<line data-mark="transfer" x1="${n1(to.x)}" y1="${n1(to.y)}" x2="${n1(from.x)}" y2="${n1(from.y)}" stroke="${PALETTE.station}"${solid ? '' : ` stroke-dasharray="${DASH}"`}/>`,
      )
    }
  }

  out.push(stations.join(''), mergedStations.join(''))

  if (!terminal) {
    const nx = n1(x(nowIso))
    out.push(`<line data-mark="now" x1="${nx}" y1="${TOP}" x2="${nx}" y2="${height - BOTTOM}" stroke="${PALETTE.now}"/>`)
  }
  out.push('</svg>')
  return out.join('')
}

function altOf(layout: Layout, envelope: Envelope): string {
  const { lines, lanes, t0, t1 } = layout
  const span = `${day(new Date(t0).toISOString())} to ${day(new Date(t1).toISOString())}`
  const n = envelope.target.number
  if (envelope.epic) {
    const merged = `${envelope.epic.shipped.length}/${envelope.epic.stories.length} merged`
    return `Metro Wave of #${n}: ${lines.length} lines in ${lanes.length} lanes, ${merged}, ${span}`
  }
  const points = lines.reduce((count, line) => count + line.points.length, 0)
  return `Metro Wave of #${n}: ${envelope.stage}, ${points} history points, ${span}`
}

/**
 * The Metro Wave for a board, `columns` cells wide, at `nowIso`: `{ source, alt }`, or `null` when
 * the drawing cannot fit the `Svg` element's 131,072-character `source` even with the commit ticks
 * dropped, and the band draws its one-line summary instead.
 */
export function metroWave(
  board: Board,
  envelope: Envelope,
  nowIso: string,
  columns: number,
): { source: string; alt: string } | null {
  const layout = layoutOf(board, envelope, nowIso)
  let source = draw(layout, envelope, nowIso, columns, true)
  if (source.length > SOURCE_LIMIT) source = draw(layout, envelope, nowIso, columns, false)
  if (source.length > SOURCE_LIMIT) return null
  return { source, alt: altOf(layout, envelope) }
}
