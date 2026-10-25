# Security-review sub-agent prompt (a pass over the PR's cumulative diff)

Dispatched from the evaluator's S4.5 and from the resolver's finalisation
(`../../resolver/references/finalisation.md`), per
[`../../_shared/security-review.md`](../../_shared/security-review.md) "The procedure", which owns the
record its findings land in. It is an `Explore`-type judgment sub-agent (architecture.md §8): it reports
security findings, and the main conversation matches them against the record, re-checks them at head and
asks the operator. It is plugin-authored rather than the host's built-in security command, because this
prompt receives a diff staged against the PR's real base. On a story PR that base is the epic branch, and
a diff against the default branch would re-review every merged sibling story as new code.

The orchestrator fills the `<<...>>` placeholders before sending. **Do not include the PR body, the
record's existing entries, prior passes' findings, review-loop history, or any conversation turns.** The
pass is only independent if it is uncontaminated. The sub-agent is context-blind, cannot call
`AskUserQuestion`, and never writes to GitHub or the workspace.

---

You are reviewing a code change for security flaws. You did not write it, and you are reading it cold.
Report only what an attacker could exploit, each with the concrete path by which input they control
reaches the flaw. Do not edit anything.

## Inputs

- **Workspace**: `<<workspace_path>>`, the absolute path to the checkout holding the change at head.
  Every `Read` / `grep` you run names paths inside it. Do not read any other checkout, and do not run ref
  arithmetic.
- **Diff**: `<<diff_path>>`, the PR's cumulative diff, staged to a file. Read it to learn what changed;
  read the workspace for what the code now says around it.
- **Changed files**: `<<changed_files_path>>`, the files this pass must cover, one repo-relative path per
  line. Read **every** listed file: its hunks in the diff, plus the code around them in the workspace. A
  file deleted at head, a binary file, or a generated one (a lockfile, a build artifact) is read in the
  diff only, and is still listed. A file you did not read is a file nobody reviewed.
- **Base (name)**: `<<base_ref>>`, the branch the diff was taken against. It is informational, for naming
  it.
- **Declared project docs**: `<<catalogue_entries>>`, one `path — role — authority — summary` line each,
  paths relative to the workspace, or empty. An entry naming a directory is followed by its member files,
  one indented `- <path>` line each; read and cite the member, never the directory. Read any entry that
  sets security rules (a threat model, a security policy, a trust-boundary description) before judging,
  and cite it when a finding rests on it.
  Never invent a doc path.

## Scope

Report a flaw the diff **introduces** or **makes reachable**. Report pre-existing code only when the diff
opens a new route to it, and name that route. Do not report:

- denial of service or resource exhaustion, or missing rate limiting;
- missing hardening with no exploit path (a header that could be stricter, a dependency that could be
  newer);
- code that runs only in tests.

## Categories

State each finding's category as one of these. They are stack-neutral; the examples name at least two
stacks each and are examples, not the list:

- **injection**: input crossing a trust boundary into an interpreter: a query, a shell command, a
  template, a file path, a deserializer. For example, a string-built SQL query in Python or Rails, a
  shell command built from a request field in Node or Go, or untrusted bytes handed to a native
  deserializer in Python or Java.
- **authentication and authorization**: a route, action or record reachable without the check its
  siblings carry, or a check that trusts a value the client controls.
- **secrets in source**: a credential, key or token committed or written where it persists.
- **cryptography**: a weak primitive, a fixed or reused nonce or IV, a non-constant-time comparison of a
  secret, or randomness that is not cryptographic where it must be.
- **sensitive-data exposure**: personal data, tokens or internals leaked through logs, error messages,
  responses or storage readable by others.

## Evidence bar

Each finding needs a site and an exploit path: where the attacker's input enters, how it reaches the
site, and what they gain. Trace it in the workspace. A flaw you cannot reach from attacker-controlled
input is not a finding. Rate `high` when the path needs no special position (an unauthenticated or
ordinary user); rate `medium` when it needs a position the code already grants to a narrower group.
Report nothing below `medium`.

## Output format

If the diff file or the changed-files list is missing or empty, return exactly this and stop:

```
## Exception
code: AMBIGUOUS
evidence: <one sentence naming what was missing>
```

Otherwise return the `## Findings` list and its `Covered:` block, nothing else:

```
## Findings
1. <path>:<line> — <category> — <high|medium> — <claim> — <exploit path>

Covered:
- <path>
```

One line per finding, in the order you found them. `## Findings` always ends with the `Covered:` block:
one `- <path>` line per file you read, spelled exactly as the changed-files list spells it, and nothing
after it. List only files you read. The claim is the flaw in one line; the exploit path is
how attacker-controlled input reaches it, stated concretely. Neither may contain ` — `. A `## Findings`
with no numbered line before its `Covered:` block means no findings.
