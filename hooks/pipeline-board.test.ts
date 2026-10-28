// The pipeline board's mod tests (#116, #156), run by `claude plugin test .` from the plugin folder (Claude Code >= 2.1.308, whose test kit mounts the Desktop band).
// Every hook registered with `on` here stands for the engine beneath the plugin.
import { expect, test } from 'claude-code/testing'

import { boardModel, emptyLog, feedPath, observe, paneRows, sightingKey, sightingOf } from './board-model.ts'
import type { BoardLog, Envelope, Sighting } from './board-model.ts'
import epicFixture from './fixtures/prep-epic.ts'
import issueFixture from './fixtures/prep-issue.ts'

const PLUGIN = 'ctrl-freaq'
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
    requestId: 'coordinator-overview',
    props: {
      title: 'coordinator-overview',
      isFocused: false,
      bodyColumns: 120,
      placement: 'dock',
      scroll: { offset: 0, bodyRows: 60 },
      view: {},
    },
  }) as const

// The engine's own band beneath the plugin, which every feed world draws: another plugin's tree.
const BENEATH = 'another band'
function bandBeneath(on: Parameters<Parameters<typeof test>[1]>[1]) {
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return h(Text, null, BENEATH)
  })
}

// The band above the chat input, which the engine raises on the terminal and desktop surfaces only.
const band = (surface: 'terminal' | 'desktop') =>
  ({
    plugin: PLUGIN,
    surface,
    component: 'AbovePrompt',
    props: {
      hasSurvey: false,
      isWorking: false,
      maxRows: 20,
      bodyColumns: 120,
      scroll: { offset: 0, bodyRows: 19 },
      view: {},
    },
  }) as const

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const count = (source: string, pattern: RegExp) => (source.match(pattern) ?? []).length

