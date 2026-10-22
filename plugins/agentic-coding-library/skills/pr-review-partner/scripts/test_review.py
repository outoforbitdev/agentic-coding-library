"""Tests for review.py. Run with: python3 -m unittest discover -s <this dir>"""
import contextlib
import copy
import io
import json
import pathlib
import tempfile
import unittest

import review


def make_chunk(n: int, **overrides) -> dict:
    """Build a valid chunk with id cNNN and order n."""
    chunk = {
        "id": f"c{n:03d}",
        "order": n,
        "file": "src/a.ts",
        "start_line": n * 10,
        "end_line": n * 10 + 5,
        "function": f"fn{n}",
        "kind": "function",
        "diff": "@@ -1,2 +1,2 @@\n-old\n+new",
        "summary": "Does a thing.",
        "review_notes": "Check edge cases.",
        "calls": [],
        "called_by": [],
        "related_chunks": [],
    }
    chunk.update(overrides)
    return chunk


def make_doc(chunks=None, **overrides) -> dict:
    """Build a valid, ordered chunks.json document."""
    doc = {
        "version": 2,
        "pr": {
            "id": "1",
            "title": "t",
            "base_ref": "main",
            "head_ref": "f",
            "head_sha": "abc",
        },
        "order_mode": "flow",
        "test_placement": "after_function",
        "created_at": "2026-10-22T07:10:00-04:00",
        "skipped_files": [],
        "chunks": chunks if chunks is not None else [make_chunk(1), make_chunk(2), make_chunk(3)],
    }
    doc.update(overrides)
    return doc


class ReviewTestCase(unittest.TestCase):
    """Base class: a temp review directory and a helper to run the CLI."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)

    def write_chunks(self, doc=None):
        (self.dir / "chunks.json").write_text(json.dumps(doc or make_doc()))

    def run_cli(self, *argv):
        """Run the CLI, returning (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review.main(["--dir", str(self.dir), *argv])
        return code, out.getvalue(), err.getvalue()

    def init(self, doc=None):
        self.write_chunks(doc)
        code, _, err = self.run_cli("init-state")
        self.assertEqual(code, 0, err)

    def state(self) -> dict:
        return json.loads((self.dir / "state.json").read_text())


class ValidateTests(ReviewTestCase):
    def test_valid_ordered_doc(self):
        self.write_chunks()
        code, out, _ = self.run_cli("validate", "--ordered")
        self.assertEqual(code, 0)
        self.assertIn("3 chunks", out)

    def test_unordered_doc_is_valid_without_flag_only(self):
        doc = make_doc(order_mode=None, test_placement=None)
        for chunk in doc["chunks"]:
            chunk["order"] = None
        self.write_chunks(doc)
        self.assertEqual(self.run_cli("validate")[0], 0)
        code, _, err = self.run_cli("validate", "--ordered")
        self.assertEqual(code, 1)
        self.assertIn("order must be set", err)

    def test_reports_missing_field_and_bad_kind(self):
        doc = make_doc()
        del doc["chunks"][0]["summary"]
        doc["chunks"][1]["kind"] = "bogus"
        self.write_chunks(doc)
        code, _, err = self.run_cli("validate", "--ordered")
        self.assertEqual(code, 1)
        self.assertIn("missing summary", err)
        self.assertIn("kind must be one of", err)

    def test_reports_duplicate_ids_and_dangling_related(self):
        doc = make_doc()
        doc["chunks"][1]["id"] = "c001"
        doc["chunks"][2]["related_chunks"] = ["c999"]
        self.write_chunks(doc)
        _, _, err = self.run_cli("validate", "--ordered")
        self.assertIn("duplicate chunk id c001", err)
        self.assertIn("related chunk c999 does not exist", err)

    def test_reports_bad_order_sequence(self):
        doc = make_doc()
        doc["chunks"][2]["order"] = 5
        self.write_chunks(doc)
        _, _, err = self.run_cli("validate", "--ordered")
        self.assertIn("run 1..N", err)

    def test_missing_file(self):
        code, _, err = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)

    def test_invalid_json(self):
        (self.dir / "chunks.json").write_text("{nope")
        code, _, err = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)


