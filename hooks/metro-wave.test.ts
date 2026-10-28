// Metro Wave's pure drawing (#156), run by `claude plugin test .` beside the board's mod tests.
import { expect, test } from 'claude-code/testing'

import { boardModel, emptyLog, observe } from './board-model.ts'
import type { Envelope, HistoryPoint, Sighting } from './board-model.ts'
import epicFixture from './fixtures/prep-epic.ts'
import issueFixture from './fixtures/prep-issue.ts'
import { metroWave, PALETTE, segmentKind, SOURCE_LIMIT } from './metro-wave.ts'
import type { Seen, WaveFrame, WaveRow } from './metro-wave.ts'

const EPIC = epicFixture as unknown as Envelope
const ISSUE = issueFixture as unknown as Envelope
const NOW = '2026-01-10T00:00:00Z'
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
// A frame drawn at `drawnAt`, in UTC, with no previous read and no sightings unless given.
const frame = (drawnAt = NOW, over: Partial<WaveFrame> = {}): WaveFrame => ({
  drawnAt,
  previousDrawnAt: null,
  utcOffsetMinutes: 0,
  sightings: [],
  elapsedSeconds: 0,
  ...over,
})
const wave = (envelope: Envelope, columns = 120) => metroWave(boardModel(envelope, emptyLog(), null), envelope, frame(), columns)
const count = (source: string, pattern: RegExp) => (source.match(pattern) ?? []).length
const esc = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const pt = (point: string, at: string): HistoryPoint => ({ point, at, source: 'test' })

// Each row's kind, a line row by its hover key.
const rowKeys = (drawn: { rows: WaveRow[] }) => drawn.rows.map(r => (r.kind === 'line' ? r.key : r.kind))

test('a lane per board group, plus Integration when its line has points', async () => {
  const drawn = wave(EPIC)!
  // A gap row between lanes, and the date row last.
  expect(rowKeys(drawn)).toEqual(['line-151', 'gap', 'line-152', 'line-153', 'gap', 'line-154', 'line-155', 'gap', 'line-integration', 'date'])
  const without = clone(EPIC)
  without.epic!.integration_history = []
  expect(rowKeys(wave(without)!)).toEqual(['line-151', 'gap', 'line-152', 'line-153', 'gap', 'line-154', 'line-155', 'date'])
  expect(drawn.alt).toBe('Metro Wave of #150: 6 lines in 4 lanes, 1/5 merged, 2026-01-01 to 2026-01-10')
})

test('lines are labelled by number only, beside the drawing, with no lane headings and no date labels', async () => {
  const drawn = wave(EPIC)!
  const labels = drawn.rows.flatMap(r => (r.kind === 'line' ? [r.label] : []))
  // One label per line: the five stories' numbers, then the integration line's (no PR yet).
  expect(labels).toEqual(['#151', '#152', '#153', '#154', '#155', 'PR'])
  // The drawing carries no label, title or lane heading: its one text is the now-line's date.
  expect(count(drawn.source, /<text/g)).toBe(1)
  for (const story of EPIC.epic!.stories) if (story.title) expect(drawn.source).not.toContain(esc(story.title))
  expect(drawn.source).not.toContain('Wave 1')
  expect(count(drawn.source, />\d{4}-\d{2}-\d{2}</g)).toBe(1)
  expect(drawn.source).toMatch(/<text data-mark="now-date"[^>]*>2026-01-10<\/text>/)
  const withPr = clone(EPIC)
  withPr.epic!.integration = { number: 160, draft: true, gate: null }
  const integration = wave(withPr)!.rows.find(r => r.kind === 'line' && r.key === 'line-integration')
  expect(integration).toMatchObject({ label: '#160', number: null })
})

test('each line\'s card names it, where it stands and its time on each kind', async () => {
  const e = clone(EPIC)
  e.epic!.integration = { number: 160, draft: true, gate: null }
  const rows = wave(e)!.rows
  const card = (key: string) => (rows.find(r => r.kind === 'line' && r.key === key) as { card: string[] }).card
  const board = boardModel(e, emptyLog(), null)
  const row = board.groups.flatMap(g => g.rows).find(r => r.number === 152)!
  const rung = row.rungs.find(r => r.state === 'current')!.label
  expect(card('line-152')[0]).toBe(`#152 ${row.title}`)
  const stands = [rung, row.gateText ?? row.reason ?? row.nextText].filter(p => p !== null).join(' · ')
  expect(card('line-152')[1]).toBe(stands.length <= 38 ? stands : `${stands.slice(0, 37)}…`)
  // Times in the board's duration form, a kind with no time left out.
  expect(card('line-152')[2]).toMatch(/^(plan|build|evaluate|waited) \d+(h|m)/)
  expect(card('line-152')[2]).not.toMatch(/ 0m/)
  // A merged story waiting on its close stands at `merged`, with its next step.
  expect(card('line-151')[1]).toBe('merged · next: close its worktree')
  expect(card('line-integration').slice(0, 2)).toEqual(['#160 integration PR', 'integration · PR #160 (draft)'])
  const noPr = wave(EPIC)!.rows.find(r => r.kind === 'line' && r.key === 'line-integration') as { card: string[] }
  expect(noPr.card.slice(0, 2)).toEqual(['Integration', 'integration · no integration PR yet'])
  // The first line is cut to the card's 40 cells less its border.
  const long = clone(e)
  long.epic!.stories.find(s => s.number === 152)!.title = 'x'.repeat(80)
  const cut = (wave(long)!.rows.find(r => r.kind === 'line' && r.key === 'line-152') as { card: string[] }).card[0]
  expect(cut.length).toBe(38)
  expect(cut.endsWith('…')).toBe(true)
  // Every line is cut to the card, so it stays three rows: here a long gate on #155.
  const gated = clone(e)
  gated.epic!.stories.find(s => s.number === 155)!.gate = { reason: 'y'.repeat(80) }
  for (const line of (wave(gated)!.rows.find(r => r.kind === 'line' && r.key === 'line-155') as { card: string[] }).card) {
    expect(line.length).toBeLessThanOrEqual(38)
  }
})

