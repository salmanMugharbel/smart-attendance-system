# QR-Code Smart Attendance System

A small university mini-project prototype with a Python HTTP server, SQLite database, lecturer dashboard, student attendance form, QR-code display, and recent attendance reports. It uses sample student profiles and is intended for a local demonstration, not production use.

## Requirements

- Python 3.10 or newer
- The `qrcode` package listed in `requirements.txt`
- A modern browser; camera scanning requires a browser that supports `BarcodeDetector` and camera access. Manual code entry is also available.

## Run on Windows

From this folder, create and activate a virtual environment, install the dependency, and start the server:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If PowerShell blocks virtual-environment activation, run the commands without activation using:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:8000>. The first run creates `data/attendance.db` and adds sample course and student records. Stop the server with Ctrl+C.

## Demo workflow

1. In **Start attendance**, select a course and start a session.
2. Display the resulting QR code to students. The six-character code is a fallback for browsers without QR scanning.
3. In **Mark your attendance**, select a sample student. Scan the QR code or enter its code, then submit.
4. Review the updated attendance counts and recent records.

The backend accepts attendance only for an existing, open, unexpired session and an enrolled student. It prevents duplicate records for one student in a session. Sessions expire after 15 minutes.

## Architecture

```text
[1. Presentation Layer]
Lecturer dashboard | Student interface | Attendance/report views
          ⇅ HTTP requests and responses
[2. Application / Processing Layer]
Python request handler | Session/enrollment checks | Attendance/report logic
          ⇅ validated reads and writes
[3. Data Layer]
SQLite: courses | students | enrollments | sessions | attendance
          ⇅
[4. Infrastructure / Device Layer]
Classroom display | Student phone/camera | Browser and local server
```

QR submissions are processed immediately. Summary/report retrieval happens when requested; larger exports could be batch work in a future version. Dedicated edge computing is not included. The response times in the accompanying project description are design targets, not measured results.

## Important limitations

- This prototype has no login, lecturer authorization, network deployment, or student self-registration.
- Sample identities are selectable by anyone using the demo. Do not treat these records as verified attendance.
- Camera QR scanning depends on browser support for `BarcodeDetector`. Entering the displayed session code is the fallback.
- The sample server is for local academic demonstration, not a secure production service.
