"""
Accept rate tracking tests.

We designed the accept rate — accepted drafts divided by total draft
requests — as the primary longitudinal metric for whether the system
is improving. This is more honest than composite quality scores because
it measures your actual behavior: did you use what the system produced?

These tests verify:
  1. Accept and dismiss events are correctly written to the event log
  2. Accept rate computation from the database is correct
  3. The rate is queryable per session, per context, and over time
  4. A rising accept rate over sessions is detectable from the data

The accept rate does not use LLM calls — it reads from the event log.
"""
import json
import sqlite3
import pytest
import time


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_db():
    """In-memory database with sessions and events schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at   TEXT,
            file_scope  TEXT DEFAULT '[]',
            event_count INTEGER NOT NULL DEFAULT 0,
            summary     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            timestamp  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
            event_type TEXT    NOT NULL,
            file_path  TEXT,
            payload    TEXT    DEFAULT '{}'
        )
    """)
    conn.commit()
    return conn


def new_session(conn: sqlite3.Connection) -> int:
    sid = conn.execute(
        "INSERT INTO sessions (started_at) VALUES (datetime('now'))"
    ).lastrowid
    conn.commit()
    return sid


def log_event(conn: sqlite3.Connection, session_id: int,
              event_type: str, file_path: str = "test.py",
              payload: dict = None) -> int:
    eid = conn.execute(
        "INSERT INTO events (session_id, timestamp, event_type, file_path, payload) "
        "VALUES (?, unixepoch('now', 'subsec'), ?, ?, ?)",
        (session_id, event_type, file_path,
         json.dumps(payload or {}))
    ).lastrowid
    conn.execute(
        "UPDATE sessions SET event_count = event_count + 1 WHERE id = ?",
        (session_id,)
    )
    conn.commit()
    return eid


def compute_accept_rate(conn: sqlite3.Connection,
                        session_id: int = None) -> float:
    """
    Compute accept rate for a session or globally.
    Returns accepted / (accepted + dismissed) or 0.0 if no drafts.
    """
    filter_clause = "WHERE session_id = ?" if session_id else ""
    params = (session_id,) if session_id else ()

    row = conn.execute(f"""
        SELECT
            COUNT(CASE WHEN event_type = 'draft_accept'  THEN 1 END) as accepts,
            COUNT(CASE WHEN event_type = 'draft_dismiss' THEN 1 END) as dismisses
        FROM events
        {filter_clause}
    """, params).fetchone()

    accepts, dismisses = row
    total = accepts + dismisses
    return accepts / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# Event logging correctness
# ---------------------------------------------------------------------------

class TestEventLogging:

    def test_draft_accept_logged(self, session_db):
        sid = new_session(session_db)
        log_event(session_db, sid, "draft_request", "module.py",
                  {"cursor_offset": 100})
        log_event(session_db, sid, "draft_accept",  "module.py",
                  {"confidence": 0.85})

        row = session_db.execute(
            "SELECT event_type FROM events WHERE event_type = 'draft_accept'"
        ).fetchone()
        assert row is not None

    def test_draft_dismiss_logged(self, session_db):
        sid = new_session(session_db)
        log_event(session_db, sid, "draft_request", "module.py",
                  {"cursor_offset": 50})
        log_event(session_db, sid, "draft_dismiss", "module.py",
                  {"reason": "user_preference"})

        row = session_db.execute(
            "SELECT event_type FROM events WHERE event_type = 'draft_dismiss'"
        ).fetchone()
        assert row is not None

    def test_event_count_increments(self, session_db):
        sid = new_session(session_db)
        for event_type in ["draft_request", "draft_accept",
                            "draft_request", "draft_dismiss"]:
            log_event(session_db, sid, event_type)

        count = session_db.execute(
            "SELECT event_count FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
        assert count == 4

    def test_payload_is_valid_json(self, session_db):
        sid = new_session(session_db)
        payload = {"cursor_offset": 42, "confidence": 0.91,
                   "model": "granite-4.0-tiny-h"}
        log_event(session_db, sid, "draft_accept", "test.py", payload)

        raw = session_db.execute(
            "SELECT payload FROM events WHERE event_type = 'draft_accept'"
        ).fetchone()[0]
        parsed = json.loads(raw)
        assert parsed["cursor_offset"] == 42
        assert parsed["confidence"] == 0.91

    def test_multiple_sessions_isolated(self, session_db):
        sid1 = new_session(session_db)
        sid2 = new_session(session_db)

        log_event(session_db, sid1, "draft_accept")
        log_event(session_db, sid1, "draft_accept")
        log_event(session_db, sid2, "draft_dismiss")

        s1_count = session_db.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?", (sid1,)
        ).fetchone()[0]
        s2_count = session_db.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?", (sid2,)
        ).fetchone()[0]

        assert s1_count == 2
        assert s2_count == 1


