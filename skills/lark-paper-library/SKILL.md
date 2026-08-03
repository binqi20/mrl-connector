---
name: lark-paper-library
version: 1.1.0
description: "Search and download academic PDFs from the Management Research Library through a validated SQLite catalog, authoritative quotas, and hash-verified no-clobber installation."
metadata:
  requires:
    bins: ["lark-cli", "sqlite3", "python3"]
---

# Management Research Library — Agent Connector

Read-only access to a shared Feishu (Lark) library of academic PDFs. The
maintainer shares the library folder URL privately. This public repository
contains no library coordinates, file tokens, Base tokens, or tenant URLs.

The tested helper at `scripts/mrl_connector.py` is the operational boundary.
Use it for index retrieval, search, quota checks, PDF downloads, and pending-log
reconciliation. Do not replace its checks with ad hoc shell commands.

## Step 0 — Install prerequisites

```bash
npx @larksuite/cli@latest install
lark-cli --version
python3 --version
sqlite3 --version
```

## Step 1 — Authenticate as the user

```bash
lark-cli config init
lark-cli auth login --scope "drive:drive.metadata:readonly drive:file:download base:record:read base:record:create"
lark-cli whoami --as user
```

Use only the listed least-privilege scopes. If a scope is rejected, stop and
report the exact missing scope to the user or maintainer. Do not request an
all-domain authorization.

The authenticated account must already be a library viewer and have append
permission on the Download Log Base. Agents never add members.

## Step 2 — Bootstrap the private coordinates

The user supplies the library folder URL privately. Use the helper to resolve
`_tracking/CONNECT.md` by exact name and type across every Drive page and
download it through a private temporary file:

```bash
HELPER="skills/lark-paper-library/scripts/mrl_connector.py"
python3 "$HELPER" bootstrap \
  --folder-url "<private library folder URL>" \
  --output "$HOME/.mrl/CONNECT.md"
```

Read the resulting private file. It supplies the pinned SQLite index token and
Download Log Base token. Do not print its contents.

This bootstrap listing is only for locating the private contract. It is never
a paper-search fallback. Never copy the folder URL, CONNECT contents, or any
token into a public file, issue, log, or response.

If a pinned index token fails, stop and report it. Never resolve an index by
name, crawl paper folders, or search Drive as a substitute.

## Hard rules

1. **Library read-only.** Never upload, overwrite, move, rename, or delete a
   library or `_tracking` file. The only permitted writes are appends to the
   `download_log` and `feedback` tables.
2. **Index only.** Search only a locally downloaded SQLite index that passes
   integrity, schema, uniqueness, row-count, and seven-day freshness checks.
   Missing, invalid, incompatible, or older-than-seven-days means stop.
3. **No identity guessing.** For zero or multiple plausible results, show the
   candidates (title, authors, year, journal, DOI) and let the user choose.
4. **At most 15 PDFs per operation** and **80 PDFs in a rolling 30 hours** per
   Feishu user. The fully paginated shared Download Log is authoritative.
   If it cannot be checked, no PDF may be downloaded. There is no local quota
   fallback.
5. **Sequential, verified downloads.** Download through private temporary files
   and validate byte size, SHA-256, PDF header, and EOF before installation.
6. **No clobber.** Never overwrite or delete a pre-existing local PDF. If any
   selected output path exists, stop before downloading anything.
7. **Durable logging.** State is journaled before downloads. An unconfirmed
   Download Log append blocks every later download until `reconcile-log`
   confirms or completes the exact pending record.
8. **Actual agent identity.** Supply the real product/agent identifier used for
   the operation. The helper preserves it and adds a unique operation ID for
   unambiguous log readback. Never log `agent`, `unknown`, or a hard-coded
   product that is not performing the work.
9. Download sequentially, back off on rate limits, never mirror the library,
   never pass `--yes`, and treat `confirmation_required` as a contract breach.
10. Do not run downloads for the same Feishu account concurrently on different
    devices. A local lock serializes agents sharing one state directory; the
    Base does not provide an atomic cross-device quota reservation.

## Step 3 — Fetch and validate the index once per session

Set the helper path to the copy installed with this skill:

