import pytest
import json
import socket
import threading
import time


class TestIPCRoundtrip:
    """
    Tests the IPC message format roundtrip without requiring
    the full kettle_server — uses a minimal echo server.
    """

    def _start_echo_server(self, port):
        """Start a minimal server that echoes back an ack for any message."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        srv.settimeout(5)

        def serve():
            try:
                conn, _ = srv.accept()
                conn.settimeout(5)
                buf = b""
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line)
                        if msg.get("type") == "session_start":
                            ack = {"type": "session_start_ack", "session_id": 1, "total_symbols": 5}
                        elif msg.get("type") == "file_saved":
                            ack = {"type": "file_saved_ack", "total_symbols": 10}
                        else:
                            ack = {"type": "ack"}
                        conn.sendall((json.dumps(ack) + "\n").encode())
                conn.close()
            except Exception:
                pass
            finally:
                srv.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return t

    def test_session_start_roundtrip(self):
        port = 19876
        self._start_echo_server(port)
        time.sleep(0.1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        sock.settimeout(5)

        msg = {"type": "session_start", "payload": {"project_root": "/test", "cwd": "/test"}}
        sock.sendall((json.dumps(msg) + "\n").encode())

        resp = b""
        while b"\n" not in resp:
            resp += sock.recv(4096)
        parsed = json.loads(resp.strip())

        assert parsed["type"] == "session_start_ack"
        assert "session_id" in parsed
        assert "total_symbols" in parsed
        sock.close()

    def test_file_saved_roundtrip(self):
        port = 19877
        self._start_echo_server(port)
        time.sleep(0.1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        sock.settimeout(5)

        # Send session_start first
        msg = {"type": "session_start", "payload": {"project_root": "/test", "cwd": "/test"}}
        sock.sendall((json.dumps(msg) + "\n").encode())
        resp = b""
        while b"\n" not in resp:
            resp += sock.recv(4096)

        # Now send file_saved
        msg = {"type": "file_saved", "payload": {"file_path": "/test/main.py"}}
        sock.sendall((json.dumps(msg) + "\n").encode())
        resp = b""
        while b"\n" not in resp:
            resp += sock.recv(4096)
        parsed = json.loads(resp.strip())

        assert parsed["type"] == "file_saved_ack"
        assert "total_symbols" in parsed
        sock.close()

    def test_message_newline_termination(self):
        """Verify that messages are properly newline-terminated."""
        msg = {"type": "test", "payload": {"key": "value"}}
        wire = json.dumps(msg) + "\n"
        # Must end with exactly one newline
        assert wire.endswith("\n")
        assert not wire.endswith("\n\n")
        # The JSON portion must not contain newlines
        assert "\n" not in wire[:-1]
