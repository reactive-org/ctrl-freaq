// The pipeline board (#116, #156): a read-only view of the coordinator prep's facts. It reads the
// feed file each `prep_coordinator.py` run writes (never the tool's stdout), draws two surfaces and
// one status line, and keeps a small log of the stage changes it saw. The `ctrl-freaq-overview`
// pane is the #116 text board, on every surface and style. The `ctrl-freaq-board` band above the
// chat input draws Metro Wave — one `Svg` from the pure `metro-wave.ts`, beside a column of line labels
// whose hover cards share each line's hover scope with its overview row (#157) — on Desktop, with
// `boardStyle` at its default `metro` and the prep's `history` read, and a one-line summary wherever
// else the engine raises the band. Each pipeline stage session's start is recorded as a sighting in
// `$.store` (#157), and the band starts that stage's segment there. It writes nothing to GitHub or
// the repository: the only `$` namespaces it calls are ui, store, fs.read, model.complete and
// command, and an offline scan in tests/test_prep_coordinator.py pins that. Every decision lives in
// the pure `board-model.ts`; this module only calls the host around it.
import type { Elements, EngineInterface, Register } from 'claude-code'

import {
  bandText,
  boardKey,
  boardModel,
  commandIssue,
  emptyLog,
  feedPath,
  isSighting,
  issueUrl,
  observe,
  orientationRequest,
  paneRows,
  SCRIPT,
  SIGHTING_PREFIX,
  SIGHTING_TTL_DAYS,
  sightingKey,
  sightingOf,
  statusText,
} from './board-model.ts'
import type { BoardLog, Envelope, Row, Sighting } from './board-model.ts'
import { lineKey, metroWave } from './metro-wave.ts'
import type { Wave, WaveFrame, WaveRow } from './metro-wave.ts'

const PANE = 'ctrl-freaq-overview'
const OVERVIEW_COMMAND = 'ctrl-freaq-overview'
const BAND_COMMAND = 'ctrl-freaq-board'
const GLYPH = { current: '●', done: '✓', pending: '○' } as const

type Orientation = { sha: string; text?: string; failed?: boolean }

// Module state, by design: the last envelope seen, the panes opened this session, whether the
// operator hid the pane (Hide, Esc or `/ctrl-freaq-overview`), so a later prep run does not reopen
// it, and whether they hid the band (`/ctrl-freaq-board`, its only show and hide). A reload starts
// them over; the next prep run refills them.
let current: Envelope | null = null
// The sightings as they stood when `current` was read, so a session that starts between prep runs
// changes nothing the band draws until the next one; and this session's folder, a sighting's repo.
let sightings: Sighting[] = []
let sessionCwd: string | undefined
// Metro Wave's frozen frame per board key (#157), taken at each feed read with the board log as it stood
// then, and the band's last drawing keyed by the frame's time and the band's width: every render between
// prep runs hands the band the same `source`, so its SMIL motion runs on unrestarted. Only a width change
// rebuilds it, `elapsedSeconds` into the frame.
const frames = new Map<string, WaveFrame>()
let bandLog: BoardLog | null = null
let bandMemo: { key: string; wave: Wave | null } | null = null
let hidden = false
let bandHidden = false
const opened = new Set<string>()
const inFlight = new Set<string>()

// Metro Wave draws once every line it needs has its `history` read: a null one draws the band's summary.
const historyReady = (envelope: Envelope): boolean =>
  envelope.epic
    ? envelope.epic.stories.every(story => Array.isArray(story.history))
    : Array.isArray(envelope.unit?.history)

// The operator's hide, from the Hide button or `/ctrl-freaq-overview`: hidden for the rest of the session.
const hidePane = async ($: EngineInterface): Promise<void> => {
  hidden = true
  await $.ui.close({ id: PANE })
}

const openArgs = (envelope: Envelope | null, focus: boolean) => ({
  id: PANE,
  title: envelope ? `ctrl-freaq-overview · #${envelope.target.number}` : 'ctrl-freaq-overview',
  rows: paneRows(envelope),
  closeOnEscape: true as const,
  ...(focus ? { focus: true as const } : {}),
})

const isFeed = (value: unknown, issue: number | undefined): value is Envelope => {
  const e = value as Envelope | null
  return (
    typeof e === 'object' && e !== null && e.status === 'ok' && typeof e.stage === 'string' &&
    typeof e.target === 'object' && e.target !== null &&
    (issue === undefined || e.target.number === issue)
  )
}

const readLog = async ($: EngineInterface, key: string): Promise<BoardLog> =>
  ((await $.store.get(`board:${key}`)) as BoardLog | undefined) ?? emptyLog()

