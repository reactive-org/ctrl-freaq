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

A pipeline **marked comment** (#179) — the plan and its part comments, a legacy shipped-phase record,
the research dossier, a question decision, a delivery-log entry, the evaluator's health cache — counts
toward its lookup only when its author is trusted: :func:`is_trusted_comment` /
:func:`partition_trusted`, applied by every reader that finds one by a body-prefix scan (`gh_gather`,
`gh_pr_gather`, `plan_parts`, `plan_shipped`, `delivery_log`, `board_history`, and the direct scans in
`prep_evaluator`, `prep_planner` and `prep_coordinator`). Each reader partitions only the comments that
carry its own marker family, so an ordinary contributor comment is never counted, and an untrusted
marked comment is skipped and counted, never read — the `MARKER_UNTRUSTED` notice.
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


def is_trusted_comment(comment):
    """Whether ``comment``'s author is trusted: its ``authorAssociation`` (a normalized thread comment or
    a GraphQL node), else its ``author_association`` (a raw REST comment), through :func:`is_trusted`.
    A comment carrying neither key, or one that is not a dict, is untrusted."""
    if not isinstance(comment, dict):
        return False
    association = comment.get("authorAssociation")
    if association is None:
        association = comment.get("author_association")
    return is_trusted(association)


def partition_trusted(comments):
    """``(trusted, untrusted_count)``: the comments :func:`is_trusted_comment` accepts, in their original
    order, and how many it skipped. Callers pass only the comments carrying their own marker family."""
    trusted = []
    untrusted = 0
    for comment in comments or []:
        if is_trusted_comment(comment):
            trusted.append(comment)
        else:
            untrusted += 1
    return trusted, untrusted


# The notice code a skipped untrusted marked comment is reported under (an open notice, never a
# decision: architecture.md §3).
MARKER_UNTRUSTED = "MARKER_UNTRUSTED"


def untrusted_notices(notices):
    """The ``MARKER_UNTRUSTED`` notices among ``notices`` — what a prep that otherwise drops a composed
    gather's success-path notices still forwards, so its other output is unchanged."""
    return [n for n in notices or [] if isinstance(n, str) and n.startswith(MARKER_UNTRUSTED + ":")]


def untrusted_notice(count, marker, where=None):
    """The ``MARKER_UNTRUSTED`` notice for ``count`` skipped comments of one marker family, or ``None``
    when none was skipped. ``where`` (e.g. ``"issue #7"``) names the thread, so two threads' notices
    stay distinct. It names the marker, the count and the thread only — never a comment's text, URL
    or author, so nothing an untrusted commenter wrote reaches a session."""
    if not count:
        return None
    return "%s: %d comment%s starting with %s by an untrusted author ignored%s" % (
        MARKER_UNTRUSTED,
        count,
        "" if count == 1 else "s",
        marker,
        " (%s)" % where if where else "",
    )