# ---------------------------------------------------------------------------
# Accept rate computation
# ---------------------------------------------------------------------------

class TestAcceptRateComputation:

    def test_all_accepts_gives_rate_1(self, session_db):
        sid = new_session(session_db)
        for _ in range(5):
            log_event(session_db, sid, "draft_request")
            log_event(session_db, sid, "draft_accept")

        rate = compute_accept_rate(session_db, session_id=sid)
        assert rate == 1.0

    def test_all_dismisses_gives_rate_0(self, session_db):
        sid = new_session(session_db)
        for _ in range(5):
            log_event(session_db, sid, "draft_request")
            log_event(session_db, sid, "draft_dismiss")

        rate = compute_accept_rate(session_db, session_id=sid)
        assert rate == 0.0

    def test_mixed_gives_correct_rate(self, session_db):
        sid = new_session(session_db)
        # 3 accepts, 2 dismisses -> 0.6
        for _ in range(3):
            log_event(session_db, sid, "draft_accept")
        for _ in range(2):
            log_event(session_db, sid, "draft_dismiss")

        rate = compute_accept_rate(session_db, session_id=sid)
        assert rate == pytest.approx(0.6)

    def test_no_drafts_gives_rate_0(self, session_db):
        sid = new_session(session_db)
        log_event(session_db, sid, "file_saved")
        log_event(session_db, sid, "file_saved")

        rate = compute_accept_rate(session_db, session_id=sid)
        assert rate == 0.0

    def test_global_rate_across_sessions(self, session_db):
        # Session 1: 2 accepts
        sid1 = new_session(session_db)
        log_event(session_db, sid1, "draft_accept")
        log_event(session_db, sid1, "draft_accept")

        # Session 2: 1 accept, 1 dismiss
        sid2 = new_session(session_db)
        log_event(session_db, sid2, "draft_accept")
        log_event(session_db, sid2, "draft_dismiss")

        # Global: 3 accepts, 1 dismiss -> 0.75
        global_rate = compute_accept_rate(session_db)
        assert global_rate == pytest.approx(0.75)

    def test_per_session_rate_isolated_from_global(self, session_db):
        sid1 = new_session(session_db)
        log_event(session_db, sid1, "draft_accept")
        log_event(session_db, sid1, "draft_accept")

        sid2 = new_session(session_db)
        log_event(session_db, sid2, "draft_dismiss")
        log_event(session_db, sid2, "draft_dismiss")

        rate1 = compute_accept_rate(session_db, session_id=sid1)
        rate2 = compute_accept_rate(session_db, session_id=sid2)

        assert rate1 == 1.0
        assert rate2 == 0.0


# ---------------------------------------------------------------------------
# Longitudinal trend detection
# ---------------------------------------------------------------------------

