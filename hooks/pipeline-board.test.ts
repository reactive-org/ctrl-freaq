// The pipeline board's mod tests (#116), run by `claude plugin test hooks` (Claude Code >= 2.1.287).
// Every hook registered with `on` here stands for the engine beneath the plugin.
import { expect, test } from 'claude-code/testing'

import { boardModel, emptyLog, feedPath, observe } from './board-model.ts'
import type { BoardLog, Envelope } from './board-model.ts'
import epicFixture from './fixtures/prep-epic.ts'
import issueFixture from './fixtures/prep-issue.ts'

const PLUGIN = 'github-pipeline'
const EPIC = epicFixture as unknown as Envelope
const ISSUE = issueFixture as unknown as Envelope
const EPIC_PIPE = "/x/scripts/prep_coordinator.py 150 octo/widgets | python3 -c 'import json,sys'"
const ISSUE_PIPE = "/x/scripts/prep_coordinator.py 100 octo/widgets | python3 -c 'import json,sys'"
const EPIC_FEED = '/tmp/gh-coordinator-150/envelope.json'

const pane = (surface: 'terminal' | 'desktop' | 'vscode' | 'mobile') =>
  ({
    plugin: PLUGIN,
    surface,
    component: 'Pane',
    requestId: 'pipeline-board',
    props: {
      title: 'Pipeline',
      isFocused: false,
      bodyColumns: 120,
      placement: 'dock',
      scroll: { offset: 0, bodyRows: 60 },
      view: {},
    },
  }) as const

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

// A `$.store` the test can read back: `mock.store` cannot show what was saved.
function mapStore(on: Parameters<Parameters<typeof test>[1]>[1], seed: Record<string, unknown> = {}) {
  const map = new Map<string, unknown>(Object.entries(seed))
  on('store.get', (_$, e) => ({ value: map.get(e.key) }))
  on('store.set', (_$, e) => {
    map.set(e.key, e.value)
    return { value: undefined }
  })
  on('store.delete', (_$, e) => {
    map.delete(e.key)
    return { value: undefined }
  })
  on('store.keys', () => ({ value: [...map.keys()] }))
  return map
}

