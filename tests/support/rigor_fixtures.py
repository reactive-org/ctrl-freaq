"""Add the rigor label read (#198, `scripts/rigor.py` `read_labels`) to a shim fixture case.

A one-off authoring helper, not run by the suite: `python3 -m tests.support.rigor_fixtures <case> <owner/repo>
<number> [--no-subissues] [--label NAME]...` appends one manifest entry answering the exact `gh api graphql`
query `read_labels` sends, with a canned response carrying the issue's labels and no parent. The manifest keeps
the compact single-line form the fixtures are written in.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import rigor  # noqa: E402


def add(case, repo, number, labels=(), subissues_available=True, parent=None):
    """Add the entry to the named case under `tests/fixtures/`."""
    add_to_dir(REPO_ROOT / "tests" / "fixtures" / case, repo, number, labels, subissues_available, parent)


def copy_with_labels(case, dest, repo, number, labels=(), subissues_available=True, parent=None):
    """Copy the named case to `dest` and add the entry there: a test's own label set, the source case untouched."""
    import shutil

    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / case, dest, dirs_exist_ok=True)
    add_to_dir(Path(dest), repo, number, labels, subissues_available, parent)
    return Path(dest)


def add_to_dir(case_dir, repo, number, labels=(), subissues_available=True, parent=None):
    """Add the entry to the case directory `case_dir`, or rewrite the response of one already answering the
    query."""
    manifest_path = case_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    argv = ["api", "graphql", "-f", "query=" + rigor.labels_query(repo, number, subissues_available)]
    issue = {"labels": {"nodes": [{"name": label} for label in labels]}}
    if subissues_available:
        issue["parent"] = parent
    existing = [entry for entry in manifest if entry["argv"] == argv]
    stdout_file = existing[0]["stdout_file"] if existing else "rigor_labels_%d.json" % number
    (case_dir / stdout_file).write_text(json.dumps({"data": {"repository": {"issue": issue}}}), encoding="utf-8")
    if not existing:
        manifest.append({"argv": argv, "stdout_file": stdout_file, "exit_code": 0})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case")
    parser.add_argument("repo")
    parser.add_argument("number", type=int)
    parser.add_argument("--no-subissues", action="store_true")
    parser.add_argument("--label", action="append", default=[])
    args = parser.parse_args(argv)
    add(args.case, args.repo, args.number, args.label, subissues_available=not args.no_subissues)


if __name__ == "__main__":
    main(sys.argv[1:])
