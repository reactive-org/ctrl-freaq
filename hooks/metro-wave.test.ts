// Metro Wave's pure drawing (#156), run by `claude plugin test .` beside the board's mod tests.
import { expect, test } from 'claude-code/testing'

import { boardModel, emptyLog } from './board-model.ts'
import type { Envelope, HistoryPoint } from './board-model.ts'
import epicFixture from './fixtures/prep-epic.ts'
import issueFixture from './fixtures/prep-issue.ts'
import { metroWave, PALETTE, segmentKind, SOURCE_LIMIT } from './metro-wave.ts'

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

test('a planning gap with no sighting is dashed, never solid; build and evaluate are solid', async () => {
  expect(segmentKind('eligible', false)).toBe('wait')
  expect(segmentKind('plan-posted', false)).toBe('wait')
  expect(segmentKind('pr-opened', false)).toBe('wait')
  expect(segmentKind('pr-opened', true)).toBe('build')
  expect(segmentKind('commit', true)).toBe('build')
  expect(segmentKind('rework', true)).toBe('build')
  expect(segmentKind('evaluate', true)).toBe('evaluate')
  expect(segmentKind('merged', true)).toBeNull()
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
