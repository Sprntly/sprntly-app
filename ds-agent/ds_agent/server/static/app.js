/* Sprntly DS-Agent UI — vanilla.
 *
 * Auth: bearer token in localStorage.
 * Chat: each assistant turn can include zero or more code-execution
 * bundles (code + stdout + stderr + chart images). They render as
 * collapsible <details> blocks inside the assistant message.
 */

const API = {
  base: window.location.pathname.startsWith("/agent") ? "/agent/api" : "/api",
};

const TOKEN_KEY = "sprntly_agent_token";
function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() { localStorage.removeItem(TOKEN_KEY); }

const $ = (sel) => document.querySelector(sel);

const loginScreen = $("#login-screen");
const chatScreen = $("#chat-screen");
const loginForm = $("#login-form");
const loginPwd = $("#login-password");
const loginErr = $("#login-error");
const loginSubmit = $("#login-submit");

const picker = $("#picker");
const chatPane = $("#chat-pane");
const samplesList = $("#samples-list");
const fileInput = $("#file-input");
const datasetPill = $("#dataset-label");
const resetBtn = $("#reset-btn");
const logoutBtn = $("#logout-btn");

const messagesEl = $("#messages");
const chatForm = $("#chat-form");
const chatInput = $("#chat-input");
const chatSubmit = $("#chat-submit");
const chatStatus = $("#chat-status");


function authHeaders() {
  const t = getToken();
  return t ? { Authorization: "Bearer " + t } : {};
}

async function api(path, opts = {}) {
  const baseHeaders = opts.body instanceof FormData
    ? { ...authHeaders() }
    : { "Content-Type": "application/json", ...authHeaders() };
  const res = await fetch(API.base + path, {
    headers: { ...baseHeaders, ...(opts.headers || {}) },
    ...opts,
  });
  if (res.status === 401) {
    clearToken();
    return { _unauthenticated: true };
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  if (res.status === 204) return {};
  return res.json();
}


// ───── auth flow ─────

async function checkSession() {
  if (!getToken()) {
    showLogin();
    return;
  }
  const resp = await api("/session", { method: "GET" });
  if (resp._unauthenticated) {
    showLogin();
    return;
  }
  await enterChat();
}

function showLogin() {
  chatScreen.hidden = true;
  loginScreen.hidden = false;
  loginPwd.focus();
}

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  loginErr.hidden = true;
  loginSubmit.disabled = true;
  loginSubmit.textContent = "Signing in…";
  try {
    const res = await fetch(API.base + "/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: loginPwd.value }),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || "Sign-in failed.");
    }
    const body = await res.json();
    if (!body.token) throw new Error("Server didn't return a session token.");
    setToken(body.token);
    await enterChat();
  } catch (err) {
    loginErr.textContent = err.message === "invalid_password" ? "Wrong password." : err.message;
    loginErr.hidden = false;
  } finally {
    loginSubmit.disabled = false;
    loginSubmit.textContent = "Sign in →";
  }
});

logoutBtn.addEventListener("click", async () => {
  try { await api("/logout", { method: "POST" }); } catch (e) {}
  clearToken();
  loginPwd.value = "";
  messagesEl.innerHTML = "";
  showLogin();
});


// ───── chat shell ─────

async function enterChat() {
  loginScreen.hidden = true;
  chatScreen.hidden = false;
  await refreshState();
}

async function refreshState() {
  const state = await api("/state", { method: "GET" });
  if (state._unauthenticated) { showLogin(); return; }
  if (!state.has_dataset) {
    picker.hidden = false;
    chatPane.hidden = true;
    datasetPill.textContent = "";
    await loadSamples();
  } else {
    picker.hidden = true;
    chatPane.hidden = false;
    datasetPill.textContent = state.dataset_label || "dataset loaded";
    messagesEl.innerHTML = "";
    for (const m of state.messages || []) renderMessage(m.role, m.text);
    scrollToBottom();
    chatInput.focus();
  }
}


// ───── dataset picker ─────

