// Metro Wave (#156): the board drawn as one SVG document, a pure function with no `$`. A lane per
// board group (plus Integration), a line per row, placed on a time axis from each line's `history`
// (the coordinator prep's fact). Each line is labelled with its number only, lanes are separated by a
// gap rather than a heading, and the axis carries no date labels (the `alt` names the span), so the
// band spends its width and height on the drawing [user decision 2026-10-28]. Segment placement is the second pure-function exception to "the
// view derives nothing" (epic #136, "Deliberate departures from #116"): every point and its time
// come from the prep, and this module only lays them out. The one input that is not the prep's is the
// board's own sightings (#157): each stage session's recorded start, merged into its line as a point.
import { sightingsFor } from './board-model.ts'
import type { Board, Envelope, HistoryPoint, Sighting } from './board-model.ts'

/** CSS pixels per band cell: the markup is `bodyColumns × CELL_PX` wide, and the surface scales it. */
export const CELL_PX = 8

/** The `Svg` element's `source` limit. */
export const SOURCE_LIMIT = 131072

/** One fixed palette, mid-tones that read on light and dark surfaces alike (epic #136 UI decisions). */
export const PALETTE = {
  plan: '#7c3aed', // a planning run, solid only from a sighted planner start
  workspace: '#0891b2', // the workspace-opened station
  build: '#2563eb',
  evaluate: '#d97706',
  wait: '#9ca3af',
  station: '#6b7280',
  merged: '#16a34a',
  rework: '#dc2626',
  now: '#ef4444',
  label: '#6b7280',
} as const

type Kind = 'wait' | 'plan' | 'build' | 'evaluate'

// `points` is the prep's history; `sightings` the board's own, as points named `<stage>-start`.
type Line = { number: number | null; label: string; points: HistoryPoint[]; sightings: HistoryPoint[]; lane: number }

/** What the run builder has seen on a line by a point, and what lies ahead of it. */
export type Seen = {
  /** a `commit` at or before this point */
  commit: boolean
  /** a `resolver-start` before this point */
  resolverStart: boolean
  /** a stage start (`resolver-start` for a `rework`, `evaluator-start` for an `evaluate`) after this
   * point and before the line's next `commit`, `evaluate`, `rework`, `merged` or `closed` */
  sightedBeforeNextWork: boolean
}

// The points that end the look-ahead for `sightedBeforeNextWork`: the next work on the line.
const NEXT_WORK = new Set(['commit', 'evaluate', 'rework', 'merged', 'closed'])
// Which sighting answers a point's look-ahead.
const SIGHTED_BY: Record<string, string> = { rework: 'resolver-start', evaluate: 'evaluator-start' }

const LANE_GAP = 8
const ROW = 22
const TOP = 4
const BOTTOM = 4
const GUTTER = 8
// The label font's average glyph width at font-size 11, for sizing the label column to its labels.
const GLYPH_PX = 6.5
const DASH = '4 3'

const esc = (text: string): string =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const n1 = (value: number): string => String(Math.round(value * 10) / 10)

const day = (iso: string): string => iso.slice(0, 10)

const cut = (text: string, chars: number): string =>
  text.length <= chars ? text : `${text.slice(0, Math.max(0, chars - 1))}…`

/**
 * The kind of the segment a point opens (#157): a stage start opens its stage — `planner-start` plan,
 * `resolver-start` build, `evaluator-start` evaluate — and no history point opens plan, so a planning
 * segment is solid only from a sighted planner start. `eligible`, `plan-posted` and
 * `workspace-opened` open a wait; a `commit` opens build; a `pr-opened` opens build once a commit or a
 * `resolver-start` has been seen, else a wait. A `rework` opens build and an `evaluate` evaluate,
 * unless its stage's start is sighted before the line's next work: then it opens a wait, and the
 * sighting opens the stage. `merged` and `closed` open nothing.
 */
export function segmentKind(point: string, seen: Seen): Kind | null {
  switch (point) {
    case 'planner-start':
      return 'plan'
    case 'resolver-start':
      return 'build'
    case 'evaluator-start':
      return 'evaluate'
    case 'eligible':
    case 'plan-posted':
    case 'workspace-opened':
      return 'wait'
    case 'pr-opened':
      return seen.commit || seen.resolverStart ? 'build' : 'wait'
    case 'commit':
      return 'build'
    case 'rework':
      return seen.sightedBeforeNextWork ? 'wait' : 'build'
    case 'evaluate':
      return seen.sightedBeforeNextWork ? 'wait' : 'evaluate'
    default:
      return null
  }
}

const SIGHTING_POINT: Record<Sighting['stage'], string> = {
  planner: 'planner-start',
  resolver: 'resolver-start',
  evaluator: 'evaluator-start',
}

// A line's history with its sightings merged in, by time; a sighting at the same instant as a history
// point follows it.
const merge = (line: Line): HistoryPoint[] =>
  [...line.points, ...line.sightings]
    .filter(p => !Number.isNaN(Date.parse(p.at)))
    .map((p, i) => ({ p, i }))
    .sort((a, b) => Date.parse(a.p.at) - Date.parse(b.p.at) || a.i - b.i)
    .map(({ p }) => p)

