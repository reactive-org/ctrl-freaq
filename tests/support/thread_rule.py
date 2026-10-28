"""#186: the marker author rule's three anchor phrases, shared by every test that pins a thread reader.

Taken from the question-status reader's #184 wording. Every prompt and playbook step that walks an issue
thread carries these three phrases verbatim; the sentences around them differ by site. Each phrase must sit
on one line, so a hard wrap cannot split it.
"""

THREAD_RULE_ANCHORS = (
    "`authorAssociation` is `OWNER`, `MEMBER` or `COLLABORATOR`",
    "no `authorAssociation`",
    "ordinary text",
)
