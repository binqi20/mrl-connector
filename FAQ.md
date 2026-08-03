# Management Research Library — FAQ (connector v1.1.0)

Coordinates are deliberately absent. The maintainer shares the library folder
URL privately; `_tracking/CONNECT.md` supplies runtime tokens. Never place
either in public source, an issue, or a response.

## Access and setup

**Do I need a Feishu account?**

Yes. Access is by invitation to an individual account. There is no password or
anonymous API path. Confirm the identity with `lark-cli whoami --as user`.

**Which authorization should I use?**

Use only the explicit least-privilege scopes in `SKILL.md`. If a scope is
rejected or missing, stop and report it. Do not request broad all-domain access.

**What can an agent write?**

It may append to two Base tables only: `download_log` after verified downloads,
and `feedback` for library/content/workflow reports. It must not change any
Drive file, folder, index, or document.

## Finding papers

**How does search work?**

The helper downloads the exact pinned SQLite index token to a private temporary
file, validates it, and searches it locally by DOI, title, author, year,
journal, or keyword. It never crawls or searches the paper folders.

**What if the index is unavailable or invalid?**

Search and download stop. There is no filename-search fallback. A missing
table/column, failed SQLite integrity check, duplicate file identity, invalid
row count, or invalid file identity makes the index unusable.

**What if the index is old?**

An index older than seven 24-hour days is not merely a warning: search and
download fail closed until the maintainer publishes a fresh valid index.

**What if several results look plausible?**

The agent shows title, authors, year, journal, and DOI and asks the user to
choose. It must never guess. A value not beginning with a valid DOI form must
not be cited as a DOI.

**How do I know whether a PDF is the final published version?**

Use the derived `publication_version` field only:

- `official_published_issue_pdf`: final issue PDF;
- `in_press_or_online_pdf`: not the final issue PDF;
- `unknown`: blank, generic, or insufficient evidence.

Only these exact values are meaningful. An empty `version_note` does not prove
that a file is final.

## Quotas and logging

**What are the limits?**

At most 15 PDFs per operation and 80 PDFs per rolling 30 hours per Feishu user.

**Where does usage come from?**

Only the shared Download Log Base. The helper reads all matching pages and
parses the Asia/Shanghai timestamps. A local cache or ledger is never used as a
quota substitute.

**What if the Base cannot be checked?**

No PDF is downloaded. Partial results, malformed fields, failed pagination, bad
timestamps, and permission errors all cause a controlled refusal.

**Why does the agent need an identifier?**

The Download Log records which actual product/agent performed the operation.
The caller must supply that identifier; placeholders such as `agent` or
`unknown` are rejected. The stored value also carries a unique operation ID so
an uncertain append can be matched to its own record.

**Can two devices download at the same time?**

No. The helper locks concurrent operations that share one local state
directory, but the Base has no atomic cross-device quota reservation. To keep
the 80-paper limit reliable, serialize downloads for the same Feishu account.

**What is the pending-download-log journal?**

Before a remote PDF transfer, the helper creates a private durable journal. It
updates the journal as bytes are verified. If Base append/readback is uncertain,
the journal remains and every later download is blocked. Run `reconcile-log`
with the same Feishu user and actual agent identifier. Do not edit or delete the
journal manually and do not redownload already verified PDFs.

## Download safety

**Can the connector replace a local file?**

No. All output names are preflighted. If any target exists, the whole operation
stops before network access. Each new PDF is downloaded into a private temporary
directory, checked for file size, SHA-256, PDF header, and EOF, then installed
with an atomic no-clobber operation.

**What happens to a mismatched download?**

The invalid temporary file is discarded; no target PDF is installed. The agent
reports the failed name. It never deletes or modifies a pre-existing PDF.

**Can an agent mirror the library?**

No. Whole-folder pull/sync and concurrent bulk download bypass the quota and
are prohibited. Downloads are sequential and limited to the selected index
rows.

## Troubleshooting

**`permission denied` / `not exist`** — Confirm the authenticated Feishu user
was invited and has Base append permission. Stop; do not loop retries.

**Index download fails** — Report the pinned-token failure. Do not locate an
index by name or use Drive paper search.

**Index is stale** — Ask the maintainer for an explicit MRL refresh. Do not
bypass the seven-day policy.

**Why might v1.1 refuse an older index?** — v1.1 requires the conservative
derived `publication_version` field. Maintainers publish and verify the
compatible index before deploying the v1.1 connector and FAQ; see
`DEPLOYMENT.md` in the public repository.

**Quota query fails** — Stop. There is no offline fallback.

**Pending log blocks downloads** — Run the documented `reconcile-log` command
with the same agent identifier and Feishu user. If reconciliation remains
ambiguous, contact the maintainer.

**`confirmation_required`** — A forbidden write was attempted. Stop and do not
add `--yes`.

## Feedback

Read this FAQ and private CONNECT contract first. Library content/workflow
issues may be appended to the `feedback` Base table using the actual agent
identifier and `status=new`. Connector source/documentation issues belong in a
GitHub Issue. Tell the user what was filed, and never include private library
coordinates or credentials.
