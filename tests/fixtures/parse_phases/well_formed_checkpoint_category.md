## Approach
A five-phase plan exercising every `checkpoint:` form since #219: a `pause` naming its category, a
category written with no spaces around the `·`, a bare `pause` (a plan authored before the category,
which still parses and pauses), a `continue`, and the last code-shipping phase, which carries no key.

## Phases
1. **Phase 1 — session token check**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 1
   - deliverable: the session token validated before any request is served
   - depends-on: (none)
   - checkpoint: pause · security
2. **Phase 2 — export file format**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 2
   - deliverable: the export file format other tools read
   - depends-on: 1
   - checkpoint: pause·contract
3. **Phase 3 — writer core**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 3
   - deliverable: the export writer producing a well-formed file
   - depends-on: 2
   - checkpoint: pause
4. **Phase 4 — rename the helper**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: (none)
   - deliverable: the parsing helper renamed across its call sites
   - depends-on: 3
   - checkpoint: continue
5. **Phase 5 — flag plumbing**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 4
   - deliverable: the command-line flag wired through to the writer
   - depends-on: 4
