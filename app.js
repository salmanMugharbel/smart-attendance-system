const $ = (selector) => document.querySelector(selector);
const courseSelect = $("#course-select");
const studentSelect = $("#student-select");
const sessionForm = $("#session-form");
const attendanceForm = $("#attendance-form");
const sessionsBody = $("#sessions-body");
const recordsBody = $("#records-body");
const toast = $("#toast");
const qrDialog = $("#qr-dialog");
let toastTimeout;
let scanStream;
let scanTimer;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  window.clearTimeout(toastTimeout);
  toastTimeout = window.setTimeout(() => toast.classList.remove("visible"), 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(data?.error || `Request failed (${response.status}).`);
  return data;
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }).format(new Date(value));
}

function sessionStatus(session) {
  if (session.closed) return { label: "Closed", className: "status-closed" };
  if (Date.now() >= new Date(session.expires_at).getTime()) {
    return { label: "Expired", className: "status-expired" };
  }
  return { label: "Open", className: "status-open" };
}

async function loadDashboard() {
  const data = await api("/api/bootstrap");
  courseSelect.innerHTML = data.courses.map((course) =>
    `<option value="${escapeHtml(course.id)}">${escapeHtml(course.id)} · ${escapeHtml(course.name)}</option>`
  ).join("");
  studentSelect.innerHTML = data.students.map((student) =>
    `<option value="${escapeHtml(student.id)}">${escapeHtml(student.name)} · ${escapeHtml(student.id)}</option>`
  ).join("");
  $("#course-count").textContent = data.courses.length;
  $("#session-count").textContent = data.sessions.length;
  await renderSessions(data.sessions);
  await loadRecords();
}

async function renderSessions(sessions) {
  if (!sessions.length) {
    sessionsBody.innerHTML = '<tr><td colspan="5" class="empty-cell">No sessions yet. Start one above.</td></tr>';
    return;
  }
  sessionsBody.innerHTML = sessions.map((session) => {
    const status = sessionStatus(session);
    return `<tr>
      <td><span class="course-cell">${escapeHtml(session.course_name)}</span><span class="cell-sub">${escapeHtml(session.course_id)}</span></td>
      <td>${formatDate(session.created_at)}</td>
      <td><span class="status-pill ${status.className}">${status.label}</span></td>
      <td>${session.attendance_count} ${session.attendance_count === 1 ? "student" : "students"}</td>
      <td><button class="action-button view-session" data-id="${escapeHtml(session.id)}" type="button">View QR</button></td>
    </tr>`;
  }).join("");
}

async function loadRecords() {
  const data = await api("/api/attendance");
  $("#attendance-count").textContent = data.records.length;
  if (!data.records.length) {
    recordsBody.innerHTML = '<tr><td colspan="4" class="empty-cell">No attendance records yet.</td></tr>';
    return;
  }
  recordsBody.innerHTML = data.records.map((record) => `<tr>
    <td><span class="course-cell">${escapeHtml(record.student_name)}</span><span class="cell-sub">${escapeHtml(record.student_id)}</span></td>
    <td>${escapeHtml(record.course_name)}<span class="cell-sub">${escapeHtml(record.course_id)}</span></td>
    <td>${formatDate(record.recorded_at)}</td>
    <td><button class="action-button view-session" data-id="${escapeHtml(record.session_id)}" type="button">View session</button></td>
  </tr>`).join("");
}

async function refresh() {
  try {
    await loadDashboard();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function openSession(sessionId) {
  try {
    const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    $("#qr-course-name").textContent = session.course_name;
    $("#qr-image").src = `/api/sessions/${encodeURIComponent(session.id)}/qr.svg`;
    $("#qr-code-text").textContent = session.code;
    $("#qr-expiry").textContent = session.closed
      ? "This session has been closed."
      : `Session ends ${formatDate(session.expires_at)}.`;
    qrDialog.showModal();
  } catch (error) {
    showToast(error.message, true);
  }
}

sessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = sessionForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const session = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ course_id: courseSelect.value }),
    });
    await refresh();
    await openSession(session.id);
    showToast("Attendance session started.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

attendanceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = attendanceForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const result = await api("/api/attendance", {
      method: "POST",
      body: JSON.stringify({
        student_id: studentSelect.value,
        code: $("#session-code").value.trim().toUpperCase(),
      }),
    });
    $("#session-code").value = "";
    await refresh();
    showToast(`${result.message} Welcome, ${result.student_name}.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest(".view-session");
  if (button) openSession(button.dataset.id);
});

$("#refresh-button").addEventListener("click", refresh);
$("#close-dialog").addEventListener("click", () => qrDialog.close());

$("#copy-code").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#qr-code-text").textContent);
    showToast("Session code copied.");
  } catch {
    showToast("Could not access the clipboard; copy the code manually.", true);
  }
});

function stopScanner() {
  window.clearInterval(scanTimer);
  scanTimer = undefined;
  if (scanStream) {
    scanStream.getTracks().forEach((track) => track.stop());
    scanStream = undefined;
  }
  $("#scanner-video").srcObject = null;
  $("#scanner-box").hidden = true;
}

$("#stop-scan").addEventListener("click", stopScanner);
$("#scan-button").addEventListener("click", async () => {
  if (!("BarcodeDetector" in window)) {
    showToast("QR scanning is unavailable in this browser. Enter the six-character code instead.", true);
    return;
  }
  try {
    const detector = new BarcodeDetector({ formats: ["qr_code"] });
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    const video = $("#scanner-video");
    video.srcObject = scanStream;
    await video.play();
    $("#scanner-box").hidden = false;
    scanTimer = window.setInterval(async () => {
      if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
      try {
        const codes = await detector.detect(video);
        if (!codes.length) return;
        const match = /^ATTENDANCE\|[^|]+\|([A-F0-9]{6})$/.exec(codes[0].rawValue);
        if (!match) {
          showToast("This QR code is not an attendance session code.", true);
          return;
        }
        $("#session-code").value = match[1];
        stopScanner();
        showToast("QR code scanned. Select your profile and submit attendance.");
      } catch (error) {
        stopScanner();
        showToast(`Could not read the QR code: ${error.message}`, true);
      }
    }, 350);
  } catch (error) {
    stopScanner();
    showToast(`Camera access failed: ${error.message}. You can enter the code manually.`, true);
  }
});

$("#today-label").textContent = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "short", day: "numeric", year: "numeric",
}).format(new Date());

loadDashboard().catch((error) => {
  showToast(error.message, true);
  sessionsBody.innerHTML = '<tr><td colspan="5" class="empty-cell">Could not load sessions. Check that the server is running.</td></tr>';
  recordsBody.innerHTML = '<tr><td colspan="4" class="empty-cell">Could not load attendance records.</td></tr>';
});