// The engine around a feed run: the tool answers, the feed file reads as `files` holds it.
function feedWorld(
  on: Parameters<Parameters<typeof test>[1]>[1],
  files: Record<string, string>,
  copy: { isCopied: true } | { isCopied: false; reason: 'no-surface' | 'no-clipboard' | 'refused' } = { isCopied: true },
  placed = true,
) {
  const seen = { status: [] as (string | undefined)[], opened: [] as string[], toasts: [] as string[], copied: [] as string[] }
  on('tool.call', () => ({ result: { stdout: '', stderr: '', interrupted: false } }))
  on('fs.read', (_$, e) => {
    if (!(e.path in files)) throw new Error(`no such file: ${e.path}`)
    return { value: files[e.path] }
  })
  on('ui.status', (_$, e) => {
    seen.status.push(e.text)
    return { value: undefined }
  })
  on('ui.open', (_$, e) => {
    seen.opened.push(e.id)
    return { value: placed ? { isPlaced: true } : { isPlaced: false, reason: 'narrow terminal' } }
  })
  on('ui.toast', (_$, e) => {
    seen.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.copy', (_$, e) => {
    seen.copied.push(e.text)
    return { value: copy }
  })
  on('ui.invalidate', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  // A cached orientation keeps the model out of the board tests.
  return seen
}

const cachedOrientation = (envelope: Envelope) => ({
  [`orientation:${envelope.repo}#${envelope.target.number}`]: { sha: envelope.target.body_sha256, text: 'cached' },
})

test('1: an epic prep, piped, draws its waves, gates and status from the feed file', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(seen.status).toEqual(['#150 · Wave 2 · 1/5 merged'])
  expect(seen.opened).toEqual(['pipeline-board'])
  // Box, Text, Button and Link only, so it draws on every surface that has a pane.
  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount(pane(surface))
    for (const name of ['Wave 1', 'Wave 2', 'Wave 3']) {
      expect(await ui.find({ type: 'Text', text: name })).toBeDefined()
    }
    const row152 = await ui.findAll({ type: 'Text', text: /next: evaluator/ })
    expect(row152.length).toBe(1)
    expect(await ui.find({ type: 'Text', text: /start: #151/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /start: #152 \(order\)/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /concurrency: serial/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('2: --scratch-dir and a redirect draw the same board; an unreadable feed keeps it', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const files: Record<string, string> = { '/tmp/x/envelope.json': JSON.stringify(EPIC) }
  const seen = feedWorld(on, files)
  await $.tool.call({ tool: 'Bash', command: '/x/prep_coordinator.py 150 octo/widgets --scratch-dir /tmp/x > /dev/null' })
  expect(seen.status).toEqual(['#150 · Wave 2 · 1/5 merged'])
  delete files['/tmp/x/envelope.json']
  await $.tool.call({ tool: 'Bash', command: '/x/prep_coordinator.py 150 octo/widgets --scratch-dir /tmp/x > /dev/null' })
  expect(seen.status.length).toBe(1)
  const ui = await $.ui.mount(pane('terminal'))
  expect(await ui.find({ type: 'Text', text: 'Wave 2' })).toBeDefined()
  await ui.unmount()
})

test('3: any other Bash call passes through untouched', async ($, on) => {
  const calls: string[] = []
  const record = (name: string, value: unknown) => () => {
    calls.push(name)
    return { value }
  }
  for (const name of ['store.get', 'store.set', 'store.delete', 'store.keys'] as const) on(name, record(name, undefined))
  on('fs.read', record('fs.read', ''))
  on('ui.open', record('ui.open', { isPlaced: true }))
  on('ui.status', record('ui.status', undefined))
  on('ui.toast', record('ui.toast', undefined))
  on('ui.copy', record('ui.copy', { isCopied: true }))
  on('ui.log', record('ui.log', undefined))
  on('ui.invalidate', record('ui.invalidate', undefined))
  on('model.complete', record('model.complete', { isAnswered: false, reason: 'empty-reply' }))
  on('command.register', record('command.register', { command: 'x' }))
  const answer = { result: { stdout: 'hello', stderr: '', interrupted: false } }
  on('tool.call', () => answer)
  const ran = await $.tool.call({ tool: 'Bash', command: 'echo hello | tr a-z A-Z' })
  expect(calls).toEqual([])
  expect(ran.result).toEqual(answer.result)
})

test('4: a serial fallback draws one group and its banner', async ($, on) => {
  const fallback = clone(EPIC)
  fallback.epic!.concurrency = {
    source: 'serial-fallback',
    fallback_reason: 'malformed',
    sets: [],
    malformed: [{ line: 7, reason: 'unknown gate "after"' }],
  }
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(fallback) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Text', text: 'Serial order' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Wave 1' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'concurrency: serial fallback — malformed' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'line 7: unknown gate "after"' })).toBeDefined()
  await ui.unmount()
})

test('5: the log records stage changes, rework and per-rung times', async () => {
  const at153 = (stage: string) => {
    const e = clone(EPIC)
    e.epic!.stories.find(s => s.number === 153)!.stage = stage
    return e
  }
  let log: BoardLog = observe(emptyLog(), at153('pr-ready'), '2026-10-25T01:00:00Z')
  log = observe(log, at153('resolving'), '2026-10-25T02:00:00Z')
  const row = boardModel(at153('resolving'), log, null).groups[1].rows.find(r => r.number === 153)!
  expect(row.rework).toBe(1)
  expect(row.firstSeen).toBe('2026-10-25T02:00:00Z')
  expect(row.rungs.find(r => r.state === 'current')?.label).toBe('build')
  expect(observe(log, at153('resolving'), '2026-10-25T03:00:00Z').entries['153'].length).toBe(2)

  const issueAt = (stage: string) => ({ ...clone(ISSUE), stage })
  let run = emptyLog()
  const steps: [string, string][] = [
    ['needs-plan', '2026-10-25T00:00:00Z'],
    ['needs-workspace', '2026-10-25T00:30:00Z'],
    ['resolving', '2026-10-25T01:00:00Z'],
    ['pr-ready', '2026-10-25T03:00:00Z'],
    ['terminal', '2026-10-25T03:15:00Z'],
  ]
  for (const [stage, at] of steps) run = observe(run, issueAt(stage), at)
  expect(run.terminalAt).toBe('2026-10-25T03:15:00Z')
  expect(observe(run, issueAt('terminal'), '2026-10-25T04:00:00Z').terminalAt).toBe('2026-10-25T03:15:00Z')
  expect(observe(run, issueAt('resolving'), '2026-10-25T05:00:00Z').terminalAt).toBeNull()
  const done = boardModel(issueAt('terminal'), run, null).groups[0].rows[0]
  expect(done.durations).toEqual(['plan 30m', 'workspace 30m', 'build 2h', 'evaluate 15m'])
  // A story's own log reaching terminal gives its row the same line, ended at its own close.
  const shipped = clone(EPIC)
  let storyLog = emptyLog()
  for (const [stage, at] of [['pr-ready', '2026-10-25T01:00:00Z'], ['terminal', '2026-10-25T01:40:00Z']]) {
    shipped.epic!.stories.find(s => s.number === 151)!.stage = stage
    storyLog = observe(storyLog, shipped, at)
  }
  const row151 = boardModel(shipped, storyLog, null).groups[0].rows[0]
  expect(row151.durations).toEqual(['evaluate 40m'])
  // Done up to the last rung the log saw, plus closed: it never saw merged.
  expect(done.rungs.filter(r => r.state === 'done').map(r => r.label)).toEqual(['plan', 'workspace', 'build', 'evaluate', 'closed'])
})