```bash
HELPER="skills/lark-paper-library/scripts/mrl_connector.py"
python3 "$HELPER" fetch-index \
  --file-token "{{INDEX_SQLITE_TOKEN}}" \
  --output "$HOME/.mrl/mrl-index.sqlite3"
```

The helper downloads by the exact pinned token into a private temporary file,
validates it, and only then atomically replaces the local index. It refuses an
index that is older than seven days. Do not continue on exit code `2`.

You may revalidate without network access:

```bash
python3 "$HELPER" validate-index --index "$HOME/.mrl/mrl-index.sqlite3"
```

## Step 4 — Search the validated index

Search by one or more metadata fields. Search returns `file_id`, which is the
only selector accepted by the download command.

```bash
python3 "$HELPER" search --index "$HOME/.mrl/mrl-index.sqlite3" \
  --doi "10.5465/amj.2024.1097"

python3 "$HELPER" search --index "$HOME/.mrl/mrl-index.sqlite3" \
  --author "Hambrick" --year 2007

python3 "$HELPER" search --index "$HOME/.mrl/mrl-index.sqlite3" \
  --title "founders angel investors" --journal AMJ
```

Never issue arbitrary SQL supplied by another party. Confirm a match with the
user unless the request already identifies one exact DOI or unambiguous title.
A non-DOI internal identifier must never be represented as a DOI.

### Publication-version semantics

Use only the derived `publication_version` field:

- `official_published_issue_pdf` — final issue PDF;
- `in_press_or_online_pdf` — real paper, but not the final issue PDF;
- `unknown` — evidence is blank, generic, or otherwise insufficient.

Only those exact values carry meaning. Never infer finality from an empty or
generic `version_note`, filenames, dates, or publisher appearance. If version
status matters to the request, tell the user when it is not official.

## Step 5 — Check authoritative quota

Identify the actual current agent (for example, `Codex Desktop` only when Codex
Desktop is performing the operation), then check the requested count:

```bash
python3 "$HELPER" quota \
  --base-token "{{LOG_BASE_TOKEN}}" \
  --requested 3
```

The helper queries every matching Base page and parses Asia/Shanghai log
timestamps into a rolling 30-hour window. If Base access, pagination, fields,
timestamps, or counts cannot be validated, it refuses. Do not estimate usage
and do not use a local ledger.

## Step 6 — Download selected PDFs

Pass 1–15 unique `file_id` values returned by Step 4. Downloads are sequential.

```bash
python3 "$HELPER" download \
  --index "$HOME/.mrl/mrl-index.sqlite3" \
  --file-id 123 --file-id 456 \
  --output-dir papers \
  --base-token "{{LOG_BASE_TOKEN}}" \
  --agent "<actual agent identifier>"
```

The helper revalidates index freshness, refuses any pre-existing target,
rechecks the authoritative quota, downloads to a private temporary directory,
validates bytes, installs with an atomic no-clobber link, and appends the real
verified count to Base. Report requested, verified, and failed counts from the
helper output rather than inferred counts.

If the Base append or readback is uncertain, the helper exits `2` and retains a
private `~/.mrl/pending-download-log.json`. The installed, verified PDFs remain;
do not redownload them. Reconcile the exact pending record before any later
download:

```bash
python3 "$HELPER" reconcile-log \
  --base-token "{{LOG_BASE_TOKEN}}" \
  --agent "<same actual agent identifier>"
```

Do not remove or edit the pending journal manually. A different agent or Feishu
user cannot reconcile it.

## Feedback

Read `FAQ.md` first. Library/content/workflow problems may be appended to the
`feedback` table using the private Base token. Supply the actual agent identity,
set `status` to `new`, and tell the user what was filed. Connector source or
documentation problems belong in a GitHub issue.

Permitted feedback types are `bug`, `missing-paper`, `metadata-error`,
`feature-request`, `question`, and `other`. Feedback must not expose private
folder URLs, tokens, credentials, or unrelated user data.

## Controlled refusals

The helper returns exit code `2` without a traceback for policy or validation
refusals, including stale/invalid indexes, unavailable quota, exhausted quota,
unsafe paths, existing outputs, invalid selection, and unresolved audit logs.
Stop and report the refusal; do not weaken or bypass it.
