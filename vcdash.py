#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vcdash — 應用程式版本控制儀表板

把本檔放進任何資料夾執行，它會管理「同一層目錄及其所有子目錄」裡的應用程式：
  * 自動找出應用程式（單一檔案的程式，或含有 package.json 等標記檔的專案資料夾）
  * 讀取各應用程式宣告的版本號
  * 每次掃描時記錄新增 / 修改 / 移除 / 復原，並保存檔案內容以便比對差異
  * 產生 HTML 儀表板報告、系統模組入口頁面與 CSV 修改歷程
  * 可還原任何一個歷史版本

只使用 Python 標準函式庫（Python 3.8 以上）。

用法（在本檔所在資料夾）：
  python vcdash.py                  掃描並產生報告（等同 scan --report）
  python vcdash.py scan -m "說明"   掃描並記錄變更
  python vcdash.py status           只查看尚未記錄的變更，不寫入紀錄
  python vcdash.py list             列出所有應用程式與目前版本
  python vcdash.py history 名稱     查看某個應用程式的修改歷程
  python vcdash.py diff 名稱 [A] [B] 比對兩個修訂版（預設為最後兩版）
  python vcdash.py restore 名稱 修訂號 [--to 資料夾]
  python vcdash.py report           重新產生 HTML 報告
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import fnmatch
import gzip
import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

TOOL_NAME = "vcdash"
TOOL_VERSION = "1.0.0"
DATA_DIR = ".vcdash"
DB_FILE = "db.json"
CONFIG_FILE = "vcdash.json"
REPORT_DIR = "vcdash-report"
PORTAL_FILE = "portal.html"
RESTORE_DIR = "vcdash-restore"
SCHEMA = 1

DEFAULT_CONFIG = {
    # 不在專案資料夾內時，這些副檔名的單一檔案各自視為一個應用程式
    "app_extensions": [
        ".html", ".htm", ".py", ".pyw", ".js", ".mjs", ".ts", ".php", ".rb", ".go",
        ".rs", ".java", ".cs", ".kt", ".swift", ".lua", ".ps1", ".bat", ".cmd", ".sh",
        ".vbs", ".ahk", ".exe", ".msi", ".apk", ".jar", ".war", ".appimage",
        ".xlsm", ".xlam", ".accdb", ".ipynb", ".nc", ".gcode",
    ],
    # 資料夾內出現任一個標記檔，整個資料夾（含子目錄）視為一個應用程式專案
    "project_markers": [
        "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
        "*.csproj", "*.vbproj", "*.sln", "composer.json", "Gemfile", "manifest.json",
        "VERSION", "version.txt", "index.html",
    ],
    "exclude_dirs": [
        ".git", ".svn", ".hg", DATA_DIR, REPORT_DIR, RESTORE_DIR, "node_modules",
        "__pycache__", ".venv", "venv", ".idea", ".vscode", ".pytest_cache",
        ".mypy_cache", "obj", "target", ".next", ".cache",
    ],
    "exclude_files": [
        "vcdash.py", "vcdash.bat", "vcdash.sh", CONFIG_FILE, "*.pyc", "*.tmp", "~$*",
        ".DS_Store", "Thumbs.db", "desktop.ini", "*.log",
    ],
    "skip_hidden": True,
    "max_blob_bytes": 5 * 1024 * 1024,  # 超過此大小的檔案只記錄雜湊，不保存內容
    "max_diff_lines": 400,              # 報告中每個檔案最多顯示的差異行數
    "max_line_chars": 300,              # 差異中每行最多顯示的字元數
}

# 專案資料夾的入口檔，依序比對；都沒有時改用最上層第一個應用程式檔
ENTRY_CANDIDATES = [
    "index.html", "index.htm", "default.html", "default.htm", "main.html", "app.html",
    "main.py", "app.py", "__main__.py", "run.py", "start.bat", "run.bat", "start.cmd",
    "start.sh", "run.sh", "*.exe", "*.lnk", "*.url", "*.xlsm", "*.accdb",
]

EVENT_LABEL = {"created": "新增", "modified": "修改", "removed": "移除", "restored": "復原"}
EVENT_MARK = {"created": "＋", "modified": "～", "removed": "－", "restored": "↺"}


# ───────────────────────────── 基本工具 ─────────────────────────────

def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def fmt_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return dt.datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_text(head: bytes) -> bool:
    return b"\0" not in head[:8192]


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp950"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def version_key(v: str | None):
    if not v:
        return None
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", v, re.I)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def version_lt(a: str, b: str) -> bool:
    """a 是否小於 b（無法解析時回傳 False）。"""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return False
    width = max(len(ka), len(kb))
    return ka + (0,) * (width - len(ka)) < kb + (0,) * (width - len(kb))


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


# ───────────────────────────── 設定與資料庫 ─────────────────────────────

def load_config(root: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = root / CONFIG_FILE
    if path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            die(f"無法讀取設定檔 {path}：{exc}")
        for key, value in user.items():
            # extra_xxx 代表附加到預設清單，而不是取代
            if key.startswith("extra_") and isinstance(cfg.get(key[6:]), list):
                cfg[key[6:]] = cfg[key[6:]] + list(value)
            else:
                cfg[key] = value
    cfg["_ext"] = {e.lower() for e in cfg["app_extensions"]}
    return cfg


def load_db(root: Path) -> dict:
    path = root / DATA_DIR / DB_FILE
    if path.is_file():
        db = json.loads(path.read_text(encoding="utf-8"))
        if db.get("schema") != SCHEMA:
            die(f"紀錄檔版本不相容：{path}")
        return db
    return {"schema": SCHEMA, "tool": TOOL_VERSION, "created": now_iso(),
            "last_scan": None, "apps": {}, "scans": []}


def save_db(root: Path, db: dict) -> None:
    path = root / DATA_DIR / DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class BlobStore:
    """以 SHA-256 為名稱保存檔案內容（gzip 壓縮），相同內容只存一份。"""

    def __init__(self, base: Path):
        self.base = base

    def _path(self, digest: str) -> Path:
        return self.base / digest[:2] / (digest[2:] + ".gz")

    def put(self, digest: str, data: bytes) -> None:
        path = self._path(digest)
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def get(self, digest: str) -> bytes | None:
        path = self._path(digest)
        if not path.is_file():
            return None
        with gzip.open(path, "rb") as fh:
            return fh.read()


# ───────────────────────────── 探索應用程式 ─────────────────────────────

def _match_any(name: str, patterns) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in patterns)


