// Metro Wave (#156): the board drawn as one SVG document, a pure function with no `$`. A lane per
// board group (plus Integration), a line per row, placed on a time axis from each line's `history`
// (the coordinator prep's fact). Each line is labelled with its number only, in a label cell beside the
// drawing that the band keys for its hover card (#157: nothing inside an `Svg` can be keyed); lanes are
// separated by a gap rather than a heading, and the axis carries no date labels but the now-line's own
// (the `alt` names the span in the frame's local dates), so the band spends its width and height on the drawing [user decision
// 2026-10-28]. The
// drawing animates with SMIL inside an interactive `Svg` (#157): frozen at its prep run's frame, so its
// markup changes only with the next prep run or the band's width. Segment placement is the second pure-function exception to "the
// view derives nothing" (epic #136, "Deliberate departures from #116"): every point and its time
// come from the prep, and this module only lays them out. The inputs that are not the prep's are the
// board's own: its sightings (#157), each stage session's recorded start merged into its line as a
// point, and the PR numbers its log remembers (`board.prs`), which match an evaluator sighting to a
// closed story.
import { duration, sightingsFor } from './board-model.ts'
import type { Board, Envelope, HistoryPoint, Row, Sighting } from './board-model.ts'

/** CSS pixels per band cell: the drawing is `(bodyColumns − labelCells) × CELL_PX` wide, drawn unscaled. */
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
} as const

type Kind = 'wait' | 'plan' | 'build' | 'evaluate'

// `points` is the prep's history; `sightings` the board's own, as points named `<stage>-start`.
// `drawsCommits` is false for the integration line alone, whose history carries none (the stories'
// own, `scripts/board_history.py` `integration_points`).
type Line = {
  number: number | null
  label: string
  points: HistoryPoint[]
  sightings: HistoryPoint[]
  lane: number
  drawsCommits: boolean
}

/** What the run builder has seen on a line by a point, and what lies ahead of it. */
export type Seen = {
  /** a `commit` at or before this point */
  commit: boolean
  /** a `resolver-start` before this point, on a line that draws commits (never the integration line's) */
  resolverStart: boolean
  /** a stage start (`resolver-start` for a `rework`, `evaluator-start` for an `evaluate`) after this
   * point and before the line's next `commit`, `evaluate`, `rework`, `merged` or `closed` */
  sightedBeforeNextWork: boolean
  /** for a `planner-start`: the line's next point after it, later planner starts and `eligible` skipped,
   * is a `plan-posted`, or none follows (planning in flight) — the only ends a planning run can name */
  plansToPost: boolean
}

// The points that end the look-ahead for `sightedBeforeNextWork`: the next work on the line.
const NEXT_WORK = new Set(['commit', 'evaluate', 'rework', 'merged', 'closed'])
// Which sighting answers a point's look-ahead.
const SIGHTED_BY: Record<string, string> = { rework: 'resolver-start', evaluate: 'evaluator-start' }

// One drawing row in CSS pixels, the band's measured row pitch, so each row lines up with its label
// cell beside the drawing; a lane gap is one row, and the drawing has no margin above or below.
const ROW = 20
const LANE_GAP = ROW
const TOP = 0
const GUTTER = 8
// The label column's most of the band's width.
const LABEL_SHARE = 0.3
// A detail card's most cells, its border included, and the two columns (or rows) its round border takes.
const CARD_CELLS = 40
export const CARD_BORDER = 2

/** A line's hover key: its band label cell's `key` and the scope its overview row shares. */
export const lineKey = (number: number | null): string => (number === null ? 'line-integration' : `line-${number}`)
const DASH = '4 3'

const esc = (text: string): string =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const n1 = (value: number): string => String(Math.round(value * 10) / 10)


const cut = (text: string, chars: number): string =>
  text.length <= chars ? text : `${text.slice(0, Math.max(0, chars - 1))}…`

