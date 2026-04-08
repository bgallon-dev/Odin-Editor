"""
Integration test for the full Ctrl+D draft generation flow.

Exercises: draft_request → pipeline (drafter → structural gate → validator) → draft_response.
Requires LM Studio running with both models loaded.
"""

import json
import socket
import sqlite3
import tempfile
import threading
import time
import pathlib
import os
import sys

import pytest

# Ensure source root is importable
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

pytestmark = [pytest.mark.lm, pytest.mark.slow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_kettle_server(project_root, port):
    """Start a real KettleServer in a background thread, return the server object."""
    from kettle_server import KettleServer

    db_dir = os.path.join(project_root, ".kettle")
    os.makedirs(db_dir, exist_ok=True)

    server = KettleServer(
        project_db_path=os.path.join(db_dir, "test_memory.db"),
        global_db_path=os.path.join(db_dir, "test_global.db"),
        host="127.0.0.1",
        port=port,
        project_root=project_root,
    )
    server.initialize()
    return server


def _send_and_recv(sock, msg: dict, timeout: float = 300) -> dict:
    """Send a JSON message and read the JSON response line."""
    sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
    sock.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(8192)
        if not chunk:
            raise ConnectionError("server closed connection")
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


# ---------------------------------------------------------------------------
# Tests — dispatch-level (no TCP, fastest)
# ---------------------------------------------------------------------------

class TestDraftDispatchDirect:
    """Call handle_draft_request directly on a KettleServer instance."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.project_root = str(tmp_path)
        self.server = _start_kettle_server(self.project_root, 0)
        # Start a real session via dispatch so FK constraints are satisfied
        self.server.dispatch({
            "type": "session_start",
            "payload": {"project_root": self.project_root, "cwd": self.project_root},
        })
        yield
        self.server.project_db.close()
        self.server.global_db.close()

    def _write_file(self, name: str, content: str) -> str:
        path = os.path.join(self.project_root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # ------------------------------------------------------------------
    # Python draft
    # ------------------------------------------------------------------

    def test_python_draft_returns_response(self):
        """A draft_request for a Python file returns a well-formed draft_response."""
        content = (
            'def greet(name: str) -> str:\n'
            '    """Return a greeting message."""\n'
        )
        path = self._write_file("hello.py", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {
                "file_path": path,
                "cursor_offset": len(content),
            },
        })

        assert resp["type"] == "draft_response"
        payload = resp["payload"]
        assert "success" in payload
        assert "draft_text" in payload
        assert "confidence" in payload
        assert "findings_count" in payload
        assert "drafter_ms" in payload
        assert "validator_ms" in payload
        print(f"\n[TEST] success={payload['success']} confidence={payload['confidence']:.2f} "
              f"draft_len={len(payload['draft_text'])} findings={payload['findings_count']} "
              f"drafter={payload['drafter_ms']}ms validator={payload['validator_ms']}ms")
        if payload["draft_text"]:
            print(f"[TEST] draft preview: {payload['draft_text'][:200]!r}")

    def test_python_draft_success_has_nonempty_text(self):
        """When the pipeline succeeds, draft_text must be non-empty."""
        content = (
            'import json\n\n'
            'def parse_config(path: str) -> dict:\n'
            '    """Read and parse a JSON config file."""\n'
        )
        path = self._write_file("config_parser.py", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": len(content)},
        })

        payload = resp["payload"]
        if payload["success"]:
            assert len(payload["draft_text"]) > 0, "Successful pipeline returned empty draft"

    def test_draft_confidence_in_range(self):
        """Confidence must be between 0.0 and 1.0."""
        content = 'def add(a, b):\n'
        path = self._write_file("math_util.py", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": len(content)},
        })

        conf = resp["payload"]["confidence"]
        assert 0.0 <= conf <= 1.0, f"Confidence {conf} out of [0, 1] range"

    def test_draft_findings_indexed_correctly(self):
        """If findings_count > 0, the indexed keys must be present."""
        content = 'def process(data):\n    # TODO: implement\n'
        path = self._write_file("process.py", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": len(content)},
        })

        payload = resp["payload"]
        count = payload["findings_count"]
        for i in range(count):
            assert f"finding_{i}_category" in payload, f"Missing finding_{i}_category"
            assert f"finding_{i}_severity" in payload, f"Missing finding_{i}_severity"
            assert f"finding_{i}_line" in payload, f"Missing finding_{i}_line"
            assert f"finding_{i}_message" in payload, f"Missing finding_{i}_message"
            assert payload[f"finding_{i}_severity"] in ("error", "warning", "info")
            print(f"  finding[{i}]: {payload[f'finding_{i}_severity']} "
                  f"L{payload[f'finding_{i}_line']} — {payload[f'finding_{i}_message']}")

    def test_validator_actually_runs(self):
        """validator_ms > 0 proves the validator LLM was called."""
        content = 'class Processor:\n    def run(self):\n'
        path = self._write_file("proc.py", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": len(content)},
        })

        payload = resp["payload"]
        if payload["success"]:
            assert payload["validator_ms"] > 0, \
                "validator_ms is 0 — validator did not run"
            assert payload["drafter_ms"] > 0, \
                "drafter_ms is 0 — drafter did not run"
            print(f"\n[TEST] drafter={payload['drafter_ms']}ms "
                  f"validator={payload['validator_ms']}ms")

    def test_draft_timing_fields_populated(self):
        """Both drafter_ms and validator_ms are non-negative."""
        content = 'def hello():\n'
        path = self._write_file("hi.py", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": len(content)},
        })

        payload = resp["payload"]
        assert payload["drafter_ms"] >= 0
        assert payload["validator_ms"] >= 0

    # ------------------------------------------------------------------
    # Odin draft (structural gate)
    # ------------------------------------------------------------------

    def test_odin_draft_runs_structural_gate(self):
        """An .odin file triggers the structural gate; structural_score is in response."""
        content = (
            'package main\n\n'
            'import "core:fmt"\n\n'
            'main :: proc() {\n'
        )
        path = self._write_file("main.odin", content)

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": len(content)},
        })

        payload = resp["payload"]
        assert "structural_score" in payload, "structural_score missing for .odin file"
        print(f"\n[TEST] odin structural_score={payload.get('structural_score')}")

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    def test_missing_file_returns_error(self):
        """A non-existent file should return success=False with an error."""
        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {
                "file_path": os.path.join(self.project_root, "nonexistent.py"),
                "cursor_offset": 0,
            },
        })

        payload = resp["payload"]
        assert payload["success"] is False
        assert len(payload["error"]) > 0

    def test_empty_file_does_not_crash(self):
        """An empty file should not crash the pipeline."""
        path = self._write_file("empty.py", "")

        resp = self.server.dispatch({
            "type": "draft_request",
            "payload": {"file_path": path, "cursor_offset": 0},
        })

        assert resp["type"] == "draft_response"


# ---------------------------------------------------------------------------
# Tests — full TCP roundtrip
# ---------------------------------------------------------------------------

class TestDraftTCPRoundtrip:
    """Full IPC roundtrip: TCP connect → session_start → draft_request → response."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.project_root = str(tmp_path)
        self.port = 19900
        self.server = _start_kettle_server(self.project_root, self.port)

        # Run server in background thread
        self._thread = threading.Thread(target=self.server.start, daemon=True)
        self._thread.start()
        time.sleep(0.3)  # let it bind
        yield
        self.server.running = False
        self._thread.join(timeout=3)

    def _write_file(self, name: str, content: str) -> str:
        path = os.path.join(self.project_root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_full_tcp_draft_roundtrip(self):
        """Connect, start session, request draft, validate response — all over TCP."""
        content = (
            'from typing import List\n\n'
            'def flatten(nested: List[List[int]]) -> List[int]:\n'
            '    """Flatten a list of lists into a single list."""\n'
        )
        path = self._write_file("flatten.py", content)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        try:
            # 1) session_start
            resp = _send_and_recv(sock, {
                "type": "session_start",
                "payload": {"project_root": self.project_root, "cwd": self.project_root},
            }, timeout=10)
            assert resp["type"] == "session_start_ack"
            print(f"\n[TCP] session started: {resp}")

            # 2) draft_request
            resp = _send_and_recv(sock, {
                "type": "draft_request",
                "payload": {"file_path": path, "cursor_offset": len(content)},
            }, timeout=300)

            assert resp["type"] == "draft_response"
            payload = resp.get("payload", resp)
            assert "success" in payload
            assert "draft_text" in payload
            assert "confidence" in payload
            print(f"[TCP] draft response: success={payload['success']} "
                  f"confidence={payload['confidence']:.2f} "
                  f"draft_len={len(payload['draft_text'])} "
                  f"findings={payload.get('findings_count', '?')} "
                  f"drafter={payload.get('drafter_ms', '?')}ms "
                  f"validator={payload.get('validator_ms', '?')}ms")
            if payload.get("draft_text"):
                print(f"[TCP] draft: {payload['draft_text'][:200]!r}")

        finally:
            sock.close()