const seen = (over: Partial<Seen> = {}): Seen => ({ commit: false, resolverStart: false, sightedBeforeNextWork: false, ...over })

test('the segment kind table: stage starts open their stage, and no history point opens plan', async () => {
  expect(segmentKind('planner-start', seen())).toBe('plan')
  expect(segmentKind('resolver-start', seen())).toBe('build')
  expect(segmentKind('evaluator-start', seen())).toBe('evaluate')
  for (const point of ['eligible', 'plan-posted', 'workspace-opened']) expect(segmentKind(point, seen())).toBe('wait')
  expect(segmentKind('commit', seen())).toBe('build')
  expect(segmentKind('pr-opened', seen())).toBe('wait')
  expect(segmentKind('pr-opened', seen({ commit: true }))).toBe('build')
  expect(segmentKind('pr-opened', seen({ resolverStart: true }))).toBe('build')
  expect(segmentKind('rework', seen())).toBe('build')
  expect(segmentKind('rework', seen({ sightedBeforeNextWork: true }))).toBe('wait')
  expect(segmentKind('evaluate', seen())).toBe('evaluate')
  expect(segmentKind('evaluate', seen({ sightedBeforeNextWork: true }))).toBe('wait')
  for (const point of ['merged', 'closed']) expect(segmentKind(point, seen({ commit: true }))).toBeNull()
})

// A single issue whose line is exactly `points`, drawn with `sightings` at a fixed now.
const issueWith = (points: HistoryPoint[]): Envelope => {
  const e = clone(ISSUE)
  e.unit!.history = points
  return e
}
const sighting = (stage: Sighting['stage'], at: string, n = 100): Sighting => ({
  stage,
  issue: stage === 'evaluator' ? null : n,
  pr: stage === 'evaluator' ? n : null,
  cwd: `${ISSUE.root!.path}/.worktrees/100-x`,
  at,
})
const drawWith = (envelope: Envelope, sightings: Sighting[]) =>
  metroWave(boardModel(envelope, emptyLog(), null), envelope, frame(NOW, { sightings }), 120)!.source
const kinds = (source: string) => (source.match(/data-kind="[a-z]+"/g) ?? []).map(k => k.slice(11, -1))

test('a sighted planner start draws a solid plan run to plan-posted; unsighted, that gap is a wait', async () => {
  const e = issueWith([pt('eligible', '2026-01-01T00:00:00Z'), pt('plan-posted', '2026-01-03T00:00:00Z')])
  expect(kinds(drawWith(e, []))).toEqual(['wait'])
  const sighted = drawWith(e, [sighting('planner', '2026-01-02T00:00:00Z')])
  expect(kinds(sighted)).toEqual(['wait', 'plan', 'wait'])
  expect(sighted).toMatch(new RegExp(`data-kind="plan"[^>]*stroke="${PALETTE.plan}" stroke-width="4"/>`))
  expect(sighted).toContain('data-point="planner-start"')
})

test('a resolver start before the first commit opens build at the sighting', async () => {
  const e = issueWith([pt('plan-posted', '2026-01-02T00:00:00Z'), pt('commit', '2026-01-05T00:00:00Z')])
  expect(kinds(drawWith(e, []))).toEqual(['wait', 'build'])
  const source = drawWith(e, [sighting('resolver', '2026-01-03T00:00:00Z')])
  expect(kinds(source)).toEqual(['wait', 'build'])
  const build = /data-kind="build" x1="([\d.]+)"/.exec(source)!
  const station = /data-point="resolver-start" cx="([\d.]+)"/.exec(source)!
  expect(build[1]).toBe(station[1])
})

test('an evaluator start after an evaluate point turns the gap between them into a wait', async () => {
  const e = issueWith([
    pt('commit', '2026-01-02T00:00:00Z'),
    pt('evaluate', '2026-01-03T00:00:00Z'),
    pt('merged', '2026-01-08T00:00:00Z'),
  ])
  expect(kinds(drawWith(e, []))).toEqual(['build', 'evaluate'])
  e.unit!.pr = { number: 58, url: null, draft: false }
  expect(kinds(drawWith(e, [sighting('evaluator', '2026-01-05T00:00:00Z', 58)]))).toEqual(['build', 'wait', 'evaluate'])
  // A sighting after the next work point is not this evaluate's: the run stays evaluate.
  e.unit!.history = [...e.unit!.history!.slice(0, 2), pt('rework', '2026-01-04T00:00:00Z'), pt('merged', '2026-01-08T00:00:00Z')]
  expect(kinds(drawWith(e, [sighting('evaluator', '2026-01-05T00:00:00Z', 58)]))).toEqual(['build', 'evaluate', 'build', 'evaluate'])
})

test('a resolver start after a rework turns the gap into a wait; the sighting opens build', async () => {
  const e = issueWith([pt('commit', '2026-01-02T00:00:00Z'), pt('rework', '2026-01-03T00:00:00Z'), pt('commit', '2026-01-06T00:00:00Z')])
  expect(kinds(drawWith(e, []))).toEqual(['build'])
  expect(kinds(drawWith(e, [sighting('resolver', '2026-01-05T00:00:00Z')]))).toEqual(['build', 'wait', 'build'])
})

