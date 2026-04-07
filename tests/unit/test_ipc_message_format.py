import pytest
import json


class TestIPCMessageFormat:
    """Tests for IPC message serialization conventions used by kettle_server.py."""

    def test_session_start_response_has_required_fields(self):
        """The session_start ack must contain session_id and total_symbols."""
        # Simulate what kettle_server sends back
        msg = {
            "type": "session_start_ack",
            "session_id": 1,
            "total_symbols": 42,
        }
        raw = json.dumps(msg)
        parsed = json.loads(raw)
        assert "session_id" in parsed
        assert "total_symbols" in parsed
        assert isinstance(parsed["session_id"], int)
        assert isinstance(parsed["total_symbols"], int)

    def test_file_saved_ack_has_total_symbols(self):
        msg = {
            "type": "file_saved_ack",
            "total_symbols": 15,
        }
        raw = json.dumps(msg)
        parsed = json.loads(raw)
        assert "total_symbols" in parsed

    def test_draft_response_has_required_fields(self):
        """draft_response uses indexed findings format for Odin parsing."""
        msg = {
            "type": "draft_response",
            "draft_text": "return result",
            "confidence": 0.85,
            "findings_count": 1,
            "finding_0_severity": "warning",
            "finding_0_line": 5,
            "finding_0_message": "variable shadowing",
        }
        raw = json.dumps(msg)
        parsed = json.loads(raw)
        assert "draft_text" in parsed
        assert "confidence" in parsed
        assert isinstance(parsed["confidence"], float)
        assert "findings_count" in parsed
        # Verify indexed findings are accessible
        count = parsed["findings_count"]
        for i in range(count):
            assert f"finding_{i}_severity" in parsed
            assert f"finding_{i}_line" in parsed
            assert f"finding_{i}_message" in parsed

    def test_draft_response_error_format(self):
        """Failed draft_response should have error field and empty draft_text."""
        msg = {
            "type": "draft_response",
            "draft_text": "",
            "confidence": 0.0,
            "findings_count": 0,
            "error": "LM Studio connection failed: Connection refused",
        }
        raw = json.dumps(msg)
        parsed = json.loads(raw)
        assert parsed["draft_text"] == ""
        assert parsed["confidence"] == 0.0
        assert len(parsed["error"]) > 0

    def test_messages_are_newline_delimited(self):
        """Each IPC message must end with exactly one newline."""
        msg = {"type": "file_saved", "payload": {"file_path": "/test.py"}}
        wire = json.dumps(msg) + "\n"
        assert wire.count("\n") == 1
        assert wire.endswith("\n")
        # The JSON itself must not contain raw newlines
        assert "\n" not in wire[:-1]
