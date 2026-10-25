# Workspace trust — why Claude Desktop may ask you to trust a new worktree

`workspace-open` creates each work worktree with `git worktree add` at `.worktrees/<branch>` under
the main checkout. When a session starts in that folder, Claude Desktop may ask you to trust it. The
prompt is Desktop's own security check. The pipeline never answers it, and trust and permission state
is yours to set. This page records what the current documentation says, what a local probe showed,
and what you can set yourself. Findings come first, then what to do, then the evidence.

## Status

- **Observed:** 2026-10-25, on Claude Desktop 3.6556.2 (macOS), against the Claude Code docs at
  changelog head 2.1.310. The research behind it is the dossier on the story that added this page
  (https://github.com/reactive-org/ctrl-freaq/issues/114#issuecomment-6390523553, sources fetched
  2026-10-25).
- **Refresh rule:** to re-check, re-run `/ctrl-freaq:researcher` on a successor issue and repeat
  the probe in `## The probe`. The Desktop docs don't describe this check, so it may change without a
  docs entry.
- **Outcome:** the worktree layout stays `.worktrees/<branch>`. No layout tested stops the prompt, so
  nothing in `workspace-open`, `workspace-close` or the coordinator changes because of it.

## The three routes

The question was whether the prompt can be avoided, by any of three routes. Primary sources are the
Claude Code docs at `code.claude.com`, each fetched 2026-10-25. Anything else is labelled as what it
is.

