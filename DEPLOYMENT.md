# Connector v1.1.0 deployment checkpoint

This is a maintainer-only publication order. It is deliberately separate from
the member workflow in `SKILL.md`.

## Required order

1. Publish the compatible v1.1 MRL SQLite index first. Its `mrl_index` table
   must include the derived `publication_version` column, exact conservative
   values, and a `built_at` no older than seven days.
2. Download that pinned SQLite artifact by token and verify integrity, schema,
   row count, uniqueness, freshness, and representative searches using the
   reviewed v1.1 helper.
3. Only after step 2 passes, publish the v1.1 connector contract and FAQ by
   in-place overwrite of their existing pinned Feishu files. Never delete and
   recreate either file.
4. Download both pinned documents by their unchanged tokens and verify the
   readback bytes.
5. Publish the public repository release last. The SKILL frontmatter, Git tag,
   and GitHub release must all identify v1.1.0.

Publishing the connector before the compatible index would intentionally make
member search fail closed. Publishing the index first avoids that outage while
preserving the safety boundary.

## Deterministic FAQ input and readback

The reviewed public file [`FAQ.md`](FAQ.md) is the sole FAQ publication input.
Do not render it through a private tool, copy it from a generated build folder,
or edit a staged twin. The maintainer should overwrite the existing pinned FAQ
token directly from this exact file:

```bash
REPO_ROOT="$(pwd)"  # run from the reviewed connector repository root
cd "$REPO_ROOT"
lark-cli markdown +overwrite \
  --file-token "<existing pinned FAQ token from private state>" \
  --file FAQ.md \
  --as user
```

Then download to a newly created private temporary directory and compare bytes:

```bash
CHECK_DIR=$(mktemp -d)
chmod 700 "$CHECK_DIR"
(
  cd "$CHECK_DIR"
  lark-cli drive +download \
    --file-token "<same existing pinned FAQ token>" \
    --output FAQ.md \
    --as user
)
cmp -s "$REPO_ROOT/FAQ.md" "$CHECK_DIR/FAQ.md"
shasum -a 256 "$REPO_ROOT/FAQ.md" "$CHECK_DIR/FAQ.md"
```

An unequal byte count, SHA-256, or `cmp` result blocks the release. The
temporary readback may be discarded only after the comparison is recorded.
No paper PDF, tracker row, index token, connector token, or FAQ token changes
during this documentation publication.

## Local review is not publication

Passing connector tests, source scans, and this checklist does not publish
anything. Remote overwrite, readback, Git commit, tag, and GitHub release remain
separate explicitly approved actions.