def _skip_dir(name: str, cfg: dict) -> bool:
    return (cfg["skip_hidden"] and name.startswith(".")) or _match_any(name, cfg["exclude_dirs"])


def _skip_file(name: str, cfg: dict) -> bool:
    return (cfg["skip_hidden"] and name.startswith(".")) or _match_any(name, cfg["exclude_files"])


def walk_tree(root: Path, cfg: dict):
    projects, files = [], []
    tool_dir = Path(__file__).resolve().parent  # vcdash 放在子目錄時，不掃描它自己的資料夾
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = sorted(d for d in dirnames
                             if not _skip_dir(d, cfg) and (Path(dirpath) / d).resolve() != tool_dir)
        if rel_dir != "." and any(_match_any(f, cfg["project_markers"]) for f in filenames):
            projects.append(rel_dir)
        for name in sorted(filenames):
            if not _skip_file(name, cfg):
                files.append(name if rel_dir == "." else f"{rel_dir}/{name}")
    return projects, files


def read_file_info(path: Path, cfg: dict, store: BlobStore | None) -> dict | None:
    try:
        if path.stat().st_size <= cfg["max_blob_bytes"]:
            data = path.read_bytes()
            digest = sha256(data)
            if store is not None:
                store.put(digest, data)
            return {"h": digest, "s": len(data), "t": is_text(data), "b": True}
        hasher, size, head = hashlib.sha256(), 0, b""
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                head = head or chunk[:8192]
                hasher.update(chunk)
                size += len(chunk)
        return {"h": hasher.hexdigest(), "s": size, "t": is_text(head), "b": False}
    except OSError:
        return None


def tree_hash(entries: dict) -> str:
    return sha256("\n".join(f"{k}\0{v['h']}" for k, v in sorted(entries.items())).encode())


def discover(root: Path, cfg: dict, store: BlobStore | None = None) -> dict:
    """回傳 {應用程式代號: 快照}。代號是相對於根目錄的路徑。"""
    projects, files = walk_tree(root, cfg)
    projects.sort(key=lambda p: p.count("/"), reverse=True)  # 最深的專案優先認領檔案
    groups: dict = {}
    for rel in files:
        owner = next((p for p in projects if rel.startswith(p + "/")), None)
        if owner is not None:
            groups.setdefault(owner, {"kind": "project", "files": []})["files"].append(rel)
        elif os.path.splitext(rel)[1].lower() in cfg["_ext"]:
            groups[rel] = {"kind": "file", "files": [rel]}

    snaps = {}
    for app_id, group in groups.items():
        entries = {}
        for rel in group["files"]:
            info = read_file_info(root / rel, cfg, store)
            if info is not None:
                key = rel[len(app_id) + 1:] if group["kind"] == "project" else rel.rsplit("/", 1)[-1]
                entries[key] = info
        if not entries:
            continue
        version, source = detect_version(root, app_id, group["kind"], entries, cfg)
        snaps[app_id] = {
            "name": app_id.rsplit("/", 1)[-1], "kind": group["kind"], "files": entries,
            "hash": tree_hash(entries), "version": version, "version_source": source,
            "size": sum(e["s"] for e in entries.values()),
        }
    return snaps


# ───────────────────────────── 版本號偵測 ─────────────────────────────

def _read_head(path: Path, limit: int = 1 << 20) -> str:
    try:
        with open(path, "rb") as fh:
            return decode(fh.read(limit))
    except OSError:
        return ""


def _json_version(text: str):
    try:
        value = json.loads(text).get("version")
    except (ValueError, AttributeError):
        return None
    return str(value).strip() if value else None


def _regex(pattern: str, flags=re.M | re.I):
    rx = re.compile(pattern, flags)

    def reader(text: str):
        m = rx.search(text)
        return m.group(1).strip() if m else None
    return reader


def _first_line(text: str):
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:50]
    return None


def _pom_version(text: str):
    for tag in ("parent", "dependencies", "dependencyManagement", "build", "plugins"):
        text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.S)
    m = re.search(r"<version>\s*([^<\s]+)\s*</version>", text)
    return m.group(1) if m else None


_TOML_VERSION = r'^\s*version\s*=\s*["\']([^"\']+)["\']'
_PROJ_VERSION = r"<(?:Version|AssemblyVersion|FileVersion)>\s*([^<\s]+)\s*<"
_GRADLE_VERSION = r'^\s*version\s*=?\s*["\']([^"\']+)["\']'

MANIFEST_READERS = [
    ("package.json", _json_version),
    ("manifest.json", _json_version),
    ("composer.json", _json_version),
    ("pyproject.toml", _regex(_TOML_VERSION)),
    ("cargo.toml", _regex(_TOML_VERSION)),
    ("setup.cfg", _regex(r"^\s*version\s*=\s*([^\s#]+)")),
    ("setup.py", _regex(r'version\s*=\s*["\']([^"\']+)["\']')),
    ("version", _first_line),
    ("version.txt", _first_line),
    ("*.csproj", _regex(_PROJ_VERSION)),
    ("*.vbproj", _regex(_PROJ_VERSION)),
    ("pom.xml", _pom_version),
    ("build.gradle", _regex(_GRADLE_VERSION)),
    ("build.gradle.kts", _regex(_GRADLE_VERSION)),
]

_VER = r"v?(\d+(?:\.\d+)*[\w.\-+]*)"
CONTENT_PATTERNS = [
    re.compile(r'<meta\s+name=["\'](?:app-)?version["\']\s+content=["\']' + _VER, re.I),
    re.compile(r'\b(?:__version__|APP_VERSION|VERSION)\s*[:=]\s*["\']' + _VER + r'["\']', re.I),
    re.compile(r"@version\s+" + _VER, re.I),
    re.compile(r'\bversion\s*[:=]\s*["\']?v?(\d+(?:\.\d+)+[\w.\-+]*)', re.I),
    re.compile(r"版本\s*[:：]?\s*" + _VER),
    re.compile(r"<title>[^<]*?(?<![A-Za-z])v(\d+(?:\.\d+)*)", re.I),
]


