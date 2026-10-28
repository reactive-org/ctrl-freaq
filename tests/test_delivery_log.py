"""Unit tests for scripts/delivery_log.py — the epic delivery log's read model
(``skills/_shared/epic-delivery-log.md``; architecture.md §2, §12).

This module is **pure** — it takes an already-fetched comment thread and returns a value, with no
`gh`, no `git`, no file I/O and no shim — so every test drives the cores in-process against literal
comment dicts. That is the whole point of its shape: two preps compose it (``prep_evaluator`` for
the writer's question, ``prep_planner`` at two sites for the reader's), so it never emits an
envelope and never exits.

Three things this suite exists to protect:

  - **The precedence rule.** A ``mixed`` epic is unioned by story number with the per-story entry
    winning. Taking one tier and dropping the other silently loses shipped stories; letting both
    survive hands the plan reviewer two shapes for one story. Neither failure is visible without a
    test, and both corrupt a later story's grounding rather than crashing.
  - **The one-marker-one-comment invariant, story-scoped.** ``MARKER_AMBIGUOUS`` still has to fire
    on a genuine duplicate now that "many comments" is the normal state. That is exactly what the
    story number in the marker buys, and losing it would make "N entries" and "one entry duplicated
    N times" indistinguishable.
  - **Absent, empty and malformed are all quiet.** No record is a value, not an error: the log is
    never a gate, because a merge already judged on its own gates must not stall on its record.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import delivery_log  # noqa: E402


def comment(comment_id, body, url=None, created_at=None, association="MEMBER"):
    """A thread comment as ``gh_gather._normalize_comment`` produces it, by a trusted author unless
    ``association`` says otherwise (#179).

    ``databaseId`` is the REST numeric id and ``id`` the GraphQL node id — the split that matters,
    because every REST comment endpoint 404s on the node id (#34).
    """
    return {
        "id": "IC_kwDO%s" % comment_id,
        "databaseId": comment_id,
        "authorAssociation": association,
        "body": body,
        "url": url or "https://github.com/octo/widgets/issues/42#issuecomment-%s" % comment_id,
        "createdAt": created_at,
    }


def entry(story, shape="Shape", sha="abc1234", pr=99, date="2026-08-01"):
    return "%s\n- #%d — delivered: %s @ `%s` (PR #%d, merged %s)" % (
        delivery_log.entry_marker(story), story, shape, sha, pr, date
    )


def legacy_body(*stories):
    lines = [delivery_log.MARKER_V1, "**Epic delivery log** — #42 Journal"]
    for story in stories:
        lines.append(
            "- #%d — delivered: Legacy%d @ `dea%d` (PR #%d, merged 2026-01-0%d)"
            % (story, story, story, story, 1)
        )
    return "\n".join(lines)


class MarkerTests(unittest.TestCase):
    def test_marker_is_built_in_one_place_and_is_story_scoped(self):
        self.assertEqual(
            delivery_log.entry_marker(185), "<!-- epic-delivery-log:v2:story:185 -->"
        )

    def test_no_story_marker_is_a_prefix_of_another(self):
        """The trailing ` -->` terminates the match, so #15's marker cannot match #150's comment.

        Without this, a `startswith` reader would attribute one story's entry to another — and the
        numbers that collide are exactly the ones a long epic reaches.
        """
        self.assertFalse(
            delivery_log.entry_marker(150).startswith(delivery_log.entry_marker(15))
        )
        self.assertEqual(delivery_log.story_number_for(comment(1, entry(150))), 150)
        log, _ = delivery_log.collect([comment(1, entry(15)), comment(2, entry(150))])
        self.assertEqual(sorted(e["story"] for e in log["entries"]), [15, 150])

    def test_a_marker_below_the_first_line_is_quoted_text_not_a_record(self):
        """The contract puts the marker on the first line; a later occurrence is someone quoting it."""
        self.assertIsNone(
            delivery_log.story_number_for(comment(1, "see also %s" % delivery_log.entry_marker(7)))
        )
        log, decision = delivery_log.collect(
            [comment(1, "discussion\n%s" % delivery_log.entry_marker(7))]
        )
        self.assertIsNone(decision)
        self.assertFalse(log["present"])

    def test_rest_numeric_id_never_the_graphql_node_id(self):
        self.assertEqual(delivery_log.comment_rest_id(comment(5350498958, entry(90))), 5350498958)
        self.assertNotIn("IC_", str(delivery_log.comment_rest_id(comment(5350498958, entry(90)))))


class TierResolutionTests(unittest.TestCase):
    def test_per_story_entries_report_the_entries_source(self):
        log, decision = delivery_log.collect([comment(1, entry(184)), comment(2, entry(185))])
        self.assertIsNone(decision)
        self.assertEqual(log["log_source"], delivery_log.SOURCE_ENTRIES)
        self.assertEqual(log["entry_count"], 2)
        self.assertEqual([e["story"] for e in log["entries"]], [184, 185])

    def test_legacy_monolith_reports_the_legacy_source_and_parses_its_lines(self):
        log, decision = delivery_log.collect([comment(9, legacy_body(184, 194))])
        self.assertIsNone(decision)
        self.assertEqual(log["log_source"], delivery_log.SOURCE_LEGACY)
        self.assertEqual([e["story"] for e in log["entries"]], [184, 194])
        self.assertEqual(log["legacy"]["comment_id"], 9)

    def test_entries_are_ordered_by_thread_position_which_is_merge_order(self):
        """Nothing maintains a position field: comments arrive in ascending creation order, so an
        entry edited in place (a re-record) keeps the position its story shipped at."""
        log, _ = delivery_log.collect(
            [comment(3, entry(190)), comment(1, entry(184)), comment(2, entry(185))]
        )
        self.assertEqual([e["story"] for e in log["entries"]], [190, 184, 185])

    def test_absent_log_is_a_value_not_an_error(self):
        log, decision = delivery_log.collect([comment(1, "unrelated chatter")])
        self.assertIsNone(decision)
        self.assertFalse(log["present"])
        self.assertIsNone(log["log_source"])
        self.assertEqual(log["entry_count"], 0)

    def test_empty_and_none_threads_are_quiet(self):
        for thread in ([], None):
            log, decision = delivery_log.collect(thread)
            self.assertIsNone(decision)
            self.assertFalse(log["present"])


class PrecedenceTests(unittest.TestCase):
    """`mixed` — the state every pre-#41 epic enters on its next merge."""

    def setUp(self):
        self.thread = [
            comment(9, legacy_body(184, 194)),
            comment(20, entry(184, shape="Reshaped")),
            comment(21, entry(200)),
        ]
        self.log, self.decision = delivery_log.collect(self.thread)

    def test_both_tiers_present_reports_mixed(self):
        self.assertIsNone(self.decision)
        self.assertEqual(self.log["log_source"], delivery_log.SOURCE_MIXED)

    def test_a_story_in_both_tiers_is_counted_once(self):
        """Unioned by story number, never halved and never doubled: #184 is in both tiers, and the
        epic has three distinct shipped stories."""
        self.assertEqual(self.log["entry_count"], 3)
        self.assertEqual(
            sorted(e["story"] for e in self.log["entries"]), [184, 194, 200]
        )

    def test_the_per_story_entry_wins_where_a_story_appears_in_both(self):
        by_story = {e["story"]: e for e in self.log["entries"]}
        self.assertEqual(by_story[184]["tier"], delivery_log.SOURCE_ENTRIES)
        self.assertEqual(by_story[194]["tier"], delivery_log.SOURCE_LEGACY)

    def test_the_superseded_legacy_line_is_absent_from_the_assembled_text(self):
        """The reviewer reads one assembled document, so a superseded shape surviving there would
        hand Dimension 8 two shapes for one story — the contradiction the precedence rule exists to
        prevent."""
        self.assertIn("Reshaped", self.log["text"])
        self.assertNotIn("Legacy184", self.log["text"])
        # The tier that still owns #194 keeps its line.
        self.assertIn("Legacy194", self.log["text"])

    def test_text_chars_tracks_the_assembled_size(self):
        self.assertEqual(self.log["text_chars"], len(self.log["text"]))


class DuplicateTests(unittest.TestCase):
    def test_two_comments_for_one_story_are_marker_ambiguous(self):
        """"Many comments" is now normal, so the duplicate check has to be story-scoped — that is
        what the story number in the marker buys."""
        log, decision = delivery_log.collect(
            [comment(1, entry(185)), comment(2, entry(185, shape="Other"))]
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(decision["context"]["story"], 185)
        self.assertEqual(sorted(decision["context"]["comment_ids"]), [1, 2])
        # No partial read escapes alongside a decision — the caller degrades, it does not guess.
        self.assertFalse(log["present"])

    def test_distinct_stories_are_not_duplicates(self):
        log, decision = delivery_log.collect([comment(1, entry(185)), comment(2, entry(186))])
        self.assertIsNone(decision)
        self.assertEqual(log["entry_count"], 2)

    def test_two_legacy_comments_are_marker_ambiguous(self):
        log, decision = delivery_log.collect(
            [comment(1, legacy_body(184)), comment(2, legacy_body(184))]
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(sorted(decision["context"]["comment_ids"]), [1, 2])

    def test_a_duplicate_on_one_story_leaves_every_other_story_readable(self):
        """The point of story-scoping. Under the single-comment log a duplicate blocked recording for
        every story on the epic; if a duplicate emptied the read here it would do the same thing by
        another route, and the "no longer blocks any other story" claim in the contract, the prep and
        the playbook would all be false.
        """
        log, decision = delivery_log.collect(
            [comment(1, entry(7)), comment(2, entry(7)), comment(3, entry(8))]
        )
        self.assertIsNotNone(decision)
        self.assertTrue(log["present"])
        self.assertEqual(log["duplicated_stories"], [7])
        # #8 resolves normally, with the id a writer needs to update it in place.
        self.assertEqual([e["story"] for e in log["entries"]], [8])
        self.assertEqual(delivery_log.record_for_story(log, 8)[1]["comment_id"], 3)
        # #7 is omitted rather than guessed — its shape is exactly what is unknown.
        self.assertEqual(delivery_log.record_for_story(log, 7), (None, None))
        self.assertNotIn(delivery_log.entry_marker(7), log["text"])

    def test_an_ambiguous_legacy_tier_leaves_the_per_story_tier_readable(self):
        """The tiers fail independently: two monoliths cannot be chosen between, but that says
        nothing about the per-story entries alongside them."""
        log, decision = delivery_log.collect(
            [comment(1, legacy_body(184)), comment(2, legacy_body(184)), comment(3, entry(200))]
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision["context"]["marker_prefix"], delivery_log.MARKER_V1)
        self.assertIsNone(log["legacy"])
        self.assertEqual([e["story"] for e in log["entries"]], [200])

    def test_a_duplicated_story_also_suppresses_its_legacy_line(self):
        """A story whose per-story tier is ambiguous must not silently fall back to its legacy line:
        that would present a shape as authoritative when the newer record contradicts it."""
        log, _ = delivery_log.collect(
            [comment(9, legacy_body(184)), comment(1, entry(184)), comment(2, entry(184, shape="X"))]
        )
        self.assertEqual(log["duplicated_stories"], [184])
        self.assertEqual(log["entries"], [])
        self.assertNotIn("Legacy184", log["text"])

    def test_a_none_element_in_the_thread_never_raises(self):
        """The module contract is "never raises on malformed input" — two preps compose it
        in-process, so an exception here takes down a session rather than degrading."""
        log, decision = delivery_log.collect([None, comment(1, entry(184)), None])
        self.assertIsNone(decision)
        self.assertEqual([e["story"] for e in log["entries"]], [184])

    def test_a_duplicate_for_one_story_still_reports_that_story(self):
        """Under the single-comment log a duplicate blocked recording for every story on the epic.
        Story-scoping is the improvement, and the decision has to name which story is affected for
        the writer to report a recovery."""
        _, decision = delivery_log.collect(
            [comment(1, entry(7)), comment(2, entry(7)), comment(3, entry(8))]
        )
        self.assertEqual(decision["context"]["story"], 7)


class RecordForStoryTests(unittest.TestCase):
    """The writer's question: is THIS story already recorded, and in which tier?

    Distinct from ``present`` ("does the epic have a log"). Conflating the two is how a re-record
    writes a second, divergent record for a story already logged.
    """

    def test_a_story_with_its_own_comment_resolves_to_that_comment(self):
        log, _ = delivery_log.collect([comment(1, entry(184)), comment(2, entry(185))])
        tier, record = delivery_log.record_for_story(log, 185)
        self.assertEqual(tier, delivery_log.SOURCE_ENTRIES)
        self.assertEqual(record["comment_id"], 2)

    def test_a_story_only_in_the_legacy_body_resolves_to_the_legacy_tier(self):
        """It must NOT resolve to "unrecorded": opening a per-story comment for a story the legacy
        body records leaves one story with two records."""
        log, _ = delivery_log.collect([comment(9, legacy_body(184, 194))])
        tier, record = delivery_log.record_for_story(log, 194)
        self.assertEqual(tier, delivery_log.SOURCE_LEGACY)
        self.assertEqual(record["comment_id"], 9)

    def test_an_unrecorded_story_resolves_to_nothing(self):
        log, _ = delivery_log.collect([comment(1, entry(184))])
        self.assertEqual(delivery_log.record_for_story(log, 999), (None, None))

    def test_a_story_in_both_tiers_resolves_to_its_per_story_comment(self):
        log, _ = delivery_log.collect(
            [comment(9, legacy_body(184)), comment(20, entry(184, shape="Reshaped"))]
        )
        tier, record = delivery_log.record_for_story(log, 184)
        self.assertEqual(tier, delivery_log.SOURCE_ENTRIES)
        self.assertEqual(record["comment_id"], 20)

    def test_an_absent_log_resolves_to_nothing_without_raising(self):
        self.assertEqual(delivery_log.record_for_story({"present": False}, 1), (None, None))
        self.assertEqual(delivery_log.record_for_story(None, 1), (None, None))



class UntrustedEntryTests(unittest.TestCase):
    """#179: a log comment counts only when its author is trusted (``pipelib.trust``)."""

    def test_an_untrusted_entry_is_ignored_and_counted(self):
        log, decision = delivery_log.collect([comment(1, entry(90), association="NONE")])
        self.assertIsNone(decision)
        self.assertFalse(log["present"])
        self.assertEqual(log["entries"], [])
        self.assertEqual(log["untrusted"], 1)

    def test_an_untrusted_duplicate_does_not_make_the_story_ambiguous(self):
        log, decision = delivery_log.collect(
            [comment(1, entry(90)), comment(2, entry(90, shape="Forged"), association="CONTRIBUTOR")]
        )
        self.assertIsNone(decision)
        self.assertEqual([(e["story"], e["comment_id"]) for e in log["entries"]], [(90, 1)])
        self.assertNotIn("Forged", log["text"])
        self.assertEqual(log["untrusted"], 1)

    def test_an_untrusted_legacy_monolith_is_ignored(self):
        log, decision = delivery_log.collect([comment(1, legacy_body(90), association="NONE")])
        self.assertIsNone(decision)
        self.assertIsNone(log["legacy"])
        self.assertEqual(log["untrusted"], 1)

    def test_an_ordinary_untrusted_comment_counts_zero(self):
        log, _ = delivery_log.collect([comment(1, entry(90)), comment(2, "lgtm", association="NONE")])
        self.assertEqual(log["untrusted"], 0)
        self.assertEqual(log["entry_count"], 1)


if __name__ == "__main__":
    unittest.main()
