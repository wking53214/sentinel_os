# History scrub plan (v1)

Status: PLAN ONLY. This document changes nothing. No history has been rewritten.
Origin: Review #3 (tree of Oct 1, 2026) noted that deleting the camo prompt file
from `main` does not remove it from git history.

## What needs scrubbing

- `docs/prompts/SYSTEM_OVERRIDE_PROMPT_HUMAN_FORENSIC_CAMO.txt`
  - added in 46f40d2 (2026-08-01), removed in 895aa08 (2026-10-10)
  - still readable by anyone who browses old commits of the public repo
- Candidate for the same pass: `sentinel_os/certs/key.pem`, which `.gitleaks.toml`
  records as committed once in 8dfa1c6 and later removed. Whatever the key was
  used for, treat it as exposed and rotate it. Removing it from history does not
  un-expose it.

## Why this is a decision, not a routine fix

A rewrite gives every commit after the first changed commit a new ID. Measured
on the Oct 21 clone: 328 commits on `main`, 3 branches, 6 tags on origin, plus
the stored pull request refs.

Things that break or need attention:

1. **The doc-freshness CI gate.** `tools/doc_freshness.py` reads the commit
   stamp in COMPLIANCE.md (for example `main` @ `216cc97`) and looks it up in git
   history. After a rewrite those commits no longer exist, so the gate goes red
   until the stamp is replaced with a new ID. Plan to re-stamp COMPLIANCE.md as
   the first commit after the rewrite.
2. **Other repos that pin sentinel_os by commit ID.** Not checked in this plan.
   Search the sibling repos (DIT, Conservation_Kernel, cns, GSA-815,
   observe-perceive) for any pin or doc link naming a sentinel_os commit.
3. **Open pull requests and branches.** Their commits predate the rewrite and
   would be orphaned. Merge or close them first, or rebase them afterwards.
4. **Pull request refs on GitHub.** GitHub keeps `refs/pull/*` and cached views
   of old commits even after a force-push. Full removal needs a request to
   GitHub Support to drop cached objects. Without that, the file can stay
   reachable by its old commit URL.
5. **Forks and local clones.** Anyone who cloned or forked in the meantime keeps
   the old history. The Review #3 note said the repo is young and unforked, so
   this is smallest now.
6. **Standing rule 1 in CLAUDE.md** says never work on `main`. A rewrite ends in
   a force-push to `main`, which is an explicit exception and should be a
   conscious decision by the owner.

## Proposed steps (owner runs these, not an agent)

1. Merge or close open pull requests. Freeze pushes.
2. Make a full backup: a mirror clone kept somewhere private.
3. In a fresh mirror clone, remove the listed paths from all history using
   `git filter-repo` with the path-removal option, once per path.
4. Check the result: the paths appear in no commit on any branch or tag, the
   test suites still pass, and ruff and bandit still pass.
5. Force-push all branches and tags (use the lease form of force-push).
6. Re-stamp COMPLIANCE.md to a new commit ID so the freshness gate passes.
7. Ask GitHub Support to purge cached objects and pull request refs.
8. Update pins and doc links in sibling repos found in the earlier search.
9. Rotate the key from `certs/key.pem` if it was ever used for anything real.

## Decision for the owner

- A. Do the full rewrite now, while the repo is young and unforked (recommended).
- B. Accept the history as is, and document that the file was removed.
- C. Wait until a design partner exists, then rewrite. This costs more later,
  because more people will have clones.

Open item: whether anything outside this repo already depends on current commit
IDs. Not verified here.