test('the workspace station is drawn in its colour and a wait follows it', async () => {
  const e = issueWith([pt('plan-posted', '2026-01-02T00:00:00Z'), pt('workspace-opened', '2026-01-03T00:00:00Z'), pt('commit', '2026-01-05T00:00:00Z')])
  const source = drawWith(e, [])
  expect(source).toMatch(new RegExp(`data-point="workspace-opened"[^>]*fill="${PALETTE.workspace}"`))
  expect(kinds(source)).toEqual(['wait', 'build'])
  const wait = /data-kind="wait" x1="([\d.]+)" y1="[\d.]+" x2="([\d.]+)"/.exec(source)!
  const commit = /data-point="commit" x1="([\d.]+)"/.exec(source)!
  expect(wait[2]).toBe(commit[1])
})

test('a sighting from another folder or for another issue is not this run\'s', async () => {
  const e = issueWith([pt('eligible', '2026-01-01T00:00:00Z'), pt('plan-posted', '2026-01-03T00:00:00Z')])
  const elsewhere = { ...sighting('planner', '2026-01-02T00:00:00Z'), cwd: '/other/repo' }
  const sibling = { ...sighting('planner', '2026-01-02T00:00:00Z'), cwd: `${ISSUE.root!.path}-fork` }
  const otherIssue = sighting('planner', '2026-01-02T00:00:00Z', 999)
  expect(kinds(drawWith(e, [elsewhere, sibling, otherIssue]))).toEqual(['wait'])
})

test('an epic story\'s evaluator sighting matches its PR, and still matches once it closes', async () => {
  // #152 is open with PR #158; its line gains an evaluate point, and an evaluator start a day later.
  const e = clone(EPIC)
  const story = e.epic!.stories.find(s => s.number === 152)!
  story.history = [...story.history!, pt('evaluate', '2026-06-11T00:00:00Z')]
  const evaluator: Sighting = { stage: 'evaluator', issue: null, pr: story.pr!.number, cwd: EPIC.root!.path, at: '2026-06-12T00:00:00Z' }
  const lineKinds = (envelope: Envelope, log = emptyLog()) => {
    const source = metroWave(boardModel(envelope, log, null), envelope, frame('2026-06-20T00:00:00Z', { sightings: [evaluator] }), 120)!.source
    const y = /data-point="workspace-opened" cx="[\d.]+" cy="([\d.]+)"/.exec(source)![1]
    return (source.match(new RegExp(`data-kind="[a-z]+" x1="[\\d.]+" y1="${y}"`, 'g')) ?? []).map(k => k.slice(11, k.indexOf('"', 11)))
  }
  expect(lineKinds(e)).toEqual(['wait', 'build', 'wait', 'evaluate'])
  // Closed, the prep reports no PR: the board log's memory of it keeps the sighting matched.
  const log = observe(emptyLog(), e, '2026-06-11T00:00:00Z')
  const closed = clone(e)
  const closedStory = closed.epic!.stories.find(s => s.number === 152)!
  closedStory.pr = null
  closedStory.closed = true
  closedStory.history = [...closedStory.history!, pt('merged', '2026-06-14T00:00:00Z'), pt('closed', '2026-06-14T00:01:00Z')]
  expect(lineKinds(closed, log)).toEqual(['wait', 'build', 'wait', 'evaluate'])
  expect(lineKinds(closed)).toEqual(['wait', 'build', 'evaluate'])
})

test('an evaluator sighting on the integration PR lands on the integration line', async () => {
  const e = clone(EPIC)
  e.epic!.integration = { number: 170, draft: false, gate: null }
  e.epic!.integration_history = [pt('pr-opened', '2026-01-05T00:00:00Z'), pt('evaluate', '2026-01-06T00:00:00Z')]
  const evaluator: Sighting = { stage: 'evaluator', issue: null, pr: 170, cwd: EPIC.root!.path, at: '2026-01-07T00:00:00Z' }
  const source = metroWave(boardModel(e, emptyLog(), null), e, frame(NOW, { sightings: [evaluator] }), 120)!.source
  // On the integration line's row: the same y as its `pr-opened` station.
  const row = /data-point="pr-opened" cx="[\d.]+" cy="([\d.]+)"/g
  const rows = [...source.matchAll(row)].map(m => m[1])
  const sightingY = /data-point="evaluator-start" cx="[\d.]+" cy="([\d.]+)"/.exec(source)![1]
  expect(sightingY).toBe(rows[rows.length - 1])
  expect(count(metroWave(boardModel(e, emptyLog(), null), e, frame(NOW, { sightings: [{ ...evaluator, pr: 999 }] }), 120)!.source, /data-point="evaluator-start"/g)).toBe(0)
})

