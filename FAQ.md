# Management Research Library — FAQ

Answers for members and their AI agents. Check here before asking the
maintainer. Coordinates (folder URL, tokens) are intentionally absent — they
come privately from the maintainer and from `_tracking/CONNECT.md` inside the
library. A copy of this FAQ also lives in `_tracking/FAQ.md`.

## Access & accounts

**Q: I don't use Feishu. Can I still use the library?**
Register a free Feishu account (feishu.cn app or website) — you never need to
use Feishu for anything else; the account only carries your access permission.
Send the maintainer the email/phone you registered with, and you'll be invited.

**Q: Is there a password or public link?**
No. Access is per-account invitation only. There is no anonymous path, and
API/agent access never works via passwords — only via your authenticated
Feishu identity holding view permission.

**Q: My agent gets `permission denied` / `not exist`.**
Your account isn't a collaborator yet (or the invite went to a different
account than the one lark-cli is logged into). Check `lark-cli whoami`, then
ask the maintainer to invite exactly that account. Agents: report this to your
user and stop — do not retry in a loop.

**Q: Can my agent write anything at all?**
Two things only: append a record to the Download Log after each download
operation, and append a record to the `feedback` table (same Base). Everything
else is view-only and enforced server-side by Feishu — a write attempt fails
with a permission error no matter what the agent does.

## Setup

**Q: How do I install the CLI?**
`npx @larksuite/cli@latest install` (needs Node.js; official repo:
github.com/larksuite/cli). Then `lark-cli config init` and the `auth login`
command from SKILL.md Step 1. `sqlite3` and `python3` ship with macOS/Linux.

**Q: `auth login` rejects a scope name.**
Tenant configurations vary. Fall back to `lark-cli auth login --domain all`,
which grants a broader but still user-bound scope set.

**Q: My agent has native Feishu integration (e.g., Workbuddy). Do I still need lark-cli?**
Not necessarily — natively integrated agents can browse to `_tracking/CONNECT.md`,
download the CSV index, and fetch PDFs with their own tools. They should still
follow the same contract (limits, verification, logging) as closely as their
toolset allows, and tell you which steps they cannot perform.

## Finding papers

**Q: How do I search?**
Your agent downloads one index file (`mrl-index.sqlite3`, ~3,500+ papers) and
queries it locally — by title words, first author, co-author, year, journal,
or DOI. Searching by DOI is the most precise when you have one.

**Q: A paper I expect isn't found. Is it missing or am I searching wrong?**
Try, in order: (1) DOI, if you have it; (2) `title_norm` keywords — the
normalized column strips punctuation and accents (search `ozturk`, not
`Öztürk`; both the index and your query should use the plain form);
(3) `first_author_norm`. If still nothing, the paper likely isn't in the
library yet — file a `missing-paper` feedback record (below) so the
maintainer can ingest it.

**Q: A row has an empty author list. Bug?**
No — it's an in-press paper whose DOI isn't registered with Crossref yet
(~180 rows). Those rows are findable by first author and title, but not by
co-author search, until a future enrichment pass fills them.

**Q: A `doi` value looks like `AMJ_20220421`. Can I cite that?**
Never. Values not starting with `10.` are internal journal manuscript IDs for
in-press papers — useful for identification, not citable. Cite only real
`10.*` DOIs, or cite the paper conventionally by author/year/title/journal.

**Q: The search returned several plausible candidates.**
Agents must show the candidates (title, authors, year, journal, DOI) and let
the human choose. Guessing is a contract violation — accuracy beats speed here.

**Q: How fresh is the index?**
Check `index_meta.built_at` (agents do this automatically). The index is
rebuilt after each ingest batch. If it's older than ~7 days, very recent
papers may be missing — mention it, don't conclude the paper doesn't exist.

## Downloading

**Q: Why at most 15 PDFs per operation and 80 per 30 hours?**
Group policy set by the maintainer: it keeps individual usage reasonable,
auditable, and fair, and protects the shared library from accidental bulk
drains. The 30-hour rolling window smooths day-boundary bursts.

**Q: How is my usage counted?**
Every download operation appends a record (who, when, how many) to the shared
Download Log; the timestamps and identity are server-stamped and cannot be
faked. Your agent sums your entries in the last 30 hours before downloading.
Usage is per Feishu account — all your devices and agents share one budget.

**Q: The downloaded file failed hash verification.**
The agent deletes it and retries once. If it fails again: the index may be
mid-update — re-download the index and retry. Persisting mismatches should be
reported as a `bug` feedback record with the paper_id and file_token.

**Q: A download produced a 0-byte file.**
Transient network failure — retry once before concluding anything.

**Q: Can my agent mirror the whole library for offline use?**
No. Bulk mirroring (`+pull`/`+sync`) is forbidden — it bypasses the quota and
duplicates a licensed collection. Download what you need, within limits.

**Q: Where do downloaded PDFs end up?**
In the agent's working directory (typically a `papers/` subfolder). Ask your
agent — and note the library's PDFs are for members' research use; don't
redistribute them outside the group.

**Q: Can I add papers to the library?**
Not directly (the folder is read-only for members). Send PDFs to the
maintainer, or file a `missing-paper` feedback record with the DOI/reference —
papers enter through the maintainer's ingest pipeline so metadata stays verified.

## Questions, problems, suggestions

**Q: Where do I report problems or ideas?**
Two channels, routed by topic:
- **Library content & workflow** (missing paper, wrong metadata, quota issues,
  feature ideas, anything about the Feishu side): append a record to the
  **`feedback` table** in the same Base as the Download Log (coordinates in
  CONNECT.md). Set `type` (bug / missing-paper / metadata-error /
  feature-request / question / other), `title`, `detail`, `paper_ref` if
  relevant, `agent`, and `status` = `new`. The maintainer triages `status`
  and writes `resolution` — check your record later for the answer.
- **Connector code & docs** (this repo — skill errors, unclear instructions):
  open a GitHub Issue on the repo.

Agents: before filing a `question`, check this FAQ and CONNECT.md — and after
filing, tell your user what you filed so they can follow up.

**Q: Is the feedback table like a pull request?**
Closer to an issue tracker. For actual changes to the connector's code or
docs, a GitHub pull request on the repo is welcome too — but reports and
suggestions belong in the feedback table (library topics) or GitHub Issues
(connector topics).
