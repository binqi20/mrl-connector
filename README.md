# Management Research Library — Connector

Connect your AI agent (Claude Code, Codex, OpenClaw, Workbuddy, …) to the **Management Research Library**: a shared, read-only Feishu (Lark) Drive folder holding our research group's collection of academic paper PDFs, indexed by a verified SQLite catalog (title, authors, year, journal, DOI, sha256, download token).

**This repository intentionally contains no library location.** Access works like this:

1. The maintainer (Binqi Tang) invites your **Feishu account** as a collaborator and shares the **library folder URL with you privately** (do not repost it).
2. You give your agent two things: this repository (the skill) and that private URL.
3. The agent bootstraps everything else from inside the library itself.

There is no password or anonymous path — agent/API access requires your own authenticated Feishu identity holding view permission. If you don't have a Feishu account, registering a free one is sufficient; the maintainer then invites it.

## Setup

**1. Install lark-cli** — Feishu's official CLI ([github.com/larksuite/cli](https://github.com/larksuite/cli)), requires Node.js:

```bash
npx @larksuite/cli@latest install
lark-cli --version        # this connector is developed against 1.0.64
```

**2. Authenticate as yourself:**

```bash
lark-cli config init
lark-cli auth login --scope "drive:drive.metadata:readonly drive:file:download base:record:read base:record:create search:docs:read"
```

**3. Give your agent the skill** in [`skills/lark-paper-library/SKILL.md`](skills/lark-paper-library/SKILL.md):

- **Claude Code:** copy the folder to `~/.claude/skills/lark-paper-library/`
- **Codex and AGENTS.md-style agents:** point your `AGENTS.md` at the skill file (see [`AGENTS.md`](AGENTS.md))
- **OpenClaw / Workbuddy / others:** any agent that can read markdown and run shell commands can follow the skill as-is

**4. Tell your agent the private folder URL**, then ask, e.g.: *“Find the Piazza 2026 AMJ paper on founders becoming angel investors in the library and download it.”* The skill's bootstrap step turns the URL into the library's own `CONNECT.md`, which carries the full operational details.

## What your agent can do

- **Search** the catalog by title, author (full author lists), year, journal, DOI, or keyword — locally against a downloaded index: instant, exact, no API quota
- **Download** PDFs by catalog token — up to **15 per operation**, up to **80 per rolling 30-hour window** per user
- **Verify** every download against the catalog's sha256 and file size — mismatches are deleted, never kept

Modifying, moving, deleting, or uploading is prohibited and **enforced by the platform**: members hold view-only permission, so the Feishu API rejects any write regardless of what a misbehaving agent attempts.

## Rules

- Search and download only — never modify, move, delete, or upload
- Max **15 PDFs per download operation**; max **80 PDFs per 30 hours** per user (log every operation in the shared Download Log)
- Verify every download against the catalog hash; report real counts
- No whole-library mirroring (`+pull` / `+sync` are forbidden)
- Keep the folder URL private; never commit it, tokens, or `CONNECT.md` to public repositories
- Library contents are for research use by group members; do not redistribute

## Questions & feedback

Read [FAQ.md](FAQ.md) first — setup, search technique, quota semantics, and common errors are covered there (a copy also lives inside the library at `_tracking/FAQ.md`).

- **Library content & workflow** (missing papers, metadata errors, quota, feature ideas): your agent files a record in the `feedback` table next to the Download Log — the skill documents the exact command. The maintainer triages and writes a resolution you can check back on.
- **Connector code & docs** (this repo): open a GitHub Issue; pull requests welcome.

Access requests: contact the maintainer.
