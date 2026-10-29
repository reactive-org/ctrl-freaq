"""``pipelib`` — shared envelope/spill/decision/subprocess primitives (architecture.md §2, §3).

Every ``scripts/*.py`` script imports from here for: the envelope contract (:mod:`pipelib.envelope`),
byte-threshold spill routing (:mod:`pipelib.spill`), the closed decision-code vocabulary
(:mod:`pipelib.decisions`), write-receipt hashing (:mod:`pipelib.hashing`), the atomic text-file
write (:mod:`pipelib.atomic`), and the locked-down subprocess runner (:mod:`pipelib.process`).
:mod:`pipelib.hooks` is the one deliberate exception — architecture.md §1's carve-out for
``workspace.py``'s repo-owned shell hooks — and is not meant to be imported by any other script.

This package must never contain skill-specific logic (architecture.md §2's component-model
table): nothing in here knows about issues, PRs, plans, or any pipeline-skill concept. This
``__init__.py`` intentionally does not re-export submodule contents at the package level — callers
import the specific submodule they need (``from pipelib import envelope``, ``from pipelib.decisions
import AUTH_REQUIRED``, …) so a reader of a script's imports sees exactly which primitive it uses.
"""
