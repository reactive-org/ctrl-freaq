"""#186: the marker author rule's three anchor phrases, shared by every test that pins a thread reader.

The question-status reader's #184 wording, byte-identical in every prompt and playbook step that walks an
issue thread. Each phrase must sit on one line, so a hard wrap cannot split it.
"""

THREAD_RULE_ANCHORS = (
    "`authorAssociation` is `OWNER`, `MEMBER` or `COLLABORATOR`",
    "no `authorAssociation`",
    "ordinary text",
)
