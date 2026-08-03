#!/usr/bin/env python3
"""Fail-closed client for the Management Research Library connector.

The helper deliberately knows no library coordinates.  Tokens are supplied at
runtime from the private CONNECT.md.  Search is local and SQLite-only; the only
remote operations are exact-token downloads and authoritative Download Log
reads/appends.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote, urlsplit


VERSION = "1.1.1"
EXIT_REFUSED = 2
MAX_OPERATION = 15
MAX_ROLLING = 80
ROLLING_HOURS = 30
MAX_INDEX_AGE = timedelta(days=7)
TZ8 = timezone(timedelta(hours=8))
VALID_VERSIONS = {
    "official_published_issue_pdf",
    "in_press_or_online_pdf",
    "unknown",
}
VALID_MATCH_METHODS = {
    "exact_path",
    "name_truncated_240",
}
OFFICIAL_MARKER_RE = re.compile(
    r"(?:^|[\s;,|])publication_version=official_published_issue_pdf(?=$|[\s;,|])"
)
IN_PRESS_MARKER_RE = re.compile(
    r"(?:^|[\s;,|])publication_version=in_press_or_online_pdf(?=$|[\s;,|])"
)
DEFAULT_JOURNAL = Path("~/.mrl/pending-download-log.json").expanduser()
DEFAULT_STATE_LOCK = Path("~/.mrl/download-state.lock").expanduser()
DOWNLOAD_LOG_FIELDS = (
    "user_open_id",
    "user_name",
    "agent",
    "papers_count",
    "paper_ids",
    "logged_at",
)
EXCLUDED_PATH_COMPONENTS = {
    "_tracking",
    "_suspect",
    "_pipeline_archive",
    "unreferenced-duplicates",
    "_orphaned-unreferenced-pdfs",
}
REQUIRED_COLUMNS = {
    "paper_id", "doi", "title", "title_norm", "authors",
    "first_author_last", "first_author_norm", "year", "journal", "file_id",
    "file_name", "file_size", "sha256", "file_token", "drive_path",
    "version_note", "publication_version", "match_method",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ConnectorError(RuntimeError):
    """A controlled, non-secret-bearing refusal."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_lark(
    args: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "check": False,
        "env": env,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    return runner(["lark-cli", *args], **kwargs)


