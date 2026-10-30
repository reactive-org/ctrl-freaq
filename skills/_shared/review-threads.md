# Review threads — shared contract

A **review thread** is a GitHub PR review comment pinned to code, which a person can reply in and
resolve. The evaluator posts threads on the code most worth the operator's attention and does not propose
APPROVE until a trusted human has accepted them (#145, #169); the resolver posts its own at a review pause, so a
phase's review is on the PR as soon as the phase ships (#170). This file is the single source of truth for what
the pipeline writes in a thread, how a thread's state is derived, and whose comment or resolution counts.

- **Writers:** the **evaluator**, which posts one review carrying the threads at the head it gates
  (`gh_persist.py review-post`), the **resolver**, which posts one review per review pause at the head it
  pushed (`review_threads.py pause`, then `review-post` with the spec's `phase`;
  `skills/resolver/references/phase-continuation.md` §2.2) and replies in each thread its fix or revision round
  addressed (`gh_persist.py review-reply`) — never a decision reply, and it never resolves or unresolves a
  thread (§11) — and any session that records an operator's consent-card answer
  as a **decision reply** (`review-reply --decision`, §10, #169): the **review** tool (`skills/review/`, #171)
  posts one per `Thread` card answer that records a decision (**Skip for now** posts none), at the PR head it showed the operator.
- **Readers:** `scripts/review_threads.py`'s `read_threads`, composed by `prep_evaluator.py` and
  `prep_resolver.py` as `facts.review_threads`, and by `scripts/prep_review.py`, which orders the threads
  waiting on a person into the review tool's `items`.
- **Renderings:** the resolver's in `skills/resolver/references/review-fix-round.md`, the evaluator's in
  `skills/evaluator/references/review-threads.md`, the review tool's in `skills/review/playbooks/review-flow.md`. Render this file; never restate it.

## 1. Why it exists

`scripts/blast_radius.py` already knows which code a change put at risk: a sensitive-path match, a file
the plan never named, a site the blast-radius judgment cited. Open `## Security review` findings were also
known. None of that reached the person reviewing the PR, and nothing on GitHub recorded that a person
looked at the flagged code. The operator is often the PR's author, and GitHub won't let an author approve
their own PR. A resolved thread is the record. It lives in the tool built for reviewing code, and the
reply a person types there reaches the resolver as data instead of chat.

A thread is pinned to a line at one commit, so a thread opened mid-flight would go stale as later fix rounds
and phases move the code. The supersession rules keep it live: an accepted thread whose code moves reads
`superseded` (§4 rules 2–3), and since #169 so does an unreviewed pipeline thread (rule 7), and the next post — the next pause's or the evaluator's — gives it one
successor (§7). That is why the resolver can post at a review pause (#170) as well as the evaluator at the head
it gates. Otherwise the resolver only replies.

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
| A thread's root | `<!-- F<n> · head <40-hex> · anchor <path>:<start>-<end>\|<path>:file · flags <key>,<key>[ · supersedes F<m>\|<human root comment url>][ · group sensitive][ · advisory][ · phase <N>] -->` | `**F<n>**`, the visible heading |
| A reply | `<!-- reply F<n>\|reply · head <40-hex>[ · decision accept\|changes\|dismiss] -->` (bare `reply` in a thread with no `F` id) | the reply text: on a decision reply, `accept: <text>`, `changes: <notes>` or `dismiss: <reason>` |
| The review body | `<!-- review · head <40-hex>[ · phase <N>] -->` | the review text |

`head` is the commit the comment was written against: the gated head on a root, the pushed commit on a
reply. `anchor` is where the root sits at that head: a line range, or `file` for a whole-file thread.
`flags` lists the flag keys (§7) the thread carries, comma-separated. A key embeds a path, so a `%` or
`,` inside one is written `%25` / `%2C`; spaces stay as they are.

`phase` (#170) appears only on a resolver **review pause**'s review and on every root it carries: the phase
whose shipped diff the pause reviewed, a positive integer, last on the line. It is how a reader selects one
pause's threads and its review body (`threads[].phase`, a review entry's `phase`, Facts). A changes-required
round's pause posts again for the same phase, so one phase can have several pause reviews: a reader takes the
union of their threads. A stamp without the field is byte-identical to one written before it, which is what the
evaluator writes.

`group sensitive` and `advisory` (#181) appear only on a root, after `supersedes` and before `phase`, and only
where the script writes them: `group sensitive` on a **sensitive group** (§7), every key `sensitive:`, and
`advisory` on an **advisory batch** (§7), every key `routine:`. A reader counts each only on a trusted pipeline
root whose keys carry it (`read_threads`' `group` / `advisory`), never inferring either from the keys or the
members' kinds: a thread without the field is never a group and never advisory. `review-post` refuses a field on a
key set that cannot carry it. A stamp without them is byte-identical.

`decision` (#169) appears only on a **decision reply**: the operator's answer on a consent card in a chat
session, recorded by `review-reply --decision` (§10). The closed set is `accept`, `changes` and `dismiss`
(`review_threads.DECISIONS`). The script writes the field and the visible word that opens the reply text;
the model writes only the acknowledgement, the notes or the reason after it. Readers key on the stamp field,
never on the visible word, so a hand-typed `accept:` is a human's comment like any other. A decision counts
only on a trusted, marked reply **by the viewer** (`review_threads.annotate_decisions`): another account's
decision stamp, a resolution-grade one's included, is read as a plain marked reply (moving the reference
commit when its author may mark, §5) and carries no decision.

**A marked comment is the pipeline's only when its author is trusted** (§5) and its second line is a stamp
this grammar reads. A marked comment by any other author is untrusted text, ignored like any other: it
neither resets a state nor moves a thread's reference commit. A trusted author's marked comment also counts
as the pipeline's only when that author is the viewer or holds a resolution-grade permission (§5): a marked
reply moves the reference commit a resolution is judged against (§6), so an author trusted to comment but
not to resolve could otherwise re-base a write-holder's resolution onto code no one reviewed (SEC-3).
Anyone else's marked comment is read as their human text. A trusted comment carrying the marker with
no stamp under it (a pasted pipeline comment, say) is a human's text, and a thread it opens is the
human's. The pipeline writes as the operator's own GitHub account, so the marker is what tells
its comments from the operator's. That is why the script, never the model, stamps it.

## 3. `F<n>` ids

Each pipeline-rooted thread carries `F<n>`, stable and never reused. The next id is one more than the
highest `F<n>` any trusted root's stamp carries in the PR, a root demoted under §2 included, so no id is
reused (`facts.review_threads.next_f`). A thread posted to
replace a superseded one takes a new id and stamps `supersedes F<m>`, or `supersedes <url>` when the old
thread was a human's. Its visible text links the old root comment. A thread a trusted human opened carries
no stamped id. `read_threads` numbers those `O<n>`, 1-based, by root `createdAt`.

## 4. States

`scripts/review_threads.py`'s `derive_state` derives each thread's state, a closed set, from GitHub's
thread-resolution data, its trusted comments (§5) and the hunk check (§6):

| State | When | Gates APPROVE |
|---|---|---|
| `unreviewed` | unresolved, with no trusted human comment after the pipeline's last marked comment, and its anchored hunk unchanged since | yes, unless advisory |
| `accepted` | resolved by a trusted human, or carrying the viewer's standing `accept` or `dismiss` decision reply, its anchored hunk unchanged since | no |
| `changes-requested` | unresolved, with a trusted human comment after the pipeline's last marked comment, or the viewer's standing `changes` decision reply | yes |
| `operator-thread` | a thread a trusted human opened that is still unresolved and carries no standing decision | yes |
| `superseded` | accepted (by a resolution or a decision), or an unreviewed pipeline thread, whose anchored hunk changed after the reference commit (§6) | until a fresh thread replaces it, unless advisory |

The **standing decision** (#169, `review_threads.standing_decision`) is the decision of the thread's latest
trusted comment, when that comment is the viewer's decision reply (§2). Any later trusted comment ends it: a
person's unmarked comment, which then reads `changes-requested` by rule 6 (`operator-thread` by rule 5 on a
thread a person opened), or another marked reply.

**Precedence**, first match wins:

1. Resolved with trust, hunk unchanged → `accepted`.
2. Resolved with trust, hunk changed → `superseded`. A resolution outranks any reply.
3. A standing `accept` or `dismiss` decision → `accepted` while the hunk is unchanged since the reference
   commit (the decision reply's own `head`, §6), else `superseded`.
4. A standing `changes` decision → `changes-requested`.
5. Unresolved, rooted by a trusted human → `operator-thread`. It carries `pending_reply: true` while the
   latest trusted comment is a marked one: the resolver has answered and a human is still to look.
6. Unresolved, with a trusted human comment after the latest marked comment that counts (§2) → `changes-requested`.
7. A pipeline thread whose hunk changed since the reference commit → `superseded`; otherwise → `unreviewed`.

Rules 3 and 4 apply on either origin, and rank above rule 5, so the operator can answer a thread they opened
in GitHub from a chat session. So `operator-thread` outranks `unreviewed` and `changes-requested`, and a
standing decision outranks `operator-thread`. `pending_reply` is false on every other state. An untrusted
resolution reads as unresolved. A thread whose root is untrusted, marked or not, gates nothing: it is counted
in `untrusted.threads` and listed nowhere else.

Rule 7 (#169) keeps a thread posted mid-flight from going stale at its old anchor: an `unreviewed` pipeline
thread whose code moves is replaced by a successor (§7), as a resolved one is. It never applies to a
`changes-requested` or `operator-thread` thread. Those carry a person's words, and the resolver's fix commit
changes the hunk before its fix reply lands, so superseding them would replace a request the resolver is about
to answer with a successor that carries keys, never words. A thread re-based by a fix reply is not re-posted:
the reply's `head` becomes the reference commit, so the check reads only changes after the fix.

**What a successor replaces** (#163). A successor (§7 "Successors") carries a predecessor's keys or members,
never a person's words. A thread a trusted pipeline successor names (`superseded_by` set) is **`replaced`**,
and gates nothing, exactly when it is `superseded`, or it is a batch (every key `routine:`) or a sensitive group
(§2, #181) in `unreviewed` or
`changes-requested` and no trusted human (unmarked) comment, and no viewer's decision reply (§2, #169), on it is
newer than its successor's root (with
several successors naming it, the earliest root). Every other thread gates by its state, named or not: a
person's newer request on a replaced thread — a batch or group commented or decided on after its re-cut, a superseded thread
someone unresolved and commented on again — still gates. A gate reads `replaced`, never `superseded_by`,
whose one job is deciding chain heads (§7 "Idempotence"); a gate also treats a thread this run's posted
`new[].supersedes` names as replaced, since every comment on it predates that successor. Only `read_threads`
sets both fields, `superseded_by` only from a trusted pipeline root's stamp and `replaced` only against
trusted comments, so an outside account can neither un-gate a thread by naming it nor re-gate a named batch.

**Advisory batches** (#181). A thread `read_threads` reports `advisory` (§2) gates nothing while `unreviewed` or
`superseded`; it still gets its successor (§7), and `changes-requested` gates and soft-rejects as on any batch, so a
person's request is never dropped. This is the one exception to "every routine file acknowledged by a person"
(#163), agreed with the operator on 2026-10-30 to keep test churn off the gate. **Its accepted cost:** a wrong `test`
or `fixture` triage verdict can let a file merge with no one having acknowledged it. One `fallout` member, or one
kept with no verdict this run, keeps the batch gating, and the field is script-written when the batch is cut,
because member kinds are not durable and a reader must never infer advisory from them. It is re-derived on every
run that has a readable triage (an `unreviewed` advisory batch that is no longer advisory gets a successor, §7); a
review pause, which runs no triage, never changes it.

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
- **A marked comment** (§2) additionally needs its author to be the viewer, or to hold one of those same
  permissions, read by the same lookup. A trusted author without one (`triage`, `read`) has the comment
  read as their human text: it moves no reference commit and resets no state as the pipeline's, and it
  does count as a human comment. A failed lookup for such an author makes the whole read
  `available: false` (`REVIEW_THREADS_UNAVAILABLE`): demoting their roots would cost them their `F<n>` and
  their chain heads, so `flags` would post their signals again.
- **A decision reply** (§2, #169) counts as the operator's answer only from the viewer: a trusted, marked
  reply by this account whose stamp carries `decision`. No permission lookup applies, because the op that
  writes it is the pipeline's own and the account is the operator's. This widens the #145 rule, under which
  only a GitHub resolution could accept a thread. The operator chose the trade to remove the trip to GitHub:
  a card-backed decision is as trustworthy as the evaluator's `Security` consent card, and weaker than a
  resolution, because a model that misreads an answer could record a decision the operator didn't make. That
  is why `--decision` carries only an answer the operator gave on a consent card in the same session (§10),
  and why a decision from any other account, a resolution-grade one included, is never read as one.
- **Unknown is untrusted.** A missing association, a permission of `read` or `none`, any other value, and
  a failed lookup (`THREAD_PERMISSION_UNAVAILABLE`) all count as untrusted.

## 6. Hunk unchanged

Whether a thread's code changed since is a script `git diff`, never GitHub's `isOutdated`, whose
meaning is undocumented. `isOutdated` would also mark a thread the resolver fixed and a human then
resolved as superseded, so every fix cycle would post a duplicate.

- **Reference commit:** the `head` on the thread's latest marked comment that counts as the pipeline's
  (§2, §5: by the viewer or a resolution-grade account), or the root comment's
  `originalCommit` when it has none. On a standing decision (§4) that is the decision reply's own `head`,
  so the decision holds for the code the operator reviewed and no later code. The op cannot check that the
  `head` it is given is that commit; its caller owns passing the head it showed the operator (§10).
- **Which threads are checked:** a trusted resolution, a standing `accept` or `dismiss` decision, and an
  unreviewed pipeline thread (§4 rules 1–3 and 7). A `changes-requested` or `operator-thread` thread runs no
  `git`.
- **Reference range:** the root's anchor (its stamp, or `originalStartLine`..`originalLine` on a human
  root), mapped from the root's commit to the reference commit (`review_threads.map_range`). Lines outside
  a hunk shift by the running delta; a hunk whose old side intersects the range widens it to the hunk's
  new side.
- **A multi-file thread** (#163, #181) — any pipeline thread whose subject is `file` — runs the `file` check below
  once per distinct path its anchor and its keys name (a security key names none), and reads changed when any one
  did: a batch's members, a sensitive group's members and a rider's test (§7) alike, so a new hunk in any of them
  after the acceptance supersedes the whole thread, as one file thread per path would have been. On a single-file
  thread that is its one path, as before.
- **Changed:** any hunk of `git diff -U0 <reference> <judged head> -- <path>` whose old side intersects the
  reference range. `<path>` is matched literally, never as a pattern, and diffed raw (no textconv). A
  `file` anchor counts any hunk. So do a path absent at the reference or at the judged head that was
  present at the thread's root commit (deleted or renamed after the thread was opened), a reference
  commit missing locally (`git cat-file -e` fails, after a force-push, say), and a binary or mode-only
  change, whose diff has no `@@` hunk. A human comment on the diff's base (`LEFT`) side numbers its lines
  in the base file, so it is checked as a `file` anchor. A `file` check whose path was absent at the root
  commit too (a base-side comment, or a file thread, on a file the PR deletes) was opened on the
  deletion, so the diff alone decides: absent at both is unchanged, restored is changed. A root commit
  missing locally keeps the path rule. The check stays conservative: an accepted thread re-threads, it
  never wrongly counts as accepted. On an unreviewed pipeline thread the conservative side costs a
  successor (§7), never a wrong acceptance: outside a checkout every reference commit is missing, so every
  unreviewed pipeline thread reads `superseded`.

The judged head is the evaluator's gated head, or the resolver's workspace HEAD, or the review tool's PR head.

The reference commit is what the human last heard about before resolving only because **the pipeline
never replies in a resolved thread** (§10): GitHub keeps a thread resolved when someone replies, and
records no resolution time to order the two. A reply posted after a resolution would move the
reference, so the resolution would count for code the human never saw. The one residual is a human
resolving in the instant between `review-reply`'s check and its post. The pipeline also posts no plain reply in
a thread whose standing decision is an `accept` or `dismiss` (§10, `refused: decided`), for a different reason: a
decision stands only while it is the thread's latest trusted comment (§4), so a later pipeline reply would void
the operator's recorded acceptance and put the thread back under review. The refusal holds only while the
decision stands. Once a person comments after it, the acceptance no longer counts, so a reply voids nothing.

## 7. Flags, grouping and the cap

A **flag** is one reason a piece of code gets a thread. Its key names the kind and the code:

| Key | Source |
|---|---|
| `sensitive:<path>` | a sensitive-path match (`blast_radius.py assess`'s `sensitive.matches`); one per member on a sensitive group (#181) |
| `out-of-plan:<path>` | a changed file no planned span matches (`out_of_plan`), less the exemptions below, that the triage did not call routine; on a subject's thread, also one per rider (#181) |
| `routine:<path>` | a member of the batch: an out-of-plan file the triage called routine (#163) |
| `judgment:<dimension>:<path>` | a site the blast-radius judgment cited (`judgment.signals`) |
| `security:SEC-<n>` | an `open` `## Security review` entry |
| `plan:<path>` | a finer plan deviation the evaluator's plan-adherence check proposed, or one the resolver's review pause names |

A resolver **review pause** (#170) reads the same sources for the phase it shipped — S5.2's shipped-diff
`assess`, the open `## Security review` entries and the deviations its `## Session summary` names — and it
dispatches no triage, so every out-of-plan file keeps its own thread, no file joins a batch or rides another's
thread, and no batch is advisory; only a re-cut successor of an existing batch carries `routine:` keys. Sensitive
groups apply at a pause as anywhere, with one exception: a pause's patterns come from its phase-scoped `assess`,
so a group head none of whose members this phase touched has no pattern and takes no fresh members, and the pause
may open a second group of that pattern, which the evaluator's run never merges. It adds no
key: a pause thread is an ordinary chain head, matched, superseded and capped like any other, so the evaluator's
later flags post no duplicate of it.

A failed sensitive-path read has nothing to anchor, so it gets no flag. It still forces the merge gate to
`ask`; at a review pause it goes in the review body.

- **Out-of-plan exemptions** (#163). Two kinds of out-of-plan file get no `out-of-plan:` flag; each is reported
  in `flags`' `exempt` list as `{path, rule}`, and nothing else about it changes — `blast_radius.out_of_plan`, the
  review-level raise and the resolver's `## Out-of-plan changes` list keep it:
  - `basename` — its basename equals a planned span with no `/` (the assess payload's `planned_spans`), and it
    is the only changed file with that basename. Files sharing the basename all keep their flags. A span that
    names an entry (a file or a directory) at the repo root at the gated head is a literal path, not a
    basename, and exempts nothing: a plan naming the root `README.md` does not exempt `docs/README.md`. A
    failed listing of the root exempts nothing by this rule (`OUT_OF_PLAN_ROOT_UNREAD`, a notice). This is a
    thread-side exception to the literal planned-span rule: it never widens the grammar the raise and the
    sensitive gate share.
  - `generated` — the repo marks it `linguist-generated` (`.gitattributes`) at **both** the merge base and
    the gated head, read from the committed `.gitattributes` files, never the working tree or the user's or
    system attribute files (the checkout's own `.git/info/attributes`, which no PR can write, still counts). A
    PR that adds the attribute
    cannot exempt the file it adds it to, as a PR cannot loosen its own sensitive-path gate. A failed read on
    either side exempts nothing by this rule (`OUT_OF_PLAN_GENERATED_UNREAD`, a notice).

  A file named `.gitattributes` is never exempt, by either rule: it decides what the generated rule reads. A
  file both rules exempt is reported under `basename`. `review_threads.py narrow` applies the same
  exemptions on their own and writes the files left as `out-of-plan-files.txt`, one per line, with its
  `## Out-of-plan changes` reason appended only when the evaluator says the reasons are trusted
  (`facts.out_of_plan.reasons_trusted`, §8); `flags` re-applies the same rules to the same assess payload
  itself, so the two lists agree unless one of the two runs' git reads fails (each failure is its notice).

- **Anchoring** reads `git diff -U0` over the gated range (`origin/<base>...HEAD`, at `assess`'s merge base).
  A review pause anchors on the PR's whole diff too — from the merge base of `origin/<base>` and the pushed
  head, never `assess`'s phase-scoped range — so an earlier pause's thread whose code this phase moved gets its
  successor rather than retiring, a batch member keeps its batch, and GitHub accepts every line (it takes a
  review comment only on a line in the PR's diff).
  A site anchors on its range when that range lies wholly inside one hunk's new side, else on its whole
  file. A file signal (`sensitive`, `out-of-plan`, or a security entry with no site) anchors on its whole file, so
  its thread's hunk check reads every later change to the file: a hunk pushed anywhere in it after the
  resolution supersedes the thread (SEC-2, where a first-hunk anchor let a later hunk pass unread).
  A model-written line whose path is not in the diff is `rejected`; a security entry whose path is not in it
  is `unanchored_security`.
- **Grouping.** Flags in the same hunk share one thread, with the union of their ranges and keys.
- **The triage and the batch** (#163). After the exemptions, the evaluator's out-of-plan triage
  (`skills/evaluator/references/out-of-plan-triage-prompt.md`) calls each remaining file `routine` (kind `test`,
  `fixture` or `fallout`) or `consequential`. A consequential file keeps its own `out-of-plan:` thread. Every
  routine file shares **one batch**: a file thread whose stamp carries one `routine:<path>` key per member, anchored
  on the first member, in sorted path order, with new-side lines (else the first in the diff). A file carrying a
  `sensitive`, `security` or `judgment` flag is never routine. With no readable verdict
  — none dispatched, unreadable, no `## Triage` section, or `code: AMBIGUOUS` (`OUT_OF_PLAN_TRIAGE_UNREAD`) — and
  for any file the verdict omits or leaves unparsed, every out-of-plan file keeps its own thread, as before #163.
  Each `new` batch entry carries `members` (`[{path, kind, reason}]`, `reason` only when
  `facts.out_of_plan.reasons_trusted`).
- **Advisory batches** (#181). A batch whose every target member's triage kind this run is `test` or `fixture` is
  stamped ` · advisory` (§2) and gates per §4, which states the accepted cost. A `fallout` member, or a member kept
  with no `routine` verdict this run (`kind: null`), means no field. The script computes it once, over a batch's
  final target set (`review_threads._advisory`), whether the batch is fresh or a successor, so it is re-derived on
  every re-cut; a `new` entry reports it as `advisory`. On a run with a readable triage, an `unreviewed` head stamped
  advisory whose target set is no longer advisory gets a successor even when its members are unchanged, so a stale
  stamp never keeps it from gating; a run without one (no verdict, or a pause) never re-cuts for advisory.
- **Riders** (#181). The triage names a consequential test or fixture's **subject** — another listed file — by
  opening its evidence with ``subject `<path>`; `` (`parse.scan_out_of_plan_triage`'s `subject`; a subject no line
  lists is dropped). Such a file rides its subject's thread as one more `out-of-plan:<path>` key, adding no key kind,
  when all hold: the subject's `out-of-plan:` thread posts this run (fresh, or a successor); the file carries no
  `sensitive`, `security` or `judgment` flag; no split line names it; and its own flag matched no chain head.
  Otherwise it keeps its own thread. A subject counts only when it posts its own thread: a test whose subject
  itself rides another file's thread, this run or from an earlier one, keeps its own thread (riders never chain). A rider never joins a live head: re-cutting a thread a person may be reading
  would save one acknowledgement at the cost of their place. On later runs the rider's key matches the subject's
  thread by kind and path (Idempotence). Each `new` entry reports its `riders` (`[{path, subject, reason}]`, `reason` only when the reasons are trusted); a generic successor lists every `out-of-plan:` key on another file than its anchor, the predecessor's riders as well as those joining, its `subject` the anchor path when this run names none.
- **Sensitive groups** (#181). A file whose only flag this run is a sensitive match joins the group of the first
  declaration entry it matches, in `sensitive.matches` order (declaration order, self-edit pairs first): one file
  thread stamped ` · group sensitive`, one `sensitive:<path>` key per member, anchored like a batch. A match with no
  pattern names no group, so its file keeps its own thread. The group's pattern is recomputed every run from its
  kept members' first matches (the first member in sorted order with one decides), never stored. Group chain heads
  re-cut through `supersedes` successors as batches do: a member leaves when it gains another flag (it takes its own
  thread) or its path left the gated diff, and one merely no longer sensitive stays; an `unreviewed` or `superseded`
  head takes its kept members plus the fresh members of its pattern (the first such head in `F` order), an
  `unreviewed` one only when that set differs; an `accepted` or `changes-requested` one is left as it is, and fresh
  members of its pattern form a new group; an emptied group `retires` (`group-emptied`). There is no split for a
  group. Each `new` entry reports `group` (`sensitive` or `null`) and the group's `pattern`.
- **Existing threads are never re-covered.** A thread without the `group` field is never a group, so an old per-file
  `sensitive:` head keeps matching its file (Idempotence) and keeps gating by its state, and a superseded one gets
  the one-file successor below. An old per-file `out-of-plan:` head likewise matches its file, so the file never
  rides another thread.
- **Batch membership.** Resolving the batch accepts every member. A comment naming members asks to split them
  out. The evaluator reads split requests from **every** trusted human comment on a batch chain head (its
  `comments` in `threads_path`), never from `reply_reason` alone, which resets once the resolver replies; a later
  comment withdrawing a split cancels it. A marked comment is never a split request, the operator's decision
  reply (§2) included: a `changes` decision's notes never split a member out (#169). It writes one `F<m> <path>` line per member to split (`flags
  --splits`; a line naming no batch chain head, or no member of it, is `rejected` as `not-a-member` and moves
  nothing), and the split file gets its own thread. **One exception:** while a head is `changes-requested` and
  its latest trusted human comment asks for more than a split, or its standing decision (§4) is `changes`, it
  writes no line for that head this run — the
  resolver answers the change and replies, and the next run writes the split from the history; re-cutting now
  would replace the batch and let that request stop gating. Each batch chain head has a **target set**: its
  members, minus each member that **leaves**, plus, on the first eligible head in `F` order, every routine file
  no batch carries. A member leaves only when a split line names it, when it takes its own out-of-plan thread
  this run (reclassified consequential, omitted or unparsed by the verdict, carrying a `sensitive`, `security`
  or `judgment` signal, or no readable verdict), or when its path, followed through a rename, left the gated
  diff. A member merely no longer out of plan — now planned, now exempt, or a run with no plan — stays: it
  still waits on a person's acknowledgement, so an absent signal never empties a batch. An `unreviewed` batch
  and a `changes-requested` one a split line names get a successor stamped `supersedes F<m>` carrying exactly
  the target set when it differs from the members; a `superseded` one always gets one (re-anchored on another
  member if its anchor left the diff); an empty target set `retires` it (`batch-emptied`: every member split
  out, took its own thread, or left the diff). An `accepted` batch, or a `changes-requested` one no split line
  names, is never re-cut — that would discard a person's acceptance or answer — so a newly routine file never
  joins it, and a member that leaves it gets its own thread now. On an accepted batch the stale key holds no
  member's acknowledgement, though the member still feeds the batch's hunk check (§6): a later change to it
  supersedes the batch, whose successor drops the key. A `changes-requested` one still gates by its state until
  the resolver replies, and a later re-cut drops the key. A split request posted on a batch a successor names
  is not applied there (splits are read on chain heads): the resolver's reply points the person to the chain
  head. A routine file whose own `out-of-plan:` chain head exists stays with that head. Members the leave
  rule keeps with no `routine` verdict this run carry `kind: null` in `members`.
- **Cap.** At most `THREAD_CAP` (8) new threads per review. Over the cap, a file with several threads
  collapses them into one file thread (never the batch or a sensitive group, each one candidate, #181). If that is
  still over, threads are kept in priority order security > sensitive > judgment > out-of-plan > routine > plan (a
  group ranks `sensitive`); the rest are `overflow`, listed in the review body, a new batch past the cap as one
  `{key: routine, paths}` entry, a new group as one `{key: sensitive, pattern, paths}` entry, a rider beside its
  subject's as `{key, path, subject}`, and a security flag among them joins `unanchored_security`.
- **Idempotence.** A flag matches a **chain head** — a trusted pipeline thread no successor names
  (`superseded_by: null`) carrying a key of the same kind and path (the same `SEC-<n>` for security; a `routine`
  flag also matches an `out-of-plan:` key on its path, and a batch or group head only through its target set). A file
  signal matches on that alone, since it names a file, not a site; a site
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
  from the cap: they exist to unblock the gate. A batch or group head never takes this generic successor: its
  successor carries its target set, never its raw keys (Batch membership, Sensitive groups).

`review_threads.py flags` computes all of this (`narrow`, the exemptions alone); the evaluator's S4.6 runs it
(`skills/evaluator/references/review-threads.md`). `review_threads.py pause` (#170) runs the same over a pause's
signals, after reading the threads itself at the pushed head (the resolver's prep read them before the push),
and always writes the spec, carrying `phase`, when the read and the diff succeed; a failed read writes none
(`REVIEW_THREADS_UNAVAILABLE`), since every flag would look new and reuse a live `F` id.

## 8. `## Out-of-plan changes`

A resolver-written PR-body section, read by a script (`parse.scan_out_of_plan`). It has one line per file
`blast_radius.py` reported as out of plan, saying why the change departs from the plan:

```
## Out-of-plan changes
- `scripts/importer.py` — the retry needed a shared backoff helper the plan's file list did not name
```

The section is kept across phases, keyed by path. The evaluator quotes the reason in that file's thread, but only
when `facts.out_of_plan.reasons_trusted` holds: it is body text, trusted on the #144 terms (a PR this account opened,
or one whose author's association is trusted), since the thread is posted under this account. The resolver's
`facts.out_of_plan.reasons_trusted` reads no association: it holds on this account's own PR, as
`facts.polish.dispositions_trusted` does, and is `false` with no PR read; a fresh first phase's pause passes
`true`, since that session's own `create-pr` opened the PR (#170).
`## Plan override` keeps its own meaning: the PR proceeded without a plan.

## 9. Thread-cited security dismissal

A security thread's root carries the finding's claim on its own line, `Claim: <claim>`, verbatim from the record
(`flags` reports it as each new thread's `claims`); the reader reports it as the thread's `claim`. The operator
dismisses by replying `dismiss: <reason>` and resolving. When every gating thread is `accepted`, that reply
(`dismiss_reason`: by the viewer or a resolution-grade author, §5 — a trusted author who could not resolve the
thread cannot dismiss through it either, SEC-4) on a security finding's own thread — a pipeline thread whose flags carry
`security:SEC-<n>` — becomes that entry's dismissal in the `## Security review` record, its reason the text after
`dismiss:` (`review_threads.dismissal_reason`). The line cites the thread:

```
- SEC-<n> · dismissed — <claim> — <anchor> — operator: <reason> · thread: <root comment url>
```

The reason is normalised to one line (`review_threads.reason_line`). Later runs re-read the reason from the
cited thread, not from the body, so an edited body line counts only while the thread still says it. It counts
only when the reply leads `dismiss:`, the thread's `claim` equals the entry's claim (SEC-1) and the entry's anchor
path is the thread's path (SEC-5: a rename after the dismissal un-confirms it, and the operator is asked again):
any other trusted
reply — a fix request included — is never a dismissal, whatever a body line later cites it as, and a body line
rewritten to describe another finding cannot borrow the thread. A security thread accepted with no `dismiss:`
reply — resolved, or by the viewer's `accept` decision reply (§4) — leaves its entry `open`, and the evaluator
asks for the reason on its `Security` card
(`skills/_shared/security-review.md`).

The second way to dismiss on the thread (#169) is the viewer's **`dismiss` decision reply** (§2), recorded
when the operator dismisses the finding on a consent card in a chat session. While it stands (§4) the thread
is `accepted` with no resolution, and its `dismiss_reason` is the reason after `dismiss:`. Everything else
above holds unchanged: it counts only on the finding's own thread (claim and anchor path), it is re-read from
the thread on every run, and it is recorded with the same line. `review-reply` refuses a `dismiss` decision
on a thread whose root stamp carries no `security:` key, the root read as the reader reads it (`refused:
not-security`, §10).

## 10. The two write ops

Both live in `scripts/gh_persist.py`. Each stamps every body itself into `<body>.stamped.md`, size-gates it
before the first `gh` call, reports its hash in the receipt, and passes it as `-F body=@<stamped path>`.

- `review-post <owner/repo> <pr> <spec.json> [--dry-run]` posts one `COMMENT` review carrying every thread
  in the spec. The spec is `{head, phase?, review_body, threads: [{f, path, subject, start_line, line, flags,
  supersedes, group, advisory, body}]}` (`group` and `advisory` the §2 fields, #181). `phase` (#170), a positive integer, is a resolver review pause's: the op stamps it on
  the review and on every root, and the receipt reports it (`null` without). `threads` may be empty only with
  `phase` — every pause posts its review, so its anchorless triggers reach the PR — and an empty list without it,
  or a `phase` that is not a positive integer, is a usage error, so the evaluator's post-only-when-new rule holds. `review_body` and each thread's `body` are **paths** to the text the model wrote.
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
- `review-reply <owner/repo> <pr> --thread <node-id> --head <pushed sha> [--f <n>] <body>
  [--decision accept|changes|dismiss] [--dry-run]` replies in one thread. `--thread` is the thread's `id` from
  `facts.review_threads.threads`. It reads the thread first and refuses a resolved one, one on another PR, or a
  node that is not a thread: `ok` with `posted: false` and `refused: resolved | other-pr | not-a-thread`
  (`decided | not-security` below), nothing written — a human may
  resolve an addressed thread between the caller's read and its reply, and the caller skips it. Any
  resolved thread is refused, an untrusted resolution included; the operator unresolves that one by hand.
  `--decision accept|changes|dismiss` (#169) makes it a decision reply (§2): the op opens the reply text
  with `<decision>: ` (unless the caller's file already does) and writes the decision into the stamp, and the
  caller's file is the acknowledgement
  (`accept`), the notes (`changes`) or the reason (`dismiss`), required and size-gated like any reply. Two
  more refusals follow `resolved`, on the same `ok` / `posted: false` terms. A plain reply (no `--decision`)
  in a thread whose standing decision (§4) is an `accept` or `dismiss` is refused `decided`, and a decision
  reply never is, since it is the operator's own later word and re-bases the thread on the code they just
  reviewed. A `dismiss` decision on a thread whose root stamp carries no `security:` key is refused
  `not-security`; a root stamp counts only as §5 has `read_threads` count it — this account's, or a
  resolution-grade author's (one permission lookup; a failed one refuses) — so the op and the reader agree
  on which threads are security findings. The receipt reports `decision` (`null` on a plain reply). **A caller passes `--decision`
  only to record the answer the operator gave on a consent card in that same session.** The op cannot tell a
  card answer from a model's choice, so this rule is what the trust trade in §5 rests on: the resolver's
  fix-round replies, and every other pipeline reply, never pass it. `--head` must be the commit the operator
  reviewed, since it becomes the reference commit (§6); the op cannot check it.

## 11. Never resolve

The pipeline never resolves or unresolves a review thread, by any route. That is what lets a reader count
this account's resolution as a human's (§5): if the pipeline could resolve a thread, a resolution would
prove nothing. No op in `gh_persist.py` offers it. `tests/test_review_threads_contract.py`
(validator 8) fails on the mutation's name anywhere under `skills/` or `scripts/`, in prose and code alike.
So describe the rule in words. The GraphQL names live only under `tests/`.

## 12. Decided deviations

A resolver-written PR-body section, read by a script (`parse.scan_decided_deviations`), #181. A plan deviation the
operator already decided on a card during the resolver run should not come back to them as a `plan:<path>` thread
(§7) asking the same question, and before this section the answer reached only the resolver's session summary,
which the evaluator never sees. One line per deviation:

```
## Decided deviations
- `src/importer.py` — retries on 429 as well as 503 — operator: keep both; the API returns 429 under load
```

- **Grammar.** The path is the backticked span before the first ` — `; the answer is the text after the **last**
  ` — operator: `, so a deviation that itself holds ` — ` still splits once. A line that does not parse is
  `unparsed`, never an entry.
- **Writer.** The resolver, for every operator answer on a card this run — a `Settled item` or `Decision` card in a
  fix round, finalisation's `Decision` card — that makes the build depart from the plan at a code site in the PR
  diff by a finer deviation, one that leaves every locked decision true. A reversal of a locked decision is never
  recorded here: the evaluator's S4 item 5 soft-rejects an undisclosed reversal whatever this section says. The
  section is kept across phases, keyed by path plus deviation text, as §8's is keyed by path; a repeated key
  replaces its line in place, since the scanner keeps a key's first line.
- **Evaluator.** `facts.decided_deviations` is `{present, entries: [{path, deviation, answer}], unparsed, trusted}`.
  Matching a proposed deviation to an entry is the evaluator's judgment at S4 item 5. A matched deviation is left
  out of `plan-threads.txt` and listed in the review's body when a review posts (and in the PR summary either
  way), only when `trusted` holds. `trusted` is §8's
  `reasons_trusted` value, on the same #144 terms: it is body text, and planted text would suppress a thread. A
  parsed entry is itself a trigger of the one association read, so a trusted author's PR whose only body
  record is this section still reads trusted. Untrusted, every deviation the check proposes gets its thread.
- **Review pause.** The resolver leaves out of the pause's `plan-threads.txt` any deviation the PR body's
  `## Decided deviations` already records, or the operator decided on a card this session, and lists it in the
  pause review's body. The pause runs on this account's own PR, so no trust read applies.

Script-filtering `plan-threads.txt` by path was rejected: one file can carry a decided and an undecided deviation.

## Facts

`facts.review_threads` (both preps; the review tool's prep reads it the same way, at the PR head, and reports its
selection of it as `items`): `{present: false}` when no PR is read — the evaluator on a PR that is
not open, the resolver with no continued PR or integration PR, and on its `--refresh` — else

```
{present, available, viewer, head, threads_path, threads: [{id, url, f, o, origin, state, pending_reply, path,
 subject, range, reference_sha, flags, supersedes, group, advisory, phase, superseded_by, replaced, reply_reason,
 dismiss_reason, claim}],
 counts,
 untrusted: {comments, threads, resolutions}, conversation_path, next_f, truncated}
```

- `head` is the commit the threads were judged at (§6).
- `origin` is `pipeline` or `operator`. `superseded_by` is the `F<n>` of the trusted pipeline thread whose
  stamp names this one (every thread carrying that `F` id, should one id name two); a thread with
  `superseded_by: null` is a **chain head**. `replaced` says whether that successor replaced it (§4): the field
  every gate reads; `false` on every thread no successor names.
- `group` (`sensitive` or `null`) and `advisory` (a bool) are the root stamp's fields (§2, #181), read only on a
  trusted pipeline root whose keys carry them; `null` / `false` on every other thread. A gate reads `advisory` (§4).
- `phase` is the review pause that posted the thread, from its trusted root stamp (§2, #170); `null` on an
  evaluator thread and an operator one.
- `claim` is a pipeline thread root's `Claim:` line, normalised; `null` when it has none and on an operator
  thread (§9).
- `reply_reason` is the latest trusted human comment after the pipeline's last counted marked comment (on an
  operator thread with no reply, its root), normalised to one line; `null` when the pipeline answered last.
  A comment the pipeline has since answered is not the human's last word, so it never reads as a reply. On a
  standing decision (§4) it is instead a `changes` decision's notes, the text after `changes:`, and `null` on
  an `accept` or `dismiss`.
- `dismiss_reason` is the text after `dismiss:` in that same comment, normalised, only when its author is the
  viewer or holds a resolution-grade permission (§5, SEC-4); `null` otherwise. On a standing `dismiss` decision
  it is that reply's reason instead. Only it can dismiss (§9).
- Comments of this account's own unsubmitted (`PENDING`) review are skipped, and so is that review in the
  conversation: no one else can see them, so a review a failed `review-post` left pending never gates.
- `counts` counts every state over the listed threads.
- `threads_path` holds each listed thread with its trusted comments, each `{author, body, createdAt, url, marked,
  decision}`: `decision` is the viewer's decision reply's value (§2), `null` on every other comment. `conversation_path` holds the PR's
  trusted top-level comments and review bodies, each `{kind, author, body, createdAt, url, marked, phase, state}`;
  a review body is `marked` on a review stamp by the viewer or a resolution-grade author (§2, §5), and `phase` is
  a marked review's pause phase (`null` otherwise, #170).
- `flags`' `new[]` entries (not a fact, the subcommand's payload) each carry `group`, `pattern`, `advisory` and
  `riders` (`[{path, subject, reason}]`, `reason` only when trusted, `[]` when none), as §7 defines them (#181).
- `facts.decided_deviations` (the evaluator's prep) is §12's record and its trust value; it is separate from
  `facts.review_threads`.
- A failed or partial read is `available: false` with `REVIEW_THREADS_UNAVAILABLE`, and no gate may approve
  on it. More data than one read carries is `truncated: true` with `REVIEW_THREADS_TRUNCATED`. These are
  notices, never a decision code.
