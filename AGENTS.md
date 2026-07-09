# Agent instructions — Management Research Library

You may be asked to find or download academic papers from the **Management Research Library**, a shared read-only Feishu (Lark) Drive folder.

**Read and follow [`skills/lark-paper-library/SKILL.md`](skills/lark-paper-library/SKILL.md) exactly.** Your user will give you the library folder URL privately; the skill's bootstrap step (Step 2) turns it into the library's own `CONNECT.md`, which contains every coordinate this repository deliberately omits.

The contract in brief (the skill is authoritative):

1. Read-only — never upload, modify, rename, move, or delete anything in the library (the platform blocks it anyway; the only permitted write is appending to the Download Log)
2. At most **15 PDFs per download operation**, sequentially; at most **80 PDFs per rolling 30-hour window** per user — check the Download Log before, append to it after
3. Search the downloaded SQLite index first; do not crawl folders
4. Verify every downloaded PDF against the index sha256 + file size; delete and report mismatches
5. Never guess paper identity — ambiguous matches go back to the user as candidates
6. Never mirror the library; never pass `--yes`; report real verified counts
7. Keep the folder URL and all tokens out of anything public
8. Check [`FAQ.md`](FAQ.md) before asking the maintainer; file library/content issues as records in the `feedback` table (the only other permitted write, alongside download logging), and connector code/docs issues as GitHub Issues — then tell your user what you filed
