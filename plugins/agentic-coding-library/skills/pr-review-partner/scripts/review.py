#!/usr/bin/env python3
"""Manage the chunks.json / state.json files used by the pr-review-partner skill.

chunks.json holds the chunk content and ordering and is read-only during a
review. state.json holds review progress (status, questions, findings) and is
updated through the subcommands below, so every write is atomic, timestamped,
and consistent.

Usage: review.py [--dir DIR] <command> [args]

Commands:
  validate [--ordered]        Check chunks.json against the schema.
  init-state [--force]        Create state.json from chunks.json.
  show <chunk>                Print a chunk formatted for presentation and make
                              it the current chunk.
  reviewed <chunk>            Mark a chunk reviewed.
  skip <chunk>                Mark a chunk skipped (not actually reviewed).
  ask <chunk> <text>          Record a question.
  answer <chunk> <text>       Answer the most recent question on a chunk.
  finding <chunk> <text>      Record a finding (suspected bug or concern).
  next                        Print the id of the chunk to present next.
  status                      Print progress, open questions, and findings.

<chunk> is a chunk id (e.g. c007) or its 1-based position in the review order.
DIR defaults to the current directory.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import tempfile

CHUNKS_FILE = "chunks.json"
STATE_FILE = "state.json"
FORMAT_VERSION = 2

ORDER_MODES = {"flow", "building_blocks"}
TEST_PLACEMENTS = {"after_function", "end", "skip"}
KINDS = {"function", "types", "config", "test", "docs", "mechanical", "other"}
STATUSES = {"pending", "reviewed", "skipped"}

PR_FIELDS = ("id", "title", "base_ref", "head_ref", "head_sha")
CHUNK_FIELDS = {
    "id": str,
    "file": str,
    "start_line": int,
    "end_line": int,
    "function": (str, type(None)),
    "kind": str,
    "diff": str,
    "summary": str,
    "review_notes": str,
    "calls": list,
    "called_by": list,
    "related_chunks": list,
}


class ReviewError(Exception):
    """A user-facing error. Printed to stderr with exit status 1."""


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def now() -> str:
    """Return the current local time as an ISO 8601 string with a UTC offset."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: pathlib.Path) -> dict:
    """Load a JSON file, raising ReviewError if it is missing or invalid."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ReviewError(f"{path} does not exist")
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{path} is not valid JSON ({exc})")


def write_json(path: pathlib.Path, data: dict) -> None:
    """Write JSON atomically (temp file, then rename) with 2-space indentation."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Review:
    """The pair of files for one review, plus lookups and saving."""

    def __init__(self, directory: pathlib.Path):
        self.directory = directory
        self.chunks_path = directory / CHUNKS_FILE
        self.state_path = directory / STATE_FILE
        self._chunks = None
        self._state = None

    @property
    def chunks_doc(self) -> dict:
        if self._chunks is None:
            self._chunks = read_json(self.chunks_path)
        return self._chunks

    @property
    def state(self) -> dict:
        if self._state is None:
            if not self.state_path.exists():
                raise ReviewError(
                    f"{self.state_path} does not exist; run init-state first"
                )
            self._state = read_json(self.state_path)
        return self._state

    def ordered_chunks(self) -> list[dict]:
        """Chunks sorted by their review order (unordered chunks sort last)."""
        chunks = self.chunks_doc["chunks"]
        return sorted(
            chunks,
            key=lambda c: (c.get("order") is None, c.get("order") or 0),
        )

    def resolve(self, token: str) -> dict:
        """Find a chunk by id (c007) or by 1-based review position (7)."""
        chunks = self.ordered_chunks()
        for chunk in chunks:
            if chunk["id"] == token:
                return chunk
        if token.isdigit():
            for chunk in chunks:
                if chunk.get("order") == int(token):
                    return chunk
        raise ReviewError(f"no chunk matches '{token}'")

    def entry(self, chunk_id: str) -> dict:
        """Return the mutable state entry for a chunk id."""
        try:
            return self.state["chunks"][chunk_id]
        except KeyError:
            raise ReviewError(f"chunk {chunk_id} is not in {STATE_FILE}")

    def save_state(self) -> None:
        """Stamp updated_at and atomically write state.json."""
        self.state["updated_at"] = now()
        write_json(self.state_path, self.state)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def validate_chunks_doc(doc: dict, ordered: bool) -> list[str]:
    """Return a list of schema problems in a chunks.json document."""
    errors = []
    if doc.get("version") != FORMAT_VERSION:
        errors.append(f"version must be {FORMAT_VERSION}")

    pr = doc.get("pr")
    if not isinstance(pr, dict):
        errors.append("pr must be an object")
    else:
        for field in PR_FIELDS:
            if not isinstance(pr.get(field), str):
                errors.append(f"pr.{field} must be a string")

    order_mode = doc.get("order_mode")
    if ordered and order_mode not in ORDER_MODES:
        errors.append(f"order_mode must be one of {sorted(ORDER_MODES)}")
    elif not ordered and order_mode is not None and order_mode not in ORDER_MODES:
        errors.append(f"order_mode must be null or one of {sorted(ORDER_MODES)}")
    placement = doc.get("test_placement")
    if placement is not None and placement not in TEST_PLACEMENTS:
        errors.append(f"test_placement must be null or one of {sorted(TEST_PLACEMENTS)}")

    skipped = doc.get("skipped_files")
    if not isinstance(skipped, list):
        errors.append("skipped_files must be a list")
    else:
        for i, item in enumerate(skipped):
            if not (
                isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("reason"), str)
            ):
                errors.append(f"skipped_files[{i}] needs string path and reason")

    chunks = doc.get("chunks")
    if not isinstance(chunks, list):
        errors.append("chunks must be a list")
        return errors

    ids = [c.get("id") for c in chunks if isinstance(c, dict)]
    for dup in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        errors.append(f"duplicate chunk id {dup}")

    for index, chunk in enumerate(chunks):
        errors.extend(_validate_chunk(chunk, index, set(ids), ordered))

    if ordered:
        orders = [c.get("order") for c in chunks if isinstance(c, dict)]
        if None not in orders and sorted(orders) != list(range(1, len(chunks) + 1)):
            errors.append("order values must be unique and run 1..N")
    return errors