test('6: an unplanned story whose epic worktree opens first is still at plan', async () => {
  const e = clone(EPIC)
  const story = e.epic!.stories.find(s => s.number === 154)!
  story.stage = 'needs-workspace'
  story.next = { kind: 'open', issue: 150, command: '/github-pipeline:workspace-open 150', folder: null }
  const row = boardModel(e, emptyLog(), null).groups[2].rows.find(r => r.number === 154)!
  expect(row.rungs.find(r => r.state === 'current')?.label).toBe('plan')
  expect(row.rungs.filter(r => r.state === 'done')).toEqual([])
})

test('6b: a gated story shows its last-seen rung with the rungs before it done', async () => {
  const e = clone(EPIC)
  const story = e.epic!.stories.find(s => s.number === 154)!
  let log = observe(emptyLog(), { ...e, epic: { ...e.epic!, stories: [{ ...story, stage: 'resolving' }] } }, '2026-10-25T01:00:00Z')
  story.stage = 'gated'
  story.gate = { reason: 'open-pr-other-stale' }
  log = observe(log, e, '2026-10-25T02:00:00Z')
  const row = boardModel(e, log, null).groups[2].rows.find(r => r.number === 154)!
  expect(row.rungs.find(r => r.state === 'current')?.label).toBe('build')
  expect(row.rungs.filter(r => r.state === 'done').map(r => r.label)).toEqual(['plan', 'workspace'])
  expect(row.reason).toBe('open-pr-other-stale')
})

test('6c: a shipped story keeps every rung done whatever its log saw', async () => {
  const e = clone(EPIC)
  const story = e.epic!.stories.find(s => s.number === 151)!
  story.stage = 'pr-ready'
  let log = observe(emptyLog(), e, '2026-10-25T01:00:00Z')
  story.stage = 'terminal'
  log = observe(log, e, '2026-10-25T02:00:00Z')
  const row = boardModel(e, log, null).groups[0].rows[0]
  expect(row.collapsed).toBe(true)
  expect(row.rungs.every(r => r.state === 'done')).toBe(true)
  expect(row.firstSeen).toBeNull()
})

test('7a: an answered orientation renders labelled and is cached by sha', async ($, on) => {
  const store = mapStore(on)
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC), [EPIC.target.body_path!]: 'Sort the inbox.' })
  on('model.complete', () => ({
    value: { isAnswered: true, text: 'The inbox can be sorted by sender, date and tag.', usage: {} },
  }))
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const first = await $.ui.mount(pane('terminal'))
  await first.unmount()
  expect(store.get('orientation:octo/widgets#150')).toEqual({
    sha: EPIC.target.body_sha256,
    text: 'The inbox can be sorted by sender, date and tag.',
  })
  const ui = await $.ui.mount(pane('terminal'))
  expect(await ui.find({ type: 'Text', text: 'Generated summary' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /sorted by sender/ })).toBeDefined()
  await ui.unmount()
})