class TestLongitudinalTrend:
    """
    These tests verify that a rising accept rate over sessions
    is detectable from the database. This is the core claim we
    made: the system should produce measurable improvement over time.
    """

    def _build_session_history(self, conn: sqlite3.Connection,
                                accept_rates: list[float]) -> list[int]:
        """Build a sequence of sessions with the given accept rates."""
        session_ids = []
        for rate in accept_rates:
            sid = new_session(conn)
            n_total = 10
            n_accepts = round(rate * n_total)
            n_dismisses = n_total - n_accepts
            for _ in range(n_accepts):
                log_event(conn, sid, "draft_accept")
            for _ in range(n_dismisses):
                log_event(conn, sid, "draft_dismiss")
            session_ids.append(sid)
        return session_ids

    def test_rising_trend_is_detectable(self, session_db):
        """
        Sessions with improving accept rates should show a positive trend
        when queried from the database.
        """
        # Simulate improving accept rate: 0.3 -> 0.5 -> 0.7 -> 0.9
        rates = [0.3, 0.5, 0.7, 0.9]
        sids = self._build_session_history(session_db, rates)

        computed = [
            compute_accept_rate(session_db, session_id=sid)
            for sid in sids
        ]

        # Trend should be positive: later sessions have higher rates
        first_half  = sum(computed[:2]) / 2
        second_half = sum(computed[2:]) / 2
        assert second_half > first_half, (
            f"Expected rising trend. "
            f"First half mean: {first_half:.2f}, "
            f"Second half mean: {second_half:.2f}"
        )

    def test_stable_trend_is_detectable(self, session_db):
        """Stable accept rate should show near-zero trend."""
        rates = [0.7, 0.7, 0.7, 0.7, 0.7]
        sids = self._build_session_history(session_db, rates)

        computed = [
            compute_accept_rate(session_db, session_id=sid)
            for sid in sids
        ]

        first_half  = sum(computed[:2]) / 2
        second_half = sum(computed[2:3]) / 1
        delta = abs(second_half - first_half)
        assert delta < 0.2, (
            f"Expected stable trend but got delta={delta:.2f}"
        )

    def test_accept_rate_queryable_by_date_range(self, session_db):
        """
        The database must support querying accept rate within a date range.
        This is the query pattern used by the longitudinal dashboard.
        """
        sid = new_session(session_db)
        for _ in range(7):
            log_event(session_db, sid, "draft_accept")
        for _ in range(3):
            log_event(session_db, sid, "draft_dismiss")

        # Query rate for today
        row = session_db.execute("""
            SELECT
                date(timestamp, 'unixepoch') as day,
                COUNT(CASE WHEN event_type = 'draft_accept'  THEN 1 END) as accepts,
                COUNT(CASE WHEN event_type = 'draft_dismiss' THEN 1 END) as dismisses
            FROM events
            WHERE event_type IN ('draft_accept', 'draft_dismiss')
            GROUP BY day
            ORDER BY day DESC
            LIMIT 1
        """).fetchone()

        assert row is not None
        day, accepts, dismisses = row
        assert accepts == 7
        assert dismisses == 3
        rate = accepts / (accepts + dismisses)
        assert rate == pytest.approx(0.7)

    def test_per_file_accept_rate(self, session_db):
        """Accept rate should be queryable per file path."""
        sid = new_session(session_db)

        # module_a.py: 3/3 accepts
        for _ in range(3):
            log_event(session_db, sid, "draft_accept",  "module_a.py")

        # module_b.py: 1/3 accepts
        log_event(session_db, sid, "draft_accept",  "module_b.py")
        log_event(session_db, sid, "draft_dismiss", "module_b.py")
        log_event(session_db, sid, "draft_dismiss", "module_b.py")

        rows = session_db.execute("""
            SELECT
                file_path,
                COUNT(CASE WHEN event_type = 'draft_accept'  THEN 1 END) as accepts,
                COUNT(CASE WHEN event_type = 'draft_dismiss' THEN 1 END) as dismisses
            FROM events
            WHERE event_type IN ('draft_accept', 'draft_dismiss')
            GROUP BY file_path
            ORDER BY file_path
        """).fetchall()

        by_file = {r[0]: r[1] / (r[1] + r[2]) for r in rows}
        assert by_file.get("module_a.py", 0) == pytest.approx(1.0)
        assert by_file.get("module_b.py", 0) == pytest.approx(1/3)
