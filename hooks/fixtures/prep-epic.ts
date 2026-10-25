export default {
  "status": "ok",
  "repo": "octo/widgets",
  "stage": "stories",
  "next": {
    "kind": "stories"
  },
  "root": {
    "path": "/repo"
  },
  "target": {
    "number": 150,
    "title": "Epic: Inbox sorting",
    "type": "epic",
    "body_bytes": 26,
    "body_mode": "path",
    "body_path": "/tmp/gh-coordinator-150/target/body.txt",
    "body_sha256": "94ef1cb21b005bc08154f4f065ce19a508367df69834e36f50156cc44db37fca"
  },
  "refusal": null,
  "parent_epic": null,
  "unit": null,
  "epic": {
    "worktree": {
      "path": "/repo/.worktrees/epic/150-inbox"
    },
    "stories": [
      {
        "number": 151,
        "title": "Sort by sender",
        "closed": true,
        "stage": "merged-needs-close",
        "next": {
          "kind": "close",
          "command": "/ctrl-freaq:workspace-close 151-sender",
          "folder": null,
          "branch": "151-sender"
        },
        "pr": null,
        "set": "Wave 1",
        "start": [],
        "build": [],
        "merge": [],
        "worktree": {
          "path": "/repo/.worktrees/151-sender"
        }
      },
      {
        "number": 152,
        "title": "Sort by date",
        "closed": false,
        "stage": "pr-ready",
        "next": {
          "kind": "session",
          "skill": "evaluator",
          "command": "/ctrl-freaq:evaluator #160",
          "folder": "/repo/.worktrees/152-date"
        },
        "pr": {
          "number": 160,
          "url": "https://github.com/octo/widgets/pull/160",
          "draft": false
        },
        "set": "Wave 2",
        "start": [
          {
            "waits_on": 151,
            "kind": "contract",
            "condition": null,
            "merged": true
          }
        ],
        "build": [],
        "merge": [],
        "worktree": {
          "path": "/repo/.worktrees/152-date"
        }
      },
      {
        "number": 153,
        "title": "Tag filters",
        "closed": false,
        "stage": "resolving",
        "next": {
          "kind": "session",
          "skill": "resolver",
          "command": "/ctrl-freaq:resolver #153",
          "folder": "/repo/.worktrees/153-tags"
        },
        "pr": null,
        "set": "Wave 2",
        "start": [],
        "build": [
          {
            "waits_on": 151,
            "kind": "file overlap",
            "condition": "#153 changes the inbox list",
            "merged": true
          }
        ],
        "merge": [],
        "worktree": {
          "path": "/repo/.worktrees/153-tags"
        }
      },
      {
        "number": 154,
        "title": "Archive view",
        "closed": false,
        "stage": "needs-plan",
        "next": {
          "kind": "session",
          "skill": "planner",
          "command": "/ctrl-freaq:planner #154",
          "folder": "/repo/.worktrees/epic/150-inbox"
        },
        "pr": null,
        "set": "Wave 3",
        "start": [
          {
            "waits_on": 152,
            "kind": "order",
            "condition": null,
            "merged": false
          }
        ],
        "build": [],
        "merge": [],
        "worktree": null
      },
      {
        "number": 155,
        "title": "Snooze",
        "closed": false,
        "stage": "gated",
        "next": {
          "kind": "none"
        },
        "pr": null,
        "gate": {
          "reason": "MARKER_AMBIGUOUS"
        },
        "set": "Wave 3",
        "start": [],
        "build": [
          {
            "waits_on": 153,
            "kind": "order",
            "condition": null,
            "merged": false
          }
        ],
        "merge": [
          {
            "waits_on": 152,
            "kind": "merge order",
            "condition": null,
            "merged": false
          }
        ],
        "worktree": null
      }
    ],
    "shipped": [
      151
    ],
    "integration": null,
    "concurrency": {
      "source": "declared",
      "fallback_reason": null,
      "sets": [
        {
          "name": "Wave 1",
          "stories": [
            151
          ]
        },
        {
          "name": "Wave 2",
          "stories": [
            152,
            153
          ]
        },
        {
          "name": "Wave 3",
          "stories": [
            154,
            155
          ]
        }
      ],
      "malformed": []
    }
  }
}