def content_version(text: str):
    for rx in CONTENT_PATTERNS:
        m = rx.search(text)
        if m:
            return m.group(1).strip()
    return None


def detect_version(root: Path, app_id: str, kind: str, entries: dict, cfg: dict):
    """回傳 (版本號, 來源)；找不到時為 (None, None)。"""
    if kind == "file":
        entry = next(iter(entries.values()))
        if entry["t"]:
            ver = content_version(_read_head(root / app_id, 256 * 1024))
            if ver:
                return ver, "檔案內文"
        return None, None

    base = root / app_id
    top = sorted(k for k in entries if "/" not in k)
    for pattern, reader in MANIFEST_READERS:
        for key in top:
            if fnmatch.fnmatch(key.lower(), pattern):
                ver = reader(_read_head(base / key))
                if ver:
                    return ver, key
    main_first = sorted(top, key=lambda k: (k.lower() not in ("index.html", "main.py", "app.py", "main.js"), k))
    for key in main_first:
        if entries[key]["t"] and os.path.splitext(key)[1].lower() in cfg["_ext"]:
            ver = content_version(_read_head(base / key, 256 * 1024))
            if ver:
                return ver, f"{key} 內文"
    return None, None


# ───────────────────────────── 差異比對 ─────────────────────────────

def compare_files(old: dict, new: dict) -> dict:
    return {
        "added": sorted(k for k in new if k not in old),
        "modified": sorted(k for k in new if k in old and old[k]["h"] != new[k]["h"]),
        "removed": sorted(k for k in old if k not in new),
    }


def text_of(meta: dict | None, store: BlobStore, disk: Path | None = None) -> str | None:
    """取得檔案文字內容；二進位檔或內容未保存時回傳 None。"""
    if not meta or not meta.get("t"):
        return None
    data = store.get(meta["h"])
    if data is None and disk is not None and meta.get("b"):
        try:
            data = disk.read_bytes()
        except OSError:
            data = None
        if data is not None and sha256(data) != meta["h"]:
            data = None
    return decode(data) if data is not None else None


def line_stats(a: str, b: str):
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def diff_versions(old_files: dict, new_files: dict, store: BlobStore,
                  old_label: str, new_label: str, max_lines: int | None = None) -> list:
    """逐檔產生 unified diff。回傳 [{file, status, lines|None, total, note}]。"""
    changes = compare_files(old_files, new_files)
    results = []
    for status in ("modified", "added", "removed"):
        for key in changes[status]:
            old, new = old_files.get(key), new_files.get(key)
            a = text_of(old, store) if old else ""
            b = text_of(new, store) if new else ""
            if a is None or b is None:
                sizes = " → ".join(fmt_size(m["s"]) for m in (old, new) if m)
                results.append({"file": key, "status": status, "lines": None, "total": 0,
                                "note": f"二進位檔或內容未保存，僅比對雜湊（{sizes}）"})
                continue
            lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                              f"{old_label}/{key}", f"{new_label}/{key}",
                                              n=3, lineterm=""))
            total = len(lines)
            if max_lines is not None:
                lines = lines[:max_lines]
            results.append({"file": key, "status": status, "lines": lines, "total": total, "note": ""})
    return results


# ───────────────────────────── 掃描與記錄 ─────────────────────────────

def last_live(rec: dict | None) -> dict | None:
    for ver in reversed(rec["versions"] if rec else []):
        if ver["event"] != "removed":
            return ver
    return None


def version_warnings(prev: dict | None, snap: dict, event: str) -> list:
    if event != "modified" or not prev:
        return []
    old, new = prev.get("version"), snap["version"]
    if old and new:
        if old == new:
            return [f"內容已變更，但版本號仍為 {new}"]
        if version_lt(new, old):
            return [f"版本號倒退：{old} → {new}"]
    elif old and not new:
        return [f"找不到版本號（前一版為 {old}）"]
    return []


def disk_path(root: Path, app_id: str, kind: str, key: str) -> Path:
    return root / app_id / key if kind == "project" else root / app_id


def scan(root: Path, cfg: dict, note: str = "", dry_run: bool = False):
    """比對目前檔案與紀錄。回傳 [(應用程式代號, 版本紀錄)]；dry_run 時不寫入。"""
    db = load_db(root)
    store = BlobStore(root / DATA_DIR / "objects")
    snaps = discover(root, cfg, None if dry_run else store)
    ts = now_iso()
    events = []

    for app_id in sorted(snaps):
        snap = snaps[app_id]
        rec = db["apps"].get(app_id)
        last = rec["versions"][-1] if rec and rec["versions"] else None
        if last and last["event"] != "removed" and last["hash"] == snap["hash"]:
            if not dry_run:
                rec["last_seen"] = ts
            continue

        prev = last_live(rec)
        event = "created" if last is None else ("restored" if last["event"] == "removed" else "modified")
        prev_files = prev["files"] if prev else {}
        changes = compare_files(prev_files, snap["files"])
        added_lines = removed_lines = 0
        for key in changes["added"]:
            text = text_of(snap["files"][key], store, disk_path(root, app_id, snap["kind"], key))
            added_lines += len(text.splitlines()) if text else 0
        for key in changes["removed"]:
            text = text_of(prev_files[key], store)
            removed_lines += len(text.splitlines()) if text else 0
        for key in changes["modified"]:
            a = text_of(prev_files[key], store)
            b = text_of(snap["files"][key], store, disk_path(root, app_id, snap["kind"], key))
            if a is not None and b is not None:
                plus, minus = line_stats(a, b)
                added_lines += plus
                removed_lines += minus

        if rec is None:
            rec = {"name": snap["name"], "kind": snap["kind"], "first_seen": ts,
                   "last_seen": ts, "versions": []}
            if not dry_run:
                db["apps"][app_id] = rec
        entry = {
            "rev": len(rec["versions"]) + 1, "event": event, "timestamp": ts,
            "version": snap["version"], "version_source": snap["version_source"],
            "hash": snap["hash"], "files": snap["files"], "changes": changes,
            "lines": {"added": added_lines, "removed": removed_lines},
            "size": snap["size"], "note": note,
            "warnings": version_warnings(prev, snap, event),
        }
        if not dry_run:
            rec["versions"].append(entry)
            rec["kind"] = snap["kind"]
            rec["last_seen"] = ts
        events.append((app_id, entry))

    for app_id, rec in sorted(db["apps"].items()):
        last = rec["versions"][-1] if rec["versions"] else None
        if app_id in snaps or not last or last["event"] == "removed":
            continue
        entry = {
            "rev": len(rec["versions"]) + 1, "event": "removed", "timestamp": ts,
            "version": None, "version_source": None, "hash": None, "files": {},
            "changes": {"added": [], "modified": [], "removed": sorted(last["files"])},
            "lines": {"added": 0, "removed": 0}, "size": 0, "note": note, "warnings": [],
        }
        if not dry_run:
            rec["versions"].append(entry)
        events.append((app_id, entry))

    if not dry_run:
        counts = {k: sum(1 for _, e in events if e["event"] == k) for k in EVENT_LABEL}
        db["scans"].append({"timestamp": ts, "note": note, "apps": len(snaps), **counts})
        db["scans"] = db["scans"][-1000:]
        db["last_scan"] = ts
        db["tool"] = TOOL_VERSION
        save_db(root, db)
    return events


