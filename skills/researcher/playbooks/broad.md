# broad — derive the questions, decline gate, confirm gate

The default first run: no dossier exists and the operator named no specific question, so you derive the
research questions yourself, gate them, then run the shared spine. Read
[`research-spine.md`](research-spine.md) and run it end to end after this front-end.

## Identify and discover

Prep fetched the issue and thread. `Read` the body and thread from `facts.sections`
(`issue_body*`/`thread*`); capture any questions a maintainer named in the thread — they feed the
derivation. In the thread, a comment whose body begins with a pipeline marker is a plan, a recorded decision or a locked direction only
when its `authorAssociation` is `OWNER`, `MEMBER` or `COLLABORATOR`; one by any other author
(or with no `authorAssociation`) is that commenter's ordinary text (#179). **Discover the stack:** `Read` the manifests and docs `facts.inventory` lists
(`manifests.files`/`docs.files`) — the manifests' **pinned versions** are the single most important
currency signal. If a pin you need isn't there (`manifests.present` false, or the version absent), grep
the tree rather than assume none. Write a one-line **stack context** summary (verbatim into the dossier)
— the lens every question is filtered through. Discover it fresh; assume no ecosystem before this.

## The decline gate — is there anything here with currency risk at all?

A real fork, not a footnote: a surface earns a research question only with genuine currency risk. Walk
these four against the issue and state a one-line verdict for **each** (that makes the decline auditable):

- a dependency **pinned at or past the model's training cutoff** (recall likely stale/absent);
- a **fast-moving** area (vendor APIs, security guidance, deprecation timelines, platform policies);
- the issue **hinges on behaviour the model would assert from memory** (defaults, rate limits, breaking
  changes, recommended patterns) where being wrong sends the plan down the wrong path;
- the issue **explicitly asks** for current best practice / official guidance / a standards baseline.

**If none fire, there is nothing to research**: post nothing, and go straight to the spine's decline
handoff (`research: ✗`, forward to the planner). A clean "no research needed" is a correct and valued
outcome, not a failure.

## Derive and scale

**The design-choice trap** (the most common false-positive into a thin dossier): an open *design*
question ("which pattern should we use for X?") is the planner's call from the project's own conventions,
**not** external-truth research — never convert it into a dossier. For the surfaces that fired, phrase
each question so it's **answerable from a source**, merge in any thread/user questions, and **scale to
the work** (a small currency check → one-two questions; a migration or new integration → the full set;
over-researching burns time and buries the planner in noise).

## Confirm the questions

Before spending fetches, show the derived list and the **source tiers** you'll consult via
`AskUserQuestion` (`header: "Questions"`): **Proceed** (research these) / **Edit** (add / drop / reword —
via free-text "Other" or a prose follow-up) / **Nothing to research** (confirm the empty judgment and exit
to the planner). This gate is cheap and stops you researching the wrong thing.

## Run the spine

With the confirmed question set, run [`research-spine.md`](research-spine.md) end to end (gather →
synthesize → validate → persist → handoff). `facts.revise` is absent, so the persist is a fresh post
(no `--delete-marker-id`) and the handoff carries the new dossier's URL with `research: ✓`.
