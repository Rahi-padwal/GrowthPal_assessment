const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const selectBtn = document.getElementById("select-btn");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

selectBtn.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (file) processFile(file);
});

["dragenter", "dragover"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("dropzone-active");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dropzone-active");
  });
});

dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) processFile(file);
});

async function processFile(file) {
  statusEl.textContent = "Processing…";
  statusEl.className = "";
  resultsEl.hidden = true;
  selectBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/process", {
      method: "POST",
      body: formData,
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || "Something went wrong.");
    }

    renderResults(data);
    statusEl.textContent = "";
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.className = "error";
  } finally {
    selectBtn.disabled = false;
    fileInput.value = "";
  }
}

function renderResults(data) {
  document.getElementById("redacted-text").textContent = data.redactedText;
  document.getElementById("summary").textContent = data.summary;

  const keyPointsEl = document.getElementById("key-points");
  keyPointsEl.innerHTML = "";
  data.keyPoints.forEach((point) => {
    const li = document.createElement("li");
    li.textContent = point;
    keyPointsEl.appendChild(li);
  });

  const tbody = document.querySelector("#audit-table tbody");
  tbody.innerHTML = "";
  data.auditLog.forEach((entry) => {
    const row = document.createElement("tr");
    if (entry.status === "not_found") {
      row.classList.add("row-warning");
    } else if (entry.status === "excluded") {
      row.classList.add("row-excluded");
    }
    row.innerHTML = `
      <td><span class="tag">${escapeHtml(entry.type)}</span></td>
      <td>${escapeHtml(entry.originalText)}</td>
      <td><code>${escapeHtml(entry.placeholder)}</code></td>
      <td>${escapeHtml(entry.reason)}</td>
    `;
    tbody.appendChild(row);
  });

  document.getElementById("audit-count").textContent = `${data.auditLog.length} flagged`;
  resultsEl.hidden = false;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}