def print_events(events: list) -> None:
    if not events:
        print("  沒有變更。")
        return
    for app_id, e in events:
        ver = f" v{e['version']}" if e.get("version") else ""
        line = f"  {EVENT_MARK[e['event']]} {EVENT_LABEL[e['event']]}  {app_id}{ver}  [r{e['rev']}]"
        ch = e["changes"]
        if e["event"] != "removed":
            parts = []
            if ch["added"]:
                parts.append(f"新增 {len(ch['added'])} 檔")
            if ch["modified"]:
                parts.append(f"修改 {len(ch['modified'])} 檔")
            if ch["removed"]:
                parts.append(f"刪除 {len(ch['removed'])} 檔")
            parts.append(f"+{e['lines']['added']} −{e['lines']['removed']} 行")
            line += "  " + "，".join(parts)
        print(line)
        for w in e["warnings"]:
            print(f"      ⚠ {w}")


# ───────────────────────────── HTML 報告 ─────────────────────────────

CSS = """
:root{--bg:#f5f6f8;--panel:#fff;--text:#1c2230;--muted:#5c6575;--line:#e0e4ea;--accent:#2f6fde;
--ok:#1f8a4c;--warn:#a86400;--bad:#c23b3b;--add-bg:#e5f5eb;--add-fg:#16663a;--del-bg:#fbe8e8;
--del-fg:#9e2a2a;--hunk:#6750c4;--code:#f2f4f7;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--panel:#191e26;--text:#e3e7ee;--muted:#97a1b1;
--line:#2a313c;--accent:#6fa1ff;--ok:#52c585;--warn:#e3a53f;--bad:#f07070;--add-bg:#132d1d;
--add-fg:#94e2b1;--del-bg:#361a1c;--del-fg:#f3a3a3;--hunk:#b5a6ff;--code:#131820;--chip:#232a35}}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",
"Noto Sans TC","Microsoft JhengHei",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 64px}
.wrap,.mono{overflow-wrap:anywhere}td.nw{white-space:nowrap}
h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 12px}h3{font-size:17px;margin:0}
.muted{color:var(--muted)}.small{font-size:13px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Consolas,"Cascadia Mono",monospace;font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:26px;line-height:1.2}.kpi span{color:var(--muted);font-size:13px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.tools{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}
.tools input,.tools select{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);
border-radius:8px;padding:7px 10px}.tools input{flex:1;min-width:200px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--panel);font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);white-space:nowrap;background:var(--panel)}
table.sortable th{cursor:pointer;user-select:none}
th[data-dir=asc]::after{content:" ▲"}th[data-dir=desc]::after{content:" ▼"}
td.num{text-align:right;white-space:nowrap}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;
background:var(--chip);white-space:nowrap}
.ev-created{color:var(--accent)}.ev-modified{color:var(--warn)}.ev-removed{color:var(--bad)}.ev-restored{color:var(--ok)}
.st-active{color:var(--ok)}.st-removed{color:var(--bad)}
.warn{color:var(--warn)}.plus{color:var(--add-fg)}.minus{color:var(--del-fg)}
.app{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}
.app-head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;margin-bottom:10px}
details.rev{border-top:1px solid var(--line);padding:8px 0}
details.rev>summary{cursor:pointer;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}
.files{margin:8px 0 0 0;padding-left:20px;font-size:13px}
.diff{margin:8px 0;padding:8px 0;background:var(--code);border:1px solid var(--line);border-radius:8px;
overflow-x:auto;font-size:12.5px;line-height:1.45}
.diff span{display:block;padding:0 12px;white-space:pre}
.diff .da{background:var(--add-bg);color:var(--add-fg)}.diff .dr{background:var(--del-bg);color:var(--del-fg)}
.diff .dc{color:var(--hunk)}.diff .dh{color:var(--muted);font-weight:600}
.diff-file{margin-top:10px;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{display:flex;flex-direction:column;gap:2px;background:var(--panel);border:1px solid var(--line);
border-radius:10px;padding:12px 14px;color:var(--text);min-width:0}
.card:hover{border-color:var(--accent);text-decoration:none}.card b{color:var(--accent);font-size:16px}
.card span{overflow-wrap:anywhere}
footer{margin-top:40px;color:var(--muted);font-size:13px}
@media print{.tools{display:none}details.rev{break-inside:avoid}}
"""

