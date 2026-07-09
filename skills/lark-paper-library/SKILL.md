---
name: lark-paper-library
version: 1.0.1
description: "Search and download academic paper PDFs from the Management Research Library — a shared, read-only Feishu (Lark) Drive folder indexed by a verified SQLite catalog. Use when the user asks to find, locate, list, or download papers from the shared library by title, author, year, journal, DOI, or keyword. Strictly read-only: never modifies the library."
metadata:
  requires:
    bins: ["lark-cli", "sqlite3", "python3"]
---

# Management Research Library — Agent Connector

Read-only access to a shared library of academic paper PDFs hosted on Feishu (Lark).
Works with any agent that can run shell commands: Claude Code, Codex, OpenClaw, Workbuddy, etc.

**This public file contains no library coordinates.** Your user receives the
library folder URL privately from the maintainer; Step 2 turns that URL into
everything else. Values written `{{LIKE_THIS}}` are filled in
`_tracking/CONNECT.md` inside the library — the operational copy of this
contract. Never commit the folder URL, tokens, or CONNECT.md to any public place.

## Step 0 — Install lark-cli (one-time)

lark-cli is Feishu's official CLI (https://github.com/larksuite/cli):

```bash
npx @larksuite/cli@latest install     # official installer (needs Node.js)
lark-cli --version                    # verify (developed against 1.0.64)
```

`sqlite3` and `python3` ship with macOS/Linux. Optional, for Claude Code/Codex
users: `npx skills add larksuite/cli -y -g` installs the full Lark skill set.

## Step 1 — Authenticate as yourself (one-time)

```bash
lark-cli config init          # guided app configuration (browser/QR flow)
lark-cli auth login --scope "drive:drive.metadata:readonly drive:file:download base:record:read base:record:create search:docs:read"
# broader fallback if a scope name is rejected on your tenant: lark-cli auth login --domain all
lark-cli whoami               # confirm identity = user, status ready
```

Your Feishu account must already be a collaborator (viewer) of the library
folder — the maintainer invites you; there is no password or anonymous path
for API access. If a command returns `permission denied`, ask the maintainer
to (re)invite your account, or apply:

```bash
lark-cli drive +apply-permission --token <FOLDER_TOKEN from Step 2> --type folder
```

## Step 2 — Bootstrap from the privately shared folder URL (once per setup)

```bash
FOLDER_URL="<paste the URL the maintainer shared privately>"
FOLDER_TOKEN="${FOLDER_URL##*/drive/folder/}"; FOLDER_TOKEN="${FOLDER_TOKEN%%\?*}"

# find _tracking and CONNECT.md — folder listings are PAGINATED (<=200/page);
# always walk every page or you will silently miss files
CONNECT_TOKEN=$(python3 - "$FOLDER_TOKEN" <<'EOF'
import json, subprocess, sys
def walk(folder_token, want_name, want_type):
    page = ""
    while True:
        params = {"folder_token": folder_token, "page_size": 200}
        if page: params["page_token"] = page
        p = subprocess.run(["lark-cli","drive","files","list","--params",json.dumps(params),
                            "--format","json","--as","user"], capture_output=True, text=True)
        d = json.loads(p.stdout)["data"]
        for f in d.get("files", []):
            if f["name"] == want_name and f["type"] == want_type:
                return f["token"]
        if not d.get("has_more"): return None
        page = d.get("next_page_token", "")
        if not page: return None
tracking = walk(sys.argv[1], "_tracking", "folder")
print(walk(tracking, "CONNECT.md", "file") or "")
EOF
)
mkdir -p mrl
lark-cli drive +download --file-token "$CONNECT_TOKEN" --output mrl/CONNECT.md --overwrite --as user
```

**Read `mrl/CONNECT.md` and follow it.** It is this contract with every
`{{...}}` value filled (index tokens, Download Log Base, tenant domain).

## Hard rules (non-negotiable)

1. **Read-only.** Never upload to, modify, rename, move, or delete anything under the library folder or `_tracking`. Do not call `drive +upload`, `+move`, `+delete`, `+push`, `+sync`, `files patch`, or any Base write command against library resources. Sole exceptions: appending records to the Download Log Base — its `download_log` table (usage logging) and `feedback` table (reports/suggestions).
2. **≤ 15 PDFs per download operation.** For larger requests, download at most 15, tell the user the cap, and continue only when they ask.
3. **≤ 80 PDFs per rolling 30-hour window per user.** Check the ledger before every operation (Step 5). If the request exceeds the remainder, download only up to it and say when quota frees.
4. **Verify every download** against the index `sha256` and `file_size`. A mismatched file must be deleted and reported — never silently kept.
5. **Download sequentially**, never in parallel floods; back off on rate-limit errors.
6. **Never mirror the library.** `drive +pull` / `+sync` on library folders is forbidden.
7. **Never pass `--yes`** to any lark-cli command touching library resources. `confirmation_required` (exit 10) means you attempted a write — stop.
8. **Never guess a paper's identity.** Zero or multiple plausible matches → show the user the candidates (title, authors, year, journal, DOI) and let them choose.