const readSightings = async ($: EngineInterface): Promise<Sighting[]> => {
  const out: Sighting[] = []
  for (const key of await $.store.keys()) {
    if (!key.startsWith(SIGHTING_PREFIX)) continue
    const value = await $.store.get(key)
    if (isSighting(value)) out.push(value)
  }
  return out
}

export const register: Register = (on, options) => {
  // Read once: a setting change reloads the module with the new options. A stored value outside the
  // field's `options` reads as unset, so the default.
  const style = options.boardStyle === 'text' ? 'text' : 'metro'

  on('session.start', async ($, e, next) => {
    sessionCwd = e.cwd
    await $.command.register({
      name: OVERVIEW_COMMAND,
      description: 'Show or hide the pipeline overview pane for the run the coordinator drives',
      immediate: true,
    })
    await $.command.register({
      name: BAND_COMMAND,
      description: 'Show or hide the Metro Wave band above the chat input',
      immediate: true,
    })
    // Prune the logs of targets that reached terminal, with their orientation twins, and sightings
    // older than SIGHTING_TTL_DAYS (an unreadable one too).
    const cutoff = Date.now() - SIGHTING_TTL_DAYS * 86400000
    for (const key of await $.store.keys()) {
      if (key.startsWith(SIGHTING_PREFIX)) {
        const value = await $.store.get(key)
        if (!isSighting(value) || Date.parse(value.at) < cutoff) await $.store.delete(key)
        continue
      }
      if (!key.startsWith('board:')) continue
      const log = (await $.store.get(key)) as BoardLog | undefined
      if (log?.terminalAt) {
        await $.store.delete(key)
        await $.store.delete(`orientation:${key.slice('board:'.length)}`)
      }
    }
    return next(e)
  })

  // A pipeline stage session's start: every skill expansion passes here, and only the plugin's
  // planner, resolver and evaluator with a number record one. The skill's text always passes through
  // unchanged, a failed parse or store write included.
  on('skill.prompt', async ($, e, next) => {
    try {
      const sighting = sightingOf(e.skill, e.pluginName, e.text, sessionCwd, new Date().toISOString())
      if (sighting !== null) await $.store.set(sightingKey(sighting), sighting)
    } catch (err) {
      $.ui.log(`pipeline-board: sighting not recorded (${String(err)})`, { to: 'debug' })
    }
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    // Every Bash call in the session passes here: the match comes before any `$` call or await.
    if (!e.command.includes(SCRIPT)) return next(e)
    const path = feedPath(e.command)
    const issue = commandIssue(e.command)
    const ran = await next(e)
    try {
      if (path === undefined) {
        $.ui.log('pipeline-board: no feed path in that prep command; the board keeps its last state', { to: 'debug' })
        return ran
      }
      const envelope: unknown = JSON.parse(await $.fs.read(path))
      if (!isFeed(envelope, issue)) {
        $.ui.log(`pipeline-board: ${path} holds no ok envelope for this target; the board keeps its last state`, { to: 'debug' })
        return ran
      }
      const key = boardKey(envelope)
      // Read again right before writing: another session may have logged since.
      const log = observe(await readLog($, key), envelope, new Date().toISOString())
      await $.store.set(`board:${key}`, log)
      $.ui.status(statusText(envelope, log))
      // Only once the log and status hold this run, so the pane never runs ahead of them. A failed
      // sighting read keeps the last snapshot, and the run still lands.
      try {
        sightings = await readSightings($)
      } catch (err) {
        $.ui.log(`pipeline-board: sightings not read (${String(err)}); the band keeps the previous snapshot`, { to: 'debug' })
      }
      const now = new Date()
      frames.set(key, {
        drawnAt: now.toISOString(),
        previousDrawnAt: frames.get(key)?.drawnAt ?? null,
        utcOffsetMinutes: -now.getTimezoneOffset(),
        sightings,
        elapsedSeconds: 0,
      })
      bandLog = log
      current = envelope
      if (!opened.has(key)) {
        opened.add(key)
        if (!hidden) {
          const shown = await $.ui.open(openArgs(envelope, false))
          if (!shown.isPlaced) $.ui.toast('Pipeline overview ready: run /ctrl-freaq-overview to show it')
        }
      }
      $.ui.invalidate('ui.render')
    } catch (err) {
      $.ui.log(`pipeline-board: feed not read (${String(err)}); the board keeps its last state`, { to: 'debug' })
    }
    return ran
  })

  // A pane the operator closes stays hidden for the session. Esc and the close mark reach this hook
  // as the person's close; an unload does not hide it. This plugin's own `$.ui.close` starts the
  // chain beneath its own hooks, so Hide and the command set the flag through `hidePane` instead.
  on('ui.close', { id: PANE }, async (_$, e, next) => {
    if (e.origin.kind === 'plugin' || e.origin.kind === 'person') hidden = true
    return next(e)
  })

  // One toggle: close the pane when it is the one shown, else show it (also after an unasked open
  // left it waiting undrawn, or behind another pane's tab).
  on('command.run', { command: OVERVIEW_COMMAND }, async $ => {
    const pane = (await $.ui.panes()).find(p => p.id === PANE)
    if (pane?.isPlaced && pane.isShown) {
      await hidePane($)
      return { text: 'Pipeline overview hidden.' }
    }
    hidden = false
    await $.ui.open(openArgs(current, true))
    return { text: 'Pipeline overview opened.' }
  })

  // The band's own toggle: the mod cannot see or set the engine's collapse of the band, so this
  // flag is its show and hide. A prep run never changes it.
  on('command.run', { command: BAND_COMMAND }, async $ => {
    bandHidden = !bandHidden
    $.ui.invalidate('ui.render')
    return { text: bandHidden ? 'Pipeline board hidden.' : 'Pipeline board shown.' }
  })

  // The band above the chat input: Metro Wave alone on Desktop, else the one-line summary, at every
  // stage. It is shared, so it yields to a survey and draws the tree beneath under its own.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const envelope = current
    if (envelope === null || bandHidden || e.props.hasSurvey) return next(e)
    const elements = $.ui.resolve(e)
    const { Box, Text } = elements
    let own: ReturnType<typeof h> = null
    const frame = frames.get(boardKey(envelope))
    if (style === 'metro' && e.surface === 'desktop' && historyReady(envelope) && frame !== undefined) {
      const wave = bandWave(envelope, frame, e.props.bodyColumns)
      if (wave === null) {
        $.ui.log('pipeline-board: Metro Wave over the Svg source limit; drawing the summary line', { to: 'debug' })
      } else {
        // Narrowed by the surface check above: the desktop table carries `Svg`.
        const { Svg } = elements as Elements['desktop']
        own = h(
          Box,
          { flexDirection: 'row', alignItems: 'flex-start' },
          h(Box, { flexDirection: 'column', width: wave.labelCells }, ...wave.rows.map((row, i) => labelCell(wave, row, i))),
          h(Svg, { source: wave.source, alt: wave.alt, width: wave.width, height: wave.height, isInteractive: true }),
        )
      }
    }
    return h(Box, { flexDirection: 'column' }, own ?? h(Text, null, bandText(envelope)), await next(e))

    // One label cell per drawing row, a band row tall, beside its track. A line's cell is keyed and joins
    // its line's hover scope, which its overview row shares (#157): hovering either lights both and reveals
    // the cell's card. The card is absolute, so it takes no row of the column; it opens down from a
    // top-half line and up from a bottom-half one, toward the drawing's middle, to limit clipping, and the
    // band clips a card that runs past it (its lines plus two border rows), as on a short drawing.
    function labelCell(wave: Wave, row: WaveRow, index: number) {
      if (row.kind !== 'line') return h(Box, { height: 1 })
      const place = index < wave.rows.length / 2 ? { top: 0 } : { bottom: 0 }
      return h(
        Box,
        { key: row.key, height: 1, hover: { scope: row.key, backgroundColor: 'gray' } },
        h(Text, null, row.label),
        h(
          Box,
          {
            position: 'absolute',
            ...place,
            display: 'none',
            flexDirection: 'column',
            borderStyle: 'round',
            width: wave.cardCells,
            hover: { scope: row.key, display: 'flex' },
          },
          ...row.card.map(line => h(Text, null, line)),
        ),
      )
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const hide = h(Button, { key: 'hide', label: 'Hide', role: 'dismiss', onPress: () => hidePane($) })
    const envelope = current
    if (envelope === null) {
      return h(Box, { flexDirection: 'column' }, hide, h(Text, { dimColor: true }, 'No coordinator prep seen yet in this session.'))
    }
    const key = boardKey(envelope)
    const log = await readLog($, key)
    const sha = envelope.target.body_sha256
    const cached = (await $.store.get(`orientation:${key}`)) as Orientation | undefined
    const fresh = cached !== undefined && cached.sha === sha
    if (sha && !fresh && !inFlight.has(key)) {
      // Never awaited here: the model call redraws the pane when it lands.
      inFlight.add(key)
      void orient($, envelope, key, sha).finally(() => inFlight.delete(key))
    }
    const board = boardModel(envelope, log, fresh && cached?.text ? cached.text : null)

    const rowTree = (row: Row) => {
      const chips = row.collapsed
        ? GLYPH.done
        : row.rungs.map(rung => `${GLYPH[rung.state]} ${rung.label}`).join('  ')
      const facts = [
        row.rework > 0 ? `rework ×${row.rework}` : null,
        row.firstSeen ? `since ${row.firstSeen}` : null,
        row.reason,
        row.gateText,
        row.nextText,
      ].filter((part): part is string => part !== null)
      return h(
        Box,
        { flexDirection: 'column', marginBottom: 1, hover: { scope: lineKey(row.number), backgroundColor: 'gray' } },
        h(Text, null, `#${row.number} ${row.title}  ${chips}`),
        row.subProgress ? h(Text, { dimColor: true }, `  ${row.subProgress}`) : null,
        row.durations.length > 0 ? h(Text, { dimColor: true }, `  ${row.durations.join(' · ')}`) : null,
        facts.length > 0 ? h(Text, { dimColor: true }, `  ${facts.join(' · ')}`) : null,
        h(
          Box,
          { flexDirection: 'row', gap: 2 },
          h(Link, { href: row.issueUrl, label: `issue #${row.number}` }),
          row.prUrl ? h(Link, { href: row.prUrl, label: 'PR' }) : null,
        ),
      )
    }

    const orientation = board.summary
      ? h(
          Box,
          { flexDirection: 'column', marginBottom: 1 },
          h(Text, { dimColor: true }, 'Generated summary'),
          h(Text, null, board.summary),
          h(Link, { href: issueUrl(envelope.repo, envelope.target.number), label: 'the issue' }),
        )
      : h(Text, { dimColor: true }, envelope.target.title ?? '')

    return h(
      Box,
      { flexDirection: 'column' },
      hide,
      h(Text, { bold: true }, board.header),
      orientation,
      ...board.banner.map(line => h(Text, { color: 'yellow' }, line)),
      ...board.groups.map(group =>
        h(Box, { flexDirection: 'column' }, h(Text, { bold: true }, group.name), ...group.rows.map(rowTree)),
      ),
      integrationTree(),
    )

    // The integration row: no rungs, its PR (or "no integration PR yet"), gate and next.
    function integrationTree() {
      const row = board.integration
      if (row === null) return null
      const parts = [
        row.prNumber === null ? 'no integration PR yet' : `PR #${row.prNumber}${row.draft ? ' (draft)' : ''}`,
        row.gateText,
        row.nextText,
      ].filter((part): part is string => part !== null)
      return h(
        Box,
        { flexDirection: 'column', hover: { scope: lineKey(null), backgroundColor: 'gray' } },
        h(Text, { bold: true }, 'Integration'),
        h(Text, null, parts.join(' · ')),
        row.prUrl ? h(Link, { href: row.prUrl, label: 'integration PR' }) : null,
      )
    }
  })
}