// The feed with every `history` null, as a failed history read leaves it: the band draws its summary.
const nullHistory = (envelope: Envelope): Envelope => {
  const e = clone(envelope)
  for (const story of e.epic?.stories ?? []) story.history = null
  if (e.epic) e.epic.integration_history = null
  if (e.unit) e.unit.history = null
  return e
}

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
  placed = true,
) {
  const seen = {
    status: [] as (string | undefined)[],
    opened: [] as string[],
    openArgs: [] as Record<string, unknown>[],
    closed: [] as string[],
    toasts: [] as string[],
  }
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
    seen.openArgs.push({ ...e })
    return { value: placed ? { isPlaced: true } : { isPlaced: false, reason: 'narrow terminal' } }
  })
  on('ui.close', (_$, e) => {
    seen.closed.push(`${e.id}:${e.origin.kind}`)
    return { value: undefined }
  })
  on('ui.toast', (_$, e) => {
    seen.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.invalidate', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  bandBeneath(on)
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
  expect(seen.status).toEqual(['coordinator · #150 · Wave 2 · 1/5 merged'])
  expect(seen.opened).toEqual(['coordinator-overview'])
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
  expect(seen.status).toEqual(['coordinator · #150 · Wave 2 · 1/5 merged'])
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
  story.next = { kind: 'open', issue: 150, command: '/ctrl-freaq:workspace-open 150', folder: null }
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

test('6d: a closed story outside the delivery log is never drawn as merged', async () => {
  const e = clone(EPIC)
  const story = e.epic!.stories.find(s => s.number === 151)!
  e.epic!.shipped = e.epic!.shipped.filter(n => n !== 151)
  expect(story.stage).toBe('merged-needs-close')
  const row = boardModel(e, emptyLog(), null).groups[0].rows[0]
  expect(row.number).toBe(151)
  expect(row.reason).toBe('closed — not in the delivery log')
  expect(row.rungs.some(r => r.state !== 'pending')).toBe(false)
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

test('9: session start prunes a terminal log with its orientation twin', async ($, on) => {
  const store = mapStore(on, {
    'board:octo/widgets#100': { ...emptyLog(), terminalAt: '2026-10-25T03:15:00Z' },
    'orientation:octo/widgets#100': { sha: 'x', text: 'done' },
    'board:octo/widgets#150': emptyLog(),
  })
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  expect([...store.keys()].sort()).toEqual(['board:octo/widgets#150'])
})

test('9b: the board registers /coordinator-overview then /coordinator-board, and an unplaced pane names the overview\'s in a toast', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) }, false)
  const registered: string[] = []
  on('command.register', (_$, e) => {
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  expect(registered).toEqual(['coordinator-overview', 'coordinator-board'])
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(seen.toasts).toEqual(['Pipeline overview ready: run /coordinator-overview to show it'])
})

test('9c: a terminal target clears the status line, label and all', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify({ ...clone(EPIC), stage: 'terminal' }) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(seen.status).toEqual([undefined])
})

test('10: a single issue draws one row with its phase position', async ($, on) => {
  mapStore(on, cachedOrientation(ISSUE))
  const seen = feedWorld(on, { '/tmp/gh-coordinator-100/envelope.json': JSON.stringify(ISSUE) })
  await $.tool.call({ tool: 'Bash', command: ISSUE_PIPE })
  expect(seen.status).toEqual(['coordinator · #100 · resolving · phase 2 of 3'])
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

// #156: Metro Wave in the band, the `boardStyle` setting, and show and hide.

const ISSUE_FEED = '/tmp/gh-coordinator-100/envelope.json'

test('12: on Desktop the band draws the Metro Wave Svg alone', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(band('desktop'))
  const svg = await ui.find({ type: 'Svg' })
  expect(svg).toBeDefined()
  expect(String(svg!.props.source).startsWith('<svg')).toBe(true)
  expect(String(svg!.props.alt)).toMatch(/^Metro Wave of #150: /)
  // No descriptive text, links or buttons of its own around the drawing: the one Text is the band beneath.
  expect((await ui.findAll({ type: 'Text' })).map(t => t.text)).toEqual([BENEATH])
  expect(await ui.find({ type: 'Link' })).toBeUndefined()
  expect(await ui.find({ type: 'Button' })).toBeUndefined()
  await ui.unmount()
})

// The overview pane draws the #116 text tree on every surface, whatever the style, real history included.
const overviewOnEverySurface = async ($: Parameters<Parameters<typeof test>[1]>[0], on: Parameters<Parameters<typeof test>[1]>[1]) => {
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount(pane(surface))
    expect(await ui.find({ type: 'Svg' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: 'Wave 2' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^#152 .*[●✓○] plan/ })).toBeDefined()
    expect(await ui.find({ type: 'Link', label: 'issue #152' })).toBeDefined()
    await ui.unmount()
  }
}

test('13: the overview pane draws the text board on every surface under boardStyle metro', overviewOnEverySurface)

test('14: the overview pane draws the text board on every surface under boardStyle text', { options: { boardStyle: 'text' } }, overviewOnEverySurface)

test('15: the terminal, boardStyle text and a null history draw the band\'s one summary line', async ($, on) => {
  mapStore(on, { ...cachedOrientation(EPIC), ...cachedOrientation(ISSUE) })
  const files: Record<string, string> = { [EPIC_FEED]: JSON.stringify(EPIC) }
  feedWorld(on, files)
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const summary = async (surface: 'terminal' | 'desktop', text: RegExp) => {
    const ui = await $.ui.mount(band(surface))
    expect(await ui.find({ type: 'Svg' })).toBeUndefined()
    expect((await ui.findAll({ type: 'Text', text })).length).toBe(1)
    await ui.unmount()
  }
  await summary('terminal', /^coordinator-board · #150 · Wave 2 · 1\/5 merged$/)
  files[EPIC_FEED] = JSON.stringify(nullHistory(EPIC))
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  await summary('desktop', /^coordinator-board · #150 · /)
  // One story's history null is enough: the drawing needs every line.
  const partial = clone(EPIC)
  partial.epic!.stories[1].history = null
  files[EPIC_FEED] = JSON.stringify(partial)
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  await summary('desktop', /^coordinator-board · #150 · /)
  files[ISSUE_FEED] = JSON.stringify(nullHistory(ISSUE))
  await $.tool.call({ tool: 'Bash', command: ISSUE_PIPE })
  await summary('desktop', /^coordinator-board · #100 · resolving · phase 2 of 3$/)
  files[ISSUE_FEED] = JSON.stringify(ISSUE)
  await $.tool.call({ tool: 'Bash', command: ISSUE_PIPE })
  const ui = await $.ui.mount(band('desktop'))
  expect(String((await ui.find({ type: 'Svg' }))!.props.alt)).toMatch(/^Metro Wave of #100: resolving, 5 history points/)
  await ui.unmount()
})

test('15b: boardStyle text draws the summary line in the Desktop band', { options: { boardStyle: 'text' } }, async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(band('desktop'))
  expect(await ui.find({ type: 'Svg' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'coordinator-board · #150 · Wave 2 · 1/5 merged' })).toBeDefined()
  await ui.unmount()
})

test('16: every open asks for paneRows rows and closes on Escape', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(seen.openArgs[0]).toEqual({ id: 'coordinator-overview', title: 'coordinator-overview · #150', rows: 18, closeOnEscape: true })
  expect(paneRows(EPIC)).toBe(18)
  const two = clone(EPIC)
  two.epic!.stories = two.epic!.stories.slice(0, 2)
  expect(paneRows(two)).toBe(12)
  const many = clone(EPIC)
  many.epic!.stories = Array.from({ length: 30 }, (_, i) => ({ ...EPIC.epic!.stories[0], number: 200 + i }))
  expect(paneRows(many)).toBe(40)
  expect(paneRows(ISSUE)).toBe(10)
  expect(paneRows(null)).toBe(10)
})

// A `$.ui.panes()` answer the test controls: the pane listed as the surface holds it.
function panesWorld(on: Parameters<Parameters<typeof test>[1]>[1], state: { listed: boolean; isPlaced: boolean; isShown: boolean }) {
  on('ui.panes', () => ({
    value: state.listed
      ? [{ id: 'coordinator-overview', title: 'coordinator-overview', isShown: state.isShown, isFocused: false, isPlaced: state.isPlaced }]
      : [],
  }))
}

test('17: Hide closes the pane and keeps a later prep run from reopening it; /coordinator-overview toggles', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const other = { ...clone(EPIC), target: { ...EPIC.target, number: 151 } }
  const third = { ...clone(EPIC), target: { ...EPIC.target, number: 152 } }
  const seen = feedWorld(on, {
    [EPIC_FEED]: JSON.stringify(EPIC),
    '/tmp/y/envelope.json': JSON.stringify(other),
    '/tmp/z/envelope.json': JSON.stringify(third),
  })
  const panes = { listed: true, isPlaced: true, isShown: true }
  panesWorld(on, panes)
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(seen.opened).toEqual(['coordinator-overview'])
  for (const surface of ['desktop', 'terminal'] as const) {
    const ui = await $.ui.mount(pane(surface))
    expect(await ui.find({ type: 'Button', key: 'hide' })).toBeDefined()
    await ui.unmount()
  }
  const ui = await $.ui.mount(pane('desktop'))
  await ui.press({ key: 'hide' })
  await ui.unmount()
  expect(seen.closed).toEqual(['coordinator-overview:plugin'])
  panes.listed = false
  // A new target's first sighting does not reopen a hidden pane, though the feed is read.
  seen.opened.length = 0
  const statuses = seen.status.length
  await $.tool.call({ tool: 'Bash', command: '/x/scripts/prep_coordinator.py 151 octo/widgets --scratch-dir /tmp/y' })
  expect(seen.status.length).toBe(statuses + 1)
  expect(seen.opened).toEqual([])
  // The command shows it again; a second run, with it shown, hides it.
  let ran = await $.command.run({ command: 'coordinator-overview', args: '' })
  expect(ran.text).toBe('Pipeline overview opened.')
  expect(seen.openArgs[seen.openArgs.length - 1]).toEqual({
    id: 'coordinator-overview', title: 'coordinator-overview · #151', rows: 18, closeOnEscape: true, focus: true,
  })
  // Shown again, the pane is no longer hidden: a new target's first sighting opens it as usual.
  seen.opened.length = 0
  await $.tool.call({ tool: 'Bash', command: '/x/scripts/prep_coordinator.py 152 octo/widgets --scratch-dir /tmp/z' })
  expect(seen.opened).toEqual(['coordinator-overview'])
  panes.listed = true
  ran = await $.command.run({ command: 'coordinator-overview', args: '' })
  expect(ran.text).toBe('Pipeline overview hidden.')
  expect(seen.closed).toEqual(['coordinator-overview:plugin', 'coordinator-overview:plugin'])
  // The band is the band's own: hiding the pane left it drawn.
  const drawn = await $.ui.mount(band('desktop'))
  expect(await drawn.find({ type: 'Svg' })).toBeDefined()
  await drawn.unmount()
})

test('18: after an unasked open left the pane undrawn, /coordinator-overview opens it rather than closing it', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) }, false)
  panesWorld(on, { listed: true, isPlaced: false, isShown: false })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ran = await $.command.run({ command: 'coordinator-overview', args: '' })
  expect(ran.text).toBe('Pipeline overview opened.')
  expect(seen.closed).toEqual([])
  expect(seen.opened).toEqual(['coordinator-overview', 'coordinator-overview'])
})

test('18b: a drawing over the Svg source limit draws the band\'s summary line on Desktop', async ($, on) => {
  const huge = clone(EPIC)
  const base = huge.epic!.stories[0]
  huge.epic!.stories = Array.from({ length: 2000 }, (_, i) => ({
    ...clone(base),
    number: 1000 + i,
    start: [],
    history: [{ point: 'eligible', at: '2026-01-01T00:00:00Z', source: 'issue.createdAt' }],
  }))
  huge.epic!.shipped = []
  huge.epic!.concurrency = { source: 'declared', fallback_reason: null, sets: [{ name: 'All', stories: huge.epic!.stories.map(s => s.number) }], malformed: [] }
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(huge) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(band('desktop'))
  expect(await ui.find({ type: 'Svg' })).toBeUndefined()
  expect((await ui.findAll({ type: 'Text', text: /^coordinator-board · #150 · 0\/2000 merged$/ })).length).toBe(1)
  await ui.unmount()
})

test('19: the band draws exactly the Svg, then the tree beneath it; the pane starts with Hide', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const ui = await $.ui.mount(band('desktop'))
  const tree = (await ui.find({ type: 'Box' }))!
  const kinds = (tree.children ?? []).filter(Boolean).map(child => child.type)
  expect(kinds).toEqual(['Svg', 'Text'])
  expect(await ui.find({ type: 'Text', text: BENEATH })).toBeDefined()
  await ui.unmount()
  for (const surface of ['terminal', 'desktop'] as const) {
    const text = await $.ui.mount(pane(surface))
    const first = ((await text.find({ type: 'Box' }))!.children ?? []).filter(Boolean)[0]
    expect(first.type).toBe('Button')
    await text.unmount()
  }
})

test('20: the band passes through before any prep and while a survey holds it', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  const passes = async (input: ReturnType<typeof band>) => {
    const ui = await $.ui.mount(input)
    expect(await ui.find({ type: 'Svg' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /coordinator-board/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: BENEATH })).toBeDefined()
    await ui.unmount()
  }
  await passes(band('desktop'))
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const surveyed = band('desktop')
  await passes({ ...surveyed, props: { ...surveyed.props, hasSurvey: true } } as unknown as ReturnType<typeof band>)
})

test('21: the band draws at a terminal stage; /coordinator-board hides and shows it, and a prep run leaves it hidden', async ($, on) => {
  mapStore(on, cachedOrientation(EPIC))
  const closed = { ...clone(EPIC), stage: 'terminal' }
  const seen = feedWorld(on, { [EPIC_FEED]: JSON.stringify(closed) })
  panesWorld(on, { listed: true, isPlaced: true, isShown: true })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  let ui = await $.ui.mount(band('desktop'))
  expect(await ui.find({ type: 'Svg' })).toBeDefined()
  await ui.unmount()
  ui = await $.ui.mount(band('terminal'))
  expect(await ui.find({ type: 'Text', text: /^coordinator-board · #150 · / })).toBeDefined()
  await ui.unmount()
  const opens = seen.opened.length
  let ran = await $.command.run({ command: 'coordinator-board', args: '' })
  expect(ran.text).toBe('Pipeline board hidden.')
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  for (const surface of ['desktop', 'terminal'] as const) {
    ui = await $.ui.mount(band(surface))
    expect(await ui.find({ type: 'Svg' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /coordinator-board/ })).toBeUndefined()
    await ui.unmount()
  }
  ran = await $.command.run({ command: 'coordinator-board', args: '' })
  expect(ran.text).toBe('Pipeline board shown.')
  ui = await $.ui.mount(band('desktop'))
  expect(await ui.find({ type: 'Svg' })).toBeDefined()
  await ui.unmount()
  // The pane's opens and closes are untouched by either.
  expect(seen.opened.length).toBe(opens)
  expect(seen.closed).toEqual([])
})

test('22: the band draws the Integration lane, and the overview pane keeps the integration row', async ($, on) => {
  const e = { ...clone(EPIC), stage: 'epic-integration' }
  e.epic!.integration = { number: 160, draft: true, gate: { reason: 'stories-open' } }
  mapStore(on, cachedOrientation(EPIC))
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(e) })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  const drawn = await $.ui.mount(band('desktop'))
  expect(String((await drawn.find({ type: 'Svg' }))!.props.source)).toContain('Integration')
  await drawn.unmount()
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Text', text: /^PR #160 \(draft\) · stories-open/ })).toBeDefined()
  expect(await ui.find({ type: 'Link', label: 'integration PR' })).toBeDefined()
  await ui.unmount()
})

// Sightings (#157): each pipeline stage session's start, recorded at `skill.prompt`.
const AT = '2026-10-28T01:00:00.000Z'
const expanded = (args: string) => `# resolver — router\n\nBody text.\n\nARGUMENTS: ${args}`

test('23: sightingOf reads a stage skill\'s number, and nothing else', async () => {
  const cwd = '/repo/.worktrees/157-x'
  expect(sightingOf('ctrl-freaq:resolver', 'ctrl-freaq', expanded('#157'), cwd, AT)).toEqual({ stage: 'resolver', issue: 157, pr: null, cwd, at: AT })
  expect(sightingOf('ctrl-freaq:planner', 'ctrl-freaq', expanded('revise #157'), cwd, AT)?.issue).toBe(157)
  expect(sightingOf('planner', 'ctrl-freaq', expanded('https://github.com/o/r/issues/157'), cwd, AT)?.issue).toBe(157)
  expect(sightingOf('ctrl-freaq:resolver', 'ctrl-freaq', expanded('157'), cwd, AT)?.issue).toBe(157)
  expect(sightingOf('ctrl-freaq:evaluator', 'ctrl-freaq', expanded('#161'), cwd, AT)).toEqual({ stage: 'evaluator', issue: null, pr: 161, cwd, at: AT })
  expect(sightingOf('ctrl-freaq:evaluator', 'ctrl-freaq', expanded('https://github.com/o/r/pull/161/files'), cwd, AT)).toBeNull()
  expect(sightingOf('ctrl-freaq:evaluator', 'ctrl-freaq', expanded('https://github.com/o/r/pull/161'), cwd, AT)?.pr).toBe(161)
  // The last ARGUMENTS line answers: an earlier one quoted in a skill's body is not the invocation's.
  expect(sightingOf('ctrl-freaq:resolver', 'ctrl-freaq', `ARGUMENTS: #1\n${expanded('#157')}`, cwd, AT)?.issue).toBe(157)
  expect(sightingOf('ctrl-freaq:coordinator', 'ctrl-freaq', expanded('#136'), cwd, AT)).toBeNull()
  expect(sightingOf('planner', 'other-plugin', expanded('#157'), cwd, AT)).toBeNull()
  expect(sightingOf('other-plugin:planner', 'other-plugin', expanded('#157'), cwd, AT)).toBeNull()
  expect(sightingOf('ctrl-freaq:resolver', 'ctrl-freaq', expanded('#157'), undefined, AT)).toBeNull()
  expect(sightingOf('ctrl-freaq:resolver', 'ctrl-freaq', expanded('continue'), cwd, AT)).toBeNull()
  expect(sightingOf('ctrl-freaq:resolver', 'ctrl-freaq', 'no arguments here', cwd, AT)).toBeNull()
})

// The engine beneath the plugin at `skill.prompt`: the skill's text as the plugin left it.
function skillEngine(on: Parameters<Parameters<typeof test>[1]>[1]) {
  const logged: string[] = []
  on('skill.prompt', (_$, e) => ({ text: e.text }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('ui.log', (_$, e) => {
    logged.push(e.text)
    return { value: undefined }
  })
  return logged
}

test('24: the skill.prompt hook stores one sighting key and passes the text through unchanged', async ($, on) => {
  const store = mapStore(on)
  skillEngine(on)
  await $.session.start({ cwd: '/repo/.worktrees/157-x', surface: 'desktop', isInteractive: true })
  const text = expanded('#157')
  const out = await $.skill.prompt({ skill: 'ctrl-freaq:resolver', text, pluginName: 'ctrl-freaq', frontmatter: {}, source: 'plugin' })
  expect(out.text).toBe(text)
  const keys = [...store.keys()].filter(k => k.startsWith('sighting:'))
  expect(keys.length).toBe(1)
  const value = store.get(keys[0]) as Sighting
  expect(value).toMatchObject({ stage: 'resolver', issue: 157, pr: null, cwd: '/repo/.worktrees/157-x' })
  expect(keys[0]).toBe(sightingKey(value))
  const other = await $.skill.prompt({ skill: 'commit', text: 'Commit it.', frontmatter: {}, source: 'built-in' })
  expect(other.text).toBe('Commit it.')
  expect([...store.keys()].filter(k => k.startsWith('sighting:')).length).toBe(1)
})

test('24b: a rejecting store write is logged, and the skill\'s text still passes through unchanged', async ($, on) => {
  on('store.keys', () => ({ value: [] }))
  on('store.set', () => {
    throw new Error('store full')
  })
  const logged = skillEngine(on)
  await $.session.start({ cwd: '/repo', surface: 'desktop', isInteractive: true })
  const text = expanded('#157')
  const out = await $.skill.prompt({ skill: 'ctrl-freaq:planner', text, pluginName: 'ctrl-freaq', frontmatter: {}, source: 'plugin' })
  expect(out.text).toBe(text)
  // The hook's own catch, not the engine's pass-through of a failed hook.
  expect(logged.some(m => m.startsWith('pipeline-board: sighting not recorded'))).toBe(true)
})

test('25: session start prunes a 91-day-old sighting and keeps a 1-day-old one', async ($, on) => {
  const day = 86400000
  const old: Sighting = { stage: 'planner', issue: 1, pr: null, cwd: '/repo', at: new Date(Date.now() - 91 * day).toISOString() }
  const recent: Sighting = { stage: 'planner', issue: 2, pr: null, cwd: '/repo', at: new Date(Date.now() - day).toISOString() }
  const store = mapStore(on, { [sightingKey(old)]: old, [sightingKey(recent)]: recent, 'sighting:junk': 'not a sighting' })
  skillEngine(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  expect([...store.keys()].sort()).toEqual([sightingKey(recent)])
})

test('26: the band draws the sightings stored before the feed read, and no later one until the next read', async ($, on) => {
  // #152's plan was posted 2026-01-03 and #153's too: a planner start an hour before each opens a plan run.
  const sighted = (issue: number): Sighting => ({ stage: 'planner', issue, pr: null, cwd: EPIC.root!.path, at: '2026-01-02T23:00:00.000Z' })
  const store = mapStore(on, { ...cachedOrientation(EPIC), [sightingKey(sighted(152))]: sighted(152) })
  feedWorld(on, { [EPIC_FEED]: JSON.stringify(EPIC) })
  const plans = async () => {
    const ui = await $.ui.mount(band('desktop'))
    const source = String((await ui.find({ type: 'Svg' }))!.props.source)
    await ui.unmount()
    return count(source, /data-kind="plan"/g)
  }
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(await plans()).toBe(1)
  // A session that starts after the feed read changes nothing until the next prep read.
  store.set(sightingKey(sighted(153)), sighted(153))
  expect(await plans()).toBe(1)
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(await plans()).toBe(2)
  // A sighting from another repo's folder is not this run's.
  store.set(sightingKey(sighted(154)), { ...sighted(154), cwd: '/elsewhere' })
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(await plans()).toBe(2)
})

test('27: session start prunes a stored value whose number is on the wrong field or missing', async ($, on) => {
  const at = new Date(Date.now() - 86400000).toISOString()
  const good: Sighting = { stage: 'evaluator', issue: null, pr: 161, cwd: '/repo', at }
  const store = mapStore(on, {
    [sightingKey(good)]: good,
    'sighting:a': { stage: 'evaluator', issue: null, cwd: '/repo', at },
    'sighting:b': { stage: 'resolver', issue: 1, pr: 2, cwd: '/repo', at },
    'sighting:c': { stage: 'evaluator', issue: 161, pr: null, cwd: '/repo', at },
    'sighting:d': { stage: 'planner', issue: null, pr: 161, cwd: '/repo', at },
    'sighting:e': { stage: 'planner', issue: 1.5, pr: null, cwd: '/repo', at },
  })
  skillEngine(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  expect([...store.keys()]).toEqual([sightingKey(good)])
})

test('28: a failed sighting read keeps the last snapshot, and the run still lands', async ($, on) => {
  const sighted = (issue: number): Sighting => ({ stage: 'planner', issue, pr: null, cwd: EPIC.root!.path, at: '2026-01-02T23:00:00.000Z' })
  const map = new Map<string, unknown>(Object.entries({ ...cachedOrientation(EPIC), [sightingKey(sighted(152))]: sighted(152) }))
  let failSightings = false
  on('store.get', (_$, e) => {
    if (failSightings && e.key.startsWith('sighting:')) throw new Error('store busy')
    return { value: map.get(e.key) }
  })
  on('store.set', (_$, e) => {
    map.set(e.key, e.value)
    return { value: undefined }
  })
  on('store.delete', (_$, e) => {
    map.delete(e.key)
    return { value: undefined }
  })
  on('store.keys', () => ({ value: [...map.keys()] }))
  const second = clone(EPIC)
  second.stage = 'terminal'
  const files: Record<string, string> = { [EPIC_FEED]: JSON.stringify(EPIC) }
  const seen = feedWorld(on, files)
  const plans = async () => {
    const ui = await $.ui.mount(band('desktop'))
    const svg = await ui.find({ type: 'Svg' })
    await ui.unmount()
    return count(String(svg!.props.source), /data-kind="plan"/g)
  }
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(await plans()).toBe(1)
  failSightings = true
  map.set(sightingKey(sighted(153)), sighted(153))
  files[EPIC_FEED] = JSON.stringify(second)
  await $.tool.call({ tool: 'Bash', command: EPIC_PIPE })
  expect(await plans()).toBe(1)
  // The second (terminal) envelope landed in the band too: a terminal target draws no now-line.
  const ui = await $.ui.mount(band('desktop'))
  expect(String((await ui.find({ type: 'Svg' }))!.props.source)).not.toContain('data-mark="now"')
  await ui.unmount()
  expect(seen.status.length).toBe(2)
})
