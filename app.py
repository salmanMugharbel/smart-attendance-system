from __future__ import annotations

import json
import mimetypes
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import qrcode
import qrcode.image.svg


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "attendance.db"
HOST = "127.0.0.1"
PORT = 8000
SESSION_MINUTES = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS courses (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS enrollments (
                student_id TEXT NOT NULL REFERENCES students(id),
                course_id TEXT NOT NULL REFERENCES courses(id),
                PRIMARY KEY (student_id, course_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL REFERENCES courses(id),
                code TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                closed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                student_id TEXT NOT NULL REFERENCES students(id),
                recorded_at TEXT NOT NULL,
                UNIQUE (session_id, student_id)
            );
            """
        )

        if db.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 0:
            courses = [
                ("IT201", "Database Systems"),
                ("IT205", "Systems Analysis"),
                ("IT210", "Computer Networks"),
            ]
            students = [
                ("S1001", "Amina Yusuf"),
                ("S1002", "Omar Hassan"),
                ("S1003", "Sara Ali"),
                ("S1004", "Khalid Noor"),
                ("S1005", "Lina Ahmed"),
                ("S1006", "Yusuf Karim"),
            ]
            db.executemany("INSERT INTO courses (id, name) VALUES (?, ?)", courses)
            db.executemany("INSERT INTO students (id, name) VALUES (?, ?)", students)
            db.executemany(
                "INSERT INTO enrollments (student_id, course_id) VALUES (?, ?)",
                [
                    (student_id, course_id)
                    for course_id in ("IT201", "IT205", "IT210")
                    for student_id, _ in students
                ],
            )


def as_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class AttendanceHandler(BaseHTTPRequestHandler):
    server_version = "AttendanceDemo/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json(status, {"error": message})

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("Request body is missing or too large.")
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Request must contain valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request JSON must be an object.")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/bootstrap":
                self.get_bootstrap()
            elif path == "/api/attendance":
                self.get_attendance(parse_qs(parsed.query))
            elif path.startswith("/api/sessions/") and path.endswith("/qr.svg"):
                session_id = path.removeprefix("/api/sessions/").removesuffix("/qr.svg")
                self.get_qr(session_id)
            elif path.startswith("/api/sessions/"):
                session_id = path.removeprefix("/api/sessions/")
                self.get_session(session_id)
            elif path.startswith("/api/"):
                self.send_error_json(404, "API endpoint not found.")
            else:
                self.serve_static(path)
        except sqlite3.Error as exc:
            print(f"Database error: {exc}")
            self.send_error_json(500, "The database request could not be completed.")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/sessions":
                self.create_session(payload)
            elif path == "/api/attendance":
                self.record_attendance(payload)
            elif path.startswith("/api/sessions/") and path.endswith("/close"):
                session_id = path.removeprefix("/api/sessions/").removesuffix("/close")
                self.close_session(session_id)
            else:
                self.send_error_json(404, "API endpoint not found.")
        except ValueError as exc:
            self.send_error_json(400, str(exc))
        except sqlite3.Error as exc:
            print(f"Database error: {exc}")
            self.send_error_json(500, "The database request could not be completed.")

    def get_bootstrap(self) -> None:
        with connect_db() as db:
            courses = db.execute("SELECT id, name FROM courses ORDER BY id").fetchall()
            students = db.execute("SELECT id, name FROM students ORDER BY name").fetchall()
            sessions = db.execute(
                """
                SELECT s.id, s.course_id, c.name AS course_name, s.created_at,
                       s.expires_at, s.closed,
                       COUNT(a.id) AS attendance_count
                FROM sessions s
                JOIN courses c ON c.id = s.course_id
                LEFT JOIN attendance a ON a.session_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                LIMIT 20
                """
            ).fetchall()
        self.send_json(
            200,
            {
                "courses": [dict(row) for row in courses],
                "students": [dict(row) for row in students],
                "sessions": [dict(row) for row in sessions],
                "session_minutes": SESSION_MINUTES,
            },
        )

    def create_session(self, payload: dict) -> None:
        course_id = payload.get("course_id")
        if not isinstance(course_id, str) or not course_id.strip():
            raise ValueError("Choose a course before starting a session.")

        created_at = utc_now()
        session_id = secrets.token_urlsafe(12)
        expires_at = created_at + timedelta(minutes=SESSION_MINUTES)
        with connect_db() as db:
            course = db.execute(
                "SELECT id, name FROM courses WHERE id = ?", (course_id,)
            ).fetchone()
            if course is None:
                self.send_error_json(404, "The selected course was not found.")
                return

            code = secrets.token_hex(3).upper()
            while db.execute("SELECT 1 FROM sessions WHERE code = ?", (code,)).fetchone():
                code = secrets.token_hex(3).upper()
            db.execute(
                """
                INSERT INTO sessions (id, course_id, code, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    course_id,
                    code,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        self.send_json(
            201,
            {
                "id": session_id,
                "course_id": course_id,
                "course_name": course["name"],
                "code": code,
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "qr_payload": f"ATTENDANCE|{session_id}|{code}",
            },
        )

    def record_attendance(self, payload: dict) -> None:
        student_id = payload.get("student_id")
        code = payload.get("code")
        if not isinstance(student_id, str) or not student_id.strip():
            raise ValueError("Select a student before submitting attendance.")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("Scan the session QR code or enter its six-character code.")

        with connect_db() as db:
            student = db.execute(
                "SELECT id, name FROM students WHERE id = ?", (student_id,)
            ).fetchone()
            if student is None:
                self.send_error_json(404, "The selected student was not found.")
                return
            session = db.execute(
                "SELECT * FROM sessions WHERE code = ?", (code.strip().upper(),)
            ).fetchone()
            if session is None:
                self.send_error_json(404, "That attendance code is not recognized.")
                return
            if session["closed"]:
                self.send_error_json(409, "This attendance session has been closed.")
                return
            if utc_now() >= datetime.fromisoformat(session["expires_at"]):
                self.send_error_json(410, "This attendance session has expired.")
                return
            enrolled = db.execute(
                "SELECT 1 FROM enrollments WHERE student_id = ? AND course_id = ?",
                (student_id, session["course_id"]),
            ).fetchone()
            if enrolled is None:
                self.send_error_json(
                    403, "This student is not enrolled in the session's course."
                )
                return
            try:
                cursor = db.execute(
                    """
                    INSERT INTO attendance (session_id, student_id, recorded_at)
                    VALUES (?, ?, ?)
                    """,
                    (session["id"], student_id, utc_now().isoformat()),
                )
            except sqlite3.IntegrityError:
                self.send_error_json(
                    409, "Attendance has already been recorded for this student."
                )
                return
            recorded = db.execute(
                "SELECT recorded_at FROM attendance WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()

        self.send_json(
            201,
            {
                "message": "Attendance recorded successfully.",
                "student_name": student["name"],
                "course_id": session["course_id"],
                "recorded_at": recorded["recorded_at"],
            },
        )

    def get_attendance(self, query: dict[str, list[str]]) -> None:
        session_id = query.get("session_id", [""])[0]
        with connect_db() as db:
            if session_id:
                session = db.execute(
                    """
                    SELECT s.id, s.course_id, c.name AS course_name, s.created_at,
                           s.expires_at, s.closed, COUNT(a.id) AS attendance_count
                    FROM sessions s
                    JOIN courses c ON c.id = s.course_id
                    LEFT JOIN attendance a ON a.session_id = s.id
                    WHERE s.id = ?
                    GROUP BY s.id
                    """,
                    (session_id,),
                ).fetchone()
                if session is None:
                    self.send_error_json(404, "Attendance session was not found.")
                    return
                rows = db.execute(
                    """
                    SELECT a.recorded_at, st.id AS student_id, st.name AS student_name
                    FROM attendance a
                    JOIN students st ON st.id = a.student_id
                    WHERE a.session_id = ?
                    ORDER BY a.recorded_at DESC
                    """,
                    (session_id,),
                ).fetchall()
                self.send_json(
                    200,
                    {"session": dict(session), "records": [dict(row) for row in rows]},
                )
                return

            rows = db.execute(
                """
                SELECT a.recorded_at, st.id AS student_id, st.name AS student_name,
                       s.id AS session_id, c.id AS course_id, c.name AS course_name
                FROM attendance a
                JOIN students st ON st.id = a.student_id
                JOIN sessions s ON s.id = a.session_id
                JOIN courses c ON c.id = s.course_id
                ORDER BY a.recorded_at DESC
                LIMIT 100
                """
            ).fetchall()
        self.send_json(200, {"records": [dict(row) for row in rows]})

    def get_session(self, session_id: str) -> None:
        with connect_db() as db:
            row = db.execute(
                """
                SELECT s.id, s.course_id, c.name AS course_name, s.code,
                       s.created_at, s.expires_at, s.closed,
                       COUNT(a.id) AS attendance_count
                FROM sessions s
                JOIN courses c ON c.id = s.course_id
                LEFT JOIN attendance a ON a.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            self.send_error_json(404, "Attendance session was not found.")
            return
        self.send_json(200, dict(row))

    def get_qr(self, session_id: str) -> None:
        with connect_db() as db:
            session = db.execute(
                "SELECT id, code FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if session is None:
            self.send_error_json(404, "Attendance session was not found.")
            return
        qr = qrcode.make(
            f"ATTENDANCE|{session['id']}|{session['code']}",
            image_factory=qrcode.image.svg.SvgPathImage,
            box_size=8,
            border=3,
        )
        body = qr.to_string()
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def close_session(self, session_id: str) -> None:
        with connect_db() as db:
            cursor = db.execute(
                "UPDATE sessions SET closed = 1 WHERE id = ?", (session_id,)
            )
            if cursor.rowcount == 0:
                self.send_error_json(404, "Attendance session was not found.")
                return
        self.send_json(200, {"message": "Attendance session closed."})

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        target = (ROOT / relative).resolve()
        if ROOT not in target.parents and target != ROOT:
            self.send_error_json(403, "File access is not allowed.")
            return
        if not target.is_file():
            self.send_error_json(404, "File not found.")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), AttendanceHandler)
    print(f"Smart Attendance System running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