test('7b: a failed orientation shows the title, is cached as failed and is not retried', async ($, on) => {
  const store = mapStore(on)
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC), [EPIC.target.body_path!]: 'Sort the inbox.' })
  let asked = 0
  on('model.complete', () => {
    asked++
    return { value: { isAnswered: false, reason: 'empty-reply', usage: {} } }
  })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  for (let i = 0; i < 2; i++) {
    const ui = await $.ui.mount(pane('terminal'))
    expect(await ui.find({ type: 'Text', text: 'Epic: Inbox sorting' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Generated summary' })).toBeUndefined()
    await ui.unmount()
  }
  expect(asked).toBe(1)
  expect(store.get('orientation:octo/widgets#150')).toEqual({ sha: EPIC.target.body_sha256, failed: true })
})

test('7c: a refused orientation call is cached as failed too', async ($, on) => {
  const store = mapStore(on)
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC), [EPIC.target.body_path!]: 'Sort the inbox.' })
  on('model.complete', () => ({ deny: 'model blocked by policy' }))
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(pane('terminal'))
  await ui.unmount()
  expect(store.get('orientation:octo/widgets#150')).toEqual({ sha: EPIC.target.body_sha256, failed: true })
})

test('7e: concurrent draws start one orientation call, not one each', async ($, on) => {
  mapStore(on)
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC), [EPIC.target.body_path!]: 'Sort the inbox.' })
  let asked = 0
  on('model.complete', () => {
    asked++
    return { value: { isAnswered: true, text: 'Inbox sorting.', usage: {} } }
  })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const drawn = await Promise.all([$.ui.mount(pane('terminal')), $.ui.mount(pane('desktop'))])
  for (const ui of drawn) await ui.unmount()
  expect(asked).toBe(1)
})

test('7d: a cached orientation renders with no model call', async ($, on) => {
  mapStore(on, {
    'orientation:octo/widgets#150': { sha: EPIC.target.body_sha256, text: 'Sorting for the inbox.' },
  })
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Text', text: 'Sorting for the inbox.' })).toBeDefined()
  await ui.unmount()
})

test('8b: a copy the surface cannot make says so with the command', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) }, { isCopied: false, reason: 'no-clipboard' })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(pane('desktop'))
  await ui.press({ key: 'copy:152' })
  expect(seen.toasts).toEqual(['Not copied (no-clipboard): /github-pipeline:evaluator #160'])
  await ui.unmount()
})

test('8: "Copy next" copies the story\'s next command', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(pane('terminal'))
  await ui.press({ key: 'copy:152' })
  expect(seen.copied).toEqual(['/github-pipeline:evaluator #160'])
  await ui.unmount()
})

test('9: session start prunes a terminal log with its orientation twin', async ($, on) => {
  const store = mapStore(on, {
    'board:octo/widgets#100': { ...emptyLog(), terminalAt: '2026-10-25T03:15:00Z' },
    'orientation:octo/widgets#100': { sha: 'x', text: 'done' },
    'board:octo/widgets#150': emptyLog(),
  })
  on('command.register', () => ({ value: { command: 'coordinator-board' } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  expect([...store.keys()].sort()).toEqual(['board:octo/widgets#150'])
})

test('9b: the board registers /coordinator-board, and an unplaced pane names it in a toast', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) }, { isCopied: true }, false)
  const registered: string[] = []
  on('command.register', (_$, e) => {
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  expect(registered).toEqual(['coordinator-board'])
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(seen.toasts).toEqual(['Pipeline board ready: run /coordinator-board to show it'])
})

test('10: a single issue draws one row with its phase position', async ($, on) => {
  mapStore(on, cachedOrientation(ISSUE))
  const seen = feedWorld(on, { '/tmp/gh-coordinator-100/envelope.json': JSON.stringify(ISSUE) })
  await $.tool.call({ tool: 'Bash', command: ISSUE_PIPE })
  expect(seen.status).toEqual(['#100 · resolving · phase 2 of 3'])
  const ui = await $.ui.mount(pane('terminal'))
  expect((await ui.findAll({ type: 'Text', text: /^#\d+ .*[●✓○] plan/ })).length).toBe(1)
  expect(await ui.find({ type: 'Text', text: 'build: phase 1 of 3' })).toBeDefined()
  await ui.unmount()
})

test('11: the feed path comes from the command text', async () => {
  expect(feedPath(EPIC_PIPE)).toBe(EPIC_FEED)
  expect(feedPath("'/x/scripts/prep_coordinator.py' 150 octo/widgets")).toBe(EPIC_FEED)
  expect(feedPath('/x/prep_coordinator.py 150 octo/widgets --scratch-dir=/tmp/x')).toBe('/tmp/x/envelope.json')
  expect(feedPath('/x/prep_coordinator.py 150 octo/widgets --scratch-dir "/tmp/x"')).toBe('/tmp/x/envelope.json')
  expect(feedPath('/x/prep_coordinator.py "$N" octo/widgets')).toBeUndefined()
})