/** Each merged point's `Seen`, by one forward scan per point for its look-ahead. */
function seenOf(points: HistoryPoint[]): Seen[] {
  let commit = false
  let resolverStart = false
  return points.map((p, i) => {
    if (p.point === 'commit') commit = true
    const seen = { commit, resolverStart, sightedBeforeNextWork: false }
    const sighting = SIGHTED_BY[p.point]
    if (sighting !== undefined) {
      for (const later of points.slice(i + 1)) {
        if (later.point === sighting) {
          seen.sightedBeforeNextWork = true
          break
        }
        if (NEXT_WORK.has(later.point)) break
      }
    }
    if (p.point === 'resolver-start') resolverStart = true
    return seen
  })
}

function linesOf(board: Board, envelope: Envelope, sightings: Sighting[]): { lines: Line[]; lanes: string[] } {
  const lines: Line[] = []
  const lanes: string[] = []
  const stories = new Map((envelope.epic?.stories ?? []).map(story => [story.number, story]))
  const sighted = sightingsFor(envelope, sightings, board.prs)
  const asPoints = (key: number | 'integration'): HistoryPoint[] =>
    (sighted.get(key) ?? []).map(s => ({ point: SIGHTING_POINT[s.stage], at: s.at, source: 'sighting' }))
  board.groups.forEach(group => {
    const lane = lanes.length
    lanes.push(group.name)
    for (const row of group.rows) {
      const history = envelope.epic ? stories.get(row.number)?.history : envelope.unit?.history
      lines.push({ number: row.number, label: `#${row.number}`, points: history ?? [], sightings: asPoints(row.number), lane })
    }
  })
  const integration = envelope.epic?.integration_history ?? []
  if (integration.length > 0) {
    const lane = lanes.length
    lanes.push('Integration')
    const pr = envelope.epic?.integration?.number
    lines.push({ number: null, label: pr ? `#${pr}` : 'PR', points: integration, sightings: asPoints('integration'), lane })
  }
  return { lines, lanes }
}

// The lines and the time axis, built once per drawing for both the markup and its `alt`: from the
// earliest point to now, or to the last point when the target is terminal. With no parseable time at
// all the axis is the hour after the epoch, so every coordinate stays a number.
type Layout = { lines: Line[]; lanes: string[]; terminal: boolean; t0: number; t1: number }

function layoutOf(board: Board, envelope: Envelope, nowIso: string, sightings: Sighting[]): Layout {
  const { lines, lanes } = linesOf(board, envelope, sightings)
  const terminal = envelope.stage === 'terminal'
  const times = lines
    .flatMap(line => [...line.points, ...line.sightings].map(p => Date.parse(p.at)))
    .filter(t => !Number.isNaN(t))
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
  const longest = Math.max(1, ...lines.map(line => line.label.length))
  const labelWidth = Math.min(Math.ceil(longest * GLYPH_PX), Math.round(width * 0.3))
  const x0 = labelWidth + GUTTER
  const x1 = Math.max(x0 + 1, width - GUTTER)

  const x = (iso: string): number => {
    const t = Date.parse(iso)
    return x0 + (((Number.isNaN(t) ? t1 : t) - t0) / (t1 - t0)) * (x1 - x0)
  }

  // Each line's row y, lanes stacked with a gap between them.
  const ys = new Map<Line, number>()
  let y = TOP
  lanes.forEach((_name, lane) => {
    if (lane > 0) y += LANE_GAP
    for (const line of lines.filter(l => l.lane === lane)) {
      ys.set(line, y + ROW / 2)
      y += ROW
    }
  })
  const height = y + BOTTOM
  const chars = Math.max(4, Math.floor(labelWidth / GLYPH_PX))

  const out: string[] = []
  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" font-family="sans-serif" font-size="11">`,
  )

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
    out.push(
      `<text data-lane="${esc(lanes[line.lane])}" x="0" y="${n1(ly + 4)}" fill="${PALETTE.label}">${esc(cut(line.label, chars))}</text>`,
    )
    const points = merge(line)
    const seen = seenOf(points)
    const open = !points.some(p => p.point === 'merged' || p.point === 'closed')
    // Consecutive segments of one kind draw as one run, so a line's markup grows with its kind
    // changes, not its commits.
    const runs: { kind: Kind; from: number; to: number }[] = []
    for (let i = 0; i < points.length; i++) {
      const p = points[i]
      const kind = segmentKind(p.point, seen[i])
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
      const fill = merged ? PALETTE.merged : p.point === 'workspace-opened' ? PALETTE.workspace : PALETTE.station
      ;(merged ? mergedStations : stations).push(
        `<circle data-point="${esc(p.point)}" cx="${n1(px)}" cy="${n1(ly)}" r="3" fill="${fill}"/>`,
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
 * The Metro Wave for a board, `columns` cells wide, at `nowIso`, with the board's `sightings` merged
 * into their lines: `{ source, alt }`, or `null` when the drawing cannot fit the `Svg` element's
 * 131,072-character `source` even with the commit ticks dropped, and the band draws its one-line
 * summary instead.
 */
export function metroWave(
  board: Board,
  envelope: Envelope,
  nowIso: string,
  columns: number,
  sightings: Sighting[] = [],
): { source: string; alt: string } | null {
  const layout = layoutOf(board, envelope, nowIso, sightings)
  let source = draw(layout, envelope, nowIso, columns, true)
  if (source.length > SOURCE_LIMIT) source = draw(layout, envelope, nowIso, columns, false)
  if (source.length > SOURCE_LIMIT) return null
  return { source, alt: altOf(layout, envelope) }
}