## Step 3 — Get the index (once per session)

```bash
lark-cli drive +download --file-token {{INDEX_SQLITE_TOKEN}} --output mrl/mrl-index.sqlite3 --overwrite --as user
# CSV twin for environments without sqlite3: {{INDEX_CSV_TOKEN}} -> mrl-index.csv
# if a pinned token ever 404s, resolve by name in _tracking exactly as in Step 2
sqlite3 mrl/mrl-index.sqlite3 "SELECT key||': '||value FROM index_meta WHERE key IN ('built_at','rows');"
# if built_at is older than ~7 days, tell the user very recent papers may be missing
```

## Step 4 — Search by metadata (local, exact, zero API quota)

Table `mrl_index`, one row per verified PDF. Match on the pre-normalized
columns (`title_norm`, `first_author_norm`); show the user the original
`title`/`authors`. `authors` holds the full Crossref-verified author list
where available; `first_author_last` is always populated. Data semantics:
a `doi` value not starting with `10.` is an internal manuscript ID (e.g.
`AMJ_20220421` for an in-press paper) — never cite it as a DOI; and rows
with empty `authors` (in-press items) are only findable via
`first_author_norm`, not co-author search.

```bash
sqlite3 -json mrl/mrl-index.sqlite3 \
  "SELECT paper_id,title,authors,year,journal,doi,file_name,file_size,sha256,file_token
   FROM mrl_index WHERE first_author_norm LIKE '%hambrick%' AND year='2007';"
sqlite3 -json mrl/mrl-index.sqlite3 \
  "SELECT * FROM mrl_index WHERE lower(doi)=lower('10.5465/amj.2024.1097');"   # DOI = most precise
sqlite3 -json mrl/mrl-index.sqlite3 \
  "SELECT title,authors,year FROM mrl_index WHERE authors LIKE '%Hurwitz%';"   # co-author search
sqlite3 -json mrl/mrl-index.sqlite3 \
  "SELECT title,first_author_last,doi,file_token FROM mrl_index
   WHERE journal='AMJ' AND year='2026' ORDER BY title;"
```

Confirm the match with the user (title + authors + year + DOI) unless the
query was already exact. Fallback if the index is unreachable:
`lark-cli drive +search --query "<author year>" --folder-tokens $FOLDER_TOKEN --doc-types file`
(filename search, pages of 20 — less precise; re-verify results).

## Step 5 — Quota check (before every download operation)

The shared **Download Log** Base `{{LOG_BASE_TOKEN}}` (table `download_log`) is
authoritative; `~/.mrl/download-ledger.jsonl` is the offline fallback.
`logged_at` values are strings like `2026-07-09 16:33:23` in **Asia/Shanghai
time (UTC+8)** — parse with that offset regardless of your own timezone.

```bash
OPEN_ID=$(lark-cli whoami --as user 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['onBehalfOf']['openId'])")
USED=$(lark-cli base +record-search --base-token {{LOG_BASE_TOKEN}} --table-id download_log \
  --keyword "$OPEN_ID" --search-field user_open_id \
  --field-id logged_at --field-id papers_count \
  --sort-json '[{"field":"logged_at","desc":true}]' --limit 200 --as user --format json 2>/dev/null \
  | python3 -c "
import json,sys
from datetime import datetime,timezone,timedelta
env=json.load(sys.stdin); d=env.get('data',env)
rows,names=d.get('data',[]),d.get('fields',[])
i_ts,i_n=names.index('logged_at'),names.index('papers_count')
tz8=timezone(timedelta(hours=8)); cut=datetime.now(tz8)-timedelta(hours=30)
print(sum(int(r[i_n] or 0) for r in rows
      if datetime.strptime(r[i_ts],'%Y-%m-%d %H:%M:%S').replace(tzinfo=tz8)>=cut))")
echo "used $USED/80 in the last 30h"
```

If `USED + N > 80`: trim N to the remainder (or stop at 0) and tell the user
when the oldest in-window entry expires. If the log Base is unreachable, apply
the same computation to the local ledger — never skip the check. Do not edit
or delete existing log rows (`logged_by` and record history are server-side).

