from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "lark-paper-library" / "scripts" / "mrl_connector.py"
SPEC = importlib.util.spec_from_file_location("mrl_connector", SCRIPT)
mrl = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mrl)


NOW = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def completed(args, code=0, payload=None):
    return subprocess.CompletedProcess(args, code, json.dumps(payload or {}), "")


def local_cli_path(args, kwargs, flag="--output"):
    supplied = Path(args[args.index(flag) + 1])
    if supplied.is_absolute() or supplied.parts != (supplied.name,) or ".." in supplied.parts:
        raise AssertionError("lark-cli local path must be one safe relative basename")
    if "cwd" not in kwargs:
        raise AssertionError("lark-cli local path requires an explicit private cwd")
    cwd = Path(kwargs["cwd"])
    if not cwd.is_dir() or cwd.stat().st_mode & 0o077:
        raise AssertionError("lark-cli cwd must be an existing private directory")
    return cwd / supplied


def base_payload(rows, fields=None):
    fields = fields or ["user_open_id", "user_name", "agent", "papers_count", "paper_ids", "logged_at"]
    return {"ok": True, "data": {"fields": fields, "data": [[row.get(f, "") for f in fields] for row in rows]}}


def make_index(
    path: Path,
    *,
    built_at=NOW,
    version="official_published_issue_pdf",
    note=None,
    pdf=PDF_BYTES,
    match_method="exact_path",
):
    if note is None:
        note = {
            "official_published_issue_pdf": "publication_version=official_published_issue_pdf",
            "in_press_or_online_pdf": "publication_version=in_press_or_online_pdf",
        }.get(version, "generic validation note")
    digest = __import__("hashlib").sha256(pdf).hexdigest()
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE mrl_index (
          paper_id INTEGER, doi TEXT, title TEXT, title_norm TEXT, authors TEXT,
          first_author_last TEXT, first_author_norm TEXT, year TEXT, journal TEXT,
          file_id INTEGER, file_name TEXT, file_size INTEGER, sha256 TEXT,
          file_token TEXT, drive_path TEXT, version_note TEXT,
          publication_version TEXT, match_method TEXT
        );
        CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    db.execute(
        "INSERT INTO mrl_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "10.1000/example", "A Valid Paper", "a valid paper", "Ada Lovelace",
         "Lovelace", "lovelace", "2026", "AMJ", 11, "Valid Paper.pdf", len(pdf),
         digest, "fixture", "batch/Valid Paper.pdf",
         note, version, match_method),
    )
    db.executemany("INSERT INTO index_meta VALUES (?,?)", [
        ("built_at", built_at.isoformat()), ("rows", "1")
    ])
    db.commit()
    db.close()
    return digest