**(a) Does trust on the project root cover folders beneath it?** For the Claude Code CLI, yes. In a
repository the CLI keys trust on the git repository root, and in a worktree it uses the main
checkout's root (https://code.claude.com/docs/en/permissions, "Project allow rules and workspace
trust", fetched 2026-10-25). The changelog's 2.1.218 entry names the root a grant covers, and 2.1.232
stopped nested repositories inheriting a parent's trust
(https://code.claude.com/docs/en/changelog, fetched 2026-10-25). The Desktop docs don't describe a
local folder-trust check at all; the only trust question they mention is for SSH folders
(https://code.claude.com/docs/en/desktop, fetched 2026-10-25).
- *Secondary, not documentation:* anthropics/claude-code#96928 (an issue report, closed as fixed in
  Desktop 2.16120.0 with no description) describes an exact-path check. anthropics/claude-code#90638
  (an open issue report, a reporter's reading of one Windows build) describes a lookup that inherits
  trust from the repository unless the folder carries an execution-granting `.claude/settings.json`
  or `.claude/settings.local.json`: a non-empty hook, or a `Bash` / `PowerShell` allow rule, "among
  other triggers".
- *Observed, not documentation:* the 2026-10-25 probe fits the second reading. Desktop inherits the
  repository's trust for a `git worktree add` folder unless the folder carries its own
  execution-granting project settings, and then it prompts for that folder.

**(b) Are app-made worktrees pre-trusted, so that moving the layout would help?** No documentation
says so. App-made worktrees live in `<project-root>/.claude/worktrees/` by default
(https://code.claude.com/docs/en/desktop, fetched 2026-10-25), and the CLI's `--worktree` runs need
workspace trust like any other folder (https://code.claude.com/docs/en/worktrees, fetched
2026-10-25).
- *Secondary, not documentation:* anthropics/claude-code#90638 and anthropics/claude-code#96928
  quote Desktop's log line `Auto-trusted worktree … (inherited from <repo>)` for worktrees the app
  created itself.
- *Observed, not documentation:* in the probe, a `git worktree add` folder under `.claude/worktrees/`
  got no automatic trust and behaved exactly like one under `.worktrees/`. The path alone confers no
  trust, so a move would stop no prompt. Every auto-trust line in the operator's Desktop log (the
  dossier's count) is for a worktree the app made itself, which suggests the auto-trust follows
  provenance, but no source documents that and the probe doesn't test it. A move would also bring the
  costs the worktrees and desktop docs list: Desktop's archive-time cleanup of `.claude/worktrees/`,
  the CLI's refusal when `.claude` is a symlink, and a new exclusion and cleanup scope.

**(c) Does a documented setting pre-trust a path or pattern?** No. The settings reference has no key
that pre-trusts a directory or pattern (https://code.claude.com/docs/en/settings-reference, fetched
2026-10-25). The security page has no setting that skips or pre-accepts trust
(https://code.claude.com/docs/en/security, fetched 2026-10-25). The only documented trust skips are
for `claude ultrareview` alone (https://code.claude.com/docs/en/cli-reference and
https://code.claude.com/docs/en/errors, fetched 2026-10-25; the changelog's 2.1.309 entry).
- *Secondary, not documentation:* the `trustedDirectories` request, anthropics/claude-code#12737, was
  closed as a duplicate and never implemented.

## What you can set yourself

None of this is a pipeline step. Each item is information for you to act on, or not, yourself.

- **Accept the prompt once per worktree folder.** Desktop saves trust per folder path. Observed, not
  documented: in the operator's Desktop log, each `.worktrees/` folder of the repository that prompts
  logged repeated trust checks but only one acceptance, so a reused worktree has not been seen to
  prompt again. Re-opening an existing worktree with `workspace-open` reuses the folder.
- **Know what makes a folder prompt.** Observed and secondary, not documented (the probe, and the
  anthropics/claude-code#90638 report): the prompt follows the folder's own execution-granting
  `.claude/settings.json` or `.claude/settings.local.json`: hooks, or `Bash` / `PowerShell` allow
  rules. These are usually the
  repository's tracked settings. They are the repository owner's security choice, and the pipeline
  doesn't recommend changing them to avoid the prompt.
- **Know that a setup hook can bring the prompt back.** On the same undocumented reading, a
  `<!-- worktree-setup -->` hook that copies such a settings file into each worktree makes every new
  worktree prompt, even in a repository whose tracked files carry none.
- **No documented setting, flag or environment variable pre-trusts a path or pattern.**
  `--trust-workspace` and `CLAUDE_CODE_DISABLE_ULTRAREVIEW_TRUST_CHECK` apply to `claude ultrareview`
  only.
- **The CLI's manual trust key has a narrow documented scope.** The permissions doc describes setting
  `projects["<path>"].hasTrustDialogAccepted` in `~/.claude.json` for the CLI, keyed on the
  repository root, which already covers worktrees there. Its effect on Desktop for a worktree folder
  is undocumented. This is a documented finding, not a fix the pipeline suggests.
- **Desktop's Worktree location setting** (Settings > Claude Code) moves only the worktrees the app
  creates itself. It doesn't affect `git worktree add` folders.

## The probe

The probe that settled route (b) and the layout. It is yours to run; the pipeline never runs it. Run
it from the main checkout of a repository whose root you have already trusted, and note whether the
repository tracks an execution-granting project settings file. First add the two probe folders:

```bash
git -C <repo> worktree add <repo>/.worktrees/trust-probe-a -b trust-probe-a
git -C <repo> worktree add <repo>/.claude/worktrees/trust-probe-b -b trust-probe-b
```

Start a Desktop session in each folder and note whether Desktop asks.

Then read Desktop's log, which is read-only here. The location shown is the macOS one:

```bash
grep -E 'checkTrust|saveTrust|Auto-trusted worktree' ~/Library/Logs/Claude/main*.log
```

Then remove the folders and branches:

```bash
git -C <repo> worktree remove <repo>/.worktrees/trust-probe-a
git -C <repo> worktree remove <repo>/.claude/worktrees/trust-probe-b
git -C <repo> branch -D trust-probe-a trust-probe-b
```

Accepting a prompt during the probe saves a trust entry for that folder. Whether you keep it or remove
it is yours to decide.

Results on 2026-10-25, Desktop 3.6556.2, in two repositories:

| Repository | Folder | Trust check logged | Prompted | Accepted (`saveTrust`) | `Auto-trusted worktree` |
|---|---|---|---|---|---|
| tracks no project settings file (this plugin's own repository) | `.worktrees/` | no | no | — | no |
| tracks no project settings file (this plugin's own repository) | `.claude/worktrees/` | no | no | — | no |
| a repo that tracks a `PreToolUse` hook | `.worktrees/` | yes | yes | yes | no |
| a repo that tracks a `PreToolUse` hook | `.claude/worktrees/` | yes | yes | yes | no |

What it shows: the prompt split by repository, not by folder. That refutes the path reading. Only the
settings-gated reading fits all four rows. One boundary: in the probe, the plugin's own repository
had an *untracked* local settings file with `Bash` allow rules at its root. A `git worktree add`
folder doesn't inherit an untracked file, so its probe folders ran no trust check. The prompt is therefore
"once per story" only in a repository whose worktree folders carry execution-granting project
settings, usually tracked ones.

## What the pipeline never does

Trust and permission state is a security setting you own.

- No skill or script reads or writes `~/.claude.json`, `.claude/settings.json` or
  `.claude/settings.local.json`, and none passes a trust-skip flag or sets a trust-skip environment
  variable.
- No session answers the trust prompt for you, and none tells you to change trust or permission state
  to avoid it.
- The pipeline never runs the probe above. It is yours to run.
- The worktree layout is not moved to avoid the prompt, because no tested layout stops it.