def _validate_chunk(chunk, index: int, all_ids: set, ordered: bool) -> list[str]:
    """Return the schema problems for one chunk."""
    if not isinstance(chunk, dict):
        return [f"chunks[{index}] must be an object"]
    label = f"chunks[{index}] ({chunk.get('id', '?')})"
    errors = []
    for field, expected in CHUNK_FIELDS.items():
        if field not in chunk:
            errors.append(f"{label}: missing {field}")
        elif not isinstance(chunk[field], expected) or isinstance(chunk[field], bool):
            errors.append(f"{label}: {field} has the wrong type")
    if chunk.get("kind") not in KINDS:
        errors.append(f"{label}: kind must be one of {sorted(KINDS)}")
    order = chunk.get("order")
    if order is None:
        if ordered:
            errors.append(f"{label}: order must be set")
    elif not isinstance(order, int) or isinstance(order, bool) or order < 1:
        errors.append(f"{label}: order must be null or a positive integer")
    for ref in chunk.get("related_chunks") or []:
        if ref not in all_ids:
            errors.append(f"{label}: related chunk {ref} does not exist")
    return errors


def cmd_validate(review: Review, args) -> int:
    errors = validate_chunks_doc(review.chunks_doc, args.ordered)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"{CHUNKS_FILE} is valid ({len(review.chunks_doc['chunks'])} chunks)")
    return 0


# ---------------------------------------------------------------------------
# init-state
# ---------------------------------------------------------------------------