// The band's drawing for this frame and width: the memoized one while neither has changed since it was
// built. A rebuild at the same frame (the band's width changed) starts its motion as far in as the clock
// says the frame has run, the one place the clock reaches the drawing; the first build after a feed read
// starts at 0.
function bandWave(envelope: Envelope, frame: WaveFrame, columns: number): Wave | null {
  const key = `${frame.drawnAt}|${columns}`
  if (bandMemo?.key === key) return bandMemo.wave
  const rebuilt = bandMemo !== null && bandMemo.key.startsWith(`${frame.drawnAt}|`)
  const elapsedSeconds = rebuilt ? Math.max(0, Math.round((Date.now() - Date.parse(frame.drawnAt)) / 1000)) : 0
  const board = boardModel(envelope, bandLog ?? emptyLog(), null)
  const wave = metroWave(board, envelope, { ...frame, elapsedSeconds }, columns)
  bandMemo = { key, wave }
  return wave
}

// The orientation paragraph: the target's body through a fixed prompt, cached per body sha. A
// failure (no answer, a model blocked by policy) is cached too, so nothing retries until it changes.
async function orient(
  $: EngineInterface,
  envelope: Envelope,
  key: string,
  sha: string,
): Promise<void> {
  try {
    const body = envelope.target.body ?? (envelope.target.body_path ? await $.fs.read(envelope.target.body_path) : '')
    const { system, prompt } = orientationRequest(envelope.target.title ?? '', body)
    const reply = await $.model.complete({ model: 'haiku', effort: 'low', maxTokens: 200, system, prompt })
    if (reply.isAnswered) await $.store.set(`orientation:${key}`, { sha, text: reply.text.trim() })
    else {
      await $.store.set(`orientation:${key}`, { sha, failed: true })
      $.ui.log(`pipeline-board: no orientation paragraph (${reply.reason})`, { to: 'debug' })
    }
  } catch (err) {
    await $.store.set(`orientation:${key}`, { sha, failed: true })
    $.ui.log(`pipeline-board: orientation call refused (${String(err)})`, { to: 'debug' })
  }
  $.ui.invalidate('ui.render')
}
