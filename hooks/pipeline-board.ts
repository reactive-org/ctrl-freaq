// The pipeline board (#116): a read-only view of the coordinator prep's facts. It reads the feed
// file each `prep_coordinator.py` run writes (never the tool's stdout), draws a pane and one status
// line, and keeps a small log of the stage changes it saw. It writes nothing to GitHub or the
// repository: the only `$` namespaces it calls are ui, store, fs.read, model.complete and command,
// and an offline scan in tests/test_prep_coordinator.py pins that. Every decision lives in the pure
// `board-model.ts`; this module only calls the host around it.
import type { EngineInterface, Register } from 'claude-code'

import {
  boardKey,
  boardModel,
  commandIssue,
  emptyLog,
  feedPath,
  issueUrl,
  observe,
  orientationRequest,
  SCRIPT,
  statusText,
} from './board-model.ts'
import type { BoardLog, Envelope, Row } from './board-model.ts'

const PANE = 'pipeline-board'
const COMMAND = 'pipeline-board'
const GLYPH = { current: '●', done: '✓', pending: '○' } as const

type Orientation = { sha: string; text?: string; failed?: boolean }

// Module state, by design: the last envelope seen and the panes opened this session. A reload
// starts them over; the next prep run refills them.
let current: Envelope | null = null
const opened = new Set<string>()
const inFlight = new Set<string>()

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

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: COMMAND,
      description: 'Show the pipeline board for the run the coordinator drives',
      immediate: true,
    })
    // Prune the logs of targets that reached terminal, with their orientation twins.
    for (const key of await $.store.keys()) {
      if (!key.startsWith('board:')) continue
      const log = (await $.store.get(key)) as BoardLog | undefined
      if (log?.terminalAt) {
        await $.store.delete(key)
        await $.store.delete(`orientation:${key.slice('board:'.length)}`)
      }
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
      // Only once the log and status hold this run, so the pane never runs ahead of them.
      current = envelope
      if (!opened.has(key)) {
        opened.add(key)
        const shown = await $.ui.open({ id: PANE, title: `Pipeline · #${envelope.target.number}` })
        if (!shown.isPlaced) $.ui.toast('Pipeline board ready: run /pipeline-board to show it')
      }
      $.ui.invalidate('ui.render')
    } catch (err) {
      $.ui.log(`pipeline-board: feed not read (${String(err)}); the board keeps its last state`, { to: 'debug' })
    }
    return ran
  })

  on('command.run', { command: COMMAND }, async $ => {
    const title = current ? `Pipeline · #${current.target.number}` : 'Pipeline'
    await $.ui.open({ id: PANE, title, focus: true })
    return { text: 'Pipeline board opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const envelope = current
    if (envelope === null) {
      return h(Text, { dimColor: true }, 'No coordinator prep seen yet in this session.')
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
        { flexDirection: 'column', marginBottom: 1 },
        h(Text, null, `#${row.number} ${row.title}  ${chips}`),
        row.subProgress ? h(Text, { dimColor: true }, `  ${row.subProgress}`) : null,
        row.durations.length > 0 ? h(Text, { dimColor: true }, `  ${row.durations.join(' · ')}`) : null,
        facts.length > 0 ? h(Text, { dimColor: true }, `  ${facts.join(' · ')}`) : null,
        h(
          Box,
          { flexDirection: 'row', gap: 2 },
          h(Link, { href: row.issueUrl, label: `issue #${row.number}` }),
          row.prUrl ? h(Link, { href: row.prUrl, label: 'PR' }) : null,
          row.nextCommand
            ? h(Button, {
                key: `copy:${row.number}`,
                label: 'Copy next',
                onPress: async (press: { surface: 'terminal' | 'desktop' | 'vscode' | 'mobile' }) => {
                  const copied = await $.ui.copy({ text: row.nextCommand!, surface: press.surface })
                  // A surface with no clipboard path says so, with the command to copy by hand.
                  if (!copied.isCopied) $.ui.toast(`Not copied (${copied.reason}): ${row.nextCommand}`)
                },
              })
            : null,
        ),
      )
    }

    return h(
      Box,
      { flexDirection: 'column' },
      h(Text, { bold: true }, board.header),
      board.summary
        ? h(
            Box,
            { flexDirection: 'column', marginBottom: 1 },
            h(Text, { dimColor: true }, 'Generated summary'),
            h(Text, null, board.summary),
            h(Link, { href: issueUrl(envelope.repo, envelope.target.number), label: 'the issue' }),
          )
        : h(Text, { dimColor: true }, envelope.target.title ?? ''),
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
        { flexDirection: 'column' },
        h(Text, { bold: true }, 'Integration'),
        h(Text, null, parts.join(' · ')),
        row.prUrl ? h(Link, { href: row.prUrl, label: 'integration PR' }) : null,
      )
    }
  })
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
