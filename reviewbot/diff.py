"""Parse unified patches without opening or executing repository files."""

import fnmatch
import re
from dataclasses import dataclass, field

IGNORED = [
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "*.svg",
    "*.min.js",
    "*.map",
    "*.csv",
    "*.ipynb",
    "*.pem",
    "*.key",
    ".env",
    ".env.*",
    "vendor/*",
    "dist/*",
    "node_modules/*",
]


@dataclass
class Patch:
    path: str
    text: str
    added: dict[int, str] = field(default_factory=dict)


def parse_diff(text: str) -> list[Patch]:
    patches = []
    current = None
    new_line = None
    old_left = new_left = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            new_line = None
        elif raw.startswith("+++ ") and new_line is None:
            path = raw[4:].split("\t", 1)[0]
            if path == "/dev/null":
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            if path.startswith('"') or ".." in path.split("/") or path.startswith("/"):
                current = None
                continue
            if any(fnmatch.fnmatch(path.lower(), p) for p in IGNORED):
                current = None
                continue
            current = Patch(path, "")
            patches.append(current)
        elif current is not None:
            match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if match:
                old_left = int(match[2]) if match[2] is not None else 1
                new_left = int(match[4]) if match[4] is not None else 1
                new_line = int(match[3])
                current.text += raw + "\n"
                continue
            if new_line is None or not raw or raw.startswith("\\"):
                continue
            if raw[0] == "+" and new_left > 0:
                current.added[new_line] = raw[1:]
                new_line += 1
                new_left -= 1
            elif raw[0] == "-" and old_left > 0:
                old_left -= 1
            elif raw[0] == " " and old_left > 0 and new_left > 0:
                new_line += 1
                new_left -= 1
                old_left -= 1
            else:
                continue
            current.text += raw + "\n"
            if old_left == 0 and new_left == 0:
                new_line = None
    return [p for p in patches if p.added]


def prepare(text: str, max_files=20, max_chars=45000):
    parsed = parse_diff(text)
    chosen = []
    size = 0
    skipped = []
    for patch in parsed:
        if len(chosen) >= max_files or size + len(patch.text) > max_chars:
            skipped.append(patch.path)
            continue
        chosen.append(patch)
        size += len(patch.text)
    return chosen, skipped
