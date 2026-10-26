# Review threads — shared contract

A **review thread** is a GitHub PR review comment pinned to code, which a person can reply in and
resolve. The evaluator posts threads on the code most worth the operator's attention and does not propose
APPROVE until a trusted human has resolved them (#145). This file is the single source of truth for what
the pipeline writes in a thread, how a thread's state is derived, and whose comment or resolution counts.

- **Writers:** the **evaluator**, which posts one review carrying the threads at the head it gates
  (`gh_persist.py review-post`), and the **resolver**, which replies in each thread its revision run
  addressed (`gh_persist.py review-reply`).
- **Readers:** `scripts/review_threads.py`'s `read_threads`, composed by `prep_evaluator.py` and
  `prep_resolver.py` as `facts.review_threads`.
- **Renderings:** the resolver's in `skills/resolver/references/review-fix-round.md`, the evaluator's in
  `skills/evaluator/references/review-threads.md`. Render this file; never restate it.

## 1. Why it exists

`scripts/blast_radius.py` already knows which code a change put at risk: a sensitive-path match, a file
the plan never named, a site the blast-radius judgment cited. Open `## Security review` findings were also
known. None of that reached the person reviewing the PR, and nothing on GitHub recorded that a person
looked at the flagged code. The operator is often the PR's author, and GitHub won't let an author approve
their own PR. A resolved thread is the record. It lives in the tool built for reviewing code, and the
reply a person types there reaches the resolver as data instead of chat.

A thread is pinned to a line at one commit. One the resolver opened mid-flight would go stale as later
fix rounds and phases move the code, so the evaluator posts once, at the head it gates. The resolver only
replies.

## 2. Marker and stamp

Every comment the pipeline writes in a review thread starts with the literal line
`<!-- review-thread:v1 -->`, and so does the body of the review that carries the threads. The second line
is a stamp the **script** writes. The model writes only the text after it.

```
<!-- review-thread:v1 -->
<!-- F3 · head 4f1c…(40 hex) · anchor scripts/importer.py:41-58 · flags sensitive:scripts/importer.py,judgment:trust:scripts/importer.py -->
**F3**
```

| Comment | Second line | Third line |
|---|---|---|
| A thread's root | `<!-- F<n> · head <40-hex> · anchor <path>:<start>-<end>\|<path>:file · flags <key>,<key>[ · supersedes F<m>\|<human root comment url>] -->` | `**F<n>**`, the visible heading |
| A reply | `<!-- reply F<n>\|reply · head <40-hex> -->` (bare `reply` in a thread with no `F` id) | the reply text |
| The review body | `<!-- review · head <40-hex> -->` | the review text |

`head` is the commit the comment was written against: the gated head on a root, the pushed commit on a
reply. `anchor` is where the root sits at that head: a line range, or `file` for a whole-file thread.
`flags` lists the flag keys (§7) the thread carries, comma-separated. A key embeds a path, so a `%` or
`,` inside one is written `%25` / `%2C`; spaces stay as they are.

**A marked comment is the pipeline's only when its author is trusted** (§5) and its second line is a stamp
this grammar reads. A marked comment by any other author is untrusted text, ignored like any other: it
neither resets a state nor moves a thread's reference commit. A trusted comment carrying the marker with
no stamp under it (a pasted pipeline comment, say) is a human's text, and a thread it opens is the
human's. The pipeline writes as the operator's own GitHub account, so the marker is what tells
its comments from the operator's. That is why the script, never the model, stamps it.

## 3. `F<n>` ids

Each pipeline-rooted thread carries `F<n>`, stable and never reused. The next id is one more than the
highest `F<n>` on any trusted marked root in the PR (`facts.review_threads.next_f`). A thread posted to
replace a superseded one takes a new id and stamps `supersedes F<m>`, or `supersedes <url>` when the old
thread was a human's. Its visible text links the old root comment. A thread a trusted human opened carries
no stamped id. `read_threads` numbers those `O<n>`, 1-based, by root `createdAt`.

## 4. States

`scripts/review_threads.py`'s `derive_state` derives each thread's state, a closed set, from GitHub's
thread-resolution data, its trusted comments (§5) and the hunk check (§6):

| State | When | Gates APPROVE |
|---|---|---|
| `unreviewed` | unresolved, with no trusted human comment after the pipeline's last marked comment | yes |
| `accepted` | resolved by a trusted human, its anchored hunk unchanged since | no |
| `changes-requested` | unresolved, with a trusted human comment after the pipeline's last marked comment | yes |
| `operator-thread` | a thread a trusted human opened that is still unresolved | yes |
| `superseded` | resolved by a trusted human, but its anchored hunk changed after the resolution | until a fresh thread replaces it |

**Precedence**, first match wins:

1. Resolved with trust, hunk unchanged → `accepted`.
2. Resolved with trust, hunk changed → `superseded`.
3. Unresolved, rooted by a trusted human → `operator-thread`. It carries `pending_reply: true` while the
   latest trusted comment is a marked one: the resolver has answered and a human is still to look.
4. Unresolved, with a trusted human comment after the latest trusted marked comment → `changes-requested`.
5. Otherwise → `unreviewed`.

So `operator-thread` outranks `unreviewed` and `changes-requested`. `pending_reply` is false on every
other state. An untrusted resolution reads as unresolved. A thread whose root is untrusted, marked or
not, gates nothing: it is counted in `untrusted.threads` and listed nowhere else.

GitHub's native "Request changes" review state is not used. The design gates on thread resolution instead.

## 5. Trust

- **A comment** counts by its author's `authorAssociation`: `OWNER`, `MEMBER` or `COLLABORATOR`
  (`pipelib.trust.is_trusted`, the SEC-1 rule).
- **A resolution** carries no association, so it counts by the resolving account's permission on the repo,
  read through `gh api repos/<repo>/collaborators/<login>/permission`. It counts when that permission is
  `admin`, `maintain` or `write` (`pipelib.trust.TRUSTED_PERMISSIONS`, `is_trusted_permission`). GitHub
  lets the PR's opener or anyone with write access resolve, and on a public repo any account reads `read`,
  so only write and above separate a maintainer from a stranger. **The viewer's own resolution** counts
  with no lookup, because the pipeline never resolves or unresolves a thread (§11), so a resolution by
  this account is a human's.
- **Unknown is untrusted.** A missing association, a permission of `read` or `none`, any other value, and
  a failed lookup (`THREAD_PERMISSION_UNAVAILABLE`) all count as untrusted.

## 6. Hunk unchanged

Whether a resolved thread's code changed since is a script `git diff`, never GitHub's `isOutdated`, whose
meaning is undocumented. `isOutdated` would also mark a thread the resolver fixed and a human then
resolved as superseded, so every fix cycle would post a duplicate.

- **Reference commit:** the `head` on the thread's latest trusted marked comment, or the root comment's
  `originalCommit` when it has none.
- **Reference range:** the root's anchor (its stamp, or `originalStartLine`..`originalLine` on a human
  root), mapped from the root's commit to the reference commit (`review_threads.map_range`). Lines outside
  a hunk shift by the running delta; a hunk whose old side intersects the range widens it to the hunk's
  new side.
