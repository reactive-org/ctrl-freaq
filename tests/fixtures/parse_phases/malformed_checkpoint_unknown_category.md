## Approach
Adversarial mutation: Phase 1's `checkpoint:` names a pause category outside the closed set. An unknown
category must not read as a bare `pause`: the audit vocabulary would stop being closed, and a typo
would read as "no category" with no signal, so it raises (#219).

## Phases
1. **Phase 1 — session token check**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 1
   - deliverable: the session token validated before any request is served
   - depends-on: (none)
   - checkpoint: pause · seam
2. **Phase 2 — writer core**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 2
   - deliverable: the export writer producing a well-formed file
   - depends-on: 1