JS = """
(function(){
  var q=document.getElementById('q'),f=document.getElementById('f');
  function apply(){
    var term=(q.value||'').toLowerCase(),mode=f.value;
    document.querySelectorAll('[data-app]').forEach(function(el){
      var ok=el.getAttribute('data-search').indexOf(term)>=0;
      if(ok&&mode!=='all'){ok=(' '+el.getAttribute('data-flags')+' ').indexOf(' '+mode+' ')>=0;}
      el.hidden=!ok;
    });
  }
  q.addEventListener('input',apply);f.addEventListener('change',apply);
  document.querySelectorAll('table.sortable th').forEach(function(th){
    th.addEventListener('click',function(){
      var table=th.closest('table'),body=table.tBodies[0];
      var col=Array.prototype.indexOf.call(th.parentNode.children,th);
      var asc=th.getAttribute('data-dir')!=='asc',num=th.getAttribute('data-type')==='n';
      table.querySelectorAll('th').forEach(function(x){x.removeAttribute('data-dir');});
      th.setAttribute('data-dir',asc?'asc':'desc');
      var rows=Array.prototype.slice.call(body.rows);
      rows.sort(function(a,b){
        var x=a.cells[col].getAttribute('data-v')||a.cells[col].textContent;
        var y=b.cells[col].getAttribute('data-v')||b.cells[col].textContent;
        if(num){x=parseFloat(x)||0;y=parseFloat(y)||0;}else{x=x.toLowerCase();y=y.toLowerCase();}
        return (x>y?1:x<y?-1:0)*(asc?1:-1);
      });
      rows.forEach(function(r){body.appendChild(r);});
    });
  });
})();
"""

PORTAL_JS = """
(function(){
  var q=document.getElementById('mq');
  q.addEventListener('input',function(){
    var term=(q.value||'').toLowerCase();
    document.querySelectorAll('[data-group]').forEach(function(g){
      var any=false;
      g.querySelectorAll('[data-mod]').forEach(function(el){
        var ok=el.getAttribute('data-search').indexOf(term)>=0;el.hidden=!ok;any=any||ok;
      });
      g.hidden=!any;
    });
  });
})();
"""


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def anchor(app_id: str) -> str:
    return "app-" + hashlib.sha1(app_id.encode()).hexdigest()[:10]


def render_diff(lines: list, max_chars: int) -> str:
    out = []
    for i, line in enumerate(lines):
        if i < 2 and (line.startswith("---") or line.startswith("+++")):
            cls = "dh"
        elif line.startswith("@@"):
            cls = "dc"
        elif line.startswith("+"):
            cls = "da"
        elif line.startswith("-"):
            cls = "dr"
        else:
            cls = ""
        if len(line) > max_chars:
            line = line[:max_chars] + " …"
        out.append(f'<span class="{cls}">{esc(line) or " "}</span>')
    return '<div class="diff">' + "".join(out) + "</div>"


def app_state(rec: dict) -> dict:
    last = rec["versions"][-1]
    live = last_live(rec)
    return {
        "removed": last["event"] == "removed",
        "live": live,
        "version": live["version"] if live else None,
        "source": live["version_source"] if live else None,
        "files": len(live["files"]) if live else 0,
        "size": live["size"] if live else 0,
        "last_change": last["timestamp"],
        "warnings": sum(len(v["warnings"]) for v in rec["versions"]),
        "last_warnings": live["warnings"] if live and live is last else [],
    }


def render_revision(app_id: str, rec: dict, idx: int, store: BlobStore, cfg: dict) -> str:
    v = rec["versions"][idx]
    ch = v["changes"]
    head = [f'<b class="mono">r{v["rev"]}</b>',
            f'<span class="badge ev-{v["event"]}">{EVENT_LABEL[v["event"]]}</span>']
    if v.get("version"):
        head.append(f'<span class="mono">v{esc(v["version"])}</span>')
    head.append(f'<span class="muted small">{esc(fmt_time(v["timestamp"]))}</span>')
    if v["event"] != "removed":
        head.append(f'<span class="small"><span class="plus">+{v["lines"]["added"]}</span> '
                    f'<span class="minus">−{v["lines"]["removed"]}</span> 行</span>')
    if v.get("note"):
        head.append(f'<span class="small">📝 {esc(v["note"])}</span>')
    for w in v["warnings"]:
        head.append(f'<span class="small warn">⚠ {esc(w)}</span>')

    body = []
    labels = (("added", "新增"), ("modified", "修改"), ("removed", "刪除"))
    items = [f"<li>{name}：<span class='mono'>{esc(k)}</span></li>"
             for key, name in labels for k in ch[key]]
    if len(items) > 200:
        items = items[:200] + [f"<li class='muted'>…另有 {len(items) - 200} 項</li>"]
    body.append(f"<ul class='files'>{''.join(items)}</ul>")

    if v["event"] in ("modified", "restored"):
        prev = last_live({"versions": rec["versions"][:idx]})
        if prev:
            for d in diff_versions(prev["files"], v["files"], store, f"r{prev['rev']}", f"r{v['rev']}",
                                   cfg["max_diff_lines"]):
                title = f"<div class='diff-file mono'>{esc(d['file'])}</div>"
                if d["lines"] is None:
                    body.append(title + f"<div class='muted small'>{esc(d['note'])}</div>")
                elif not d["lines"]:
                    body.append(title + "<div class='muted small'>內容相同（僅空白或編碼差異）</div>")
                else:
                    more = ""
                    if d["total"] > len(d["lines"]):
                        more = (f"<div class='muted small'>差異共 {d['total']} 行，僅顯示前 {len(d['lines'])} 行。"
                                f"完整差異請執行：<code>python vcdash.py diff \"{esc(app_id)}\" "
                                f"{prev['rev']} {v['rev']}</code></div>")
                    body.append(title + render_diff(d["lines"], cfg["max_line_chars"]) + more)
    is_open = " open" if idx == len(rec["versions"]) - 1 and v["event"] != "created" else ""
    return (f"<details class='rev'{is_open}><summary>{''.join(head)}</summary>"
            f"{''.join(body)}</details>")