- **Changed:** any hunk of `git diff -U0 <reference> <judged head> -- <path>` whose old side intersects the
  reference range. `<path>` is matched literally, never as a pattern, and diffed raw (no textconv). A
  `file` anchor counts any hunk. So do a path absent at the reference or at the judged head that was
  present at the thread's root commit (deleted or renamed after the thread was opened), a reference
  commit missing locally (`git cat-file -e` fails, after a force-push, say), and a binary or mode-only
  change, whose diff has no `@@` hunk. A human comment on the diff's base (`LEFT`) side numbers its lines
  in the base file, so it is checked as a `file` anchor. A `file` check whose path was absent at the root
  commit too (a base-side comment, or a file thread, on a file the PR deletes) was opened on the
  deletion, so the diff alone decides: absent at both is unchanged, restored is changed. A root commit
  missing locally keeps the path rule. The check stays conservative: a resolved thread re-threads, it
  never wrongly counts as accepted.

The judged head is the evaluator's gated head, or the resolver's workspace HEAD.

The reference commit is what the human last heard about before resolving only because **the pipeline
never replies in a resolved thread** (§10): GitHub keeps a thread resolved when someone replies, and
records no resolution time to order the two. A reply posted after a resolution would move the
reference, so the resolution would count for code the human never saw. The one residual is a human
resolving in the instant between `review-reply`'s check and its post.

## 7. Flags, grouping and the cap

A **flag** is one reason a piece of code gets a thread. Its key names the kind and the code:

| Key | Source |
|---|---|
| `sensitive:<path>` | a sensitive-path match (`blast_radius.py assess`'s `sensitive.matches`) |
| `out-of-plan:<path>` | a changed file no planned span matches (`out_of_plan`) |
| `judgment:<dimension>:<path>` | a site the blast-radius judgment cited (`judgment.signals`) |
| `security:SEC-<n>` | an `open` `## Security review` entry |
| `plan:<path>` | a finer plan deviation the evaluator's plan-adherence check proposed |

A failed sensitive-path read has nothing to anchor, so it gets no flag. It still forces the merge gate to
`ask`.

- **Anchoring** reads `git diff -U0` over the gated range (`origin/<base>...HEAD`, at `assess`'s merge base).
  A site anchors on its range when that range lies wholly inside one hunk's new side, else on its whole
  file. A file signal (`sensitive`, `out-of-plan`, or a security entry with no site) anchors on its file's
  first hunk's new side, or on the whole file when it has no new-side lines (deleted, binary, a pure rename).
  A model-written line whose path is not in the diff is `rejected`; a security entry whose path is not in it
  is `unanchored_security`.
- **Grouping.** Flags in the same hunk share one thread, with the union of their ranges and keys.
- **Cap.** At most `THREAD_CAP` (8) new threads per review. Over the cap, a file with several threads
  collapses them into one file thread. If that is still over, threads are kept in priority order security >
  sensitive > judgment > out-of-plan > plan; the rest are `overflow`, listed in the review body, and a
  security flag among them joins `unanchored_security`.
- **Idempotence.** A flag matches a **chain head** — a trusted pipeline thread no successor names
  (`superseded_by: null`) carrying a key of the same kind and path (the same `SEC-<n>` for security). A file
  signal matches on that alone, since its anchor, the file's first hunk, moves as the PR is revised; a site
  matches when it lies inside the head's range mapped to the gated head, a file head matching any site, and
  a head whose range cannot be mapped matching on kind and path, so nothing posts twice. Any matched head
  not `superseded` → nothing posts. Every matched head `superseded` → the flag rides that head's successor.
- **Successors.** Every gating chain head that is `superseded` gets exactly one fresh thread, whatever fires
  now — a human-rooted one included. It takes a new `F<next>`, is stamped `supersedes F<m>` (or the human
  root's URL), carries the predecessor's keys plus any flag riding it (none for a human root), and anchors at
  the predecessor's range mapped to the gated head, falling back to the whole file when the mapped range
  leaves every hunk, cannot be mapped, or numbers the base side (a human `LEFT` comment). When the
  predecessor's path is no longer in the gated diff there is nothing to anchor: a security head's `SEC-<n>`
  joins `unanchored_security`, and every such head is `retired` (it stops gating: no successor can replace
  it). A head on a renamed file is followed to its new name. Successors are exempt
  from the cap: they exist to unblock the gate.

`review_threads.py flags` computes all of this; the evaluator's S4.6 runs it
(`skills/evaluator/references/review-threads.md`).

## 8. `## Out-of-plan changes`

A resolver-written PR-body section, read by a script (`parse.scan_out_of_plan`). It has one line per file
`blast_radius.py` reported as out of plan, saying why the change departs from the plan:

```
## Out-of-plan changes
- `scripts/importer.py` — the retry needed a shared backoff helper the plan's file list did not name
```

The section is kept across phases, keyed by path. The evaluator quotes the reason in that file's thread, but only
when `facts.out_of_plan.reasons_trusted` holds: it is body text, trusted on the #144 terms (a PR this account opened,
or one whose author's association is trusted), since the thread is posted under this account.
`## Plan override` keeps its own meaning: the PR proceeded without a plan.

## 9. Thread-cited security dismissal

When every gating thread is `accepted`, the trusted reply (`reply_reason`) on a security finding's own
thread — a pipeline thread whose flags carry `security:SEC-<n>` — becomes that entry's dismissal in the `## Security review` record. The line cites the thread:

```
- SEC-<n> · dismissed — <claim> — <anchor> — operator: <reply> · thread: <root comment url>
```

The reply is normalised to one line (`review_threads.reason_line`). Later runs re-read the reason from the
cited thread, not from the body, so an edited body line counts only while the thread still says it. A
security thread resolved with no trusted reply leaves its entry `open`, and the evaluator asks for the
reason on its `Security` card (`skills/_shared/security-review.md`).

## 10. The two write ops

Both live in `scripts/gh_persist.py`. Each stamps every body itself into `<body>.stamped.md`, size-gates it
before the first `gh` call, reports its hash in the receipt, and passes it as `-F body=@<stamped path>`.

- `review-post <owner/repo> <pr> <spec.json> [--dry-run]` posts one `COMMENT` review carrying every thread
  in the spec. The spec is `{pr, head, review_body, threads: [{f, path, subject, start_line, line, flags,
  supersedes, body}]}`. `review_body` and each thread's `body` are **paths** to the text the model wrote.
  `subject` is `line` (`start_line`..`line`) or `file`. Each entry names its own body file, since each is
  stamped beside it; a spec naming one file twice, a missing or wrong-type field, or a value the stamp
  cannot carry is a usage error, with nothing written. The review is created pending at `head`, each
  thread is added to it (a line range on the diff's right side, or the whole file), then it is submitted.
  If this account already has a pending review on the PR, review-post checks it first. One whose body
  carries the review stamp and whose every comment carries a thread-root stamp is the pipeline's own,
  stranded by a failed run, and is deleted. Any other is the operator's: their draft, or a stranded
  review they added line comments to in GitHub's UI (those go into the pending review). The op then
  exits 1 with stderr `REVIEW_PENDING_CONFLICT:` and touches nothing; the operator submits or discards
  it and re-runs. A submit that exits non-zero is read back: a review no longer pending landed, and is
  reported `ok`.
- `review-reply <owner/repo> <pr> --thread <node-id> --head <pushed sha> [--f <n>] <body> [--dry-run]`
  replies in one thread. `--thread` is the thread's `id` from `facts.review_threads.threads`. It reads the
  thread first and refuses a resolved one, one on another PR, or a node that is not a thread: `ok` with
  `posted: false` and `refused: resolved | other-pr | not-a-thread`, nothing written — a human may
  resolve an addressed thread between the caller's read and its reply, and the caller skips it. Any
  resolved thread is refused, an untrusted resolution included; the operator unresolves that one by hand.

## 11. Never resolve

The pipeline never resolves or unresolves a review thread, by any route. That is what lets a reader count
this account's resolution as a human's (§5): if the pipeline could resolve a thread, a resolution would
prove nothing. No op in `gh_persist.py` offers it. `tests/test_review_threads_contract.py`
(validator 8) fails on the mutation's name anywhere under `skills/` or `scripts/`, in prose and code alike.
So describe the rule in words. The GraphQL names live only under `tests/`.

## Facts

`facts.review_threads` (both preps): `{present: false}` when no PR is read — the evaluator on a PR that is
not open, the resolver with no continued PR or integration PR, and on its `--refresh` — else

```
{present, available, viewer, head, threads_path, threads: [{id, url, f, o, origin, state, pending_reply, path,
 subject, range, reference_sha, flags, supersedes, superseded_by, reply_reason}], counts,
 untrusted: {comments, threads, resolutions}, conversation_path, next_f, truncated}
```

- `head` is the commit the threads were judged at (§6).
- `origin` is `pipeline` or `operator`. `superseded_by` is the `F<n>` of the trusted pipeline thread whose
  stamp names this one (every thread carrying that `F` id, should one id name two); a thread with
  `superseded_by: null` is a **chain head**.
- `reply_reason` is the latest trusted human comment after the pipeline's last trusted marked comment (on an
  operator thread with no reply, its root), normalised to one line; `null` when the pipeline answered last.
  A comment the pipeline has since answered is not the human's last word, so it never reads as a reply.
- Comments of this account's own unsubmitted (`PENDING`) review are skipped, and so is that review in the
  conversation: no one else can see them, so a review a failed `review-post` left pending never gates.
- `counts` counts every state over the listed threads.
- `threads_path` holds each listed thread with its trusted comments. `conversation_path` holds the PR's
  trusted top-level comments and review bodies, each `{kind, author, body, createdAt, url, marked, state}`.
- A failed or partial read is `available: false` with `REVIEW_THREADS_UNAVAILABLE`, and no gate may approve
  on it. More data than one read carries is `truncated: true` with `REVIEW_THREADS_TRUNCATED`. These are
  notices, never a decision code.
