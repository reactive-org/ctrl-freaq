"""Who may write the loop comments, resume seed and security dismissals the pipeline reads back as
recorded decisions (SEC-1, #99; #139).

One definition, imported by every reader that weighs an author: `review_report` (the loop record
across a repo's PRs), `prep_evaluator` (whether a `## Security review` dismissal is the operator's)
and `prep_resolver` (which loop comments seed a resumed review loop). Any GitHub user can comment on a
public repo's PR or open a fork PR, so a settle or dismiss read from text an untrusted author wrote
would let that author skip the operator. An unknown association (a failed read, or GitHub omitting
the field) is untrusted: every reader fails closed.
"""

# The `authorAssociation` values whose text is read: the repo's owner, members of the organization
# that owns it, and its collaborators. MEMBER is org membership, not a repo permission. Matched
# exactly — GitHub spells them in upper case, and nothing else is one of them.
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")


def is_trusted(association):
    """Whether ``association`` (a GitHub ``authorAssociation`` / ``author_association`` value) is in
    :data:`TRUSTED_ASSOCIATIONS`. ``None``, an empty string and any other value are untrusted."""
    return isinstance(association, str) and association in TRUSTED_ASSOCIATIONS