class IndexTests(unittest.TestCase):
    def test_run_lark_forwards_cwd_only_for_explicit_local_file_calls(self):
        seen = []

        def runner(args, **kwargs):
            seen.append((args, kwargs))
            return completed(args)

        mrl.run_lark(["whoami", "--as", "user"], runner=runner)
        self.assertNotIn("cwd", seen[0][1])
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            os.chmod(private, 0o700)
            mrl.run_lark(
                ["drive", "+download", "--output", "artifact.bin"],
                runner=runner,
                cwd=private,
            )
        self.assertEqual(Path(seen[1][1]["cwd"]), private)

    def test_valid_index_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path)
            result = mrl.validate_index(path, now=NOW)
            self.assertEqual(result["rows"], 1)
            rows = mrl.search_index(path, author="Lovelace", now=NOW)
            self.assertEqual([row["file_id"] for row in rows], [11])
            self.assertEqual(rows[0]["publication_version"], "official_published_issue_pdf")

    def test_title_search_matches_tracker_normalization(self):
        cases = [
            ("Firm’s capability", "firms capability", "Firm’s capability"),
            ("A core–periphery strategy", "a coreperiphery strategy", "core–periphery"),
            ("A core—periphery strategy", "a coreperiphery strategy", "core—periphery"),
            ("Café strategy", "cafe strategy", "Café strategy"),
            ("Firm's capability", "firm s capability", "Firm's capability"),
            ("R&D: growth", "r d growth", "R&D: growth"),
        ]
        for title, title_norm, query in cases:
            with self.subTest(title=title):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "index.sqlite3"
                    make_index(path)
                    db = sqlite3.connect(path)
                    db.execute(
                        "UPDATE mrl_index SET title=?,title_norm=?",
                        (title, title_norm),
                    )
                    db.commit()
                    db.close()
                    rows = mrl.search_index(path, title=query, now=NOW)
                    self.assertEqual([row["file_id"] for row in rows], [11])

    def test_title_search_without_ascii_terms_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path)
            with self.assertRaisesRegex(mrl.ConnectorError, "no searchable ASCII"):
                mrl.search_index(path, title="战略管理", now=NOW)

    def test_stale_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path, built_at=NOW - timedelta(days=7, seconds=1))
            with self.assertRaisesRegex(mrl.ConnectorError, "7-day"):
                mrl.validate_index(path, now=NOW)

    def test_unknown_is_valid_but_unrecognized_value_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            unknown = Path(directory) / "unknown.sqlite3"
            make_index(unknown, version="unknown")
            self.assertEqual(mrl.validate_index(unknown, now=NOW)["rows"], 1)
            bad = Path(directory) / "bad.sqlite3"
            make_index(bad, version="probably-final")
            with self.assertRaisesRegex(mrl.ConnectorError, "publication-version"):
                mrl.validate_index(bad, now=NOW)

    def test_generic_note_cannot_claim_official_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path, version="official_published_issue_pdf", note="generic validation note")
            with self.assertRaisesRegex(mrl.ConnectorError, "conflicts with exact"):
                mrl.validate_index(path, now=NOW)

    def test_marker_with_suffix_is_not_an_exact_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(
                path,
                version="official_published_issue_pdf",
                note="publication_version=official_published_issue_pdf-typo",
            )
            with self.assertRaisesRegex(mrl.ConnectorError, "conflicts with exact"):
                mrl.validate_index(path, now=NOW)

    def test_dynamic_sqlite_type_is_rejected_before_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path)
            db = sqlite3.connect(path)
            db.execute("UPDATE mrl_index SET file_size='abc'")
            db.commit()
            db.close()
            with self.assertRaisesRegex(mrl.ConnectorError, "incomplete file identities"):
                mrl.validate_index(path, now=NOW)

    def test_unsafe_file_name_and_drive_components_are_rejected(self):
        cases = [
            ("../Valid Paper.pdf", "batch/../Valid Paper.pdf"),
            ("Valid Paper.pdf", "../Valid Paper.pdf"),
            ("Valid Paper.pdf", "batch/_suspect/Valid Paper.pdf"),
            ("Valid Paper.pdf", "batch//Valid Paper.pdf"),
        ]
        for file_name, drive_path in cases:
            with self.subTest(file_name=file_name, drive_path=drive_path):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "index.sqlite3"
                    make_index(path)
                    db = sqlite3.connect(path)
                    db.execute("UPDATE mrl_index SET file_name=?,drive_path=?", (file_name, drive_path))
                    db.commit()
                    db.close()
                    with self.assertRaisesRegex(mrl.ConnectorError, "unsafe"):
                        mrl.validate_index(path, now=NOW)

    def test_exact_documented_240_character_truncation_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path, match_method="name_truncated_240")
            local_stem = "A" * 240 + " complete local suffix"
            remote_stem = "A" * 240
            db = sqlite3.connect(path)
            db.execute(
                "UPDATE mrl_index SET file_name=?,drive_path=?",
                (local_stem + ".pdf", "batch/" + remote_stem + ".pdf"),
            )
            db.commit()
            db.close()
            self.assertEqual(mrl.validate_index(path, now=NOW)["rows"], 1)

    def test_truncation_exception_fails_closed_outside_exact_rule(self):
        cases = [
            ("exact_path", "A" * 241 + ".pdf", "A" * 240 + ".pdf"),
            ("name_truncated_240", "A" * 240 + ".pdf", "A" * 240 + ".pdf"),
            ("name_truncated_240", "A" * 241 + ".pdf", "A" * 239 + ".pdf"),
            ("name_truncated_240", "A" * 242 + ".pdf", "A" * 241 + ".pdf"),
            ("name_truncated_240", "A" * 241 + ".pdf", "B" * 240 + ".pdf"),
            ("name_truncated_240", "A" * 241 + ".pdf", "A" * 240 + ".txt"),
        ]
        for match_method, file_name, remote_name in cases:
            with self.subTest(
                match_method=match_method,
                remote_stem_length=len(Path(remote_name).stem),
            ):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "index.sqlite3"
                    make_index(path)
                    db = sqlite3.connect(path)
                    db.execute(
                        "UPDATE mrl_index SET file_name=?,drive_path=?,match_method=?",
                        (file_name, "batch/" + remote_name, match_method),
                    )
                    db.commit()
                    db.close()
                    with self.assertRaises(mrl.ConnectorError):
                        mrl.validate_index(path, now=NOW)

    def test_invalid_path_match_methods_are_rejected(self):
        for match_method in (None, "", "name_truncated", "unknown", 7, b"exact_path"):
            with self.subTest(match_method=match_method):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "index.sqlite3"
                    make_index(path, match_method=match_method)
                    with self.assertRaisesRegex(
                        mrl.ConnectorError, "invalid path-match method"
                    ):
                        mrl.validate_index(path, now=NOW)

    def test_quarantine_components_are_exact_and_case_insensitive(self):
        cases = [
            ("exact_path", "Valid Paper.pdf", "batch/_SUSPECT/Valid Paper.pdf"),
            (
                "name_truncated_240",
                "A" * 241 + ".pdf",
                "batch/_PiPeLiNe_ArChIvE/" + "A" * 240 + ".pdf",
            ),
        ]
        for match_method, file_name, drive_path in cases:
            with self.subTest(match_method=match_method, drive_path=drive_path):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "index.sqlite3"
                    make_index(path)
                    db = sqlite3.connect(path)
                    db.execute(
                        "UPDATE mrl_index SET file_name=?,drive_path=?,match_method=?",
                        (file_name, drive_path, match_method),
                    )
                    db.commit()
                    db.close()
                    with self.assertRaisesRegex(mrl.ConnectorError, "unsafe"):
                        mrl.validate_index(path, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path)
            db = sqlite3.connect(path)
            db.execute(
                "UPDATE mrl_index SET drive_path=?",
                ("batch/_suspect-review/Valid Paper.pdf",),
            )
            db.commit()
            db.close()
            self.assertEqual(mrl.validate_index(path, now=NOW)["rows"], 1)

    def test_missing_match_method_column_is_incompatible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path)
            db = sqlite3.connect(path)
            db.execute("ALTER TABLE mrl_index DROP COLUMN match_method")
            db.commit()
            db.close()
            with self.assertRaisesRegex(mrl.ConnectorError, "incompatible"):
                mrl.validate_index(path, now=NOW)

    def test_missing_derived_column_is_incompatible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            make_index(path)
            db = sqlite3.connect(path)
            db.execute("ALTER TABLE mrl_index RENAME TO old")
            db.execute("CREATE TABLE mrl_index AS SELECT paper_id,file_id,title FROM old")
            db.commit()
            db.close()
            with self.assertRaisesRegex(mrl.ConnectorError, "incompatible"):
                mrl.validate_index(path, now=NOW)

    def test_fetch_uses_exact_token_private_temp_and_validates_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            source = Path(directory) / "source.sqlite3"
            make_index(source, built_at=datetime.now(timezone.utc))
            output = state / "mrl-index.sqlite3"
            seen = []

            def runner(args, **kwargs):
                seen.append(args)
                target = local_cli_path(args, kwargs)
                self.assertFalse(target.exists())
                shutil.copyfile(source, target)
                return completed(args)

            result = mrl.fetch_index("exact-runtime-token", output, runner=runner)
            self.assertEqual(result["rows"], 1)
            self.assertEqual(oct(output.stat().st_mode & 0o777), "0o600")
            self.assertIn("exact-runtime-token", seen[0])
            self.assertNotIn("+search", seen[0])


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_paginates_exact_path_and_installs_private_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            output = state / "CONNECT.md"
            listed = []

            def runner(args, **kwargs):
                if args[1:4] == ["drive", "files", "list"]:
                    params = json.loads(args[args.index("--params") + 1])
                    listed.append(params)
                    if params["folder_token"] == "rootfolder123" and not params.get("page_token"):
                        payload = {"ok": True, "data": {"files": [], "has_more": True, "next_page_token": "page2"}}
                    elif params["folder_token"] == "rootfolder123":
                        payload = {"ok": True, "data": {"files": [{"name": "_tracking", "type": "folder", "token": "tracking-runtime-token"}], "has_more": False}}
                    else:
                        payload = {"ok": True, "data": {"files": [{"name": "CONNECT.md", "type": "file", "token": "connect-runtime-token"}], "has_more": False}}
                    return completed(args, payload=payload)
                if args[1:3] == ["drive", "+download"]:
                    target = local_cli_path(args, kwargs)
                    target.write_text("mrl_connector.py --file-token runtime --base-token runtime\n", encoding="utf-8")
                    return completed(args)
                raise AssertionError(args)

            result = mrl.bootstrap("https://private.invalid/drive/folder/rootfolder123", output, runner=runner)
            self.assertEqual(result["status"], "ready")
            self.assertNotIn("token", json.dumps(result))
            self.assertEqual(oct(output.stat().st_mode & 0o777), "0o600")
            self.assertEqual([item.get("page_token") for item in listed[:2]], [None, "page2"])

    def test_ambiguous_contract_path_refuses_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            downloaded = []

            def runner(args, **kwargs):
                if args[1:4] == ["drive", "files", "list"]:
                    payload = {"ok": True, "data": {"files": [
                        {"name": "_tracking", "type": "folder", "token": "one-runtime-token"},
                        {"name": "_tracking", "type": "folder", "token": "two-runtime-token"},
                    ], "has_more": False}}
                    return completed(args, payload=payload)
                downloaded.append(args)
                return completed(args)

            with self.assertRaisesRegex(mrl.ConnectorError, "ambiguous"):
                mrl.bootstrap("https://private.invalid/drive/folder/rootfolder123", state / "CONNECT.md", runner=runner)
            self.assertEqual(downloaded, [])


