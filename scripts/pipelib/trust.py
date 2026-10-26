"""Who may write the loop comments, resume seed and security dismissals the pipeline reads back as
recorded decisions (SEC-1, #99; #139).

One definition, imported by every reader that weighs an author: `review_report` (the loop record
across a repo's PRs), `prep_evaluator` (whether a `## Security review` dismissal is the operator's)
and `prep_resolver` (which loop comments seed a resumed review loop). Any GitHub user can comment on a
public repo's PR or open a fork PR, so a settle or dismiss read from text an untrusted author wrote
would let that author skip the operator. An unknown association (a failed read, or GitHub omitting
the field) is untrusted: every reader fails closed.

`review_threads` (#145) reads both rules: a review-thread comment counts by its author's association, a
thread's resolution by the resolving account's repo permission (:data:`TRUSTED_PERMISSIONS`), and a
marked thread comment, which moves the reference commit a resolution is judged against, needs both
(SEC-3) unless this account wrote it.
"""

# The `authorAssociation` values whose text is read: the repo's owner, members of the organization
# that owns it, and its collaborators. MEMBER is org membership, not a repo permission. Matched
# exactly — GitHub spells them in upper case, and nothing else is one of them.
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")


def is_trusted(association):
    """Whether ``association`` (a GitHub ``authorAssociation`` / ``author_association`` value) is in
    :data:`TRUSTED_ASSOCIATIONS`. ``None``, an empty string and any other value are untrusted."""
    return isinstance(association, str) and association in TRUSTED_ASSOCIATIONS


# The repo permissions whose review-thread resolution counts (#145). A resolution carries no
# `authorAssociation`, so its trust is the resolving account's permission on the repo, read through
# `gh api repos/<repo>/collaborators/<login>/permission` (the legacy `permission` field, which reports
# `maintain` as `write`). GitHub lets the PR's opener or anyone with write access resolve a thread, and on
# a public repo any account reads `read`, so only write and above separate a maintainer from a stranger.
TRUSTED_PERMISSIONS = ("admin", "maintain", "write")


def is_trusted_permission(permission):
    """Whether ``permission`` (a repo permission string) is in :data:`TRUSTED_PERMISSIONS`. ``None`` (a
    failed lookup), ``read``, ``none`` and any other value are untrusted."""
    return isinstance(permission, str) and permission in TRUSTED_PERMISSIONS