def _parse_json_process(process: subprocess.CompletedProcess[str], purpose: str) -> dict[str, Any]:
    if process.returncode != 0:
        raise ConnectorError(f"{purpose} failed")
    try:
        payload = json.loads(process.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConnectorError(f"{purpose} returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ConnectorError(f"{purpose} was rejected")
    return payload


def _parse_time(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ConnectorError("index built_at is missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorError("index built_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ConnectorError("index built_at has no timezone")
    return parsed.astimezone(timezone.utc)


@contextmanager
def _readonly_connection(path: Path):
    resolved = path.resolve(strict=True)
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def validate_index(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate an index completely and fail closed if it is stale."""

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ConnectorError("index is missing or is not a regular file")
    if path.stat().st_size <= 0:
        raise ConnectorError("index is empty")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        with _readonly_connection(path) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ConnectorError("index integrity check failed")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"mrl_index", "index_meta"}.issubset(tables):
                raise ConnectorError("index schema is incomplete")
            columns = {row[1] for row in db.execute("PRAGMA table_info(mrl_index)")}
            missing = REQUIRED_COLUMNS - columns
            if missing:
                raise ConnectorError("index schema is incompatible with connector v1.1.1")
            meta = dict(db.execute("SELECT key,value FROM index_meta"))
            built_at = _parse_time(meta.get("built_at", ""))
            age = current - built_at
            if age < -timedelta(minutes=5):
                raise ConnectorError("index built_at is implausibly in the future")
            if age > MAX_INDEX_AGE:
                raise ConnectorError("index is older than the 7-day freshness policy")
            count = int(db.execute("SELECT COUNT(*) FROM mrl_index").fetchone()[0])
            try:
                declared = int(meta.get("rows", ""))
            except (TypeError, ValueError) as exc:
                raise ConnectorError("index row count metadata is invalid") from exc
            if count != declared:
                raise ConnectorError("index row count does not match metadata")
            bad_versions = db.execute(
                "SELECT COUNT(*) FROM mrl_index "
                "WHERE publication_version NOT IN (?,?,?) OR publication_version IS NULL",
                tuple(sorted(VALID_VERSIONS)),
            ).fetchone()[0]
            if bad_versions:
                raise ConnectorError("index contains invalid publication-version values")
            bad_version_types = db.execute(
                "SELECT COUNT(*) FROM mrl_index WHERE typeof(version_note)<>'text' "
                "OR typeof(publication_version)<>'text'"
            ).fetchone()[0]
            if bad_version_types:
                raise ConnectorError("index contains invalid publication-version types")
            bad_match_methods = db.execute(
                "SELECT COUNT(*) FROM mrl_index WHERE typeof(match_method)<>'text' "
                "OR match_method NOT IN (?,?)",
                tuple(sorted(VALID_MATCH_METHODS)),
            ).fetchone()[0]
            if bad_match_methods:
                raise ConnectorError("index contains an invalid path-match method")
            for note, derived in db.execute(
                "SELECT version_note,publication_version FROM mrl_index"
            ):
                official = bool(OFFICIAL_MARKER_RE.search(note))
                in_press = bool(IN_PRESS_MARKER_RE.search(note))
                if official and in_press:
                    raise ConnectorError("index contains conflicting publication-version evidence")
                expected = (
                    "official_published_issue_pdf" if official else
                    "in_press_or_online_pdf" if in_press else "unknown"
                )
                if derived != expected:
                    raise ConnectorError("index publication version conflicts with exact validation evidence")
            bad_files = db.execute(
                "SELECT COUNT(*) FROM mrl_index WHERE "
                "typeof(paper_id)<>'integer' OR paper_id<=0 OR "
                "typeof(file_id)<>'integer' OR file_id<=0 OR "
                "typeof(file_size)<>'integer' OR file_size<=0 OR "
                "typeof(file_name)<>'text' OR file_name='' OR "
                "typeof(file_token)<>'text' OR file_token='' OR "
                "typeof(drive_path)<>'text' OR drive_path='' OR "
                "typeof(sha256)<>'text' OR length(sha256)<>64 OR "
                "typeof(version_note)<>'text' OR "
                "typeof(publication_version)<>'text' OR "
                "typeof(match_method)<>'text'"
            ).fetchone()[0]
            if bad_files:
                raise ConnectorError("index contains incomplete file identities")
            duplicates = db.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT file_id FROM mrl_index GROUP BY file_id HAVING COUNT(*)<>1 "
                "UNION ALL SELECT file_token FROM mrl_index GROUP BY file_token HAVING COUNT(*)<>1 "
                "UNION ALL SELECT drive_path FROM mrl_index GROUP BY drive_path HAVING COUNT(*)<>1)"
            ).fetchone()[0]
            if duplicates:
                raise ConnectorError("index contains duplicate file identities")
            for row in db.execute("SELECT sha256 FROM mrl_index"):
                if not SHA256_RE.fullmatch(str(row[0] or "")):
                    raise ConnectorError("index contains an invalid SHA-256 value")
            for file_name, drive_path, match_method in db.execute(
                "SELECT file_name,drive_path,match_method FROM mrl_index"
            ):
                safe_name = _safe_file_name(file_name)
                _safe_drive_path(drive_path, safe_name, match_method)
    except sqlite3.Error as exc:
        raise ConnectorError("index cannot be opened safely") from exc
    return {
        "built_at": built_at.isoformat(),
        "rows": count,
        "sha256": sha256_file(path),
        "age_seconds": max(0, int(age.total_seconds())),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _secure_state_dir(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ConnectorError("private state path is unsafe")
        if path.stat().st_mode & 0o077:
            raise ConnectorError("private state directory permissions must be 0700")
        return
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def fetch_index(file_token: str, output: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Download by the exact pinned token, validate, then atomically install."""

    if not str(file_token).strip():
        raise ConnectorError("index file token is required")
    output = Path(output).expanduser()
    _secure_state_dir(output.parent)
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise ConnectorError("index destination is unsafe")
    temp_dir = Path(tempfile.mkdtemp(prefix=".index-", dir=output.parent))
    os.chmod(temp_dir, 0o700)
    candidate = temp_dir / "candidate.sqlite3"
    try:
        process = run_lark(
            ["drive", "+download", "--file-token", file_token, "--output", candidate.name, "--as", "user"],
            runner=runner,
            cwd=temp_dir,
        )
        if process.returncode != 0 or not candidate.is_file() or candidate.is_symlink():
            raise ConnectorError("exact-token index download failed")
        os.chmod(candidate, 0o600)
        result = validate_index(candidate)
        os.replace(candidate, output)
        os.chmod(output, 0o600)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

        return result
    finally:
        if candidate.exists():
            candidate.unlink()
        temp_dir.rmdir()


def _drive_page(folder_token: str, page_token: str, *, runner: Runner) -> dict[str, Any]:
    params: dict[str, Any] = {"folder_token": folder_token, "page_size": 200}
    if page_token:
        params["page_token"] = page_token
    payload = _parse_json_process(
        run_lark(
            ["drive", "files", "list", "--params", json.dumps(params, separators=(",", ":")),
             "--format", "json", "--as", "user"],
            runner=runner,
        ),
        "private contract listing",
    )
    data = payload.get("data", payload)
    if not isinstance(data, dict) or not isinstance(data.get("files", []), list):
        raise ConnectorError("private contract listing returned malformed data")
    return data


def _find_exact_drive_item(folder_token: str, name: str, item_type: str, *, runner: Runner) -> str:
    matches: list[str] = []
    page_token = ""
    seen_tokens: set[str] = set()
    for _ in range(10000):
        data = _drive_page(folder_token, page_token, runner=runner)
        for item in data.get("files", []):
            if not isinstance(item, dict):
                raise ConnectorError("private contract listing contains an invalid item")
            if item.get("name") == name and item.get("type") == item_type:
                token = str(item.get("token") or "")
                if not token:
                    raise ConnectorError("private contract item has no token")
                matches.append(token)
        if not data.get("has_more"):
            break
        next_token = str(data.get("next_page_token") or "")
        if not next_token or next_token in seen_tokens:
            raise ConnectorError("private contract pagination did not advance")
        seen_tokens.add(next_token)
        page_token = next_token
    else:
        raise ConnectorError("private contract pagination exceeded its safety bound")
    if len(matches) != 1:
        raise ConnectorError("private contract path is missing or ambiguous")
    return matches[0]


def bootstrap(folder_url: str, output: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Resolve only _tracking/CONNECT.md, with full pagination, then install privately."""

    parsed = urlsplit(str(folder_url).strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConnectorError("library folder URL must be an HTTPS URL")
    parts = [part for part in parsed.path.split("/") if part]
    try:
        marker = next(index for index in range(len(parts) - 2) if parts[index:index + 2] == ["drive", "folder"])
        folder_token = parts[marker + 2]
    except (StopIteration, IndexError) as exc:
        raise ConnectorError("library folder URL has an unsupported path") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", folder_token):
        raise ConnectorError("library folder URL contains an invalid folder token")
    tracking_token = _find_exact_drive_item(folder_token, "_tracking", "folder", runner=runner)
    connect_token = _find_exact_drive_item(tracking_token, "CONNECT.md", "file", runner=runner)
    output = Path(output).expanduser()
    _secure_state_dir(output.parent)
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise ConnectorError("private contract destination is unsafe")
    temp_dir = Path(tempfile.mkdtemp(prefix=".connect-", dir=output.parent))
    os.chmod(temp_dir, 0o700)
    candidate = temp_dir / "CONNECT.md"
    try:
        process = run_lark(
            ["drive", "+download", "--file-token", connect_token, "--output", candidate.name, "--as", "user"],
            runner=runner,
            cwd=temp_dir,
        )
        if process.returncode != 0 or candidate.is_symlink() or not candidate.is_file():
            raise ConnectorError("private contract download failed")
        size = candidate.stat().st_size
        if not 1 <= size <= 1024 * 1024:
            raise ConnectorError("private contract has an unsafe size")
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectorError("private contract is not UTF-8 text") from exc
        if "mrl_connector.py" not in text or "--file-token" not in text or "--base-token" not in text:
            raise ConnectorError("private contract is missing required coordinates")
        os.chmod(candidate, 0o600)
        digest = sha256_file(candidate)
        os.replace(candidate, output)
        os.chmod(output, 0o600)
        return {"status": "ready", "output": str(output), "sha256": digest}
    finally:
        if candidate.exists() or candidate.is_symlink():
            candidate.unlink()
        temp_dir.rmdir()


def _normalized_query(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", plain.lower()))


def _normalized_title_query(text: str) -> str:
    """Mirror the canonical tracker's existing ``title_norm`` convention."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text.lower()))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_index(
    path: Path,
    *,
    doi: str | None = None,
    title: str | None = None,
    author: str | None = None,
    keyword: str | None = None,
    year: str | None = None,
    journal: str | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    before = validate_index(path, now=now)
    if not any((doi, title, author, keyword, year, journal)):
        raise ConnectorError("at least one search field is required")
    if not 1 <= limit <= 100:
        raise ConnectorError("search limit must be between 1 and 100")
    clauses: list[str] = []
    values: list[Any] = []
    if doi:
        clauses.append("lower(doi)=lower(?)")
        values.append(doi.strip())
    if title:
        normalized_title = _normalized_title_query(title)
        if not normalized_title:
            raise ConnectorError("title query has no searchable ASCII terms")
        clauses.append("title_norm LIKE ? ESCAPE '\\'")
        values.append(_like(normalized_title))
    if author:
        normalized = _like(_normalized_query(author))
        clauses.append("(first_author_norm LIKE ? ESCAPE '\\' OR lower(authors) LIKE ? ESCAPE '\\')")
        values.extend((normalized, _like(author.lower())))
    if keyword:
        normalized = _like(_normalized_query(keyword))
        clauses.append("(title_norm LIKE ? ESCAPE '\\' OR first_author_norm LIKE ? ESCAPE '\\' OR lower(authors) LIKE ? ESCAPE '\\')")
        values.extend((normalized, normalized, _like(keyword.lower())))
    if year:
        clauses.append("year=?")
        values.append(str(year))
    if journal:
        clauses.append("lower(journal)=lower(?)")
        values.append(journal.strip())
    query = (
        "SELECT paper_id,file_id,title,authors,first_author_last,year,journal,doi,"
        "file_name,file_size,sha256,publication_version FROM mrl_index WHERE "
        + " AND ".join(clauses)
        + " ORDER BY year DESC,title COLLATE NOCASE,file_id LIMIT ?"
    )
    values.append(limit)
    with _readonly_connection(Path(path)) as db:
        rows = [dict(row) for row in db.execute(query, values)]
    after = validate_index(path, now=now)
    if before["sha256"] != after["sha256"]:
        raise ConnectorError("index changed during search")
    for row in rows:
        value = row.get("publication_version")
        row["publication_version"] = value if value in VALID_VERSIONS else "unknown"
    return rows


def whoami(*, runner: Runner = subprocess.run) -> tuple[str, str]:
    payload = _parse_json_process(
        run_lark(["whoami", "--as", "user"], runner=runner), "identity check"
    )
    identity = payload.get("onBehalfOf")
    if identity is None:
        envelope = payload.get("data")
        if not isinstance(envelope, dict):
            raise ConnectorError("identity check returned a malformed envelope")
        identity = envelope.get("onBehalfOf")
    if not isinstance(identity, dict):
        raise ConnectorError("identity check returned a malformed identity")
    open_id = str(identity.get("openId") or "").strip()
    user_name = str(identity.get("userName") or "").strip()
    if not open_id or not user_name:
        raise ConnectorError("authenticated user identity is incomplete")
    return open_id, user_name


def _record_page(payload: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    data = payload.get("data", payload)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    fields = data.get("fields", []) if isinstance(data, dict) else []
    rows = data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise ConnectorError("quota service returned malformed records")
    if any(not isinstance(row, list) for row in rows):
        raise ConnectorError("quota service returned malformed records")
    return [str(field) for field in fields], rows


def _download_log_rows_once(base_token: str, open_id: str, *, runner: Runner) -> list[dict[str, Any]]:
    """Read every matching Download Log row from the authoritative Base once."""

    if not base_token or not open_id:
        raise ConnectorError("Download Log coordinates and user identity are required")
    results: list[dict[str, Any]] = []
    page_size = 200
    for page in range(1000):
        args = [
            "base", "+record-search", "--base-token", base_token,
            "--table-id", "download_log", "--keyword", open_id,
            "--search-field", "user_open_id",
        ]
        for field in DOWNLOAD_LOG_FIELDS:
            args += ["--field-id", field]
        args += ["--sort-json", '[{"field":"logged_at","desc":true}]', "--limit", str(page_size), "--offset", str(page * page_size), "--as", "user", "--format", "json"]
        payload = _parse_json_process(run_lark(args, runner=runner), "authoritative quota query")
        fields, rows = _record_page(payload)
        if fields != list(DOWNLOAD_LOG_FIELDS) or len(set(fields)) != len(fields):
            raise ConnectorError("quota projection is incomplete or unexpected")
        for values in rows:
            if len(values) != len(fields):
                raise ConnectorError("quota service returned a malformed row")
            row = dict(zip(fields, values))
            if str(row.get("user_open_id") or "") != open_id:
                raise ConnectorError("quota search returned another user's record")
            results.append(row)
        if len(rows) < page_size:
            return results
    raise ConnectorError("quota pagination exceeded its safety bound")


def _canonical_rows(rows: list[dict[str, Any]]) -> str:
    encoded = [json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows]
    return hashlib.sha256("\n".join(sorted(encoded)).encode("utf-8")).hexdigest()


def download_log_rows(base_token: str, open_id: str, *, runner: Runner = subprocess.run) -> list[dict[str, Any]]:
    """Return a stable, fully paginated authoritative Base snapshot."""

    first = _download_log_rows_once(base_token, open_id, runner=runner)
    second = _download_log_rows_once(base_token, open_id, runner=runner)
    if _canonical_rows(first) != _canonical_rows(second):
        raise ConnectorError("Download Log changed during pagination; retry later")
    return second


def _log_time(value: Any) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ8)
    except ValueError as exc:
        raise ConnectorError("Download Log contains an invalid timestamp") from exc


def quota_usage(rows: Iterable[dict[str, Any]], *, now: datetime | None = None) -> tuple[int, datetime | None]:
    current = (now or datetime.now(TZ8)).astimezone(TZ8)
    cutoff = current - timedelta(hours=ROLLING_HOURS)
    used = 0
    expiries: list[datetime] = []
    for row in rows:
        logged_at = _log_time(row.get("logged_at"))
        if logged_at > current + timedelta(minutes=5):
            raise ConnectorError("Download Log contains an implausible future timestamp")
        try:
            count = int(row.get("papers_count") or 0)
        except (TypeError, ValueError) as exc:
            raise ConnectorError("Download Log contains an invalid paper count") from exc
        if count < 0:
            raise ConnectorError("Download Log contains a negative paper count")
        if cutoff <= logged_at <= current + timedelta(minutes=5):
            used += count
            expiries.append(logged_at + timedelta(hours=ROLLING_HOURS))
    return used, min(expiries) if expiries else None


def _validate_agent(agent: str) -> str:
    value = str(agent or "").strip()
    if not value or len(value) > 40 or value.lower() in {"agent", "unknown", "<your product name>"}:
        raise ConnectorError("an actual agent identifier is required")
    if any(ord(ch) < 32 for ch in value):
        raise ConnectorError("agent identifier contains control characters")
    return value


class PendingJournal:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser()

    def exists(self) -> bool:
        return self.path.exists() or self.path.is_symlink()

    def load(self) -> dict[str, Any]:
        if self.path.is_symlink() or not self.path.is_file():
            raise ConnectorError("pending-log journal path is unsafe")
        if self.path.stat().st_mode & 0o077:
            raise ConnectorError("pending-log journal permissions must be 0600")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConnectorError("pending-log journal is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise ConnectorError("pending-log journal has an unsupported format")
        _validate_journal_payload(payload)
        return payload

    def write(self, payload: dict[str, Any]) -> None:
        _secure_state_dir(self.path.parent)
        if self.path.is_symlink():
            raise ConnectorError("pending-log journal path is unsafe")
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.path.parent)
        temporary_path = Path(temporary)
        try:
            os.fchmod(fd, 0o600)
            data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary_path, self.path)
        os.chmod(self.path, 0o600)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def create(self, payload: dict[str, Any]) -> None:
        """Create a new journal without replacing any concurrent state."""

        _secure_state_dir(self.path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            raise ConnectorError("an unresolved pending Download Log record blocks further downloads") from exc
        try:
            data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def clear(self) -> None:
        if self.path.is_symlink() or not self.path.is_file():
            raise ConnectorError("pending-log journal path is unsafe")
        self.path.unlink()
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


@contextmanager
def exclusive_state_lock(lock_path: Path, timeout_seconds: float = 30.0):
    """Serialize quota, journal, transfer, and log confirmation on this device."""

    lock_path = Path(lock_path).expanduser()
    _secure_state_dir(lock_path.parent)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        raise ConnectorError("download state lock is unsafe or unavailable") from exc
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConnectorError("another download operation holds the state lock")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

def _safe_file_name(value: str) -> str:
    name = str(value or "")
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(ord(character) < 32 for character in name)
    ):
        raise ConnectorError("index contains an unsafe output file name")
    if not name.lower().endswith(".pdf"):
        raise ConnectorError("index output is not a PDF file name")
    return name


def _safe_drive_path(value: str, file_name: str, match_method: str) -> str:
    path = str(value or "")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(ord(character) < 32 for character in path)
    ):
        raise ConnectorError("index contains an unsafe Drive path")
    parts = path.split("/")
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold() in EXCLUDED_PATH_COMPONENTS for part in parts)
    ):
        raise ConnectorError("index contains an unsafe Drive path")
    remote_name = parts[-1]
    if match_method == "exact_path":
        name_matches = remote_name == file_name
    elif match_method == "name_truncated_240":
        local = PurePosixPath(file_name)
        remote = PurePosixPath(remote_name)
        name_matches = (
            local.suffix.casefold() == ".pdf"
            and remote.suffix.casefold() == ".pdf"
            and len(remote.stem) == 240
            and local.stem.startswith(remote.stem)
            and local.stem != remote.stem
        )
    else:
        name_matches = False
    if not name_matches:
        raise ConnectorError("index contains an unsafe Drive path")
    return path


def _pdf_matches(path: Path, size: int, sha256: str) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            return False
        handle.seek(max(0, size - 2048))
        if b"%%EOF" not in handle.read():
            return False
    return sha256_file(path) == sha256


def _selected_rows(index: Path, file_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not file_ids or len(file_ids) > MAX_OPERATION or len(set(file_ids)) != len(file_ids):
        raise ConnectorError("select between 1 and 15 unique file IDs")
    placeholders = ",".join("?" for _ in file_ids)
    with _readonly_connection(index) as db:
        rows = [
            dict(row)
            for row in db.execute(
                f"SELECT paper_id,file_id,file_name,file_size,sha256,file_token FROM mrl_index WHERE file_id IN ({placeholders})",
                tuple(file_ids),
            )
        ]
    by_id = {int(row["file_id"]): row for row in rows}
    if set(by_id) != set(file_ids):
        raise ConnectorError("one or more selected file IDs are absent from the index")
    return [by_id[file_id] for file_id in file_ids]


def _validate_journal_payload(payload: dict[str, Any]) -> None:
    if payload.get("state") not in {"in_progress", "needs_log", "write_attempting"}:
        raise ConnectorError("pending-log journal has an invalid state")
    for key in ("user_open_id", "user_name", "agent", "started_at"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ConnectorError("pending-log journal is incomplete")
    _parse_time(payload["started_at"])
    operation_id = str(payload.get("operation_id") or "")
    expected_log_agent = f"{payload['agent']} [mrl-op:{operation_id}]"
    if not operation_id or payload.get("log_agent") != expected_log_agent:
        raise ConnectorError("pending-log journal has an invalid operation identity")
    verified = payload.get("verified")
    if not isinstance(verified, list) or len(verified) > MAX_OPERATION:
        raise ConnectorError("pending-log journal has invalid verified rows")
    for row in verified:
        if not isinstance(row, dict) or not isinstance(row.get("paper_id"), int) or not isinstance(row.get("file_id"), int):
            raise ConnectorError("pending-log journal has invalid verified rows")
        _safe_file_name(str(row.get("file_name") or ""))
    if payload.get("state") == "write_attempting" and not isinstance(payload.get("baseline_match_count"), int):
        raise ConnectorError("pending-log journal has no valid write baseline")


def _matching_log_rows(rows: Iterable[dict[str, Any]], journal: dict[str, Any]) -> int:
    paper_ids = ";".join(str(item["paper_id"]) for item in journal["verified"])
    count = len(journal["verified"])
    started = _parse_time(journal["started_at"]).astimezone(TZ8) - timedelta(minutes=2)
    matched = 0
    for row in rows:
        try:
            row_count = int(row.get("papers_count") or 0)
        except (TypeError, ValueError) as exc:
            raise ConnectorError("Download Log contains an invalid paper count") from exc
        if (
            str(row.get("agent") or "") == journal["log_agent"]
            and row_count == count
            and str(row.get("paper_ids") or "") == paper_ids
            and _log_time(row.get("logged_at")) >= started
        ):
            matched += 1
    return matched


def _append_log(base_token: str, journal: dict[str, Any], *, runner: Runner) -> bool:
    verified = journal["verified"]
    body = {
        "fields": ["user_open_id", "user_name", "agent", "papers_count", "paper_ids"],
        "rows": [[
            journal["user_open_id"], journal["user_name"], journal["log_agent"],
            len(verified), ";".join(str(item["paper_id"]) for item in verified),
        ]],
    }
    process = run_lark(
        ["base", "+record-batch-create", "--base-token", base_token,
         "--table-id", "download_log", "--json", json.dumps(body, separators=(",", ":")),
         "--as", "user", "--format", "json"],
        runner=runner,
    )
    return process.returncode == 0


def _finish_pending_log(base_token: str, journal_store: PendingJournal, payload: dict[str, Any], *, runner: Runner) -> None:
    if not payload.get("verified"):
        journal_store.clear()
        return
    current_rows = download_log_rows(base_token, payload["user_open_id"], runner=runner)
    current_matches = _matching_log_rows(current_rows, payload)
    state = payload.get("state")
    baseline = payload.get("baseline_match_count")
    if state == "write_attempting":
        if not isinstance(baseline, int) or current_matches < baseline:
            raise ConnectorError("pending-log baseline is invalid")
        if current_matches == baseline + 1:
            journal_store.clear()
            return
        if current_matches > baseline + 1:
            raise ConnectorError("Download Log reconciliation is ambiguous; downloads remain blocked")
    else:
        baseline = current_matches
    payload["baseline_match_count"] = baseline
    payload["state"] = "write_attempting"
    journal_store.write(payload)
    _append_log(base_token, payload, runner=runner)
    after_rows = download_log_rows(base_token, payload["user_open_id"], runner=runner)
    after_matches = _matching_log_rows(after_rows, payload)
    if after_matches != baseline + 1:
        raise ConnectorError("Download Log append is unconfirmed; downloads remain blocked")
    journal_store.clear()


def _reconcile_pending_locked(base_token: str, agent: str, journal_path: Path, *, runner: Runner) -> dict[str, Any]:
    store = PendingJournal(journal_path)
    if not store.exists():
        return {"status": "clear", "verified": 0}
    payload = store.load()
    actual_agent = _validate_agent(agent)
    if payload.get("agent") != actual_agent:
        raise ConnectorError("pending log belongs to a different agent identifier")
    open_id, _ = whoami(runner=runner)
    if payload.get("user_open_id") != open_id:
        raise ConnectorError("pending log belongs to a different Feishu user")
    _finish_pending_log(base_token, store, payload, runner=runner)
    return {"status": "reconciled", "verified": len(payload.get("verified", []))}


def reconcile_pending(
    base_token: str,
    agent: str,
    journal_path: Path = DEFAULT_JOURNAL,
    *,
    runner: Runner = subprocess.run,
    lock_path: Path = DEFAULT_STATE_LOCK,
) -> dict[str, Any]:
    with exclusive_state_lock(lock_path):
        return _reconcile_pending_locked(base_token, agent, journal_path, runner=runner)


def _download_files_locked(
    index: Path,
    file_ids: Sequence[int],
    output_dir: Path,
    base_token: str,
    agent: str,
    journal_path: Path,
    *,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> dict[str, Any]:
    validated = validate_index(index, now=now)
    actual_agent = _validate_agent(agent)
    store = PendingJournal(journal_path)
    if store.exists():
        raise ConnectorError("an unresolved pending Download Log record blocks further downloads")
    rows = _selected_rows(Path(index), file_ids)
    if validate_index(index, now=now)["sha256"] != validated["sha256"]:
        raise ConnectorError("index changed during download selection")
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and (output_dir.is_symlink() or not output_dir.is_dir()):
        raise ConnectorError("output directory is unsafe")
    output_dir.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    for row in rows:
        name = _safe_file_name(row["file_name"])
        target = output_dir / name
        if target.exists() or target.is_symlink():
            raise ConnectorError("a target PDF already exists; nothing was downloaded")
        targets.append(target)
    if len(set(targets)) != len(targets):
        raise ConnectorError("selected PDFs have colliding output names")
    open_id, user_name = whoami(runner=runner)
    quota_rows = download_log_rows(base_token, open_id, runner=runner)
    used, expires = quota_usage(quota_rows, now=now)
    if used + len(rows) > MAX_ROLLING:
        detail = expires.isoformat() if expires else "unknown"
        raise ConnectorError(f"rolling quota exceeded; next capacity may free at {detail}")
    operation_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "schema": 1,
        "operation_id": operation_id,
        "state": "in_progress",
        "started_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "user_open_id": open_id,
        "user_name": user_name,
        "agent": actual_agent,
        "log_agent": f"{actual_agent} [mrl-op:{operation_id}]",
        "selected_file_ids": [int(row["file_id"]) for row in rows],
        "verified": [],
    }
    store.create(payload)
    failures: list[str] = []
    temp_dir = Path(tempfile.mkdtemp(prefix=".mrl-download-", dir=output_dir))
    os.chmod(temp_dir, 0o700)
    try:
        for row, target in zip(rows, targets):
            temporary = temp_dir / f"{row['file_id']}.pdf"
            process = run_lark(
                ["drive", "+download", "--file-token", str(row["file_token"]),
                 "--output", temporary.name, "--as", "user"],
                runner=runner,
                cwd=temp_dir,
            )
            expected_size = int(row["file_size"])
            expected_sha = str(row["sha256"])
            if process.returncode != 0 or not _pdf_matches(temporary, expected_size, expected_sha):
                if temporary.exists() or temporary.is_symlink():
                    temporary.unlink()
                failures.append(str(row["file_name"]))
                continue
            os.chmod(temporary, 0o600)
            payload["verified"].append({
                "paper_id": int(row["paper_id"]),
                "file_id": int(row["file_id"]),
                "file_name": str(row["file_name"]),
            })
            store.write(payload)
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ConnectorError("target appeared during download; no file was overwritten") from exc
            with target.open("rb") as installed:
                os.fsync(installed.fileno())
            temporary.unlink()
        payload["state"] = "needs_log"
        store.write(payload)
        _finish_pending_log(base_token, store, payload, runner=runner)
    finally:
        for child in temp_dir.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
        temp_dir.rmdir()
    return {
        "requested": len(rows),
        "verified": len(payload["verified"]),
        "failed": failures,
        "quota_used_before": used,
    }


def download_files(
    index: Path,
    file_ids: Sequence[int],
    output_dir: Path,
    base_token: str,
    agent: str,
    journal_path: Path,
    *,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
    lock_path: Path = DEFAULT_STATE_LOCK,
) -> dict[str, Any]:
    with exclusive_state_lock(lock_path):
        return _download_files_locked(
            index, file_ids, output_dir, base_token, agent, journal_path,
            runner=runner, now=now,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = sub.add_parser("bootstrap", help="resolve and privately download _tracking/CONNECT.md")
    bootstrap_parser.add_argument("--folder-url", required=True)
    bootstrap_parser.add_argument("--output", type=Path, default=Path("~/.mrl/CONNECT.md").expanduser())
    fetch = sub.add_parser("fetch-index", help="download and validate the exact pinned SQLite index")
    fetch.add_argument("--file-token", required=True)
    fetch.add_argument("--output", type=Path, default=Path("~/.mrl/mrl-index.sqlite3").expanduser())
    validate = sub.add_parser("validate-index")
    validate.add_argument("--index", type=Path, required=True)
    search = sub.add_parser("search")
    search.add_argument("--index", type=Path, required=True)
    for name in ("doi", "title", "author", "keyword", "year", "journal"):
        search.add_argument(f"--{name}")
    search.add_argument("--limit", type=int, default=20)
    quota = sub.add_parser("quota")
    quota.add_argument("--base-token", required=True)
    quota.add_argument("--requested", type=int, default=0)
    download = sub.add_parser("download")
    download.add_argument("--index", type=Path, required=True)
    download.add_argument("--file-id", type=int, action="append", required=True)
    download.add_argument("--output-dir", type=Path, default=Path("papers"))
    download.add_argument("--base-token", required=True)
    download.add_argument("--agent", required=True)
    reconcile = sub.add_parser("reconcile-log")
    reconcile.add_argument("--base-token", required=True)
    reconcile.add_argument("--agent", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bootstrap":
            result = bootstrap(args.folder_url, args.output)
        elif args.command == "fetch-index":
            result = fetch_index(args.file_token, args.output)
        elif args.command == "validate-index":
            result = validate_index(args.index)
        elif args.command == "search":
            result = search_index(
                args.index, doi=args.doi, title=args.title, author=args.author,
                keyword=args.keyword, year=args.year, journal=args.journal, limit=args.limit,
            )
        elif args.command == "quota":
            if not 0 <= args.requested <= MAX_OPERATION:
                raise ConnectorError("requested count must be between 0 and 15")
            open_id, _ = whoami()
            rows = download_log_rows(args.base_token, open_id)
            used, expires = quota_usage(rows)
            if used + args.requested > MAX_ROLLING:
                raise ConnectorError("authoritative rolling quota would be exceeded")
            result = {"used": used, "remaining": MAX_ROLLING - used, "earliest_expiry": expires.isoformat() if expires else None}
        elif args.command == "download":
            result = download_files(
                args.index, args.file_id, args.output_dir, args.base_token,
                args.agent, DEFAULT_JOURNAL,
            )
        else:
            result = reconcile_pending(args.base_token, args.agent, DEFAULT_JOURNAL)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        if args.command == "download" and result.get("failed"):
            return 1
        return 0
    except ConnectorError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