class InitStateTests(ReviewTestCase):
    def test_creates_pending_entries(self):
        self.init()
        state = self.state()
        self.assertEqual(state["version"], 2)
        self.assertIsNone(state["current"])
        self.assertEqual(set(state["chunks"]), {"c001", "c002", "c003"})
        self.assertTrue(all(e["status"] == "pending" for e in state["chunks"].values()))

    def test_skip_placement_marks_tests_skipped(self):
        doc = make_doc(test_placement="skip")
        doc["chunks"][2]["kind"] = "test"
        self.init(doc)
        statuses = {k: v["status"] for k, v in self.state()["chunks"].items()}
        self.assertEqual(statuses, {"c001": "pending", "c002": "pending", "c003": "skipped"})

    def test_refuses_to_overwrite_without_force(self):
        self.init()
        code, _, err = self.run_cli("init-state")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.run_cli("init-state", "--force")[0], 0)

    def test_rejects_unordered_doc(self):
        doc = make_doc()
        doc["chunks"][0]["order"] = None
        self.write_chunks(doc)
        code, _, err = self.run_cli("init-state")
        self.assertEqual(code, 1)
        self.assertIn("not valid and ordered", err)


class ShowTests(ReviewTestCase):
    def test_formats_chunk_and_sets_current(self):
        doc = make_doc()
        doc["chunks"][1]["calls"] = ["helper (src/b.ts)"]
        self.init(doc)
        code, out, _ = self.run_cli("show", "c002")
        self.assertEqual(code, 0)
        self.assertIn("Chunk 2 of 3 - src/a.ts:20-25 - fn2()", out)
        self.assertIn("Calls: helper (src/b.ts)", out)
        self.assertIn("```diff\n@@ -1,2 +1,2 @@\n-old\n+new\n```", out)
        self.assertIn("Summary: Does a thing.", out)
        self.assertIn("Look for: Check edge cases.", out)
        self.assertEqual(self.state()["current"], "c002")

    def test_resolves_by_order_number(self):
        self.init()
        _, out, _ = self.run_cli("show", "3")
        self.assertIn("Chunk 3 of 3", out)

    def test_status_is_not_changed_by_showing(self):
        self.init()
        self.run_cli("show", "c001")
        self.assertEqual(self.state()["chunks"]["c001"]["status"], "pending")

    def test_longer_fence_when_diff_contains_backticks(self):
        doc = make_doc()
        doc["chunks"][0]["diff"] = "+```js\n+x\n+```"
        self.init(doc)
        _, out, _ = self.run_cli("show", "c001")
        self.assertIn("````diff", out)

    def test_omits_function_suffix_when_none(self):
        doc = make_doc()
        doc["chunks"][0]["function"] = None
        self.init(doc)
        _, out, _ = self.run_cli("show", "c001")
        self.assertIn("src/a.ts:10-15\n", out)

    def test_unknown_chunk(self):
        self.init()
        code, _, err = self.run_cli("show", "c999")
        self.assertEqual(code, 1)
        self.assertIn("no chunk matches", err)


class MutationTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_reviewed_sets_status_and_timestamp(self):
        self.run_cli("reviewed", "c001")
        entry = self.state()["chunks"]["c001"]
        self.assertEqual(entry["status"], "reviewed")
        self.assertIsNotNone(entry["reviewed_at"])

    def test_skip_leaves_reviewed_at_null(self):
        self.run_cli("skip", "c001")
        entry = self.state()["chunks"]["c001"]
        self.assertEqual(entry["status"], "skipped")
        self.assertIsNone(entry["reviewed_at"])

    def test_ask_and_answer(self):
        self.run_cli("ask", "c001", "Why?")
        entry = self.state()["chunks"]["c001"]
        self.assertEqual(entry["questions"][0]["text"], "Why?")
        self.assertFalse(entry["questions"][0]["resolved"])
        self.run_cli("answer", "c001", "Because.")
        question = self.state()["chunks"]["c001"]["questions"][0]
        self.assertEqual(question["answer_summary"], "Because.")
        self.assertTrue(question["resolved"])

    def test_answer_targets_latest_question_and_can_stay_open(self):
        self.run_cli("ask", "c001", "One?")
        self.run_cli("ask", "c001", "Two?")
        self.run_cli("answer", "c001", "Unsure.", "--unresolved")
        questions = self.state()["chunks"]["c001"]["questions"]
        self.assertIsNone(questions[0]["answer_summary"])
        self.assertEqual(questions[1]["answer_summary"], "Unsure.")
        self.assertFalse(questions[1]["resolved"])

    def test_answer_without_question_errors(self):
        code, _, err = self.run_cli("answer", "c001", "x")
        self.assertEqual(code, 1)
        self.assertIn("no questions", err)

    def test_finding(self):
        self.run_cli("finding", "c002", "Counter never reset.")
        findings = self.state()["chunks"]["c002"]["findings"]
        self.assertEqual(findings[0]["text"], "Counter never reset.")

    def test_updated_at_changes_and_no_temp_files_left(self):
        before = self.state()["updated_at"]
        self.run_cli("reviewed", "c001")
        self.assertGreaterEqual(self.state()["updated_at"], before)
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_state_requires_init(self):
        (self.dir / "state.json").unlink()
        code, _, err = self.run_cli("reviewed", "c001")
        self.assertEqual(code, 1)
        self.assertIn("init-state", err)


class NextAndStatusTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_next_starts_at_first_chunk(self):
        self.assertEqual(self.run_cli("next")[1].strip(), "c001")

    def test_next_resumes_at_current_if_pending(self):
        self.run_cli("show", "c002")
        self.assertEqual(self.run_cli("next")[1].strip(), "c002")

    def test_next_advances_after_review_or_skip(self):
        self.run_cli("show", "c001")
        self.run_cli("reviewed", "c001")
        self.assertEqual(self.run_cli("next")[1].strip(), "c002")
        self.run_cli("show", "c002")
        self.run_cli("skip", "c002")
        self.assertEqual(self.run_cli("next")[1].strip(), "c003")

    def test_next_wraps_to_earlier_pending_chunk(self):
        self.run_cli("show", "c003")
        self.run_cli("reviewed", "c003")
        self.assertEqual(self.run_cli("next")[1].strip(), "c001")

    def test_next_none_when_all_done(self):
        for chunk_id in ("c001", "c002", "c003"):
            self.run_cli("reviewed", chunk_id)
        self.assertEqual(self.run_cli("next")[1].strip(), "none")

    def test_status_summarizes_progress(self):
        self.run_cli("reviewed", "c001")
        self.run_cli("skip", "c002")
        self.run_cli("ask", "c003", "Why?")
        self.run_cli("finding", "c003", "Bug.")
        _, out, _ = self.run_cli("status")
        self.assertIn("reviewed 1 / skipped 1 / pending 1 (of 3)", out)
        self.assertIn("c003 (src/a.ts:30): Why?", out)
        self.assertIn("c003 (src/a.ts:30): Bug.", out)

    def test_status_hides_resolved_questions(self):
        self.run_cli("ask", "c001", "Why?")
        self.run_cli("answer", "c001", "Because.")
        _, out, _ = self.run_cli("status")
        self.assertIn("unresolved questions: none", out)


class WriteJsonTests(unittest.TestCase):
    def test_round_trip_uses_two_space_indent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "x.json"
            review.write_json(path, copy.deepcopy({"a": [1]}))
            self.assertEqual(path.read_text(), '{\n  "a": [\n    1\n  ]\n}\n')


if __name__ == "__main__":
    unittest.main()