def cmd_init_state(review: Review, args) -> int:
    if review.state_path.exists() and not args.force:
        raise ReviewError(f"{review.state_path} already exists; use --force to replace it")
    errors = validate_chunks_doc(review.chunks_doc, ordered=True)
    if errors:
        raise ReviewError(
            f"{CHUNKS_FILE} is not valid and ordered:\n  " + "\n  ".join(errors)
        )
    skip_tests = review.chunks_doc.get("test_placement") == "skip"
    entries = {}
    for chunk in review.ordered_chunks():
        skipped = skip_tests and chunk["kind"] == "test"
        entries[chunk["id"]] = {
            "status": "skipped" if skipped else "pending",
            "reviewed_at": None,
            "questions": [],
            "findings": [],
        }
    review._state = {
        "version": FORMAT_VERSION,
        "updated_at": now(),
        "current": None,
        "chunks": entries,
    }
    review.save_state()
    print(f"initialized {len(entries)} chunks")
    return 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def fence_for(text: str) -> str:
    """Return a backtick fence longer than any backtick run inside text."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def format_chunk(chunk: dict, total: int, status: str) -> str:
    """Render a chunk for presentation: header, links, diff block, notes."""
    function = f" - {chunk['function']}()" if chunk.get("function") else ""
    lines = [
        f"Chunk {chunk['order']} of {total} - "
        f"{chunk['file']}:{chunk['start_line']}-{chunk['end_line']}{function}",
        f"Id: {chunk['id']}  Kind: {chunk['kind']}  Status: {status}",
    ]
    for label, key in (
        ("Calls", "calls"),
        ("Called by", "called_by"),
        ("Related chunks", "related_chunks"),
    ):
        if chunk.get(key):
            lines.append(f"{label}: {', '.join(chunk[key])}")
    diff = chunk["diff"].rstrip("\n")
    fence = fence_for(diff)
    lines += ["", f"{fence}diff", diff, fence, ""]
    lines.append(f"Summary: {chunk['summary']}")
    lines.append(f"Look for: {chunk['review_notes']}")
    return "\n".join(lines)


def cmd_show(review: Review, args) -> int:
    chunk = review.resolve(args.chunk)
    status = review.entry(chunk["id"])["status"]
    print(format_chunk(chunk, len(review.chunks_doc["chunks"]), status))
    review.state["current"] = chunk["id"]
    review.save_state()
    return 0


# ---------------------------------------------------------------------------
# Status changes, questions, findings
# ---------------------------------------------------------------------------


def cmd_reviewed(review: Review, args) -> int:
    chunk = review.resolve(args.chunk)
    entry = review.entry(chunk["id"])
    entry["status"] = "reviewed"
    entry["reviewed_at"] = now()
    review.save_state()
    print(f"{chunk['id']} reviewed")
    return 0


def cmd_skip(review: Review, args) -> int:
    chunk = review.resolve(args.chunk)
    review.entry(chunk["id"])["status"] = "skipped"
    review.save_state()
    print(f"{chunk['id']} skipped")
    return 0


def cmd_ask(review: Review, args) -> int:
    chunk = review.resolve(args.chunk)
    questions = review.entry(chunk["id"])["questions"]
    questions.append(
        {
            "text": args.text,
            "asked_at": now(),
            "answer_summary": None,
            "resolved": False,
        }
    )
    review.save_state()
    print(f"{chunk['id']} question {len(questions)} recorded")
    return 0


def cmd_answer(review: Review, args) -> int:
    chunk = review.resolve(args.chunk)
    questions = review.entry(chunk["id"])["questions"]
    if not questions:
        raise ReviewError(f"{chunk['id']} has no questions to answer")
    questions[-1]["answer_summary"] = args.text
    questions[-1]["resolved"] = not args.unresolved
    review.save_state()
    resolved = "unresolved" if args.unresolved else "resolved"
    print(f"{chunk['id']} question {len(questions)} answered ({resolved})")
    return 0


def cmd_finding(review: Review, args) -> int:
    chunk = review.resolve(args.chunk)
    findings = review.entry(chunk["id"])["findings"]
    findings.append({"text": args.text, "noted_at": now()})
    review.save_state()
    print(f"{chunk['id']} finding {len(findings)} recorded")
    return 0


# ---------------------------------------------------------------------------
# next, status
# ---------------------------------------------------------------------------


def next_pending(review: Review):
    """Pick the chunk to present next, or None if nothing is pending.

    The current chunk if it is still pending (resume), otherwise the first
    pending chunk after it in order, otherwise the first pending chunk.
    """
    chunks = review.ordered_chunks()
    status = {c["id"]: review.entry(c["id"])["status"] for c in chunks}
    current = review.state.get("current")
    if current in status and status[current] == "pending":
        return current
    start = 0
    if current in status:
        start = [c["id"] for c in chunks].index(current) + 1
    after = chunks[start:] + chunks[:start]
    for chunk in after:
        if status[chunk["id"]] == "pending":
            return chunk["id"]
    return None


def cmd_next(review: Review, args) -> int:
    chunk_id = next_pending(review)
    print(chunk_id if chunk_id else "none")
    return 0


def cmd_status(review: Review, args) -> int:
    chunks = review.ordered_chunks()
    counts = {s: 0 for s in sorted(STATUSES)}
    open_questions, findings = [], []
    for chunk in chunks:
        entry = review.entry(chunk["id"])
        counts[entry["status"]] += 1
        where = f"{chunk['file']}:{chunk['start_line']}"
        for q in entry["questions"]:
            if not q["resolved"]:
                open_questions.append(f"{chunk['id']} ({where}): {q['text']}")
        for f in entry["findings"]:
            findings.append(f"{chunk['id']} ({where}): {f['text']}")
    print(
        f"reviewed {counts['reviewed']} / skipped {counts['skipped']} / "
        f"pending {counts['pending']} (of {len(chunks)})"
    )
    print(f"current: {review.state.get('current') or 'none'}")
    print(f"next: {next_pending(review) or 'none'}")
    print("unresolved questions:" + ("" if open_questions else " none"))
    for line in open_questions:
        print(f"  {line}")
    print("findings:" + ("" if findings else " none"))
    for line in findings:
        print(f"  {line}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage pr-review-partner chunks.json and state.json."
    )
    parser.add_argument(
        "--dir", type=pathlib.Path, default=pathlib.Path("."), help="review directory"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check chunks.json")
    p.add_argument("--ordered", action="store_true", help="also require order and order_mode")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("init-state", help="create state.json")
    p.add_argument("--force", action="store_true", help="replace an existing state.json")
    p.set_defaults(func=cmd_init_state)

    p = sub.add_parser("show", help="print a chunk and make it current")
    p.add_argument("chunk")
    p.set_defaults(func=cmd_show)

    for name, func, helptext in (
        ("reviewed", cmd_reviewed, "mark a chunk reviewed"),
        ("skip", cmd_skip, "mark a chunk skipped"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("chunk")
        p.set_defaults(func=func)

    for name, func, helptext in (
        ("ask", cmd_ask, "record a question"),
        ("answer", cmd_answer, "answer the most recent question"),
        ("finding", cmd_finding, "record a finding"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("chunk")
        p.add_argument("text")
        if name == "answer":
            p.add_argument("--unresolved", action="store_true", help="leave the question open")
        p.set_defaults(func=func)

    p = sub.add_parser("next", help="print the id of the next chunk to present")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("status", help="print progress")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(Review(args.dir), args)
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