/**
 * The kind of the segment a point opens (#157): a stage start opens its stage — `planner-start` plan,
 * `resolver-start` build, `evaluator-start` evaluate — and no history point opens plan, so a planning
 * segment is solid only from a sighted planner start. `eligible`, `plan-posted` and
 * `workspace-opened` open a wait; a `commit` opens build; a `pr-opened` opens build once a commit or a
 * `resolver-start` has been seen on a line that draws commits, else a wait (the integration line's
 * `pr-opened` stays a wait after the epic's own resolver start: operator decision 2026-10-28). A `rework` opens build and an `evaluate` evaluate,
 * unless its stage's start is sighted before the line's next work: then it opens a wait, and the
 * sighting opens the stage. `merged` and `closed` open nothing. A `planner-start` opens plan only when
 * its run ends at the line's `plan-posted` or the line ends there (`plansToPost`); otherwise — the epic's
 * planner on the integration line, whose history carries no `plan-posted`, or a re-plan after the first
 * plan comment — no point says when planning ended, so it opens a wait rather than a plan run that spans
 * whatever follows.
 */
export function segmentKind(point: string, seen: Seen): Kind | null {
  switch (point) {
    case 'planner-start':
      return seen.plansToPost ? 'plan' : 'wait'
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
function seenOf(points: HistoryPoint[], drawsCommits: boolean): Seen[] {
  let commit = false
  let resolverStart = false
  return points.map((p, i) => {
    if (p.point === 'commit') commit = true
    const seen = { commit, resolverStart, sightedBeforeNextWork: false, plansToPost: false }
    if (p.point === 'planner-start') {
      const next = points.slice(i + 1).find(later => later.point !== 'planner-start' && later.point !== 'eligible')
      seen.plansToPost = next === undefined || next.point === 'plan-posted'
    }
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
    if (drawsCommits && p.point === 'resolver-start') resolverStart = true
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
      lines.push({
        number: row.number,
        label: `#${row.number}`,
        points: history ?? [],
        sightings: asPoints(row.number),
        lane,
        drawsCommits: true,
      })
    }
  })
  const integration = envelope.epic?.integration_history ?? []
  if (integration.length > 0) {
    const lane = lanes.length
    lanes.push('Integration')
    const pr = envelope.epic?.integration?.number
    lines.push({
      number: null,
      label: pr ? `#${pr}` : 'PR',
      points: integration,
      sightings: asPoints('integration'),
      lane,
      drawsCommits: false,
    })
  }
  return { lines, lanes }
}

/**
 * What one drawing is frozen at (#157): the board's feed read, so the band's `source` is byte-identical
 * until the next prep run. `drawnAt` is the read's time and `previousDrawnAt` the read before it for the
 * same board (null on the first since load); `utcOffsetMinutes` sets the local dates; `sightings` is the
 * `$.store` snapshot taken at the read; `elapsedSeconds` is how far into the frame's motion a rebuild at
 * a new band width starts (0 on the first build after a read).
 */
export type WaveFrame = {
  drawnAt: string
  previousDrawnAt: string | null
  utcOffsetMinutes: number
  sightings: Sighting[]
  elapsedSeconds: number
}

/**
 * One drawing row, top to bottom: a line's track, the gap between lanes, or the now-line's date row.
 * A line row carries its label cell beside the drawing: the hover `key` it shares with the line's
 * overview row (`line-<N>`, `line-integration`), its number label, and its detail card's lines.
 */
export type WaveRow =
  | { kind: 'line'; key: string; label: string; number: number | null; card: string[] }
  | { kind: 'gap' }
  | { kind: 'date' }

/**
 * The band's drawing: the `Svg`'s `source` and `alt`, its size in CSS pixels, its rows (each `ROW` px,
 * one band row tall), the cells the label column beside it takes, and the detail card's width in cells,
 * its border included.
 */
export type Wave = {
  source: string
  alt: string
  width: number
  height: number
  rows: WaveRow[]
  labelCells: number
  cardCells: number
}

const HOUR = 3600000
// A shared idle stretch is compressed when it is longer than both of these.
const COMPRESS_MIN_MS = 2 * HOUR
const COMPRESS_MIN_SHARE = 0.15
// A compressed stretch's drawn width, and the most of the axis compression may take.
const COMPRESSED_PX = 24
// The real-time headroom after the drawn time that the now-line advances into.
const HEADROOM_MIN_MS = HOUR
const HEADROOM_SHARE = 0.2
// How long new work since the previous prep run takes to grow in, and a train marker's pulse.
const GROW_S = 1
const PULSE_S = 1.6
// The now-line's date at font-size 11, `YYYY-MM-DD` plus a margin: the least x its right edge may sit at.
const DATE_PX = 64
// The transfer lines past the next wave, shown only while the pointer is over the drawing.
const TRANSFER_STYLE = '<style>.transfer-more{visibility:hidden} svg:hover .transfer-more{visibility:visible}</style>'

// The name a station's tooltip gives its point: a sighting reads as the stage starting.
const STATION_NAME: Record<string, string> = {
  'planner-start': 'planner started',
  'resolver-start': 'resolver started',
  'evaluator-start': 'evaluator started',
}

type Run = { kind: Kind; from: number; to: number }

// A line with its merged points (each with a parseable time) and its runs, in milliseconds.
type Track = Line & { merged: HistoryPoint[]; runs: Run[]; open: boolean }

// The lines and the time axis, built once per drawing for both the markup and its `alt`. `drawn` is the
// frame's time; `end` is the last time the drawing shows: the drawn time, or the last point when the
// target is terminal. The axis itself runs on past `end` into the headroom, which `axisOf` sizes once it
// knows what compression left. With no parseable time at all a terminal axis is the hour after the epoch,
// so every coordinate stays a number.
type Layout = { tracks: Track[]; lanes: string[]; terminal: boolean; t0: number; drawn: number; end: number }

function layoutOf(board: Board, envelope: Envelope, frame: WaveFrame): Layout {
  const { lines, lanes } = linesOf(board, envelope, frame.sightings)
  const terminal = envelope.stage === 'terminal'
  const times = lines
    .flatMap(line => [...line.points, ...line.sightings].map(p => Date.parse(p.at)))
    .filter(t => !Number.isNaN(t))
  const parsed = Date.parse(frame.drawnAt)
  const drawn = Number.isNaN(parsed) ? (times.length > 0 ? Math.max(...times) : 0) : parsed
  const t0 = times.length > 0 ? Math.min(...times) : drawn
  let end = drawn
  if (terminal) {
    end = times.length > 0 ? Math.max(...times) : drawn
    if (!(end > t0)) end = t0 + HOUR
  }
  const tracks = lines.map(line => {
    const merged = merge(line)
    const seen = seenOf(merged, line.drawsCommits)
    const open = !merged.some(p => p.point === 'merged' || p.point === 'closed')
    // Consecutive segments of one kind are one run, so a line's markup grows with its kind changes,
    // not its commits. An open line's last run reaches the drawn time.
    const runs: Run[] = []
    for (let i = 0; i < merged.length; i++) {
      const kind = segmentKind(merged[i].point, seen[i])
      const from = Date.parse(merged[i].at)
      const to = i === merged.length - 1 ? (open && !terminal ? drawn : null) : Date.parse(merged[i + 1].at)
      if (kind === null || to === null || !(to > from)) continue
      const run = runs[runs.length - 1]
      if (run !== undefined && run.kind === kind && run.to === from) run.to = to
      else runs.push({ kind, from, to })
    }
    return { ...line, merged, runs, open }
  })
  return { tracks, lanes, terminal, t0, drawn, end }
}

// `t1` is the axis's end: `end` plus the headroom the now-line advances into (none when terminal).
type Axis = { x: (t: number) => number; compressed: { from: number; to: number }[]; t1: number }

// Whether a line's wait runs through a compressed stretch: which mark that stretch gets, a line's break
// on each such line or, with none, the shared axis break.
const waitsThrough = (track: Track, c: { from: number; to: number }): boolean =>
  track.runs.some(run => run.kind === 'wait' && run.from <= c.from && run.to >= c.to)

/**
 * The one time-to-x mapping every coordinate goes through: piecewise linear, with each long idle stretch
 * shared by every line drawn `COMPRESSED_PX` wide [user decision 2026-10-28]. The breakpoints are the
 * station times up to the drawn time (the drawn time itself is not one, so the stretch from the last
 * station to it is never compressed); a stretch is compressed when no line has a solid run over any part
 * of it and it is longer than both `COMPRESS_MIN_MS` and `COMPRESS_MIN_SHARE` of the span up to the drawn
 * time. The longest go first, at most enough to take half the width, and every other stretch, the headroom
 * included, shares one scale, so equal durations of work are equally wide. The headroom is sized after
 * compression, as a share of the linear time left up to the drawn time: sized on the whole span, it took
 * most of the width whenever compression bit.
 */
function axisOf(layout: Layout, x0: number, x1: number): Axis {
  const { tracks, terminal, t0, drawn, end } = layout
  const stops = [...new Set(tracks.flatMap(track => track.merged.map(p => Date.parse(p.at))))]
    .filter(t => t >= t0 && t <= end)
    .sort((a, b) => a - b)
  const solid = tracks.flatMap(track => track.runs.filter(run => run.kind !== 'wait'))
  const candidates: { from: number; to: number }[] = []
  for (let i = 0; i + 1 < stops.length; i++) {
    const [from, to] = [stops[i], stops[i + 1]]
    const length = to - from
    if (length <= COMPRESS_MIN_MS || length <= COMPRESS_MIN_SHARE * (end - t0)) continue
    if (solid.some(run => run.from < to && run.to > from)) continue
    candidates.push({ from, to })
  }
  const cap = Math.floor((x1 - x0) / 2 / COMPRESSED_PX)
  let linear = end - t0
  const compressed: { from: number; to: number }[] = []
  for (const c of candidates.sort((a, b) => b.to - b.from - (a.to - a.from) || a.from - b.from)) {
    if (compressed.length >= cap) break
    // Some linear time always remains, so the scale stays finite.
    if (!(linear - (c.to - c.from) > 0)) continue
    compressed.push(c)
    linear -= c.to - c.from
  }
  let t1 = terminal ? end : drawn + Math.max(HEADROOM_MIN_MS, HEADROOM_SHARE * linear)
  if (!(t1 > t0)) t1 = t0 + HOUR
  linear += t1 - end
  compressed.sort((a, b) => a.from - b.from)
  const scale = (x1 - x0 - compressed.length * COMPRESSED_PX) / linear
  const x = (t: number): number => {
    let pos = x0
    let cursor = t0
    for (const c of compressed) {
      if (t <= c.from) break
      pos += (c.from - cursor) * scale
      if (t <= c.to) return pos + ((t - c.from) / (c.to - c.from)) * COMPRESSED_PX
      pos += COMPRESSED_PX
      cursor = c.to
    }
    return pos + (t - cursor) * scale
  }
  return { x, compressed, t1 }
}

// `YYYY-MM-DD HH:MM` of an instant at the frame's UTC offset.
const local = (t: number, offsetMinutes: number): string => {
  const iso = new Date(t + offsetMinutes * 60000).toISOString()
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`
}

// `YYYY-MM-DD` of an instant at the frame's UTC offset: every date the drawing and its `alt` show.
const localDay = (t: number, offsetMinutes: number): string => local(t, offsetMinutes).slice(0, 10)

// The stories whose transfer lines are drawn by default: the next wave, the first board group holding a
// story that is not closed (under the serial fallback, that group's first unclosed story alone).
function nextWave(board: Board, envelope: Envelope): Set<number> {
  const epic = envelope.epic
  if (!epic) return new Set()
  const closed = new Set(epic.stories.filter(story => story.closed).map(story => story.number))
  const serial = epic.concurrency?.source !== 'declared'
  for (const group of board.groups) {
    const open = group.rows.map(row => row.number).filter(n => !closed.has(n))
    if (open.length > 0) return new Set(serial ? open.slice(0, 1) : open)
  }
  return new Set()
}

// The time a line spent on each kind, as its card's third line names them.
const KIND_NAME: Record<Kind, string> = { plan: 'plan', build: 'build', evaluate: 'evaluate', wait: 'waited' }

/**
 * A line's detail card, the lines its line's hover scope reveals (the band draws it after the `Svg`), each cut to the card so it stays three
 * rows: the story and its title, where it stands (its current rung and its gate, reason or next step; the integration PR for the
 * integration line), and its time on each kind summed from its runs, a kind with none left out.
 */
function cardOf(track: Track, board: Board, envelope: Envelope, cardCells: number): string[] {
  const card: string[] = []
  const inner = Math.max(1, cardCells - CARD_BORDER)
  if (track.number === null) {
    // The envelope's integration PR, not the board's row, which the pane shows only near the end. The
    // prep reads that PR only once every story has closed, but the line's history finds a draft opened
    // earlier, so a `pr-opened` point with no number yet still says the PR is open.
    const pr = envelope.epic?.integration ?? null
    const opened = track.points.some(p => p.point === 'pr-opened')
    const stands = pr?.number
      ? `integration · PR #${pr.number}${pr.draft ? ' (draft)' : ''}`
      : opened
        ? 'integration · PR open'
        : 'integration · no integration PR yet'
    card.push(cut(pr?.number ? `#${pr.number} integration PR` : 'Integration', inner))
    card.push(cut(stands, inner))
  } else {
    const row = board.groups.flatMap(group => group.rows).find((r: Row) => r.number === track.number)
    card.push(cut(row ? `#${row.number} ${row.title}` : track.label, inner))
    if (row) {
      const rung = row.collapsed ? 'closed' : row.rungs.find(r => r.state === 'current')?.label ?? null
      const note = row.gateText ?? row.reason ?? row.nextText
      const stands = [rung, note].filter((part): part is string => part !== null)
      if (stands.length > 0) card.push(cut(stands.join(' · '), inner))
    }
  }
  const spent = new Map<Kind, number>()
  for (const run of track.runs) spent.set(run.kind, (spent.get(run.kind) ?? 0) + (run.to - run.from))
  const times = (['plan', 'build', 'evaluate', 'wait'] as const)
    .filter(kind => (spent.get(kind) ?? 0) > 0)
    .map(kind => `${KIND_NAME[kind]} ${duration(spent.get(kind)!)}`)
  if (times.length > 0) card.push(cut(times.join(' · '), inner))
  return card
}