def entry_file(rec: dict, cfg: dict) -> str | None:
    """回傳應用程式的入口檔（相對於應用程式本身），找不到時回傳 None。"""
    live = last_live(rec)
    if live is None or rec["versions"][-1]["event"] == "removed":
        return None
    keys = sorted(live["files"])
    if rec["kind"] == "file":
        return keys[0] if keys else None
    top = [k for k in keys if "/" not in k]
    for pattern in ENTRY_CANDIDATES:
        hit = next((k for k in top if fnmatch.fnmatch(k.lower(), pattern)), None)
        if hit:
            return hit
    return next((k for k in top if os.path.splitext(k)[1].lower() in cfg["_ext"]), None)


def entry_href(root: Path, out_dir: Path, app_id: str, kind: str, key: str) -> str:
    """從報告資料夾連到入口檔的網址；可相對時用相對路徑，方便整個資料夾搬移。"""
    target = disk_path(root, app_id, kind, key)
    try:
        rel = os.path.relpath(target, out_dir).replace(os.sep, "/")
    except ValueError:  # Windows 上位於不同磁碟機
        return target.resolve().as_uri()
    return "/".join(quote(part) for part in rel.split("/"))


def render_portal(root: Path, cfg: dict, db: dict, out_dir: Path, generated: str) -> str:
    groups: dict = {}
    for app_id, rec in sorted(db["apps"].items()):
        if not rec["versions"]:
            continue
        key = entry_file(rec, cfg)
        if key is None:
            continue
        st = app_state(rec)
        group = app_id.split("/", 1)[0] if "/" in app_id else "根目錄"
        href = entry_href(root, out_dir, app_id, rec["kind"], key)
        where = app_id if rec["kind"] == "file" else f"{app_id}/{key}"
        search = esc(f"{group} {app_id} {key} {st['version'] or ''}".lower())
        ext = os.path.splitext(key)[1].lower()
        target = " target='_blank' rel='noopener'" if ext in (".html", ".htm") else ""
        warn = f" <span class='warn small'>⚠ {len(st['last_warnings'])}</span>" if st["last_warnings"] else ""
        groups.setdefault(group, []).append(
            f"<a class='card' data-mod data-search='{search}' href='{esc(href)}'{target}>"
            f"<b>{esc(rec['name'])}</b>"
            f"<span class='mono small'>{esc('v' + st['version'] if st['version'] else '未宣告版本')}"
            f" · r{len(rec['versions'])}{warn}</span>"
            f"<span class='muted small mono'>{esc(where)}</span>"
            f"<span class='muted small'>最後變更 {esc(fmt_time(st['last_change']))}</span></a>")

    order = sorted(groups, key=lambda g: (g != "根目錄", g.lower()))
    blocks = "".join(
        f"<section class='mod-group' data-group><h2>{esc(g)} <span class='muted small'>{len(groups[g])}</span></h2>"
        f"<div class='cards'>{''.join(groups[g])}</div></section>" for g in order)
    total = sum(len(v) for v in groups.values())
    empty = "<p class='muted'>尚無可開啟的模組，請先執行 python vcdash.py scan</p>" if not total else ""
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>系統模組入口</title><style>{CSS}</style></head>
<body><main>
<h1>系統模組入口</h1>
<div class="muted small wrap">管理目錄：<span class="mono">{esc(root)}</span><br>
共 {total} 個模組　最後掃描：{esc(fmt_time(db.get("last_scan")))}　·　<a href="index.html">版本控制儀表板 →</a></div>
<div class="tools" style="margin-top:16px">
<input id="mq" type="search" placeholder="搜尋模組名稱、路徑或版本…" aria-label="搜尋模組"></div>
{blocks}{empty}
<footer>{TOOL_NAME} {TOOL_VERSION}　·　產生於 {esc(fmt_time(generated))}　·　點選卡片即開啟該模組的入口檔</footer>
</main><script>{PORTAL_JS}</script></body></html>
"""


def generate_report(root: Path, cfg: dict, out_dir: Path | None = None) -> Path:
    db = load_db(root)
    store = BlobStore(root / DATA_DIR / "objects")
    out_dir = out_dir or root / REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    apps = sorted(db["apps"].items())
    states = {a: app_state(r) for a, r in apps if r["versions"]}
    last_scan = db.get("last_scan")

    active = sum(1 for s in states.values() if not s["removed"])
    removed = len(states) - active
    total_revs = sum(len(r["versions"]) for _, r in apps)
    changed_last = sum(1 for a, r in apps if r["versions"] and r["versions"][-1]["timestamp"] == last_scan)
    warn_apps = sum(1 for s in states.values() if s["last_warnings"])

    rows, sections = [], []
    for app_id, rec in apps:
        if app_id not in states:
            continue
        st = states[app_id]
        flags = ["removed" if st["removed"] else "active"]
        if st["last_warnings"]:
            flags.append("warn")
        if rec["versions"][-1]["timestamp"] == last_scan:
            flags.append("changed")
        attrs = (f'data-app data-flags="{" ".join(flags)}" '
                 f'data-search="{esc((app_id + " " + (st["version"] or "")).lower())}"')
        kind = "專案資料夾" if rec["kind"] == "project" else "單一檔案"
        status = ('<span class="badge st-removed">已移除</span>' if st["removed"]
                  else '<span class="badge st-active">使用中</span>')
        warn = f'<span class="warn">⚠ {len(st["last_warnings"])}</span>' if st["last_warnings"] else ""
        rows.append(
            f"<tr {attrs}><td><a href='#{anchor(app_id)}'><b>{esc(rec['name'])}</b></a>"
            f"<div class='muted small mono'>{esc(app_id)}</div></td>"
            f"<td>{kind}</td>"
            f"<td class='mono' data-v='{esc(st['version'] or '')}'>{esc(st['version'] or '—')}"
            f"<div class='muted small'>{esc(st['source'] or '未宣告版本')}</div></td>"
            f"<td class='num' data-v='{len(rec['versions'])}'>r{len(rec['versions'])}</td>"
            f"<td class='num' data-v='{st['files']}'>{st['files']}</td>"
            f"<td class='num' data-v='{st['size']}'>{fmt_size(st['size'])}</td>"
            f"<td data-v='{esc(st['last_change'])}'>{esc(fmt_time(st['last_change']))}</td>"
            f"<td>{status} {warn}</td></tr>")

        revs = "".join(render_revision(app_id, rec, i, store, cfg)
                       for i in range(len(rec["versions"]) - 1, -1, -1))
        sections.append(
            f"<section class='app' id='{anchor(app_id)}' {attrs}>"
            f"<div class='app-head'><h3>{esc(rec['name'])}</h3>"
            f"<span class='muted small mono'>{esc(app_id)}</span>{status}"
            f"<span class='small'>目前版本 <b class='mono'>{esc(st['version'] or '—')}</b></span>"
            f"<span class='muted small'>首次記錄 {esc(fmt_time(rec['first_seen']))}</span></div>"
            f"{revs}</section>")

    activity = sorted(((v["timestamp"], a, v) for a, r in apps for v in r["versions"]),
                      key=lambda t: (t[0], t[1]), reverse=True)[:40]
    act_rows = "".join(
        f"<tr><td class='nw'>{esc(fmt_time(ts))}</td><td><span class='badge ev-{v['event']}'>{EVENT_LABEL[v['event']]}</span></td>"
        f"<td><a href='#{anchor(a)}'>{esc(a)}</a></td><td class='mono'>r{v['rev']}</td>"
        f"<td class='mono'>{esc(v.get('version') or '—')}</td>"
        f"<td class='num'><span class='plus'>+{v['lines']['added']}</span> "
        f"<span class='minus'>−{v['lines']['removed']}</span></td>"
        f"<td>{esc(v.get('note') or '')}{''.join(f' <span class=warn>⚠ {esc(w)}</span>' for w in v['warnings'])}</td></tr>"
        for ts, a, v in activity)
    scan_rows = "".join(
        f"<tr><td class='nw'>{esc(fmt_time(s['timestamp']))}</td><td class='num'>{s['apps']}</td>"
        f"<td class='num'>{s['created']}</td><td class='num'>{s['modified']}</td>"
        f"<td class='num'>{s['removed']}</td><td class='num'>{s['restored']}</td>"
        f"<td>{esc(s.get('note') or '')}</td></tr>"
        for s in reversed(db["scans"][-50:]))

    generated = now_iso()
    page = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>應用程式版本儀表板</title><style>{CSS}</style></head>
<body><main>
<h1>應用程式版本控制儀表板</h1>
<div class="muted small wrap">管理目錄：<span class="mono">{esc(root)}</span><br>
最後掃描：{esc(fmt_time(last_scan))}　報告產生：{esc(fmt_time(generated))}　·　<a href="{PORTAL_FILE}">系統模組入口 →</a></div>

<div class="kpis">
<div class="kpi"><b>{active}</b><span>使用中的應用程式</span></div>
<div class="kpi"><b>{removed}</b><span>已移除</span></div>
<div class="kpi"><b>{total_revs}</b><span>版本紀錄總數</span></div>
<div class="kpi"><b>{changed_last}</b><span>最近一次掃描有變更</span></div>
<div class="kpi"><b class="{'warn' if warn_apps else ''}">{warn_apps}</b><span>目前版本有警告</span></div>
</div>

<h2>應用程式總覽</h2>
<div class="tools">
<input id="q" type="search" placeholder="搜尋名稱、路徑或版本…" aria-label="搜尋">
<select id="f" aria-label="篩選">
<option value="all">全部</option><option value="active">使用中</option>
<option value="changed">最近一次掃描有變更</option><option value="warn">有警告</option>
<option value="removed">已移除</option></select>
</div>
<div class="scroll"><table class="sortable">
<thead><tr><th>應用程式</th><th>類型</th><th>目前版本</th><th data-type="n">修訂</th>
<th data-type="n">檔案</th><th data-type="n">大小</th><th>最後變更</th><th>狀態</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="8" class="muted">尚無紀錄，請先執行 python vcdash.py scan</td></tr>'}</tbody>
</table></div>

<h2>最近修改歷程</h2>
<div class="scroll"><table>
<thead><tr><th>時間</th><th>事件</th><th>應用程式</th><th>修訂</th><th>版本</th><th>行數</th><th>說明</th></tr></thead>
<tbody>{act_rows}</tbody></table></div>

<h2>各應用程式版本與差異</h2>
{''.join(sections)}

<h2>掃描紀錄</h2>
<div class="scroll"><table>
<thead><tr><th>時間</th><th>應用程式數</th><th>新增</th><th>修改</th><th>移除</th><th>復原</th><th>說明</th></tr></thead>
<tbody>{scan_rows}</tbody></table></div>

<footer>{TOOL_NAME} {TOOL_VERSION}　·　完整歷程另存於 history.csv　·　紀錄資料位於 {esc(DATA_DIR)}/</footer>
</main><script>{JS}</script></body></html>
"""
    report = out_dir / "index.html"
    report.write_text(page, encoding="utf-8")
    (out_dir / PORTAL_FILE).write_text(render_portal(root, cfg, db, out_dir, generated), encoding="utf-8")

    with open(out_dir / "history.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["應用程式", "類型", "修訂", "事件", "版本", "版本來源", "時間",
                         "新增檔案", "修改檔案", "刪除檔案", "新增行數", "刪除行數", "大小(bytes)", "說明", "警告"])
        for app_id, rec in apps:
            for v in rec["versions"]:
                ch = v["changes"]
                writer.writerow([app_id, rec["kind"], v["rev"], EVENT_LABEL[v["event"]], v.get("version") or "",
                                 v.get("version_source") or "", v["timestamp"],
                                 "; ".join(ch["added"]), "; ".join(ch["modified"]), "; ".join(ch["removed"]),
                                 v["lines"]["added"], v["lines"]["removed"], v["size"], v.get("note") or "",
                                 "; ".join(v["warnings"])])
    return report


