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
