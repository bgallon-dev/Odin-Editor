"""
Kettle Memory Server — IPC bridge between Odin editor and the memory system.

Manages two SQLite databases:
  - Project DB (.kettle/memory.db): events, sessions, symbols for this project
  - Global DB  (~/.kettle/global.db): cross-project knowledge, library symbols

Communicates with the Odin editor over TCP (localhost) using newline-delimited JSON.
"""

import argparse
import ast
import json
import os
import pathlib
import socket
import sqlite3
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

PROJECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at   TEXT,
    file_scope  TEXT    DEFAULT '[]',   -- JSON array of file paths
    event_count INTEGER NOT NULL DEFAULT 0,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    timestamp  TEXT    NOT NULL DEFAULT (datetime('now')),
    event_type TEXT    NOT NULL,
    file_path  TEXT,
    payload    TEXT    DEFAULT '{}' -- JSON
);

CREATE TABLE IF NOT EXISTS symbols (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path     TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    kind          TEXT    NOT NULL,  -- function, class, method, variable, import
    signature     TEXT    DEFAULT '',
    docstring     TEXT    DEFAULT '',
    line_start    INTEGER NOT NULL DEFAULT 0,
    line_end      INTEGER NOT NULL DEFAULT 0,
    last_seen     TEXT    NOT NULL DEFAULT (datetime('now')),
    session_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(file_path, name, kind)
);
"""

GLOBAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT    NOT NULL,  -- e.g. 'stdlib:ast', 'stdlib:json', 'lmstudio'
    name       TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    signature  TEXT    DEFAULT '',
    docstring  TEXT    DEFAULT '',
    UNIQUE(source, name, kind)
);
"""


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

