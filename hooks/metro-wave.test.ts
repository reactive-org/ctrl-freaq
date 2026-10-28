// Metro Wave's pure drawing (#156), run by `claude plugin test .` beside the board's mod tests.
import { expect, test } from 'claude-code/testing'

import { boardModel, emptyLog, observe } from './board-model.ts'
import type { Envelope, HistoryPoint, Sighting } from './board-model.ts'
import epicFixture from './fixtures/prep-epic.ts'
import issueFixture from './fixtures/prep-issue.ts'
import { metroWave, PALETTE, segmentKind, SOURCE_LIMIT } from './metro-wave.ts'
import type { Seen } from './metro-wave.ts'

const EPIC = epicFixture as unknown as Envelope
const ISSUE = issueFixture as unknown as Envelope
const NOW = '2026-01-10T00:00:00Z'
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const wave = (envelope: Envelope, columns = 120) => metroWave(boardModel(envelope, emptyLog(), null), envelope, NOW, columns)
const count = (source: string, pattern: RegExp) => (source.match(pattern) ?? []).length
const esc = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const pt = (point: string, at: string): HistoryPoint => ({ point, at, source: 'test' })

test('a lane per board group, plus Integration when its line has points', async () => {
  const drawn = wave(EPIC)!
  for (const name of ['Wave 1', 'Wave 2', 'Wave 3', 'Integration']) expect(drawn.source).toContain(`data-lane="${name}"`)
  const without = clone(EPIC)
  without.epic!.integration_history = []
  expect(wave(without)!.source).not.toContain('data-lane="Integration"')
  expect(drawn.alt).toBe('Metro Wave of #150: 6 lines in 4 lanes, 1/5 merged, 2026-01-01 to 2026-01-10')
})

test('lines are labelled by number only, with no lane headings and no date labels', async () => {
  const drawn = wave(EPIC)!
  const labels = (drawn.source.match(/<text[^>]*>[^<]*<\/text>/g) ?? []).map(t => t.replace(/<[^>]+>/g, ''))
  // One label per line: the five stories' numbers, then the integration line's (no PR yet).
  expect(labels).toEqual(['#151', '#152', '#153', '#154', '#155', 'PR'])
  for (const story of EPIC.epic!.stories) if (story.title) expect(drawn.source).not.toContain(esc(story.title))
  expect(drawn.source).not.toContain('>Wave 1<')
  expect(drawn.source).not.toMatch(/>\d{4}-\d{2}-\d{2}</)
  const withPr = clone(EPIC)
  withPr.epic!.integration = { number: 160, draft: true, gate: null }
  expect(wave(withPr)!.source).toContain('data-lane="Integration" x="0"')
  expect(wave(withPr)!.source).toContain('>#160</text>')
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
  metroWave(boardModel(envelope, emptyLog(), null), envelope, NOW, 120, sightings)!.source
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
    const source = metroWave(boardModel(envelope, log, null), envelope, '2026-06-20T00:00:00Z', 120, [evaluator])!.source
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
  const source = metroWave(boardModel(e, emptyLog(), null), e, NOW, 120, [evaluator])!.source
  // On the integration line's row: the same y as its `pr-opened` station.
  const row = /data-point="pr-opened" cx="[\d.]+" cy="([\d.]+)"/g
  const rows = [...source.matchAll(row)].map(m => m[1])
  const sightingY = /data-point="evaluator-start" cx="[\d.]+" cy="([\d.]+)"/.exec(source)![1]
  expect(sightingY).toBe(rows[rows.length - 1])
  expect(count(metroWave(boardModel(e, emptyLog(), null), e, NOW, 120, [{ ...evaluator, pr: 999 }])!.source, /data-point="evaluator-start"/g)).toBe(0)
})

test('on the integration line the epic\'s own resolver start does not make its PR opening build', async () => {
  // The epic's resolver opens the draft integration PR; that line draws no commits.
  const e = clone(EPIC)
  e.epic!.integration = { number: 170, draft: true, gate: null }
  e.epic!.integration_history = [pt('pr-opened', '2026-01-05T00:00:00Z'), pt('evaluate', '2026-01-08T00:00:00Z')]
  const resolver: Sighting = { stage: 'resolver', issue: e.target.number, pr: null, cwd: EPIC.root!.path, at: '2026-01-04T00:00:00Z' }
  const source = metroWave(boardModel(e, emptyLog(), null), e, NOW, 120, [resolver])!.source
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
  expect(count(wave(EPIC)!.source, /data-mark="now"[^>]*stroke="#ef4444"/g)).toBe(1)
  const done = { ...clone(ISSUE), stage: 'terminal' }
  done.unit!.history = [pt('eligible', '2026-01-01T00:00:00Z'), pt('closed', '2026-01-03T00:00:00Z')]
  const drawn = wave(done)!
  expect(drawn.source).not.toContain('data-mark="now"')
  expect(drawn.alt).toBe('Metro Wave of #100: terminal, 2 history points, 2026-01-01 to 2026-01-03')
})

test('the markup is sized from the pane columns with a matching viewBox', async () => {
  const source = wave(EPIC, 100)!.source
  expect(source).toMatch(/^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" viewBox="0 0 800 \d+"/)
  expect(source).not.toMatch(/^<svg[^>]* width=/)
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

test('a lane name is escaped where the drawing carries it', async () => {
  const e = clone(EPIC)
  e.epic!.concurrency!.sets[0].name = '<script>&"'
  const source = wave(e)!.source
  expect(source).not.toContain('<script>')
  expect(source).toContain('data-lane="&lt;script&gt;&amp;&quot;"')
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
  const drawn = metroWave(boardModel(e, emptyLog(), null), e, 'not a time', 120)!
  expect(drawn.source).not.toContain('NaN')
  expect(drawn.alt).toBe('Metro Wave of #100: resolving, 0 history points, 1970-01-01 to 1970-01-01')
  const timed = metroWave(boardModel(ISSUE, emptyLog(), null), ISSUE, 'not a time', 120)!
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
})
