#!/usr/bin/env python3
"""Fail if a `uses:` is not pinned to a full commit SHA with a version comment.

Scans `.github/workflows/*.y*ml` and `.github/actions/**/action.y*ml` under the
given root. Why: a tag can be moved by whoever controls the action; a commit SHA
cannot. The `# vX.Y.Z` comment is what Dependabot reads to propose the next
version, so a SHA without it can never be bumped.

Exempt: local refs (`./...`) and `docker://` images pinned by `@sha256:<digest>`.
Line-based on purpose (like AyakaRadiology/needle-protocol's
tools/check-action-pins.py): the report needs file:line and a YAML parser would
cost a dependency to lose it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(r"""^\s*(?:-\s*)?uses:\s*(?P<q>['"]?)(?P<target>[^'"\s#]+)(?P=q)(?P<rest>.*)$""")
SHA_PIN = re.compile(r"@[0-9a-f]{40}$")
DOCKER_DIGEST = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")
VERSION_COMMENT = re.compile(r"^\s*#\s*v\d+(\.\d+)*([-+][0-9A-Za-z.-]+)?\s*$")


def files(root: Path) -> list[Path]:
    found = list((root / ".github" / "workflows").glob("*.y*ml"))
    found += (root / ".github" / "actions").glob("**/action.y*ml")
    return sorted(found)


def problem(target: str, rest: str) -> str | None:
    if target.startswith("./"):
        return None
    if target.startswith("docker://"):
        return None if DOCKER_DIGEST.match(target) else "docker image is not pinned by @sha256:<digest>"
    if not SHA_PIN.search(target):
        return "not pinned to a full 40-hex commit SHA"
    if not VERSION_COMMENT.match(rest):
        return "SHA pin lacks a trailing `# vX.Y.Z` version comment (Dependabot needs it)"
    return None


def check(root: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    seen = 0
    for path in files(root):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            match = USES.match(line)
            if not match:
                continue
            seen += 1
            why = problem(match.group("target"), match.group("rest"))
            if why:
                errors.append(f"{path.relative_to(root)}:{number}: {match.group('target')}: {why}")
    return seen, errors


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    seen, errors = check(root)
    if errors:
        for e in errors:
            print(f"::error::{e}", file=sys.stderr)
        print(f"{len(errors)} unpinned reference(s) of {seen}.", file=sys.stderr)
        return 1
    print(f"[check-pins] {seen} reference(s), all pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
