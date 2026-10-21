"""Review a diff or GitHub PR. Publishing is an explicit opt-in."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .engine import review_diff
from .github import GitHub


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="AI Code Review Bot")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--diff", type=Path)
    target.add_argument("--repository")
    parser.add_argument("--pull-number", type=int)
    parser.add_argument("--expected-head")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        github = GitHub()
        source = None
        if args.diff:
            if args.publish:
                parser.error("--publish requires a GitHub pull request.")
            diff = args.diff.read_text()
        else:
            if not args.pull_number:
                parser.error("--pull-number is required with --repository.")
            source = github.pull(args.repository, args.pull_number)
            if args.expected_head and source["head_sha"] != args.expected_head:
                raise ValueError("Pull request changed; review skipped.")
            diff = source.pop("diff")
        result = review_diff(diff)
        if source:
            result["source"] = source
            if source["omitted"]:
                result["review"]["limitations"].append(
                    "Unavailable patches: " + ", ".join(source["omitted"])
                )
        if args.publish:
            result["publication"] = github.publish(source, result)
        text = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(text + "\n")
        print(text)
    except Exception as exc:
        # Provider exception strings can contain request data; report categories only.
        parser.exit(
            1,
            f"Review failed ({type(exc).__name__}). Check configuration, quota, and repository access.\n",
        )


if __name__ == "__main__":
    main()