def init_db(db_path: str, schema: str, label: str) -> sqlite3.Connection:
    """Create or open a database file and ensure schema exists."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(schema)

    # Ensure system_config rows
    cur = conn.execute("SELECT value FROM system_config WHERE key='schema_version'")
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    cur = conn.execute("SELECT value FROM system_config WHERE key='seeding_complete'")
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO system_config (key, value) VALUES ('seeding_complete', 'false')"
        )
    conn.commit()
    log(f"[{label}] initialized: {db_path}")
    return conn


def needs_seeding(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT value FROM system_config WHERE key='seeding_complete'")
    row = cur.fetchone()
    return row is None or row[0] != "true"


def mark_seeding_complete(conn: sqlite3.Connection):
    conn.execute(
        "UPDATE system_config SET value='true' WHERE key='seeding_complete'"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Global database seeding — stdlib signatures
# ---------------------------------------------------------------------------

# Minimal seed set: modules the user is most likely to need immediately
SEED_MODULES = {
    "ast": [
        ("parse", "function", "parse(source, filename='<unknown>', mode='exec', *, type_comments=False, feature_version=None)", "Parse source into an AST node."),
        ("dump", "function", "dump(node, annotate_fields=True, include_attributes=False, *, indent=None)", "Return a formatted dump of the tree in node."),
        ("literal_eval", "function", "literal_eval(node_or_string)", "Evaluate an expression node or a string containing only a Python literal."),
        ("walk", "function", "walk(node)", "Recursively yield all descendant nodes in the tree."),
        ("NodeVisitor", "class", "class NodeVisitor", "A node visitor base class that walks the abstract syntax tree."),
        ("NodeTransformer", "class", "class NodeTransformer(NodeVisitor)", "A NodeVisitor subclass that walks the AST and allows modification."),
    ],
    "sqlite3": [
        ("connect", "function", "connect(database, timeout=5.0, ...)", "Open a connection to an SQLite database."),
        ("Connection", "class", "class Connection", "SQLite database connection object."),
        ("Cursor", "class", "class Cursor", "SQLite database cursor object."),
        ("Row", "class", "class Row", "Row factory that provides column-name-based access."),
    ],
    "json": [
        ("dumps", "function", "dumps(obj, *, skipkeys=False, ensure_ascii=True, ...)", "Serialize obj to a JSON formatted string."),
        ("loads", "function", "loads(s, *, cls=None, ...)", "Deserialize s to a Python object."),
        ("dump", "function", "dump(obj, fp, *, skipkeys=False, ...)", "Serialize obj as a JSON formatted stream to fp."),
        ("load", "function", "load(fp, *, cls=None, ...)", "Deserialize fp to a Python object."),
    ],
    "pathlib": [
        ("Path", "class", "class Path(*pathsegments)", "PurePath subclass that can make system calls."),
        ("PurePath", "class", "class PurePath(*pathsegments)", "Base class for manipulating paths without I/O."),
        ("PurePosixPath", "class", "class PurePosixPath(*pathsegments)", "PurePath subclass for non-Windows systems."),
        ("PureWindowsPath", "class", "class PureWindowsPath(*pathsegments)", "PurePath subclass for Windows systems."),
    ],
    "dataclasses": [
        ("dataclass", "function", "dataclass(cls=None, /, *, init=True, repr=True, eq=True, order=False, ...)", "Returns a class with generated special methods."),
        ("field", "function", "field(*, default=MISSING, default_factory=MISSING, ...)", "Customize a field in a dataclass."),
        ("asdict", "function", "asdict(instance, *, dict_factory=dict)", "Return the fields of a dataclass instance as a dict."),
        ("astuple", "function", "astuple(instance, *, tuple_factory=tuple)", "Return the fields of a dataclass instance as a tuple."),
    ],
    "typing": [
        ("Optional", "type", "Optional[X]", "Equivalent to Union[X, None]."),
        ("List", "type", "List[X]", "Generic version of list."),
        ("Dict", "type", "Dict[K, V]", "Generic version of dict."),
        ("Tuple", "type", "Tuple[X, ...]", "Generic version of tuple."),
        ("Union", "type", "Union[X, Y, ...]", "Union type; Union[X, Y] means either X or Y."),
        ("Any", "type", "Any", "Special form indicating an unconstrained type."),
    ],
    "threading": [
        ("Thread", "class", "class Thread(group=None, target=None, name=None, args=(), kwargs={}, *, daemon=None)", "A class that represents a thread of control."),
        ("Lock", "class", "class Lock", "A factory function that returns a new primitive lock object."),
        ("Event", "class", "class Event", "A class that implements event objects."),
    ],
    "socket": [
        ("socket", "class", "class socket(family=AF_INET, type=SOCK_STREAM, proto=0, fileno=None)", "Create a new socket using the given address family, socket type and protocol number."),
        ("create_connection", "function", "create_connection(address, timeout=None, source_address=None)", "Connect to a TCP service."),
    ],
}


def seed_global_db(conn: sqlite3.Connection):
    """Insert stdlib symbol signatures into the global database."""
    log("[global] seeding stdlib symbols...")
    count = 0
    for module, symbols in SEED_MODULES.items():
        source = f"stdlib:{module}"
        for name, kind, signature, docstring in symbols:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO symbols (source, name, kind, signature, docstring)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source, name, kind, signature, docstring),
                )
                count += 1
            except sqlite3.Error:
                pass
    conn.commit()
    mark_seeding_complete(conn)
    log(f"[global] seeded {count} symbols")


# ---------------------------------------------------------------------------
# Project seeding — scan existing source files for symbols
# ---------------------------------------------------------------------------

SCAN_EXTENSIONS = {".py", ".pyw", ".pyi"}
SKIP_DIRS = {"__pycache__", ".git", ".kettle", "node_modules", ".venv", "venv", ".env"}


def seed_project_db(conn: sqlite3.Connection, project_root: str):
    """Walk the project tree and extract symbols from all Python files."""
    log("[project] seeding symbols from existing files...")
    count = 0
    root = pathlib.Path(project_root)
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            symbols = extract_symbols(str(path.relative_to(root)), source)
            count += upsert_symbols(conn, symbols)
        except Exception:
            pass
    conn.commit()
    mark_seeding_complete(conn)
    log(f"[project] seeded {count} symbols")


# ---------------------------------------------------------------------------
# AST symbol extraction
# ---------------------------------------------------------------------------

@dataclass
class SymbolInfo:
    file_path: str
    name: str
    kind: str  # function, class, method, variable, import
    signature: str = ""
    docstring: str = ""
    line_start: int = 0
    line_end: int = 0


def extract_symbols(file_path: str, source: str) -> list[SymbolInfo]:
    """Parse Python source and extract top-level symbols."""
    symbols = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            sig = _format_function_sig(node)
            doc = ast.get_docstring(node) or ""
            symbols.append(SymbolInfo(
                file_path=file_path,
                name=node.name,
                kind="function",
                signature=sig,
                docstring=doc[:500],
                line_start=node.lineno - 1,
                line_end=node.end_lineno or node.lineno,
            ))
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            bases = ", ".join(_name_of(b) for b in node.bases)
            sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
            symbols.append(SymbolInfo(
                file_path=file_path,
                name=node.name,
                kind="class",
                signature=sig,
                docstring=doc[:500],
                line_start=node.lineno - 1,
                line_end=node.end_lineno or node.lineno,
            ))
            # Extract methods
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    msig = _format_function_sig(item)
                    mdoc = ast.get_docstring(item) or ""
                    symbols.append(SymbolInfo(
                        file_path=file_path,
                        name=f"{node.name}.{item.name}",
                        kind="method",
                        signature=msig,
                        docstring=mdoc[:500],
                        line_start=item.lineno - 1,
                        line_end=item.end_lineno or item.lineno,
                    ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append(SymbolInfo(
                    file_path=file_path,
                    name=alias.asname or alias.name,
                    kind="import",
                    signature=f"import {alias.name}",
                    line_start=node.lineno - 1,
                    line_end=node.lineno,
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                symbols.append(SymbolInfo(
                    file_path=file_path,
                    name=alias.asname or alias.name,
                    kind="import",
                    signature=f"from {module} import {alias.name}",
                    line_start=node.lineno - 1,
                    line_end=node.lineno,
                ))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.append(SymbolInfo(
                        file_path=file_path,
                        name=target.id,
                        kind="variable",
                        line_start=node.lineno - 1,
                        line_end=node.end_lineno or node.lineno,
                    ))

    return symbols


def _format_function_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Build a readable function signature from an AST node."""
    args = []
    all_args = node.args

    # Positional args
    defaults_offset = len(all_args.args) - len(all_args.defaults)
    for i, arg in enumerate(all_args.args):
        s = arg.arg
        if arg.annotation:
            s += f": {_name_of(arg.annotation)}"
        di = i - defaults_offset
        if di >= 0 and di < len(all_args.defaults):
            s += f"={ast.dump(all_args.defaults[di])}"
        args.append(s)

    # *args
    if all_args.vararg:
        s = f"*{all_args.vararg.arg}"
        if all_args.vararg.annotation:
            s += f": {_name_of(all_args.vararg.annotation)}"
        args.append(s)

    # **kwargs
    if all_args.kwarg:
        s = f"**{all_args.kwarg.arg}"
        if all_args.kwarg.annotation:
            s += f": {_name_of(all_args.kwarg.annotation)}"
        args.append(s)

    ret = ""
    if node.returns:
        ret = f" -> {_name_of(node.returns)}"

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)}){ret}"