## Step 6 — Download and verify (≤15 per operation)

```bash
# rows.json = selected rows from Step 4 (max 15)
python3 - <<'EOF'
import json, subprocess, hashlib, os, sys
rows = json.load(open("rows.json"))
assert len(rows) <= 15, "batch cap is 15 PDFs per operation"
os.makedirs("papers", exist_ok=True)
ok, failed = [], []
for r in rows:
    out = os.path.join("papers", r["file_name"])
    p = subprocess.run(["lark-cli","drive","+download","--file-token",r["file_token"],
                        "--output",out,"--overwrite","--as","user"], capture_output=True, text=True)
    good = False
    if p.returncode == 0 and os.path.exists(out):
        sha = hashlib.sha256(open(out,"rb").read()).hexdigest()
        good = (sha == r["sha256"] and os.path.getsize(out) == int(r["file_size"]))
        if not good:
            os.remove(out)            # never keep an unverified file
    (ok if good else failed).append(r["file_name"])
print(f"verified {len(ok)}/{len(rows)}")
for f in failed: print("FAILED:", f)
sys.exit(1 if failed else 0)
EOF
```

Afterwards, using **real** numbers from the verification output:

```bash
# 1) append to the shared Download Log (papers_count = verified count)
USER_NAME=$(lark-cli whoami --as user 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['onBehalfOf']['userName'])")
lark-cli base +record-batch-create --base-token {{LOG_BASE_TOKEN}} --table-id download_log \
  --json "{\"fields\":[\"user_open_id\",\"user_name\",\"agent\",\"papers_count\",\"paper_ids\"],
           \"rows\":[[\"$OPEN_ID\",\"$USER_NAME\",\"claude-code\",N_VERIFIED,\"p123;p456\"]]}" --as user
# 2) mirror into the local ledger
python3 -c "
import json,os
from datetime import datetime,timezone
p=os.path.expanduser('~/.mrl/download-ledger.jsonl'); os.makedirs(os.path.dirname(p),exist_ok=True)
open(p,'a').write(json.dumps({'ts':datetime.now(timezone.utc).isoformat(timespec='seconds'),'count':N_VERIFIED,'paper_ids':'p123;p456'})+'\n')"
# 3) report requested / downloaded / verified counts and any FAILED names to the user
```

If the shared-log append fails, retry once; if it still fails, stop further
download operations this session and tell the user (no log = no more downloads).

## Permissions

| Operation | Scope | Library permission |
|---|---|---|
| Download index & PDFs | `drive:file:download` | folder viewer |
| Resolve tokens / metadata | `drive:drive.metadata:readonly` | folder viewer |
| Filename search fallback | `search:docs:read` | folder viewer |
| Download Log read/append | `base:record:read` + `base:record:create` | edit on the Log Base only |

## Questions & feedback

Check `FAQ.md` (repo root; mirrored at `_tracking/FAQ.md`) before asking the
maintainer — most setup, search, quota, and error questions are answered there.

**Library / content / workflow issues** (missing paper, metadata error, quota
problem, feature idea): append a record to the **`feedback` table** — it lives
in the same Base as the Download Log, and is the second and last permitted write:

```bash
lark-cli base +record-batch-create --base-token {{LOG_BASE_TOKEN}} --table-id feedback \
  --json '{"fields":["type","title","detail","paper_ref","agent","status"],
           "rows":[["missing-paper","<short title>","<what happened / what you propose, incl. exact errors>","<DOI or paper_id, or empty>","<your product name>","new"]]}' --as user
```

`type` is one of: `bug`, `missing-paper`, `metadata-error`, `feature-request`,
`question`, `other`. The maintainer triages `status` and writes `resolution` —
re-query your record later for the answer. **Connector code/docs issues**
(this skill, the repo): open a GitHub Issue on the repo instead. After filing
anything, tell your user what you filed.

## Troubleshooting

| Symptom | Action |
|---|---|
| `permission denied` / `not exist` | Your account is not a library member → `+apply-permission` (Step 1) or contact the maintainer; do not retry in a loop |
| Error lists `permission_violations` + `hint` | Missing OAuth scope → run the suggested `lark-cli auth login --scope "..."` |
| sha256 mismatch after download | Delete the file, retry once; if persistent, re-download the index (it may be mid-update), then report to the maintainer with paper_id and file_token |
| Downloaded file is 0 bytes / index won't open in sqlite3 | Transient download failure — retry the download once before concluding anything |
| Rate limit | Wait, resume sequentially |
| `confirmation_required` (exit 10) | You attempted a write — forbidden; stop, never add `--yes` |
| `unsafe file path` | Use a relative `--output` path |
