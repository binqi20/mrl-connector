# Agent instructions — Management Research Library connector v1.1.4

Read and follow [`skills/lark-paper-library/SKILL.md`](skills/lark-paper-library/SKILL.md)
exactly. Use its tested helper; do not recreate the workflow with ad hoc shell
commands.

The maintainer shares the library folder URL privately. Never expose that URL,
CONNECT contents, file tokens, Base tokens, credentials, or tenant coordinates
in public source, issues, logs, or responses.

Contract summary:

1. Search only a validated, compatible SQLite index no older than seven days.
   Never crawl/search Drive as a fallback.
2. Library Drive is read-only. Only Download Log and feedback Base appends are
   permitted.
3. At most 15 PDFs per operation and 80 per rolling 30 hours per user. Read the
   authoritative Base with complete pagination; failure blocks downloads.
4. Record the actual agent identifier. Never use a hard-coded placeholder.
5. Download sequentially through private temporary files; validate size,
   SHA-256, PDF header, and EOF; never overwrite or delete an existing PDF.
6. An uncertain log append leaves a durable pending journal. Reconcile it before
   any later download; never delete or edit the journal to bypass the block.
7. Interpret only exact derived publication-version values. Blank or generic
   evidence is `unknown`.
8. Never guess identity, mirror the library, pass `--yes`, or weaken a
   controlled refusal.

Maintainers must follow [`DEPLOYMENT.md`](DEPLOYMENT.md): validate the compatible
v1.1 index with the reviewed v1.1.4 helper first, publish connector/FAQ second,
and publish the public release last. The reviewed root `FAQ.md` is the direct
deterministic FAQ publication input.