def _name_of(node) -> str:
    """Get a string representation of a name/attribute/subscript AST node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    elif isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Subscript):
        return f"{_name_of(node.value)}[{_name_of(node.slice)}]"
    elif isinstance(node, ast.Tuple):
        return ", ".join(_name_of(e) for e in node.elts)
    return ast.dump(node)


def upsert_symbols(conn: sqlite3.Connection, symbols: list[SymbolInfo]) -> int:
    """Insert or update symbols in the project database. Returns count."""
    count = 0
    for sym in symbols:
        try:
            conn.execute(
                """INSERT INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end, last_seen, session_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 1)
                   ON CONFLICT(file_path, name, kind) DO UPDATE SET
                     signature = excluded.signature,
                     docstring = excluded.docstring,
                     line_start = excluded.line_start,
                     line_end = excluded.line_end,
                     last_seen = datetime('now'),
                     session_count = session_count + 1""",
                (sym.file_path, sym.name, sym.kind, sym.signature, sym.docstring, sym.line_start, sym.line_end),
            )
            count += 1
        except sqlite3.Error:
            pass
    return count


def sync_symbols_for_file(conn: sqlite3.Connection, file_path: str,
                          symbols: list[SymbolInfo]) -> int:
    """Sync symbols for a single file: upsert current symbols, delete stale ones.

    Returns the number of symbols now present for this file.
    """
    # Build the set of (name, kind) tuples that currently exist in the file
    current_keys = {(sym.name, sym.kind) for sym in symbols}

    # Upsert all current symbols
    upsert_symbols(conn, symbols)

    # Delete symbols that were in the DB for this file but are no longer in source
    existing = conn.execute(
        "SELECT id, name, kind FROM symbols WHERE file_path = ?", (file_path,)
    ).fetchall()

    stale_ids = [
        row[0] for row in existing if (row[1], row[2]) not in current_keys
    ]
    if stale_ids:
        placeholders = ",".join("?" * len(stale_ids))
        conn.execute(
            f"DELETE FROM symbols WHERE id IN ({placeholders})", stale_ids
        )

    return len(symbols)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO sessions DEFAULT VALUES")
    conn.commit()
    return cur.lastrowid or 0


def close_session(conn: sqlite3.Connection, session_id: int):
    conn.execute(
        "UPDATE sessions SET closed_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def record_event(conn: sqlite3.Connection, session_id: int, event_type: str,
                 file_path: str = "", payload: dict | None = None):
    conn.execute(
        "INSERT INTO events (session_id, event_type, file_path, payload) VALUES (?, ?, ?, ?)",
        (session_id, event_type, file_path, json.dumps(payload or {})),
    )
    conn.execute(
        "UPDATE sessions SET event_count = event_count + 1 WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def get_file_symbols(db: sqlite3.Connection, file_path: str) -> list[SymbolInfo]:
    """Query stored symbols for a specific file."""
    rows = db.execute(
        "SELECT name, kind, signature, docstring, line_start, line_end "
        "FROM symbols WHERE file_path = ? ORDER BY line_start",
        (file_path,)
    ).fetchall()
    return [
        SymbolInfo(file_path=file_path, name=r[0], kind=r[1], signature=r[2],
                   docstring=r[3], line_start=r[4], line_end=r[5])
        for r in rows
    ]


def get_recent_findings(db: sqlite3.Connection, file_path: str, limit: int = 10) -> list[str]:
    """Extract recent validator findings for this file from the events table."""
    rows = db.execute(
        "SELECT payload FROM events WHERE event_type = 'draft_complete' "
        "AND file_path = ? ORDER BY timestamp DESC LIMIT ?",
        (file_path, limit)
    ).fetchall()
    findings = []
    for (payload_str,) in rows:
        try:
            payload = json.loads(payload_str)
            for f in payload.get("findings", []):
                msg = f.get("message", "")
                cat = f.get("category", "")
                if msg and cat:
                    findings.append(f"[{cat}] {msg}")
        except (json.JSONDecodeError, KeyError):
            continue
    return findings


# ---------------------------------------------------------------------------
# IPC server
# ---------------------------------------------------------------------------

def log(msg: str):
    print(f"[kettle] {msg}", flush=True)


class KettleServer:
    def __init__(self, project_db_path: str, global_db_path: str,
                 host: str = "127.0.0.1", port: int = 9999,
                 project_root: str = ".",
                 parent_pid: Optional[int] = None):
        self.host = host
        self.port = port
        self.project_root = os.path.abspath(project_root)
        self.project_db_path = project_db_path
        self.global_db_path = global_db_path
        self.project_db: sqlite3.Connection
        self.global_db: sqlite3.Connection
        self.session_id: Optional[int] = None
        self.parent_pid = parent_pid
        self.running = False

    def initialize(self):
        """Initialize databases and run seeding if needed."""
        self.project_db = init_db(self.project_db_path, PROJECT_SCHEMA, "project")
        self.global_db = init_db(self.global_db_path, GLOBAL_SCHEMA, "global")

        # Seed global DB on first launch
        if needs_seeding(self.global_db):
            seed_global_db(self.global_db)

        # Seed project DB on first launch
        if needs_seeding(self.project_db):
            seed_project_db(self.project_db, self.project_root)

    def signal_ready(self):
        """Write the ready file so Odin knows the server is listening."""
        ready_path = os.path.join(self.project_root, ".kettle", "server.ready")
        with open(ready_path, "w") as f:
            f.write(str(self.port))
        log(f"ready file written: {ready_path}")

    def clear_ready(self):
        ready_path = os.path.join(self.project_root, ".kettle", "server.ready")
        try:
            os.remove(ready_path)
        except OSError:
            pass

    def start(self):
        """Start the TCP server and listen for connections."""
        self.initialize()
        self.running = True

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(1)
        sock.settimeout(1.0)  # Allow periodic check of self.running

        log(f"listening on {self.host}:{self.port}")
        self.signal_ready()

        # Start parent-process watchdog if parent PID was provided
        if self.parent_pid is not None:
            watchdog = threading.Thread(target=self._watch_parent, daemon=True)
            watchdog.start()

        try:
            while self.running:
                try:
                    client, addr = sock.accept()
                    log(f"client connected from {addr}")
                    self.handle_client(client)
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            self.clear_ready()
            sock.close()
            if self.session_id is not None:
                close_session(self.project_db, self.session_id)
            self.project_db.close()
            self.global_db.close()
            log("server shut down")

    def _watch_parent(self):
        """Periodically check if the parent editor process is still alive.
        If it has died, initiate graceful shutdown."""
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32

        while self.running:
            time.sleep(2)

            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, self.parent_pid
            )
            if handle == 0:
                log(f"parent process {self.parent_pid} gone, shutting down")
                self.running = False
                return

            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)

            if exit_code.value != STILL_ACTIVE:
                log(f"parent process {self.parent_pid} exited (code={exit_code.value}), shutting down")
                self.running = False
                return

    def handle_client(self, client: socket.socket):
        """Handle a single client connection with line-delimited JSON."""
        client.settimeout(0.5)
        buf = b""

        while self.running:
            try:
                data = client.recv(4096)
                if not data:
                    log("client disconnected")
                    break
                buf += data

                # Process complete lines
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                        response = self.dispatch(msg)
                        resp_bytes = json.dumps(response).encode("utf-8") + b"\n"
                        client.sendall(resp_bytes)
                    except json.JSONDecodeError:
                        log(f"invalid JSON: {line[:100]}")
                    except Exception as e:
                        log(f"handler error: {e}")
                        traceback.print_exc()

            except socket.timeout:
                continue
            except ConnectionResetError:
                log("client connection reset")
                break
            except OSError:
                break

        try:
            client.close()
        except OSError:
            pass

    def dispatch(self, msg: dict) -> dict:
        """Route a message to the appropriate handler."""
        msg_type = msg.get("type", "")
        payload = msg.get("payload", {})

        handler = {
            "session_start": self.handle_session_start,
            "file_saved": self.handle_file_saved,
            "draft_request": self.handle_draft_request,
            "draft_accept": self.handle_draft_accept,
            "draft_dismiss": self.handle_draft_dismiss,
            "session_end": self.handle_session_end,
            "symbol_count": self.handle_symbol_count,
        }.get(msg_type)

        if handler is None:
            log(f"unknown message type: {msg_type}")
            return {"type": "error", "payload": {"message": f"unknown type: {msg_type}"}}

        return handler(payload)

    # --- Message handlers ---

    def handle_session_start(self, payload: dict) -> dict:
        self.session_id = create_session(self.project_db)
        project_root = payload.get("project_root", self.project_root)
        cwd = payload.get("cwd", "")
        record_event(self.project_db, self.session_id, "session_start",
                     payload={"project_root": project_root, "cwd": cwd})

        # Get current symbol count
        cur = self.project_db.execute("SELECT COUNT(*) FROM symbols")
        symbol_count = cur.fetchone()[0]

        log(f"session started: id={self.session_id}, symbols={symbol_count}")
        return {
            "type": "session_start_ack",
            "payload": {
                "session_id": self.session_id,
                "symbol_count": symbol_count,
            },
        }

    def handle_file_saved(self, payload: dict) -> dict:
        file_path = payload.get("file_path", "")

        if self.session_id is None:
            self.session_id = create_session(self.project_db)

        # Read the file from disk — the editor sends only the path
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (OSError, IOError) as e:
            log(f"could not read file: {file_path}: {e}")
            return {"type": "file_saved_ack", "payload": {"error": str(e)}}

        # Record the save event
        record_event(self.project_db, self.session_id, "file_saved",
                     file_path=file_path,
                     payload={"content_length": len(content)})

        # Extract and upsert symbols (with stale symbol deletion)
        symbols_updated = 0
        if file_path.endswith((".py", ".pyw", ".pyi")):
            symbols = extract_symbols(file_path, content)
            symbols_updated = sync_symbols_for_file(self.project_db, file_path, symbols)
            self.project_db.commit()

        # Total symbol count
        cur = self.project_db.execute("SELECT COUNT(*) FROM symbols")
        total_symbols = cur.fetchone()[0]

        log(f"file_saved: {file_path}, {symbols_updated} symbols synced, total={total_symbols}")
        return {
            "type": "file_saved_ack",
            "payload": {
                "file_path": file_path,
                "symbols_updated": symbols_updated,
                "total_symbols": total_symbols,
            },
        }

    def handle_draft_request(self, payload: dict) -> dict:
        file_path     = payload.get("file_path", "")
        cursor_offset = payload.get("cursor_offset", 0)

        # Read the file from disk
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                file_content = f.read()
        except (OSError, IOError) as e:
            log(f"draft_request: could not read {file_path}: {e}")
            return self._draft_error(f"Could not read file: {e}")

        # Check LM Studio availability
        from lm_studio import check_lm_studio_available
        if not check_lm_studio_available():
            log("draft_request: LM Studio not available")
            return self._draft_error("LM Studio is not running")

        # Record the request event
        if self.session_id:
            record_event(
                self.project_db, self.session_id, "draft_request",
                file_path=file_path,
                payload={"cursor_offset": cursor_offset},
            )

        log(f"draft_request: running pipeline for {file_path} at offset {cursor_offset}")

        # Gather symbol context and past findings for the validator
        symbols = get_file_symbols(self.project_db, file_path)
        symbol_str = "\n".join(
            f"  {s.kind} {s.signature}" for s in symbols
        ) if symbols else ""

        recent = get_recent_findings(self.project_db, file_path)
        past_findings_str = "\n".join(
            f"  - {f}" for f in recent[:5]
        ) if recent else ""

        # Run the pipeline synchronously
        from pipeline import run_pipeline
        result = run_pipeline(
            file_path=file_path,
            file_content=file_content,
            cursor_offset=cursor_offset,
            symbol_context=symbol_str,
            past_findings=past_findings_str,
        )

        # Record the outcome
        if self.session_id:
            record_event(
                self.project_db, self.session_id,
                "draft_complete" if result.success else "draft_failed",
                file_path=file_path,
                payload={
                    "success":          result.success,
                    "drafter_ms":       result.drafter_ms,
                    "validator_ms":     result.validator_ms,
                    "drafter_tokens":   result.drafter_tokens,
                    "validator_tokens": result.validator_tokens,
                    "finding_count":    len(result.findings),
                    "confidence":       result.confidence,
                    "error":            result.error,
                    "findings": [
                        {"category": f.category, "severity": f.severity,
                         "message": f.message}
                        for f in result.findings
                    ],
                },
            )

        log(
            f"pipeline complete: success={result.success} "
            f"drafter={result.drafter_ms}ms validator={result.validator_ms}ms "
            f"findings={len(result.findings)}"
        )

        # Build the response with indexed findings for Odin parsing
        resp_payload = {
            "success":        result.success,
            "draft_text":     result.draft_text,
            "confidence":     result.confidence,
            "findings_count": len(result.findings),
            "drafter_ms":     result.drafter_ms,
            "validator_ms":   result.validator_ms,
            "error":          result.error,
        }

        # Flatten findings into indexed keys:
        #   finding_0_severity, finding_0_line, finding_0_message, ...
        for i, f in enumerate(result.findings):
            resp_payload[f"finding_{i}_category"] = f.category
            resp_payload[f"finding_{i}_severity"]  = f.severity
            resp_payload[f"finding_{i}_line"]      = f.line
            resp_payload[f"finding_{i}_message"]   = f.message

        return {"type": "draft_response", "payload": resp_payload}

    def _draft_error(self, error_msg: str) -> dict:
        return {
            "type": "draft_response",
            "payload": {
                "success": False,
                "draft_text": "",
                "confidence": 0.0,
                "findings_count": 0,
                "drafter_ms": 0,
                "validator_ms": 0,
                "error": error_msg,
            },
        }

    def handle_draft_accept(self, payload: dict) -> dict:
        if self.session_id:
            record_event(self.project_db, self.session_id, "draft_accept",
                         payload=payload)
        log("draft_accept recorded")
        return {"type": "draft_accept_ack", "payload": {}}

    def handle_draft_dismiss(self, payload: dict) -> dict:
        if self.session_id:
            record_event(self.project_db, self.session_id, "draft_dismiss",
                         payload=payload)
        log("draft_dismiss recorded")
        return {"type": "draft_dismiss_ack", "payload": {}}

    def handle_session_end(self, payload: dict) -> dict:
        if self.session_id:
            close_session(self.project_db, self.session_id)
            log(f"session ended: id={self.session_id}")
            self.session_id = None
        return {"type": "session_end_ack", "payload": {}}

    def handle_symbol_count(self, payload: dict) -> dict:
        cur = self.project_db.execute("SELECT COUNT(*) FROM symbols")
        total = cur.fetchone()[0]
        return {"type": "symbol_count_ack", "payload": {"total_symbols": total}}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kettle Memory Server")
    parser.add_argument("--project-db", default=".kettle/memory.db",
                        help="Path to the project database")
    parser.add_argument("--global-db", default=os.path.expanduser("~/.kettle/global.db"),
                        help="Path to the global database")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host to listen on")
    parser.add_argument("--port", type=int, default=9999,
                        help="Port to listen on")
    parser.add_argument("--project-root", default=".",
                        help="Project root directory")
    parser.add_argument("--parent-pid", type=int, default=None,
                        help="PID of the parent editor process (for orphan detection)")
    args = parser.parse_args()

    server = KettleServer(
        project_db_path=args.project_db,
        global_db_path=args.global_db,
        host=args.host,
        port=args.port,
        project_root=args.project_root,
        parent_pid=args.parent_pid,
    )

    try:
        server.start()
    except KeyboardInterrupt:
        log("interrupted")
        server.running = False


if __name__ == "__main__":
    main()