// Commit ticks: titled, untitled when the source is too full for their tooltips, or dropped.
type Ticks = 'titled' | 'bare' | 'none'

function draw(layout: Layout, board: Board, envelope: Envelope, frame: WaveFrame, columns: number, ticks: Ticks): Wave {
  const { tracks, lanes, terminal, drawn } = layout
  // The label column takes the longest label plus one cell, at most `LABEL_SHARE` of the band, and the
  // drawing the rest.
  const cells = Math.max(1, Math.floor(columns))
  const longest = Math.max(1, ...tracks.map(track => track.label.length))
  const labelCells = Math.max(1, Math.min(longest + 1, Math.floor(cells * LABEL_SHARE)))
  const width = Math.max(1, cells - labelCells) * CELL_PX
  // A card sits beside the label column, so it takes at most the cells right of it (a border and one
  // cell at the least), and its lines are cut to that width rather than clipped by the band's edge.
  const cardCells = Math.max(CARD_BORDER + 1, Math.min(CARD_CELLS, cells - labelCells))
  const x0 = GUTTER
  const x1 = Math.max(x0 + 1, width - GUTTER)
  const { x, compressed, t1 } = axisOf(layout, x0, x1)
  const xOf = (iso: string): number => {
    const t = Date.parse(iso)
    return x(Number.isNaN(t) ? drawn : t)
  }

  // Each line's row y, lanes stacked with a gap between them, then the now-line's date row.
  const rows: WaveRow[] = []
  const ys = new Map<Track, number>()
  let y = TOP
  lanes.forEach((_name, lane) => {
    if (lane > 0) {
      y += LANE_GAP
      rows.push({ kind: 'gap' })
    }
    for (const track of tracks.filter(t => t.lane === lane)) {
      ys.set(track, y + ROW / 2)
      // The label is cut to its cell, less the one-cell gap before the drawing, when the cap bites.
      const label = cut(track.label, Math.max(1, labelCells - 1))
      rows.push({ kind: 'line', key: lineKey(track.number), label, number: track.number, card: cardOf(track, board, envelope, cardCells) })
      y += ROW
    }
  })
  const tracksBottom = y
  // The date row closes every drawing, so the rows always tile the band; a terminal target leaves it
  // empty, with no now-line to carry a date.
  y += ROW
  rows.push({ kind: 'date' })
  const height = y

  // The motion's shared timing: every animation starts `elapsedSeconds` into the frame: 0 on the first
  // build after a read, and on a rebuild at a new width as far as the clock says the frame has run since
  // its read (ahead of the motion shown when that first build came late or the band replayed). New work
  // since the previous read grows in.
  const begin = frame.elapsedSeconds > 0 ? `-${Math.round(frame.elapsedSeconds)}s` : '0s'
  const headroomS = Math.max(1, Math.round((t1 - drawn) / 1000))
  const parsedPrevious = frame.previousDrawnAt === null ? NaN : Date.parse(frame.previousDrawnAt)
  const previous = !terminal && !Number.isNaN(parsedPrevious) && parsedPrevious < drawn ? parsedPrevious : null
  const xDrawn = x(drawn)
  const xEnd = x(t1)
  const grow = (attribute: string, from: number, to: number, freeze = true): string =>
    `<animate attributeName="${attribute}" from="${n1(from)}" to="${n1(to)}" dur="${GROW_S}s" begin="${begin}"${freeze ? ' fill="freeze"' : ''}/>`
  // A head's motion: it advances with the now-line, and when it is new work it first grows in from
  // `start`. The grow comes later in the document, so at the same `begin` it overrides the advance for its
  // one second, then ends unfrozen and the advance's own value shows, in step with the now-line.
  const advance = (attribute: string, start: number | null): string =>
    `<animate attributeName="${attribute}" from="${n1(xDrawn)}" to="${n1(xEnd)}" dur="${headroomS}s" begin="${begin}" fill="freeze"/>` +
    (start !== null && xDrawn > start ? grow(attribute, start, xDrawn, false) : '')
  // Whatever hangs on a station shows when that station does: a station, tick, rework loop or transfer
  // line at a time after the previous read fades in over its one second (an in-flight head grows in
  // instead, through `advance`).
  const fadeIn = (t: number | null): string =>
    previous !== null && t !== null && t > previous
      ? `<animate attributeName="opacity" from="0" to="1" dur="${GROW_S}s" begin="${begin}" fill="freeze"/>`
      : ''

  const out: string[] = []
  out.push(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" font-family="sans-serif" font-size="11">`,
    TRANSFER_STYLE,
  )

  // Where each story's line ends, for the transfer lines: its merged station, else its last x (the
  // track's start for a line with no points, so a wait on it is still drawn).
  // An in-flight line's end is its moving head, so a transfer line into it advances with it (`head`
  // carries the head's grow-in start, as `advance` takes it). `at` is the station time at the end, for
  // the transfer line's fade; null where no station sits there (a head, or a line with no points).
  type End = { x: number; y: number; at: number | null; head?: { start: number | null } }
  const merges = new Map<number, End>()
  const ends = new Map<number, End>()
  // The paint order, since a later element covers an earlier one: labels and each line's track
  // (segments, break marks, ticks, rework loops), then the shared axis breaks, then every transfer line,
  // then the stations, merged
  // ones last so the issue's `closed` station seconds later cannot hide the green, then the in-flight
  // lines' train markers, then the now-line on top.
  const stations: string[] = []
  const mergedStations: string[] = []
  const trains: string[] = []

  for (const track of tracks) {
    const ly = ys.get(track)!
    const inFlight = track.open && !terminal
    let headStart: { start: number | null } | undefined
    track.runs.forEach((run, i) => {
      const from = x(run.from)
      const to = x(run.to)
      const head = inFlight && i === track.runs.length - 1 && run.to === drawn
      const fresh = previous !== null && run.to > previous ? x(Math.max(run.from, previous)) : null
      const motion = head ? advance('x2', fresh) : fresh !== null && to > fresh ? grow('x2', fresh, to) : ''
      const stroke =
        run.kind === 'wait'
          ? `stroke="${PALETTE.wait}" stroke-width="2" stroke-dasharray="${DASH}"`
          : `stroke="${PALETTE[run.kind]}" stroke-width="4"`
      const open = `<line data-kind="${run.kind}" x1="${n1(from)}" y1="${n1(ly)}" x2="${n1(to)}" y2="${n1(ly)}" ${stroke}`
      out.push(motion === '' ? `${open}/>` : `${open}>${motion}</line>`)
      if (head) {
        headStart = { start: fresh }
        trains.push(
          `<circle data-mark="train" cx="${n1(xDrawn)}" cy="${n1(ly)}" r="3" fill="${PALETTE[run.kind]}">` +
            advance('cx', fresh) +
            `<animate attributeName="r" values="3;5;3" dur="${PULSE_S}s" begin="${begin}" repeatCount="indefinite"/></circle>`,
        )
      }
    })
    // A break mark on every line whose wait runs through a compressed stretch.
    for (const c of compressed) {
      if (!waitsThrough(track, c)) continue
      const cx = x((c.from + c.to) / 2)
      out.push(
        `<path data-mark="break" d="M${n1(cx - 3)} ${n1(ly + 3)}l3 -6M${n1(cx)} ${n1(ly + 3)}l3 -6" fill="none" stroke="${PALETTE.wait}" stroke-width="1.5"/>`,
      )
    }
    for (const p of track.merged) {
      const t = Date.parse(p.at)
      const px = x(t)
      const title = `<title>${esc(STATION_NAME[p.point] ?? p.point)} · ${local(t, frame.utcOffsetMinutes)}</title>`
      const fade = fadeIn(t)
      if (p.point === 'commit') {
        // A commit's station is its tick on the build segment; its tooltip goes first when the source is
        // full, then the tick.
        if (ticks !== 'none') {
          const tick = `<line data-point="commit" x1="${n1(px)}" y1="${n1(ly - 5)}" x2="${n1(px)}" y2="${n1(ly + 5)}" stroke="${PALETTE.station}"`
          const inner = (ticks === 'titled' ? title : '') + fade
          out.push(inner === '' ? `${tick}/>` : `${tick}>${inner}</line>`)
        }
        continue
      }
      if (p.point === 'rework') {
        const loop = `<path data-mark="rework" d="M${n1(px)} ${n1(ly)}a6 6 0 1 0 -12 0" fill="none" stroke="${PALETTE.rework}" stroke-width="1.5"`
        out.push(fade === '' ? `${loop}/>` : `${loop}>${fade}</path>`)
      }
      const merged = p.point === 'merged'
      const fill = merged ? PALETTE.merged : p.point === 'workspace-opened' ? PALETTE.workspace : PALETTE.station
      ;(merged ? mergedStations : stations).push(
        `<circle data-point="${esc(p.point)}" cx="${n1(px)}" cy="${n1(ly)}" r="3" fill="${fill}">${title}${fade}</circle>`,
      )
      if (merged && track.number !== null) merges.set(track.number, { x: px, y: ly, at: t })
    }
    if (track.number !== null) {
      const last = track.merged[track.merged.length - 1]
      const atDrawn = last !== undefined && track.open && !terminal
      const endX = last === undefined ? x0 : atDrawn ? xDrawn : xOf(last.at)
      ends.set(track.number, { x: endX, y: ly, at: last === undefined || atDrawn ? null : Date.parse(last.at), head: headStart })
    }
  }

  // A compressed stretch no line's wait runs through (no line exists over it) gets one shared axis break
  // across every line row instead, so no compressed stretch hides its cut time [user decision 2026-10-28].
  // Its two strokes lean 4 px over the rows' height, 4 px apart, centred on the stretch.
  for (const c of compressed) {
    if (tracks.some(track => waitsThrough(track, c))) continue
    const cx = x((c.from + c.to) / 2)
    const rise = `l4 -${n1(tracksBottom - TOP)}`
    out.push(
      `<path data-mark="axis-break" d="M${n1(cx - 4)} ${n1(tracksBottom)}${rise}M${n1(cx)} ${n1(tracksBottom)}${rise}" fill="none" stroke="${PALETTE.station}" stroke-width="1.5"/>`,
    )
  }

  // Transfer lines: a story's `eligible` station to each story its `start` edges wait on. Only the next
  // wave's are drawn by default; the rest show while the pointer is over the drawing.
  const shown = nextWave(board, envelope)
  for (const story of envelope.epic?.stories ?? []) {
    const track = tracks.find(t => t.number === story.number)
    const eligible = track?.points.find(p => p.point === 'eligible')
    if (!track || !eligible) continue
    const from = { x: xOf(eligible.at), y: ys.get(track)! }
    for (const edge of story.start ?? []) {
      const merged = merges.get(edge.waits_on)
      const to = merged ?? ends.get(edge.waits_on)
      if (!to) continue
      const solid = edge.merged === true
      const open = `<line data-mark="transfer"${shown.has(story.number) ? '' : ' class="transfer-more"'} x1="${n1(to.x)}" y1="${n1(to.y)}" x2="${n1(from.x)}" y2="${n1(from.y)}" stroke="${PALETTE.station}"${solid ? '' : ` stroke-dasharray="${DASH}"`}`
      // It fades in with the later of its two stations, and its end advances with an in-flight head.
      const parsed = Date.parse(eligible.at)
      const inner = fadeIn(Math.max(Number.isNaN(parsed) ? drawn : parsed, to.at ?? -Infinity)) + (to.head ? advance('x1', to.head.start) : '')
      out.push(inner === '' ? `${open}/>` : `${open}>${inner}</line>`)
    }
  }

  out.push(stations.join(''), mergedStations.join(''), trains.join(''))

  // The now-line and its date, the drawing's one date, move together into the headroom.
  // The date ends 3 px before the line; a line too near the left edge for the whole date pins it at
  // `DATE_PX` instead, its own counter-motion cancelling the group's until the line catches up, after
  // which it rides the line as usual.
  if (!terminal) {
    const date = localDay(drawn, frame.utcOffsetMinutes)
    const nx = n1(xDrawn)
    const dateY = n1(tracksBottom + ROW / 2 + 4)
    let dateText = `<text data-mark="now-date" x="${n1(xDrawn - 3)}" y="${dateY}" text-anchor="end" fill="${PALETTE.now}">${date}</text>`
    if (xDrawn - 3 < DATE_PX) {
      const span = xEnd - xDrawn
      const k = span > 0 ? Math.min(1, (DATE_PX - (xDrawn - 3)) / span) : 1
      const caught = n1(DATE_PX - k * span)
      dateText =
        `<text data-mark="now-date" x="${n1(DATE_PX)}" y="${dateY}" text-anchor="end" fill="${PALETTE.now}">${date}` +
        `<animate attributeName="x" values="${n1(DATE_PX)};${caught};${caught}" keyTimes="0;${Math.round(k * 1000) / 1000};1" dur="${headroomS}s" begin="${begin}" fill="freeze"/></text>`
    }
    out.push(
      `<g data-mark="now">` +
        `<line x1="${nx}" y1="${TOP}" x2="${nx}" y2="${n1(tracksBottom)}" stroke="${PALETTE.now}"/>` +
        dateText +
        `<animateTransform attributeName="transform" type="translate" from="0 0" to="${n1(xEnd - xDrawn)} 0" dur="${headroomS}s" begin="${begin}" fill="freeze"/>` +
        `</g>`,
    )
  }
  out.push('</svg>')
  return { source: out.join(''), alt: altOf(layout, envelope, frame.utcOffsetMinutes), width, height, rows, labelCells, cardCells }
}

