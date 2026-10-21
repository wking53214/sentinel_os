#!/usr/bin/env python3
"""doc_freshness -- fail the build when COMPLIANCE.md has gone stale.

COMPLIANCE.md makes claims about specific source files (what is wired, what an
entry point starts, what was removed). A claim in a document is a claim to
test, not evidence, so this gate turns documentation drift into a red build.
It uses git history only. It never executes repo code and opens no network
connection.

Three checks, all of which must hold:

  1. The "Last Updated" line carries a verification stamp: the first
     `main` @ `<sha>` reference on that line. The stamp must name a commit
     that exists and is an ancestor of HEAD.
  2. The stamped commit is no more than --max-age-days older than HEAD,
     measured in commit dates, so the result does not depend on today's date.
  3. No file the document makes claims about has changed since the last
     commit that touched the document. A commit that changes both counts as
     a refresh, so a pull request can fix code and the document together.

"Files the document makes claims about" means every backticked file name in
the document that resolves to a tracked source or config file (.py .sql .yml
.yaml .lua .sh, or a Dockerfile). Test files are excluded. Names that no
longer resolve are ignored, because the document names removed files on
purpose.

    python3 tools/doc_freshness.py
    python3 tools/doc_freshness.py --max-age-days 14

Exit code zero only when the document is FRESH. Needs full git history: in CI
the checkout must use fetch-depth 0.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

DEFAULT_DOC = "sentinel_os/COMPLIANCE.md"
DEFAULT_MAX_AGE_DAYS = 28

_CLAIM_TOKEN = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|sql|yml|yaml|lua|sh)|Dockerfile)`"
)
_STAMP = re.compile(r"@\s*`([0-9a-f]{7,40})`")
_TEST_PATH = re.compile(r"(^|/)(Tests?|tests)/|(^|/)test_[^/]*$|(^|/)conftest\.py$")


class GitError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _git_ok(repo: str, *args: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def find_claim_files(repo: str, doc_text: str, doc_path: str) -> list[str]:
    """Tracked, non-test files named in backticks in the document."""
    tracked = _git(repo, "ls-files").splitlines()
    claimed: set[str] = set()
    for token in set(_CLAIM_TOKEN.findall(doc_text)):
        for path in tracked:
            if path == token or path.endswith("/" + token):
                claimed.add(path)
    claimed.discard(doc_path)
    return sorted(p for p in claimed if not _TEST_PATH.search(p))


def parse_stamp(doc_text: str) -> str | None:
    """First `main` @ `<sha>` reference on the Last Updated line."""
    for line in doc_text.splitlines():
        if line.startswith("**Last Updated:**"):
            match = _STAMP.search(line)
            return match.group(1) if match else None
    return None


def check(repo: str, doc_path: str, max_age_days: int) -> list[str]:
    """Return a list of problems. An empty list means FRESH."""
    problems: list[str] = []
    full = os.path.join(repo, doc_path)
    if not os.path.isfile(full):
        return [f"{doc_path} does not exist"]
    with open(full, encoding="utf-8") as handle:
        doc_text = handle.read()

    # 1 and 2: the stamp is real, reachable, and not too old.
    stamp = parse_stamp(doc_text)
    if stamp is None:
        problems.append(
            "no verification stamp: the Last Updated line must name the "
            "commit the document was checked against, as `main` @ `<sha>`"
        )
    elif not _git_ok(repo, "cat-file", "-e", f"{stamp}^{{commit}}"):
        problems.append(
            f"stamp {stamp} is not a commit in this history (typo, or a "
            "shallow checkout: CI needs fetch-depth 0)"
        )
    elif not _git_ok(repo, "merge-base", "--is-ancestor", stamp, "HEAD"):
        problems.append(f"stamp {stamp} is not an ancestor of HEAD")
    else:
        stamp_time = int(_git(repo, "log", "-1", "--format=%ct", stamp).strip())
        head_time = int(_git(repo, "log", "-1", "--format=%ct", "HEAD").strip())
        age_days = (head_time - stamp_time) // 86400
        if age_days > max_age_days:
            problems.append(
                f"stamp {stamp} is {age_days} days older than HEAD "
                f"(limit {max_age_days}): re-verify the document"
            )

    # 3: no claimed file changed after the document was last touched.
    last_doc_commit = _git(
        repo, "log", "-1", "--format=%H", "--", doc_path
    ).strip()
    if not last_doc_commit:
        problems.append(f"{doc_path} has no commit in this history")
        return problems
    claim_files = find_claim_files(repo, doc_text, doc_path)
    if claim_files:
        changed = _git(
            repo, "diff", "--name-only", last_doc_commit, "HEAD", "--", *claim_files
        ).split()
        for path in sorted(changed):
            problems.append(
                f"{path} changed after {doc_path} was last updated "
                f"({last_doc_commit[:7]})"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=None, help="repo root (default: cwd's)")
    parser.add_argument("--doc", default=DEFAULT_DOC, help="path from repo root")
    parser.add_argument(
        "--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
        help="largest allowed gap between the stamp and HEAD, in days",
    )
    args = parser.parse_args(argv)

    repo = args.repo
    if repo is None:
        repo = _git(os.getcwd(), "rev-parse", "--show-toplevel").strip()

    try:
        problems = check(repo, args.doc, args.max_age_days)
    except GitError as exc:
        print(f"STALE\n  cannot read git history: {exc}")
        return 1

    if problems:
        print("STALE")
        for problem in problems:
            print(f"  {problem}")
        print(
            f"Fix: re-check the claims in {args.doc} against the code, then "
            "update the document and its Last Updated stamp."
        )
        return 1
    print("FRESH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