test('on the integration line the epic\'s own resolver start does not make its PR opening build', async () => {
  // The epic's resolver opens the draft integration PR; that line draws no commits.
  const e = clone(EPIC)
  e.epic!.integration = { number: 170, draft: true, gate: null }
  e.epic!.integration_history = [pt('pr-opened', '2026-01-05T00:00:00Z'), pt('evaluate', '2026-01-08T00:00:00Z')]
  const resolver: Sighting = { stage: 'resolver', issue: e.target.number, pr: null, cwd: EPIC.root!.path, at: '2026-01-04T00:00:00Z' }
  const source = metroWave(boardModel(e, emptyLog(), null), e, frame(NOW, { sightings: [resolver] }), 120)!.source
  const y = /data-point="resolver-start" cx="[\d.]+" cy="([\d.]+)"/.exec(source)![1]
  const onRow = (source.match(new RegExp(`data-kind="[a-z]+" x1="[\\d.]+" y1="${y}"`, 'g')) ?? []).map(k => k.slice(11, k.indexOf('"', 11)))
  expect(onRow).toEqual(['build', 'wait', 'evaluate'])
  // A story line with the same sequence still draws build from its PR opening.
  const story = issueWith([pt('pr-opened', '2026-01-05T00:00:00Z'), pt('evaluate', '2026-01-08T00:00:00Z')])
  expect(kinds(drawWith(story, [sighting('resolver', '2026-01-04T00:00:00Z')]))).toEqual(['build', 'evaluate'])
})

test('a sighting with no number on its field matches no line', async () => {
  const e = issueWith([pt('eligible', '2026-01-01T00:00:00Z'), pt('plan-posted', '2026-01-03T00:00:00Z')])
  e.unit!.pr = null
  const broken = { stage: 'evaluator', issue: null, pr: undefined, cwd: ISSUE.root!.path, at: '2026-01-02T00:00:00Z' } as unknown as Sighting
  expect(drawWith(e, [broken])).not.toContain('evaluator-start')
})

test('a planning gap with no sighting is dashed, never solid; build and evaluate are solid', async () => {
  const source = wave(EPIC)!.source
  expect(source).toMatch(new RegExp(`data-kind="wait"[^>]*stroke="${PALETTE.wait}"[^>]*stroke-dasharray`))
  expect(source).not.toMatch(/data-kind="wait"(?![^>]*stroke-dasharray)[^>]*\/>/)
  expect(source).toMatch(new RegExp(`data-kind="build"[^>]*stroke="${PALETTE.build}" stroke-width="4"/>`))
  expect(source).toMatch(new RegExp(`data-kind="evaluate"[^>]*stroke="${PALETTE.evaluate}" stroke-width="4"/>`))
  expect(source).not.toMatch(/data-kind="(build|evaluate)"[^>]*stroke-dasharray/)
})

