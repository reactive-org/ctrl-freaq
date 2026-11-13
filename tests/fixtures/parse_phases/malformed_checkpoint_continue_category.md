## Approach
Adversarial mutation: Phase 1's `checkpoint:` puts a pause category on `continue`. A category says why a
phase pauses, so on `continue` it contradicts the value it qualifies, and it raises rather than reading
as either answer (#219).

## Phases
1. **Phase 1 — session token check**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 1
   - deliverable: the session token validated before any request is served
   - depends-on: (none)
   - checkpoint: continue · data
2. **Phase 2 — writer core**
   - kind: code-shipping
   - ships: PR commits to the issue branch
   - closes-dod: 2
   - deliverable: the export writer producing a well-formed file
   - depends-on: 1
