# History scrub record (v2)

On 2026-10-21 the repository history was rewritten to remove two files that
should not have stayed public: an internal writing-style prompt file under
`docs/prompts/` and a self-signed development TLS private key under
`sentinel_os/certs/`.

What this changed:

- Every commit ID changed. The commit IDs stamped in COMPLIANCE.md, the
  README and the glossary were updated to match, and the doc-freshness CI
  check passes again.
- Branches and the three release tags were moved onto the rewritten history.
- Clones or forks made before the rewrite still hold the old history. Delete
  or re-clone them.
- GitHub keeps hidden pull request refs and cached commit views. A request to
  GitHub Support to purge them is separate from this repository.

The earlier plan document (v1) named the specific commits and file. It was
removed from the current tree so this repository does not point readers at
what was removed.
