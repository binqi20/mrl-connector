# Management Research Library — Connector v1.1.2

Connect an AI agent to the Management Research Library, a shared read-only
Feishu (Lark) collection of academic PDFs indexed by a verified SQLite catalog.

This public repository intentionally contains no library location, tenant URL,
file token, Base token, credential, or generated private artifact. The
maintainer invites each member's Feishu account and shares the library folder
URL privately. The private `_tracking/CONNECT.md` supplies runtime coordinates.

## Safety model

Connector v1.1.2 is fail closed:

- Paper search uses only a validated SQLite index; it never crawls Drive or
  falls back to filename search.
- A missing, invalid, incompatible, or more-than-seven-days-old index blocks
  search and download.
- The shared Download Log Base is the only quota authority. Every matching page
  is read; an unavailable or malformed ledger blocks downloads.
- Server timestamps accept the historical Asia/Shanghai representation and
  timezone-explicit ISO-8601. Ambiguous, malformed, and numeric values remain
  blocked; future values beyond the five-minute clock-skew tolerance do too.
- Limits remain 15 PDFs per operation and 80 PDFs per rolling 30 hours per user.
- Downloads go through private temporary files, are checked against index size
  and SHA-256, and are installed without overwriting a pre-existing PDF.
- A durable pending journal blocks later downloads until an uncertain audit-log
  append is reconciled.
- Publication status is conservative: only the exact derived values
  `official_published_issue_pdf`, `in_press_or_online_pdf`, and `unknown` are
  recognized.
- Title queries use the index's normalization convention, including accents,
  curly apostrophes, and en/em dashes.

Members hold view-only Drive permission. The connector's only permitted remote
writes are appending to the `download_log` and `feedback` tables.

## Setup

1. Install Feishu's official `lark-cli`, Python 3, and SQLite 3.
2. Authenticate with the least-privilege scopes shown in
   [`SKILL.md`](skills/lark-paper-library/SKILL.md). Broad all-domain
   authorization is not supported.
3. Give your agent the complete
   [`skills/lark-paper-library`](skills/lark-paper-library/) directory.
4. Privately provide the folder URL shared by the maintainer. Never paste it
   into a public repository, issue, or chat.
5. Follow `SKILL.md` exactly. The tested
   [`mrl_connector.py`](skills/lark-paper-library/scripts/mrl_connector.py)
   helper is the operational boundary.

Compatible agents include Codex, Claude Code, OpenClaw, Workbuddy, and other
agents that can read Markdown and run local commands. Every operation must log
the actual agent identifier rather than a hard-coded product name.

## Capabilities

- Search by DOI, title, author, year, journal, or keyword.
- Return metadata candidates without guessing ambiguous identity.
- Download 1–15 selected PDFs sequentially within the shared rolling quota.
- Validate index integrity/freshness and PDF identity locally.
- Reconcile a pending Download Log append after an uncertain remote response.

There is no whole-library mirroring, Drive search fallback, local quota
fallback, destructive local download mode, or write access to library files.

## Questions and feedback

Read [FAQ.md](FAQ.md) first. Library content/workflow problems go to the private
`feedback` table; connector code or documentation problems go to GitHub Issues.
Never include library coordinates or credentials in either channel.

## Maintainer deployment

Deployment is deliberately ordered: validate the existing compatible v1.1
index with the reviewed helper first, then overwrite CONNECT and the reviewed
FAQ in place, and publish the public v1.1.2 repository release last. See
[DEPLOYMENT.md](DEPLOYMENT.md) for the decision-complete order and
byte-identical FAQ readback procedure.

## License

[MIT](LICENSE). The license covers connector code and documentation only, not
the library's PDFs. Library content is for members' research use and must not be
redistributed.