class QuotaTests(unittest.TestCase):
    def test_full_offset_pagination(self):
        calls = []
        base_row = {
            "user_open_id": "ou_test", "user_name": "Tester", "agent": "Codex Desktop",
            "papers_count": 1, "paper_ids": "1", "logged_at": "2026-08-03 11:00:00",
        }

        def runner(args, **kwargs):
            offset = int(args[args.index("--offset") + 1])
            calls.append(offset)
            rows = [base_row] * (200 if offset == 0 else 1)
            return completed(args, payload=base_payload(rows))

        rows = mrl.download_log_rows("runtime-base-token", "ou_test", runner=runner)
        self.assertEqual(len(rows), 201)
        self.assertEqual(calls, [0, 200, 0, 200])

    def test_quota_unavailable_fails_without_fallback(self):
        def runner(args, **kwargs):
            return completed(args, code=1)

        with self.assertRaisesRegex(mrl.ConnectorError, "quota query failed"):
            mrl.download_log_rows("runtime-base-token", "ou_test", runner=runner)

    def test_empty_response_requires_exact_projection(self):
        malformed_projections = [
            ["user_open_id", "user_name", "agent", "papers_count", "paper_ids"],
            ["user_open_id", "user_name", "agent", "papers_count", "paper_ids", "paper_ids"],
            ["user_open_id", "user_name", "agent", "papers_count", "paper_ids", "logged_at", "unexpected"],
        ]
        for fields in malformed_projections:
            with self.subTest(fields=fields):
                def runner(args, **kwargs):
                    return completed(args, payload=base_payload([], fields=fields))

                with self.assertRaisesRegex(mrl.ConnectorError, "projection"):
                    mrl.download_log_rows("runtime-base-token", "ou_test", runner=runner)

    def test_rolling_window_is_30_hours(self):
        rows = [
            {"papers_count": 7, "logged_at": "2026-08-02 06:01:00"},
            {"papers_count": 9, "logged_at": "2026-08-02 05:59:59"},
        ]
        used, _ = mrl.quota_usage(rows, now=NOW.astimezone(mrl.TZ8))
        self.assertEqual(used, 7)

    def test_timezone_explicit_iso_timestamps_are_counted(self):
        rows = [
            {"papers_count": 7, "logged_at": "2026-08-03T11:00:00.123+08:00"},
            {"papers_count": 5, "logged_at": "2026-08-03T03:30:00Z"},
        ]
        try:
            used, _ = mrl.quota_usage(rows, now=NOW.astimezone(mrl.TZ8))
        except mrl.ConnectorError as error:
            self.fail(f"valid timezone-explicit timestamps were refused: {error}")
        self.assertEqual(used, 12)

    def test_timezone_ambiguous_or_non_string_timestamps_fail_closed(self):
        invalid_values = [
            None,
            1786455000000,
            "2026-08-03T11:00:00",
            " 2026-08-03T11:00:00+08:00",
            "2026-08-03X11:00:00+08:00",
            "2026-08-03\x0011:00:00+08:00",
            "2026-08-03T11:00:00-00:00",
            "not-a-time",
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(mrl.ConnectorError, "invalid timestamp"):
                    mrl.quota_usage(
                        [{"papers_count": 1, "logged_at": value}],
                        now=NOW.astimezone(mrl.TZ8),
                    )

    def test_future_timestamp_fails_closed(self):
        rows = [{"papers_count": 1, "logged_at": "2026-08-03 12:05:01"}]
        with self.assertRaisesRegex(mrl.ConnectorError, "future timestamp"):
            mrl.quota_usage(rows, now=NOW.astimezone(mrl.TZ8))

    def test_timezone_explicit_iso_future_timestamp_fails_closed(self):
        rows = [{"papers_count": 1, "logged_at": "2026-08-03T12:05:01.000+08:00"}]
        with self.assertRaisesRegex(mrl.ConnectorError, "future timestamp"):
            mrl.quota_usage(rows, now=NOW.astimezone(mrl.TZ8))


class IdentityTests(unittest.TestCase):
    def test_malformed_nested_identity_envelope_is_controlled(self):
        def runner(args, **kwargs):
            return completed(args, payload={"ok": True, "data": []})

        with self.assertRaisesRegex(mrl.ConnectorError, "malformed envelope"):
            mrl.whoami(runner=runner)

    def test_cli_malformed_identity_returns_two_without_traceback(self):
        if os.name == "nt":
            process = subprocess.run(
                [sys.executable, str(SCRIPT), "quota", "--base-token", "fixture", "--requested", "0"],
                capture_output=True,
                text=True,
                check=False,
                env=dict(os.environ, PATH=""),
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn("refused: native lark-cli executable is unavailable", process.stderr)
            self.assertNotIn("Traceback", process.stderr)
            return
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "lark-cli"
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"data\":[]}'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            environment = dict(os.environ, PATH=directory + os.pathsep + os.environ.get("PATH", ""))
            process = subprocess.run(
                [sys.executable, str(SCRIPT), "quota", "--base-token", "fixture", "--requested", "0"],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn("refused: identity check returned a malformed envelope", process.stderr)
            self.assertNotIn("Traceback", process.stderr)


class DownloadTests(unittest.TestCase):
    def test_state_lock_serializes_same_device_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            journal = state / "pending.json"
            with mrl.exclusive_state_lock(journal):
                with self.assertRaisesRegex(mrl.ConnectorError, "holds the state lock"):
                    with mrl.exclusive_state_lock(journal, timeout_seconds=0):
                        self.fail("second lock must not be admitted")

    def test_journal_creation_is_no_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            store = mrl.PendingJournal(state / "pending.json")
            first = {"schema": 1, "sentinel": "first"}
            store.create(first)
            with self.assertRaisesRegex(mrl.ConnectorError, "unresolved pending"):
                store.create({"schema": 1, "sentinel": "second"})
            self.assertEqual(json.loads(store.path.read_text()), first)

    def test_existing_pdf_refuses_before_remote_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            make_index(index)
            output = root / "papers"
            output.mkdir()
            existing = output / "Valid Paper.pdf"
            existing.write_bytes(b"existing bytes")
            calls = []

            def runner(args, **kwargs):
                calls.append(args)
                raise AssertionError("remote access must not occur")

            with self.assertRaisesRegex(mrl.ConnectorError, "already exists"):
                mrl.download_files(index, [11], output, "base", "Codex Desktop", root / "state" / "pending.json", runner=runner, now=NOW, lock_path=root / "state" / "lock")
            self.assertEqual(existing.read_bytes(), b"existing bytes")
            self.assertEqual(calls, [])

    def test_pending_journal_blocks_next_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            make_index(index)
            state = root / "state"
            state.mkdir(mode=0o700)
            journal = mrl.PendingJournal(state / "pending.json")
            journal.write({"schema": 1, "state": "write_attempting"})
            with self.assertRaisesRegex(mrl.ConnectorError, "unresolved pending"):
                mrl.download_files(index, [11], root / "papers", "base", "Codex Desktop", journal.path, now=NOW, lock_path=state / "lock")

    def test_verified_download_is_no_clobber_logged_and_journal_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            make_index(index)
            output = root / "papers"
            state = root / "state"
            state.mkdir(mode=0o700)
            journal = state / "pending.json"
            searches = 0
            create_seen = []
            logged_agent = None

            def runner(args, **kwargs):
                nonlocal searches, logged_agent
                if args[1] == "whoami":
                    return completed(args, payload={"onBehalfOf": {"openId": "ou_test", "userName": "Tester"}})
                if args[1:3] == ["base", "+record-search"]:
                    searches += 1
                    rows = []
                    if searches >= 5:
                        rows = [{
                            "user_open_id": "ou_test", "user_name": "Tester", "agent": logged_agent,
                            "papers_count": 1, "paper_ids": "1", "logged_at": "2026-08-03 12:00:01",
                        }]
                    return completed(args, payload=base_payload(rows))
                if args[1:3] == ["drive", "+download"]:
                    target = local_cli_path(args, kwargs)
                    self.assertFalse(target.exists())
                    target.write_bytes(PDF_BYTES)
                    return completed(args)
                if args[1:3] == ["base", "+record-batch-create"]:
                    create_seen.append(json.loads(args[args.index("--json") + 1]))
                    logged_agent = create_seen[-1]["rows"][0][2]
                    return completed(args, payload={"ok": True})
                raise AssertionError(args)

            result = mrl.download_files(index, [11], output, "base", "Codex Desktop", journal, runner=runner, now=NOW, lock_path=state / "lock")
            self.assertEqual(result["verified"], 1)
            self.assertFalse(journal.exists())
            self.assertEqual((output / "Valid Paper.pdf").read_bytes(), PDF_BYTES)
            self.assertRegex(create_seen[0]["rows"][0][2], r"^Codex Desktop \[mrl-op:[0-9a-f-]{36}\]$")

    def test_unconfirmed_log_keeps_journal_and_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            make_index(index)
            state = root / "state"
            state.mkdir(mode=0o700)
            journal = state / "pending.json"

            def runner(args, **kwargs):
                if args[1] == "whoami":
                    return completed(args, payload={"onBehalfOf": {"openId": "ou_test", "userName": "Tester"}})
                if args[1:3] == ["base", "+record-search"]:
                    return completed(args, payload=base_payload([]))
                if args[1:3] == ["drive", "+download"]:
                    local_cli_path(args, kwargs).write_bytes(PDF_BYTES)
                    return completed(args)
                if args[1:3] == ["base", "+record-batch-create"]:
                    return completed(args, code=1)
                raise AssertionError(args)

            with self.assertRaisesRegex(mrl.ConnectorError, "unconfirmed"):
                mrl.download_files(index, [11], root / "papers", "base", "Codex Desktop", journal, runner=runner, now=NOW, lock_path=state / "lock")
            self.assertTrue(journal.exists())
            self.assertEqual(oct(journal.stat().st_mode & 0o777), "0o600")

    def test_reconcile_confirms_exact_pending_record_and_unblocks(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            journal_path = state / "pending.json"
            store = mrl.PendingJournal(journal_path)
            store.write({
                "schema": 1, "operation_id": "test-operation", "state": "write_attempting",
                "started_at": NOW.isoformat(), "user_open_id": "ou_test", "user_name": "Tester",
                "agent": "Codex Desktop", "log_agent": "Codex Desktop [mrl-op:test-operation]", "selected_file_ids": [11],
                "verified": [{"paper_id": 1, "file_id": 11, "file_name": "Valid Paper.pdf"}],
                "baseline_match_count": 0,
            })
            writes = []

            def runner(args, **kwargs):
                if args[1] == "whoami":
                    return completed(args, payload={"onBehalfOf": {"openId": "ou_test", "userName": "Tester"}})
                if args[1:3] == ["base", "+record-search"]:
                    return completed(args, payload=base_payload([{
                        "user_open_id": "ou_test", "user_name": "Tester", "agent": "Codex Desktop [mrl-op:test-operation]",
                        "papers_count": 1, "paper_ids": "1", "logged_at": "2026-08-03 12:00:01",
                    }]))
                writes.append(args)
                return completed(args)

            result = mrl.reconcile_pending("base", "Codex Desktop", journal_path, runner=runner, lock_path=state / "lock")
            self.assertEqual(result, {"status": "reconciled", "verified": 1})
            self.assertFalse(journal_path.exists())
            self.assertEqual(writes, [])

    def test_reconcile_ambiguous_remote_records_keeps_block(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            journal_path = state / "pending.json"
            mrl.PendingJournal(journal_path).write({
                "schema": 1, "operation_id": "test-operation", "state": "write_attempting",
                "started_at": NOW.isoformat(), "user_open_id": "ou_test", "user_name": "Tester",
                "agent": "Codex Desktop", "log_agent": "Codex Desktop [mrl-op:test-operation]", "selected_file_ids": [11],
                "verified": [{"paper_id": 1, "file_id": 11, "file_name": "Valid Paper.pdf"}],
                "baseline_match_count": 0,
            })
            row = {
                "user_open_id": "ou_test", "user_name": "Tester", "agent": "Codex Desktop [mrl-op:test-operation]",
                "papers_count": 1, "paper_ids": "1", "logged_at": "2026-08-03 12:00:01",
            }

            def runner(args, **kwargs):
                if args[1] == "whoami":
                    return completed(args, payload={"onBehalfOf": {"openId": "ou_test", "userName": "Tester"}})
                return completed(args, payload=base_payload([row, row]))

            with self.assertRaisesRegex(mrl.ConnectorError, "ambiguous"):
                mrl.reconcile_pending("base", "Codex Desktop", journal_path, runner=runner, lock_path=state / "lock")
            self.assertTrue(journal_path.exists())


class WindowsPortTests(unittest.TestCase):
    def test_helper_imports_and_reports_v113(self):
        imported = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.util,sys;"
                "s=importlib.util.spec_from_file_location('connector',sys.argv[1]);"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "print(m.VERSION)",
                str(SCRIPT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(imported.stdout.strip(), "1.1.3")
        version = subprocess.run(
            [sys.executable, str(SCRIPT), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout.strip(), "1.1.3")

    def test_paths_with_spaces_support_bootstrap_fetch_search_and_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "connector state with spaces"
            state = root / "private state with spaces"
            state.mkdir(parents=True, mode=0o700)
            os.chmod(state, 0o700)
            source = root / "source index with spaces.sqlite3"
            make_index(source, built_at=datetime.now(timezone.utc))

            def fetch_runner(args, **kwargs):
                target = Path(kwargs["cwd"]) / args[args.index("--output") + 1]
                self.assertIn(" ", str(target))
                shutil.copyfile(source, target)
                return completed(args)

            index = state / "local index with spaces.sqlite3"
            self.assertEqual(mrl.fetch_index("runtime-token", index, runner=fetch_runner)["rows"], 1)
            self.assertEqual(
                [row["file_id"] for row in mrl.search_index(index, doi="10.1000/example")],
                [11],
            )

            def bootstrap_runner(args, **kwargs):
                if args[1:4] == ["drive", "files", "list"]:
                    params = json.loads(args[args.index("--params") + 1])
                    if params["folder_token"] == "rootfolder123":
                        files = [{"name": "_tracking", "type": "folder", "token": "tracking-token"}]
                    else:
                        files = [{"name": "CONNECT.md", "type": "file", "token": "connect-token"}]
                    return completed(args, payload={"ok": True, "data": {"files": files, "has_more": False}})
                target = Path(kwargs["cwd"]) / args[args.index("--output") + 1]
                self.assertIn(" ", str(target))
                target.write_text(
                    "mrl_connector.py --file-token runtime --base-token runtime\n",
                    encoding="utf-8",
                )
                return completed(args)

            contract = state / "private contract with spaces.md"
            result = mrl.bootstrap(
                "https://private.invalid/drive/folder/rootfolder123",
                contract,
                runner=bootstrap_runner,
            )
            self.assertEqual(result["status"], "ready")
            self.assertTrue(contract.is_file())

            def quota_runner(args, **kwargs):
                return completed(args, payload=base_payload([]))

            rows = mrl.download_log_rows("runtime-base", "ou_test", runner=quota_runner)
            self.assertEqual(mrl.quota_usage(rows, now=NOW), (0, None))

    def test_state_lock_times_out_across_processes_then_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "state with spaces" / "download.lock"
            holder = textwrap.dedent(
                """
                import importlib.util
                import sys
                from pathlib import Path
                spec = importlib.util.spec_from_file_location("connector", sys.argv[1])
                connector = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(connector)
                with connector.exclusive_state_lock(Path(sys.argv[2]), timeout_seconds=2):
                    print("locked", flush=True)
                    sys.stdin.readline()
                """
            )
            process = subprocess.Popen(
                [sys.executable, "-c", holder, str(SCRIPT), str(lock_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                started = time.monotonic()
                with self.assertRaisesRegex(mrl.ConnectorError, "holds the state lock"):
                    with mrl.exclusive_state_lock(lock_path, timeout_seconds=0.2):
                        self.fail("a second process must not enter the critical section")
                self.assertGreaterEqual(time.monotonic() - started, 0.15)
                process.stdin.write("release\n")
                process.stdin.flush()
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                with mrl.exclusive_state_lock(lock_path, timeout_seconds=1):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_invalid_pdf_is_not_installed(self):
        invalid_pdf = b"not-a-pdf\n%%EOF\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            make_index(index)
            state = root / "state"
            state.mkdir(mode=0o700)

            def runner(args, **kwargs):
                if args[1] == "whoami":
                    return completed(args, payload={"onBehalfOf": {"openId": "ou_test", "userName": "Tester"}})
                if args[1:3] == ["base", "+record-search"]:
                    return completed(args, payload=base_payload([]))
                if args[1:3] == ["drive", "+download"]:
                    (Path(kwargs["cwd"]) / args[args.index("--output") + 1]).write_bytes(invalid_pdf)
                    return completed(args)
                raise AssertionError(args)

            result = mrl.download_files(
                index, [11], root / "papers with spaces", "base", "Codex Desktop",
                state / "pending.json", runner=runner, now=NOW, lock_path=state / "lock",
            )
            self.assertEqual(result["verified"], 0)
            self.assertEqual(result["failed"], ["Valid Paper.pdf"])
            self.assertFalse((root / "papers with spaces" / "Valid Paper.pdf").exists())
            self.assertFalse((state / "pending.json").exists())

    def test_target_created_during_download_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            make_index(index)
            output = root / "papers"
            state = root / "state"
            state.mkdir(mode=0o700)
            target = output / "Valid Paper.pdf"

            def runner(args, **kwargs):
                if args[1] == "whoami":
                    return completed(args, payload={"onBehalfOf": {"openId": "ou_test", "userName": "Tester"}})
                if args[1:3] == ["base", "+record-search"]:
                    return completed(args, payload=base_payload([]))
                if args[1:3] == ["drive", "+download"]:
                    (Path(kwargs["cwd"]) / args[args.index("--output") + 1]).write_bytes(PDF_BYTES)
                    target.write_bytes(b"race winner")
                    return completed(args)
                raise AssertionError(args)

            with self.assertRaisesRegex(mrl.ConnectorError, "target appeared"):
                mrl.download_files(
                    index, [11], output, "base", "Codex Desktop", state / "pending.json",
                    runner=runner, now=NOW, lock_path=state / "lock",
                )
            self.assertEqual(target.read_bytes(), b"race winner")
            self.assertTrue((state / "pending.json").exists())

    @unittest.skipUnless(os.name == "nt", "native Windows command resolution")
    def test_run_lark_resolves_native_binary_behind_lark_cli_cmd_without_shell(self):
        with tempfile.TemporaryDirectory(prefix="cli path with spaces ") as directory:
            command = Path(directory) / "lark-cli.cmd"
            command.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
            native = (
                Path(directory) / "node_modules" / "@larksuite" / "cli" /
                "bin" / "lark-cli.exe"
            )
            native.parent.mkdir(parents=True)
            native.write_bytes(b"fixture")
            (native.parents[1] / "package.json").write_text(
                '{"name":"@larksuite/cli","version":"fixture"}',
                encoding="utf-8",
            )
            seen = []

            def runner(args, **kwargs):
                seen.append((args, kwargs))
                return completed(args)

            with mock.patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ.get("PATH", "")}):
                mrl.run_lark(["--version"], runner=runner)
            self.assertEqual(Path(seen[0][0][0]).resolve(), native.resolve())
            self.assertFalse(seen[0][1].get("shell", False))

    @unittest.skipUnless(os.name == "nt", "native Windows ACL semantics")
    def test_mode_700_state_directory_acl_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private state"
            private.mkdir(mode=0o700)
            mrl._secure_state_dir(private)
            secret = private / "secret.bin"
            secret.write_bytes(b"private")
            mrl._set_private_file(secret)
            child = mrl._make_private_temp_dir(prefix="temporary-", parent=private)
            mrl._secure_state_dir(child)

    @unittest.skipUnless(os.name == "nt", "native Windows ACL semantics")
    def test_windows_acl_with_everyone_read_grant_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private state"
            private.mkdir(mode=0o700)
            granted = subprocess.run(
                ["icacls", str(private), "/grant", "*S-1-1-0:(OI)(CI)R", "/Q"],
                capture_output=True,
                text=True,
                check=False,
            )
            if granted.returncode != 0:
                self.skipTest("this Windows runner cannot add a synthetic read ACE")
            with self.assertRaisesRegex(mrl.ConnectorError, "ACL is not owner-only"):
                mrl._secure_state_dir(private)

    @unittest.skipUnless(os.name == "nt", "native Windows command resolution")
    def test_run_lark_resolves_project_local_official_package_layout(self):
        with tempfile.TemporaryDirectory(prefix="project cli with spaces ") as directory:
            node_modules = Path(directory) / "node_modules"
            shim_dir = node_modules / ".bin"
            shim_dir.mkdir(parents=True)
            (shim_dir / "lark-cli.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
            package_root = node_modules / "@larksuite" / "cli"
            native = package_root / "bin" / "lark-cli.exe"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"fixture")
            (package_root / "package.json").write_text(
                '{"name":"@larksuite/cli","version":"fixture"}',
                encoding="utf-8",
            )
            seen = []

            def runner(args, **kwargs):
                seen.append((args, kwargs))
                return completed(args)

            with mock.patch.dict(os.environ, {"PATH": str(shim_dir)}):
                mrl.run_lark(["--version"], runner=runner)
            self.assertEqual(Path(seen[0][0][0]).resolve(), native.resolve())
            self.assertFalse(seen[0][1].get("shell", False))

    @unittest.skipUnless(os.name == "nt", "Windows path rules")
    def test_windows_device_ads_and_trailing_names_are_rejected(self):
        for unsafe_name in (
            "CON.pdf", "AUX.PDF", "COM1.pdf", "LPT9.pdf",
            "paper:stream.pdf", "paper.pdf ", "paper.pdf.",
        ):
            with self.subTest(name=unsafe_name), tempfile.TemporaryDirectory() as directory:
                index = Path(directory) / "index.sqlite3"
                make_index(index)
                db = sqlite3.connect(index)
                db.execute(
                    "UPDATE mrl_index SET file_name=?,drive_path=?",
                    (unsafe_name, "batch/" + unsafe_name),
                )
                db.commit()
                db.close()
                with self.assertRaises(mrl.ConnectorError):
                    mrl.validate_index(index, now=NOW)

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    def test_junction_state_directory_is_rejected_before_remote_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real private state"
            target.mkdir()
            junction = root / "junction state"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest("this Windows runner cannot create a directory junction")
            calls = []

            def runner(args, **kwargs):
                calls.append(args)
                raise AssertionError("unsafe state must be refused before remote access")

            with self.assertRaises(mrl.ConnectorError):
                mrl.fetch_index("runtime-token", junction / "index.sqlite3", runner=runner)
            self.assertEqual(calls, [])


class PublicContractTests(unittest.TestCase):
    def test_frontmatter_version_is_exact(self):
        text = (ROOT / "skills/lark-paper-library/SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        self.assertIn("\nversion: 1.1.3\n", "\n" + frontmatter)
        self.assertEqual(mrl.VERSION, "1.1.3")

    def test_public_tree_has_no_forbidden_fallbacks(self):
        files = [ROOT / "AGENTS.md", ROOT / "README.md", ROOT / "FAQ.md", ROOT / "DEPLOYMENT.md", ROOT / "skills/lark-paper-library/SKILL.md", SCRIPT]
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertNotIn("auth login --domain all", text)
        self.assertNotIn("drive +search", text)
        self.assertNotIn("download-ledger.jsonl", text)

    def test_deployment_order_and_direct_faq_input_are_explicit(self):
        text = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        release_step = "publish the v1.1.3 connector"
        self.assertIn(release_step, text)
        if release_step in text:
            self.assertLess(
                text.index("Require the compatible v1.1 MRL SQLite index first"),
                text.index(release_step),
            )
        self.assertIn("The reviewed public file [`FAQ.md`](FAQ.md) is the sole FAQ publication input", text)
        self.assertIn("--file FAQ.md", text)
        self.assertNotIn('--file "$REPO_ROOT/FAQ.md"', text)
        self.assertIn("--output FAQ.md", text)
        self.assertNotIn('--output "$CHECK_DIR/FAQ.md"', text)
        self.assertIn('cmp -s "$REPO_ROOT/FAQ.md" "$CHECK_DIR/FAQ.md"', text)


if __name__ == "__main__":
    unittest.main()