test('commit ticks, a rework loop and a station per history point', async () => {
  const e = clone(EPIC)
  const story = e.epic!.stories.find(s => s.number === 153)!
  story.history = [
    pt('eligible', '2026-01-01T00:00:00Z'),
    pt('commit', '2026-01-02T00:00:00Z'),
    pt('commit', '2026-01-03T00:00:00Z'),
    pt('pr-opened', '2026-01-03T06:00:00Z'),
    pt('evaluate', '2026-01-04T00:00:00Z'),
    pt('rework', '2026-01-05T00:00:00Z'),
  ]
  const source = wave(e)!.source
  const points = e.epic!.stories.reduce((n, s) => n + (s.history ?? []).length, 0) + e.epic!.integration_history!.length
  expect(count(source, /data-point="/g)).toBe(points)
  const commits = e.epic!.stories.reduce((n, s) => n + (s.history ?? []).filter(p => p.point === 'commit').length, 0)
  expect(count(source, /<line data-point="commit"/g)).toBe(commits)
  expect(count(source, /data-mark="rework"[^>]*stroke="#dc2626"/g)).toBe(1)
  // The merged station is green.
  expect(source).toMatch(new RegExp(`data-point="merged"[^>]*fill="${PALETTE.merged}"`))
})

test('a transfer line is dashed while its edge has not merged and solid once it has', async () => {
  const source = wave(EPIC)!.source
  const transfers = source.match(/<line data-mark="transfer"[^>]*\/>/g) ?? []
  // #152 waits on merged #151 (solid); #154 waits on unmerged #152 (dashed).
  expect(transfers.length).toBe(2)
  expect(transfers.filter(t => t.includes('stroke-dasharray')).length).toBe(1)
  const merged = clone(EPIC)
  for (const story of merged.epic!.stories) for (const edge of story.start ?? []) edge.merged = true
  expect((wave(merged)!.source.match(/<line data-mark="transfer"[^>]*stroke-dasharray/g) ?? []).length).toBe(0)
})

test('a now-line on a live target, none on a terminal one', async () => {
  expect(count(wave(EPIC)!.source, /<g data-mark="now"><line [^>]*stroke="#ef4444"/g)).toBe(1)
  const done = { ...clone(ISSUE), stage: 'terminal' }
  done.unit!.history = [pt('eligible', '2026-01-01T00:00:00Z'), pt('closed', '2026-01-03T00:00:00Z')]
  const drawn = wave(done)!
  expect(drawn.source).not.toContain('data-mark="now"')
  expect(drawn.alt).toBe('Metro Wave of #100: terminal, 2 history points, 2026-01-01 to 2026-01-03')
})

test('the drawing takes the band less its label column, one row tall per row', async () => {
  // 100 cells: the longest label, `#151`, plus one is 5 cells, so the drawing is 95 cells wide.
  const drawn = wave(EPIC, 100)!
  expect(drawn.labelCells).toBe(5)
  expect(drawn.width).toBe(760)
  expect(drawn.height).toBe(drawn.rows.length * 20)
  expect(drawn.source).toMatch(new RegExp(`^<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 ${drawn.height}"`))
  expect(drawn.source).not.toMatch(/^<svg[^>]* width=/)
  // Each line's track sits at its row's vertical centre.
  const index = drawn.rows.findIndex(r => r.kind === 'line' && r.key === 'line-152')
  expect(drawn.source).toContain(`y1="${index * 20 + 10}"`)
  // The label column is capped at 30 % of the band, and its label cut to fit before the drawing.
  const narrow = wave(EPIC, 10)!
  expect(narrow.labelCells).toBe(3)
  expect(narrow.rows.flatMap(r => (r.kind === 'line' ? [r.label] : []))[0]).toBe('#…')
  expect(wave(EPIC, 2)!.labelCells).toBe(1)
  // A terminal target keeps its (empty) date row, so the rows still tile the band.
  const done = clone(EPIC)
  done.stage = 'terminal'
  expect(wave(done)!.rows[wave(done)!.rows.length - 1]).toEqual({ kind: 'date' })
})

test('12 stories with 100 commits each fit the source limit; an input that cannot fit is null', async () => {
  const big = clone(EPIC)
  const base = big.epic!.stories[0]
  big.epic!.stories = Array.from({ length: 12 }, (_, i) => ({
    ...clone(base),
    number: 300 + i,
    start: [],
    history: [
      pt('eligible', '2026-01-01T00:00:00Z'),
      ...Array.from({ length: 100 }, (_, c) => pt('commit', new Date(Date.parse('2026-01-02T00:00:00Z') + c * 60000).toISOString())),
    ],
  }))
  big.epic!.concurrency = { source: 'declared', fallback_reason: null, sets: [{ name: 'All', stories: big.epic!.stories.map(s => s.number) }], malformed: [] }
  const drawn = wave(big)
  expect(drawn).not.toBeNull()
  expect(drawn!.source.length).toBeLessThanOrEqual(SOURCE_LIMIT)

  // Past the limit with ticks, under it without: the ticks are dropped and the rest kept.
  const ticky = clone(big)
  for (const story of ticky.epic!.stories) {
    story.history = [
      pt('eligible', '2026-01-01T00:00:00Z'),
      ...Array.from({ length: 1000 }, (_, c) => pt('commit', new Date(Date.parse('2026-01-02T00:00:00Z') + c * 60000).toISOString())),
    ]
  }
  const dropped = wave(ticky)!
  expect(dropped.source.length).toBeLessThanOrEqual(SOURCE_LIMIT)
  expect(dropped.source).not.toContain('data-point="commit"')
  expect(count(dropped.source, /data-point="eligible"/g)).toBe(12)

  const huge = clone(big)
  huge.epic!.stories = Array.from({ length: 2000 }, (_, i) => ({ ...clone(base), number: 1000 + i, start: [], history: [pt('eligible', '2026-01-01T00:00:00Z')] }))
  huge.epic!.concurrency = { source: 'declared', fallback_reason: null, sets: [{ name: 'All', stories: huge.epic!.stories.map(s => s.number) }], malformed: [] }
  expect(wave(huge)).toBeNull()
})

test('a lane name never reaches the drawing', async () => {
  const e = clone(EPIC)
  e.epic!.concurrency!.sets[0].name = '<script>&"'
  const source = wave(e)!.source
  expect(source).not.toContain('<script>')
  expect(source).not.toContain('&lt;script')
})

test('a wait on a story whose line has no points still draws its transfer line', async () => {
  const e = clone(EPIC)
  e.epic!.stories.find(s => s.number === 152)!.history = []
  const transfers = wave(e)!.source.match(/<line data-mark="transfer"[^>]*\/>/g) ?? []
  // #154 waits on the now-empty #152 (dashed); #152's own wait on #151 needs an eligible station, so it is gone.
  expect(transfers.length).toBe(1)
  expect(transfers[0]).toContain('stroke-dasharray')
})

test('an unparseable now still lays every coordinate out as a number', async () => {
  const e = clone(ISSUE)
  e.unit!.history = []
  const drawn = metroWave(boardModel(e, emptyLog(), null), e, frame('not a time'), 120)!
  expect(drawn.source).not.toContain('NaN')
  expect(drawn.alt).toBe('Metro Wave of #100: resolving, 0 history points, 1970-01-01 to 1970-01-01')
  const timed = metroWave(boardModel(ISSUE, emptyLog(), null), ISSUE, frame('not a time'), 120)!
  expect(timed.source).not.toContain('NaN')
})

test('the paint order: track, then transfer lines, then stations with merged ones on top, then the now-line', async () => {
  const e = clone(EPIC)
  // An in-axis line that merges and closes seconds apart, as the pipeline closes a story.
  e.epic!.stories.find(s => s.number === 151)!.history = [
    pt('eligible', '2026-01-02T00:00:00Z'),
    pt('commit', '2026-01-03T00:00:00Z'),
    pt('pr-opened', '2026-01-04T00:00:00Z'),
    pt('evaluate', '2026-01-05T00:00:00Z'),
    pt('rework', '2026-01-06T00:00:00Z'),
    pt('merged', '2026-01-08T00:00:00Z'),
    pt('closed', '2026-01-08T00:00:05Z'),
  ]
  const source = wave(e)!.source
  const merged = source.indexOf('data-point="merged"')
  expect(merged).toBeGreaterThan(source.indexOf('<circle data-point="closed"'))
  const firstCircle = source.indexOf('<circle')
  for (const mark of ['data-mark="transfer"', 'data-mark="rework"', 'data-kind=']) {
    expect(source.lastIndexOf(mark)).toBeLessThan(firstCircle)
  }
  expect(source.indexOf('data-mark="now"')).toBeGreaterThan(source.lastIndexOf('<circle'))
  expect(source.indexOf('data-mark="train"')).toBeGreaterThan(source.lastIndexOf('data-point="merged"'))
})

// Phase 3 (#157): the long-run scale, motion, the now-line date, transfer thinning and tooltips.
const T0 = Date.parse('2026-03-01T00:00:00Z')
const at = (hours: number) => new Date(T0 + hours * 3600000).toISOString()
const drawIssue = (points: HistoryPoint[], drawnAt: string, over: Partial<WaveFrame> = {}, columns = 120) =>
  metroWave(boardModel(issueWith(points), emptyLog(), null), issueWith(points), frame(drawnAt, over), columns)!.source
const attr = (source: string, pattern: RegExp) => [...source.matchAll(pattern)].map(m => Number(m[1]))

test('an idle stretch past the threshold is drawn 24 px wide with a break mark, and work keeps one scale', async () => {
  // Build 1h, a five-day idle wait, build 1h again, then a short wait to the drawn time.
  const points = [pt('commit', at(0)), pt('plan-posted', at(1)), pt('commit', at(120)), pt('plan-posted', at(121))]
  const source = drawIssue(points, at(123))
  expect(count(source, /data-mark="break"/g)).toBe(1)
  expect(source).toMatch(new RegExp(`data-mark="break"[^>]*stroke="${PALETTE.wait}"`))
  // The waiting line carries the stretch's mark, so there is no shared axis break.
  expect(source).not.toContain('data-mark="axis-break"')
  const posted = attr(source, /data-point="plan-posted" cx="([\d.]+)"/g)
  const commits = attr(source, /data-point="commit" x1="([\d.]+)"/g)
  expect(Math.abs(commits[1] - posted[0] - 24)).toBeLessThanOrEqual(0.2)
  const builds = [...source.matchAll(/data-kind="build" x1="([\d.]+)" y1="[\d.]+" x2="([\d.]+)"/g)].map(m => Number(m[2]) - Number(m[1]))
  expect(builds.length).toBe(2)
  expect(Math.abs(builds[0] - builds[1])).toBeLessThanOrEqual(0.2)
})

test('a stretch under the threshold, one a solid run covers, or the last one before the drawn time is not compressed', async () => {
  // Under 2 hours.
  expect(drawIssue([pt('commit', at(0)), pt('plan-posted', at(1)), pt('commit', at(2.5)), pt('plan-posted', at(3.5))], at(4))).not.toContain('data-mark="break"')
  // Covered by one long build run: drawn at its true width, not 24 px (a covered stretch has no wait to
  // carry a break mark, so the width is the test).
  const covered = drawIssue([pt('commit', at(0)), pt('commit', at(120)), pt('plan-posted', at(121))], at(123))
  const ticks = attr(covered, /data-point="commit" x1="([\d.]+)"/g)
  expect(ticks[1] - ticks[0]).toBeGreaterThan(500)
  // The long wait runs from the last station to the drawn time.
  expect(drawIssue([pt('commit', at(0)), pt('plan-posted', at(1))], at(120))).not.toContain('data-mark="break"')
})

test('no more stretches are compressed than leave half the width linear', async () => {
  const points = [
    pt('commit', at(0)), pt('plan-posted', at(1)),
    pt('commit', at(72)), pt('plan-posted', at(73)),
    pt('commit', at(140)), pt('plan-posted', at(141)),
  ]
  expect(count(drawIssue(points, at(142)), /data-mark="break"/g)).toBe(2)
  // 12 cells: the label column takes 3, so the drawing is 72 px, its axis 8 to 64, and (64 - 8) / 2 / 24 allows one.
  expect(count(drawIssue(points, at(142), {}, 12), /data-mark="break"/g)).toBe(1)
})

test('the now-line and its date advance into the headroom from the frame, starting elapsedSeconds in', async () => {
  // Drawn nine days after the first point: the headroom is 20 % of that, 155,520 s.
  const fresh = wave(EPIC)!.source
  expect(fresh).toMatch(/<g data-mark="now">.*<animateTransform attributeName="transform" type="translate" from="0 0" to="[\d.]+ 0" dur="155520s" begin="0s" fill="freeze"\/><\/g>/)
  const resumed = metroWave(boardModel(EPIC, emptyLog(), null), EPIC, frame(NOW, { elapsedSeconds: 37 }), 120)!.source
  expect(resumed).toContain('dur="155520s" begin="-37s" fill="freeze"')
  expect(resumed).not.toContain('begin="0s"')
  // A short run's headroom is the one-hour floor.
  expect(drawIssue([pt('eligible', at(0)), pt('plan-posted', at(1))], at(2))).toContain('dur="3600s"')
})

test('one train marker per in-flight line, in its run\'s colour; none on a merged line or a terminal target', async () => {
  const live = drawIssue([pt('eligible', at(0)), pt('commit', at(2))], at(4))
  expect(count(live, /data-mark="train"/g)).toBe(1)
  expect(live).toMatch(new RegExp(`<circle data-mark="train"[^>]*fill="${PALETTE.build}"><animate attributeName="cx"[^>]*\/><animate attributeName="r" values="3;5;3" dur="1.6s" begin="0s" repeatCount="indefinite"\/>`))
  // The head run grows its x2 in step with the now-line.
  expect(live).toMatch(/data-kind="build"[^>]*><animate attributeName="x2" from="[\d.]+" to="[\d.]+" dur="\d+s"/)
  expect(drawIssue([pt('eligible', at(0)), pt('commit', at(2)), pt('merged', at(3))], at(4))).not.toContain('data-mark="train"')
  const done = { ...issueWith([pt('eligible', at(0)), pt('commit', at(2))]), stage: 'terminal' }
  const terminal = metroWave(boardModel(done, emptyLog(), null), done, frame(at(4)), 120)!.source
  expect(terminal).not.toContain('data-mark="train"')
  expect(terminal).not.toContain('<animate')
  // The epic at NOW: #153, #154, #155 and the integration line run to the drawn time. #151 merged, and
  // #152's history runs past NOW, so no run of its ends at the drawn time.
  expect(count(wave(EPIC)!.source, /data-mark="train"/g)).toBe(4)
})

test('work after the previous read grows in and its stations fade in; with no previous read nothing does', async () => {
  const points = [pt('eligible', at(0)), pt('plan-posted', at(2)), pt('commit', at(6))]
  const first = drawIssue(points, at(8))
  expect(first).not.toContain('attributeName="opacity"')
  expect(first).not.toContain('dur="1s"')
  const next = drawIssue(points, at(8), { previousDrawnAt: at(4) })
  // The commit after the previous read fades in; the plan-posted station before it does not.
  expect(count(next, /attributeName="opacity" from="0" to="1" dur="1s" begin="0s" fill="freeze"/g)).toBe(1)
  expect(next).toMatch(/data-point="commit"[^>]*>(<title>[^<]*<\/title>)?<animate attributeName="opacity"/)
  // The wait from plan-posted grows from the previous read's x; the head grows in, then advances.
  expect(next).toMatch(/data-kind="wait"[^>]*><animate attributeName="x2" from="[\d.]+" to="[\d.]+" dur="1s"/)
  expect(next).toMatch(/data-kind="build"[^>]*><animate attributeName="x2" from="([\d.]+)" to="[\d.]+" dur="\d+s" begin="0s" fill="freeze"\/><animate attributeName="x2" from="[\d.]+" to="\1" dur="1s" begin="0s"\/>/)
})

test('the now-line carries the one date: the drawn time\'s local day', async () => {
  const points = [pt('eligible', at(0)), pt('plan-posted', at(1))]
  const utc = drawIssue(points, '2026-03-05T20:00:00Z')
  expect(count(utc, />\d{4}-\d{2}-\d{2}</g)).toBe(1)
  expect(utc).toMatch(/<g data-mark="now">[^]*<text data-mark="now-date"[^>]*>2026-03-05<\/text>/)
  const ahead = drawIssue(points, '2026-03-05T20:00:00Z', { utcOffsetMinutes: 300 })
  expect(ahead).toContain('>2026-03-06</text>')
  expect(count(ahead, />\d{4}-\d{2}-\d{2}</g)).toBe(1)
})

test('only the transfer lines into the next wave are drawn by default; hovering shows the rest', async () => {
  const source = wave(EPIC)!.source
  expect(source).toContain('<style>.transfer-more{visibility:hidden} svg:hover .transfer-more{visibility:visible}</style>')
  // Wave 1 is closed, so the next wave is Wave 2: #152's wait on #151 shows, #154's on #152 does not.
  const transfers = source.match(/<line data-mark="transfer"[^>]*\/>/g) ?? []
  expect(transfers.filter(t => !t.includes('class="transfer-more"')).length).toBe(1)
  expect(transfers.filter(t => t.includes('class="transfer-more"')).length).toBe(1)
  const allClosed = clone(EPIC)
  for (const story of allClosed.epic!.stories) story.closed = true
  const closed = wave(allClosed)!.source.match(/<line data-mark="transfer"[^>]*\/>/g) ?? []
  expect(closed.length).toBe(2)
  expect(closed.every(t => t.includes('class="transfer-more"'))).toBe(true)
  // Under the serial fallback, the next wave is the first unclosed story alone: #152 still shows.
  const serial = clone(EPIC)
  serial.epic!.concurrency = { source: 'serial-fallback', fallback_reason: 'no plan', sets: [], malformed: [] }
  const shown = (wave(serial)!.source.match(/<line data-mark="transfer"[^>]*\/>/g) ?? []).filter(t => !t.includes('transfer-more'))
  expect(shown.length).toBe(1)
})

test('every station and commit tick carries a tooltip with its local time', async () => {
  const source = drawWith(issueWith([pt('eligible', at(0)), pt('commit', at(2)), pt('pr-opened', at(3))]), [sighting('resolver', at(1))])
  expect(count(source, /<circle data-point=/g)).toBe(3)
  expect(count(source, /<circle data-point="[^"]*"[^>]*><title>/g)).toBe(3)
  expect(source).toContain('<title>resolver started · 2026-03-01 01:00</title>')
  expect(source).toContain('<title>commit · 2026-03-01 02:00</title>')
  expect(source).toContain('<title>eligible · 2026-03-01 00:00</title>')
})

test('the same frame and columns give a byte-identical drawing, sized as the Svg is', async () => {
  const board = boardModel(EPIC, emptyLog(), null)
  const a = metroWave(board, EPIC, frame(), 120)!
  const b = metroWave(boardModel(EPIC, emptyLog(), null), clone(EPIC), frame(), 120)!
  expect(a.source).toBe(b.source)
  expect(a.width).toBe(920)
  expect(a.source).toContain(`viewBox="0 0 ${a.width} ${a.height}"`)
  expect(a.rows.filter(r => r.kind === 'line').length).toBe(6)
  expect(a.rows[a.rows.length - 1]).toEqual({ kind: 'date' })
})

test('a transfer line into an in-flight line advances with its head; one into a merged line stays put', async () => {
  // At June 20, #152 is building (its PR opened June 10) and #154 waits on it; #152 waits on merged #151.
  const source = metroWave(boardModel(EPIC, emptyLog(), null), EPIC, frame('2026-06-20T00:00:00Z'), 120)!.source
  const transfers = source.match(/<line data-mark="transfer"[^>]*?(\/>|>.*?<\/line>)/g) ?? []
  expect(transfers.length).toBe(2)
  const intoHead = transfers.filter(t => t.includes('<animate attributeName="x1"'))
  expect(intoHead.length).toBe(1)
  expect(intoHead[0]).toContain('class="transfer-more"')
  // It starts where #152's head starts and ends where the head ends.
  const head = /data-kind="build"[^>]*><animate attributeName="x2" from="([\d.]+)" to="([\d.]+)"/.exec(source)!
  expect(intoHead[0]).toContain(`x1="${head[1]}"`)
  expect(intoHead[0]).toContain(`from="${head[1]}" to="${head[2]}"`)
  expect(transfers.filter(t => t.endsWith('/>')).length).toBe(1)
  // With new work since a previous read on this months-long run, the transfer line's end grows in with
  // the head: the same unfrozen one-second grow after the advance, from the head's grow start.
  const grown = metroWave(boardModel(EPIC, emptyLog(), null), EPIC, frame('2026-06-20T00:00:00Z', { previousDrawnAt: '2026-06-15T00:00:00Z' }), 120)!.source
  const headGrow = /data-kind="build"[^>]*><animate attributeName="x2" from="([\d.]+)" to="[\d.]+" dur="\d+s" begin="0s" fill="freeze"\/><animate attributeName="x2" from="([\d.]+)" to="\1" dur="1s" begin="0s"\/>/.exec(grown)!
  expect(headGrow).not.toBeNull()
  const into = (grown.match(/<line data-mark="transfer"[^>]*>.*?<\/line>/g) ?? []).filter(t => t.includes('attributeName="x1"'))
  expect(into.length).toBe(1)
  expect(into[0]).toContain(`<animate attributeName="x1" from="${headGrow[2]}" to="${headGrow[1]}" dur="1s" begin="0s"/>`)
})

// An epic whose second story is filed days after the first merged: the stretch between them has no line.
const lateFiled = (terminal = false): Envelope => {
  const e = clone(EPIC)
  if (terminal) e.stage = 'terminal'
  e.epic!.integration_history = []
  for (const story of e.epic!.stories) story.history = []
  e.epic!.stories.find(s => s.number === 151)!.history = [
    pt('eligible', at(0)), pt('commit', at(1)), pt('pr-opened', at(2)), pt('merged', at(3)), pt('closed', at(3.01)),
  ]
  e.epic!.stories.find(s => s.number === 152)!.history = [pt('eligible', at(100)), pt('commit', at(101)), pt('pr-opened', at(102))]
  return e
}

test('a compressed stretch no line exists over gets one shared axis break, before the transfer lines', async () => {
  for (const terminal of [false, true]) {
    const e = lateFiled(terminal)
    const source = metroWave(boardModel(e, emptyLog(), null), e, frame(at(103)), 120)!.source
    expect(count(source, /data-mark="axis-break"/g)).toBe(1)
    expect(source).not.toContain('data-mark="break"')
    expect(source).toMatch(new RegExp(`<path data-mark="axis-break" d="[^"]+" fill="none" stroke="${PALETTE.station}" stroke-width="1.5"/>`))
    // Both strokes lie inside the 24 px stretch, between #151's closed station and #152's eligible one.
    const closed = attr(source, /data-point="closed" cx="([\d.]+)"/g)[0]
    const eligible = attr(source, /data-point="eligible" cx="([\d.]+)"/g)[1]
    expect(Math.abs(eligible - closed - 24)).toBeLessThanOrEqual(0.2)
    const strokes = [...source.matchAll(/data-mark="axis-break" d="M([\d.]+) [\d.]+l4 -[\d.]+M([\d.]+) [\d.]+l4 -[\d.]+"/g)][0]
    expect(Number(strokes[1])).toBeGreaterThanOrEqual(closed)
    expect(Number(strokes[2]) + 4).toBeLessThanOrEqual(eligible)
    // The paint order: after the tracks, before the first transfer line and the stations.
    const mark = source.indexOf('data-mark="axis-break"')
    expect(mark).toBeGreaterThan(source.lastIndexOf('data-kind='))
    expect(mark).toBeLessThan(source.indexOf('data-mark="transfer"'))
    expect(mark).toBeLessThan(source.indexOf('<circle'))
  }
})

test('on a run of days the head still grows in over one second before it advances', async () => {
  // A 200-hour span: the headroom is 40 hours, 144,000 s, so the grow is a tiny share of the advance.
  const points = [pt('eligible', at(0)), pt('plan-posted', at(2)), pt('commit', at(170))]
  const source = drawIssue(points, at(200), { previousDrawnAt: at(150) })
  expect(source).toContain('dur="144000s"')
  for (const attribute of ['x2', 'cx']) {
    const pair = new RegExp(`<animate attributeName="${attribute}" from="([\\d.]+)" to="[\\d.]+" dur="144000s" begin="0s" fill="freeze"/><animate attributeName="${attribute}" from="([\\d.]+)" to="\\1" dur="1s" begin="0s"/>`)
    const m = pair.exec(source)!
    expect(m).not.toBeNull()
    expect(Number(m[2])).toBeLessThan(Number(m[1]))
  }
})