# ───────────────────────────── 命令列 ─────────────────────────────

def find_app(db: dict, query: str) -> str:
    if query in db["apps"]:
        return query
    q = query.replace("\\", "/").strip("/").lower()
    hits = [a for a in db["apps"] if q in a.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        die(f"找不到應用程式：{query}（可用 list 查看）")
    die("符合多個應用程式，請輸入更完整的路徑：\n  " + "\n  ".join(hits))
    return ""


def get_rev(rec: dict, rev: int) -> dict:
    if not 1 <= rev <= len(rec["versions"]):
        die(f"修訂號 r{rev} 不存在（共 {len(rec['versions'])} 版）")
    return rec["versions"][rev - 1]


def cmd_scan(root, cfg, args):
    events = scan(root, cfg, note=args.message or "", dry_run=False)
    print(f"掃描完成：{root}")
    print_events(events)
    if args.report:
        print(f"報告：{generate_report(root, cfg)}")


def cmd_status(root, cfg, args):
    events = scan(root, cfg, dry_run=True)
    print(f"尚未記錄的變更（{root}）：")
    print_events(events)
    if events:
        print("\n執行 python vcdash.py scan 記錄這些變更。")


def cmd_list(root, cfg, args):
    db = load_db(root)
    if not db["apps"]:
        print("尚無紀錄，請先執行 python vcdash.py scan")
        return
    for app_id, rec in sorted(db["apps"].items()):
        st = app_state(rec)
        state = "已移除" if st["removed"] else "使用中"
        print(f"  {app_id:<50} {st['version'] or '—':<14} r{len(rec['versions']):<4} "
              f"{fmt_time(st['last_change'])}  {state}")


def cmd_history(root, cfg, args):
    db = load_db(root)
    app_id = find_app(db, args.app)
    rec = db["apps"][app_id]
    print(f"{app_id} 的修改歷程：")
    for v in rec["versions"]:
        ch = v["changes"]
        ver = f"v{v['version']}" if v.get("version") else "—"
        print(f"  r{v['rev']:<4} {fmt_time(v['timestamp'])}  {EVENT_LABEL[v['event']]}  {ver:<12} "
              f"+{v['lines']['added']} −{v['lines']['removed']}  "
              f"(檔案 +{len(ch['added'])} ~{len(ch['modified'])} −{len(ch['removed'])})"
              + (f"  {v['note']}" if v.get("note") else ""))
        for w in v["warnings"]:
            print(f"        ⚠ {w}")


def cmd_diff(root, cfg, args):
    db = load_db(root)
    app_id = find_app(db, args.app)
    rec = db["apps"][app_id]
    store = BlobStore(root / DATA_DIR / "objects")
    if args.b is not None:
        new = get_rev(rec, args.b)
    else:
        new = rec["versions"][-1]
    if args.a is not None:
        old = get_rev(rec, args.a)
    else:
        old = last_live({"versions": rec["versions"][:new["rev"] - 1]})
        if old is None:
            die(f"{app_id} 只有一個版本，沒有可比對的前一版。")
    results = diff_versions(old["files"], new["files"], store, f"r{old['rev']}", f"r{new['rev']}")
    if not results:
        print(f"r{old['rev']} 與 r{new['rev']} 內容相同。")
    for d in results:
        if d["lines"] is None:
            print(f"### {d['file']}：{d['note']}")
        else:
            print("\n".join(d["lines"]))


def cmd_restore(root, cfg, args):
    db = load_db(root)
    app_id = find_app(db, args.app)
    rec = db["apps"][app_id]
    v = get_rev(rec, args.rev)
    if v["event"] == "removed":
        die(f"r{v['rev']} 是移除紀錄，沒有檔案可還原。")
    store = BlobStore(root / DATA_DIR / "objects")
    out = Path(args.to) if args.to else root / RESTORE_DIR / f"{app_id.replace('/', '__')}_r{v['rev']}"
    out = out.resolve()
    missing = []
    for key, meta in sorted(v["files"].items()):
        target = (out / key).resolve()
        if out not in target.parents:
            missing.append(key)
            continue
        data = store.get(meta["h"])
        if data is None:
            missing.append(key)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f"已將 {app_id} r{v['rev']} 還原到：{out}")
    if missing:
        print("以下檔案內容未保存（超過大小上限），無法還原：")
        for key in missing:
            print(f"  {key}")


