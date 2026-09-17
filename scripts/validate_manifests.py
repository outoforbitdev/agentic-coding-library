#!/usr/bin/env python3
"""Validate plugin marketplace JSON manifests and skill frontmatter."""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def check_json_file(path: pathlib.Path) -> list[str]:
    errors = []
    try:
        json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON ({exc})")
    return errors


def check_skill_frontmatter(path: pathlib.Path) -> list[str]:
    errors = []
    text = path.read_text()
    match = FRONTMATTER_RE.match(text)
    if not match:
        errors.append(f"{path}: missing YAML frontmatter block")
        return errors
    frontmatter = match.group(1)
    if "name:" not in frontmatter:
        errors.append(f"{path}: frontmatter missing 'name' field")
    if "description:" not in frontmatter:
        errors.append(f"{path}: frontmatter missing 'description' field")
    return errors


def main() -> int:
    errors: list[str] = []
    for json_path in ROOT.glob("**/*.json"):
        if "node_modules" in json_path.parts:
            continue
        errors.extend(check_json_file(json_path))
    for skill_path in ROOT.glob("**/SKILL.md"):
        errors.extend(check_skill_frontmatter(skill_path))

    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"All manifests and skill frontmatter valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
