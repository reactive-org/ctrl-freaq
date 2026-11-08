# The rigor level — the coordinator's one GitHub write

The rigor level (#198) sets which review steps the stages run, not how hard each one looks
(`scripts/rigor.py`). Each stage's own prep resolves it again from GitHub and its checkout, so what the
coordinator shows is a preview: it reads the block in the project root, and a branch that changes the block
runs every stage at `full` whatever this shows.

`facts.rigor` is the target's level (`level`, its `source`: the block's effective `default` and `floor`, the
target's and its ancestors' labels, and `chosen_by`). `facts.rigor_offer`, computed from it, is what this tool may
write: `levels` (every level at or above the floor, strictest first — the only ones it ever writes),
`recommended` (the block's default), `first_run` (the card's trigger) and `target_labels` (the target's own
`rigor:*` labels, the only ones it ever removes).

## The status line

Every turn's status line names the level (router §4). When `facts.rigor.source.chosen_by` is not `default`, add
what chose it in a few words — a label and its issue, a malformed block, an unread label set.

## The first-run `Rigor` card

While `facts.rigor_offer.first_run` holds — the floor permits a lower level and neither the target nor an
ancestor carries a `rigor:*` label — render a card (`header: "Rigor"`) before the turn offers any stage start.
It writes a label, so it always renders and never only announces. Its brief says what the level sets, the
block's default and floor, what each offered level skips, and that the answer writes the label on #<target> —
which a story's run inherits from its epic, and which stops this card asking again. One option per
`facts.rigor_offer.levels` entry, `facts.rigor_offer.recommended` first and marked recommended. On the answer,
write `--add rigor:<answer>` ("The write"), announce it in one line, re-run prep, and carry on with the turn.

## A change the operator asks for in chat

Their own words in this chat are the go-ahead, as a card answer is. A level outside `facts.rigor_offer.levels`
is refused in plain words naming the floor (`facts.rigor.source.block.floor`) — and, when `facts.rigor.source.block.present`
is false, saying the repo has no usable rigor block (absent, or malformed: its notice names the line), which is why
every run is `full` — and nothing is written.
Otherwise write `--add rigor:<level>` with one `--remove` per other `facts.rigor_offer.target_labels` entry. A
stricter label on an ancestor (`facts.rigor.source.labels`) still wins: say so, naming that issue — this tool
writes only on its target. A running stage keeps the level its prep resolved; the next stage started reads the
new label.

## The write

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-labels <owner/repo> <target> --add rigor:<level> [--remove <label>]...
```

`LABEL_MISSING: <label>` on stderr (exit 1, no envelope) means the repo has no such label yet: say so in plain
words, say that `/ctrl-freaq:setup` creates the three when it writes the rigor block, and carry on at the level
prep resolved — never create the label here. Any other failure: report its stderr verbatim; the level is
unchanged.