def cmd_report(root, cfg, args):
    print(f"報告：{generate_report(root, cfg, Path(args.out).resolve() if args.out else None)}")


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except AttributeError:
        pass

    root_help = "要管理的目錄（預設為 vcdash.py 所在目錄）"
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help=root_help)
    parser = argparse.ArgumentParser(prog="vcdash",
                                     description="應用程式版本控制儀表板：掃描同一層目錄及子目錄的應用程式版本、差異與修改歷程。")
    parser.add_argument("--root", help=root_help)
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("scan", parents=[common], help="掃描並記錄變更")
    p.add_argument("-m", "--message", help="這次變更的說明")
    p.add_argument("--report", action="store_true", help="掃描後產生 HTML 報告")
    p.set_defaults(func=cmd_scan)
    sub.add_parser("status", parents=[common], help="查看尚未記錄的變更（不寫入）").set_defaults(func=cmd_status)
    sub.add_parser("list", parents=[common], help="列出所有應用程式").set_defaults(func=cmd_list)
    p = sub.add_parser("history", parents=[common], help="查看修改歷程")
    p.add_argument("app")
    p.set_defaults(func=cmd_history)
    p = sub.add_parser("diff", parents=[common], help="比對兩個修訂版")
    p.add_argument("app")
    p.add_argument("a", nargs="?", type=int, help="舊修訂號")
    p.add_argument("b", nargs="?", type=int, help="新修訂號")
    p.set_defaults(func=cmd_diff)
    p = sub.add_parser("restore", parents=[common], help="還原某個修訂版的檔案")
    p.add_argument("app")
    p.add_argument("rev", type=int)
    p.add_argument("--to", help="輸出資料夾（預設 vcdash-restore/）")
    p.set_defaults(func=cmd_restore)
    p = sub.add_parser("report", parents=[common], help="產生 HTML 報告")
    p.add_argument("--out", help="輸出資料夾（預設 vcdash-report/）")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    if not root.is_dir():
        die(f"目錄不存在：{root}")
    cfg = load_config(root)
    if args.cmd is None:  # 直接執行（例如雙擊）時：掃描並產生報告
        args = parser.parse_args(["scan", "--report"] + (["--root", str(root)] if args.root else []))
    args.func(root, cfg, args)


if __name__ == "__main__":
    main()
