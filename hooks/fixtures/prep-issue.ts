export default {
  "status": "ok",
  "repo": "octo/widgets",
  "stage": "resolving",
  "next": {
    "kind": "session",
    "skill": "resolver",
    "command": "/ctrl-freaq:resolver #100",
    "folder": "/repo/.worktrees/100-fix-the-widget"
  },
  "root": {
    "path": "/repo"
  },
  "target": {
    "number": 100,
    "title": "Fix the widget",
    "type": "standard",
    "body_bytes": 66,
    "body_mode": "path",
    "body_path": "/tmp/gh-coordinator-100/target/body.txt",
    "body_sha256": "f6b450bc1e3013116c7c795a02ef3ba9aa1f5d6ea6bfa319534c33608b21f76c"
  },
  "refusal": null,
  "parent_epic": null,
  "unit": {
    "closed": false,
    "worktree": {
      "path": "/repo/.worktrees/100-fix-the-widget"
    },
    "pr": {
      "number": 58,
      "url": "https://github.com/octo/widgets/pull/58",
      "draft": true,
      "phases": {
        "total": 3,
        "shipped": 1,
        "next_phase": 2
      }
    },
    "gate": null,
    "phase_view": {
      "path": "/tmp/gh-coordinator-100/phases/100.json",
      "sha256": "9c1d6f2a4b7e8c3d5f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f6a",
      "total": 3,
      "shipped": 1,
      "next_phase": 2
    },
    "history": [
      {
        "point": "eligible",
        "at": "2026-01-01T00:00:00Z",
        "source": "issue.createdAt"
      },
      {
        "point": "plan-posted",
        "at": "2026-01-03T00:00:00Z",
        "source": "plan-comment.createdAt"
      },
      {
        "point": "workspace-opened",
        "at": "2026-01-03T12:00:00Z",
        "source": "reflog"
      },
      {
        "point": "commit",
        "at": "2026-01-04T00:00:00Z",
        "source": "commit.committedDate"
      },
      {
        "point": "pr-opened",
        "at": "2026-01-05T00:00:00Z",
        "source": "pr.createdAt"
      }
    ]
  },
  "epic": null
}