async function loadSamples() {
  samplesList.innerHTML = "";
  const { samples } = await api("/samples", { method: "GET" });
  for (const s of samples) {
    const row = document.createElement("div");
    row.className = "sample-item";
    row.innerHTML = `
      <div class="info">
        <div class="name"></div>
        <div class="desc"></div>
      </div>
      <button class="sample-pick" type="button">Try it</button>
    `;
    row.querySelector(".name").textContent = s.label;
    row.querySelector(".desc").textContent = s.description;
    row.querySelector(".sample-pick").addEventListener("click", async () => {
      row.querySelector(".sample-pick").textContent = "Loading…";
      await api("/load-sample", { method: "POST", body: JSON.stringify({ sample_id: s.id }) });
      await refreshState();
      autopilotKickoff();
    });
    samplesList.appendChild(row);
  }
}

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  setChatStatus("Uploading…");
  try {
    const res = await fetch(API.base + "/upload", {
      method: "POST",
      headers: authHeaders(),
      body: fd,
    });
    if (res.status === 401) {
      clearToken();
      showLogin();
      return;
    }
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || "Upload failed.");
    }
    setChatStatus(null);
    await refreshState();
    autopilotKickoff();
  } catch (err) {
    setChatStatus("Upload error: " + err.message, true);
  }
});

resetBtn.addEventListener("click", async () => {
  if (!confirm("Reset the session? This clears the loaded dataset and chat history.")) return;
  await api("/reset", { method: "POST" });
  await refreshState();
});


// ───── chat rendering ─────

function renderMessage(role, text, codeExecutions = []) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  if (text) {
    const t = document.createElement("div");
    t.className = "msg-text";
    t.textContent = text;
    el.appendChild(t);
  }
  for (const ce of codeExecutions || []) {
    el.appendChild(renderCodeBundle(ce));
  }
  messagesEl.appendChild(el);
}

function renderCodeBundle(ce) {
  const wrap = document.createElement("details");
  wrap.className = "code-bundle";

  const summary = document.createElement("summary");
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = summaryLabel(ce);
  summary.appendChild(label);

  if (ce.error_code) {
    const badge = document.createElement("span");
    badge.className = "badge error";
    badge.textContent = ce.error_code;
    summary.appendChild(badge);
  } else if (typeof ce.return_code === "number" && ce.return_code !== 0) {
    const badge = document.createElement("span");
    badge.className = "badge error";
    badge.textContent = `exit ${ce.return_code}`;
    summary.appendChild(badge);
  }

  wrap.appendChild(summary);

  if (ce.code) {
    const pre = document.createElement("pre");
    pre.className = "code-src";
    pre.textContent = ce.code;
    wrap.appendChild(pre);
  }
  if (ce.stdout) {
    const pre = document.createElement("pre");
    pre.className = "code-stdout";
    pre.textContent = ce.stdout;
    wrap.appendChild(pre);
  }
  if (ce.stderr) {
    const pre = document.createElement("pre");
    pre.className = "code-stderr";
    pre.textContent = ce.stderr;
    wrap.appendChild(pre);
  }
  if (ce.file_ids && ce.file_ids.length) {
    const charts = document.createElement("div");
    charts.className = "code-charts";
    for (const fid of ce.file_ids) {
      const img = document.createElement("img");
      img.alt = "generated artifact";
      // <img> can't carry the Authorization header, so fetch as blob.
      loadAuthedImage(fid).then((url) => { if (url) img.src = url; });
      charts.appendChild(img);
    }
    wrap.appendChild(charts);
  }
  return wrap;
}

async function loadAuthedImage(fileId) {
  try {
    const res = await fetch(API.base + "/files/" + encodeURIComponent(fileId), {
      headers: authHeaders(),
    });
    if (!res.ok) return null;
    return URL.createObjectURL(await res.blob());
  } catch (e) {
    return null;
  }
}

function summaryLabel(ce) {
  if (ce.error_code) return "Sandbox error";
  const lines = (ce.code || "").split("\n");
  const firstNonEmpty = lines.find((l) => l.trim());
  if (firstNonEmpty) {
    return "Ran: " + firstNonEmpty.trim().slice(0, 80);
  }
  return "Tool call";
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setChatStatus(text, isError = false) {
  if (!text) { chatStatus.hidden = true; return; }
  chatStatus.textContent = text;
  chatStatus.hidden = false;
  chatStatus.classList.toggle("error", isError);
}

async function sendMessage(text) {
  renderMessage("user", text);
  scrollToBottom();
  chatInput.value = "";
  chatInput.disabled = true;
  chatSubmit.disabled = true;
  setChatStatus("Agent is thinking…");
  try {
    const resp = await api("/chat", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    if (resp._unauthenticated) { showLogin(); return; }
    renderMessage("assistant", resp.assistant || "(no reply)", resp.code_executions || []);
    scrollToBottom();
    setChatStatus(null);
  } catch (err) {
    setChatStatus("Chat error: " + err.message, true);
  } finally {
    chatInput.disabled = false;
    chatSubmit.disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  sendMessage(text);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});


// ───── autopilot opening message ─────

function autopilotKickoff() {
  sendMessage("I just loaded a dataset. Take a look and tell me what you'd analyze.");
}


// boot
checkSession();