function altOf(layout: Layout, envelope: Envelope, offsetMinutes: number): string {
  const { tracks, lanes, t0, end } = layout
  const span = `${localDay(t0, offsetMinutes)} to ${localDay(end, offsetMinutes)}`
  const n = envelope.target.number
  if (envelope.epic) {
    const merged = `${envelope.epic.shipped.length}/${envelope.epic.stories.length} merged`
    return `Metro Wave of #${n}: ${tracks.length} lines in ${lanes.length} lanes, ${merged}, ${span}`
  }
  const points = tracks.reduce((count, track) => count + track.points.length, 0)
  return `Metro Wave of #${n}: ${envelope.stage}, ${points} history points, ${span}`
}

/**
 * The Metro Wave for a board, `columns` cells wide, frozen at `frame` with its sightings merged into
 * their lines: the drawing, or `null` when it cannot fit the `Svg` element's 131,072-character `source`
 * even with the commit ticks dropped, and the band draws its one-line summary instead. The same frame
 * and columns always give a byte-identical `source`: nothing here reads the clock.
 */
export function metroWave(board: Board, envelope: Envelope, frame: WaveFrame, columns: number): Wave | null {
  const layout = layoutOf(board, envelope, frame)
  for (const ticks of ['titled', 'bare', 'none'] as const) {
    const wave = draw(layout, board, envelope, frame, columns, ticks)
    if (wave.source.length <= SOURCE_LIMIT) return wave
  }
  return null
}
