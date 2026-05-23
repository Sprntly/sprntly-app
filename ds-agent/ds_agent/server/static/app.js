/* Sprntly DS-Agent UI — vanilla.
 *
 * Auth: bearer token in localStorage. The login response returns a
 * `token` field that we stash and send back as `Authorization: Bearer
 * <token>` on every subsequent request. No cookies — they were
 * unreliable through the Vercel rewrite for some browser configs.
 *
 * All endpoints live under /agent on the public side (nginx prefix) and
 * under / inside this app (FastAPI). We use absolute /agent/api/... so
 * the UI works on the prod domain and on a direct localhost dev server.
 */

const API = {
  base: window.location.pathname.startsWith("/agent") ? "/agent/api" : "/api",
};

const TOKEN_KEY = "sprntly_agent_token";
function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() { localStorage.removeItem(TOKEN_KEY); }

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

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


// ───── chat ─────

function renderMessage(role, text, toolCalls = []) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  if (toolCalls && toolCalls.length) {
    const chips = document.createElement("div");
    chips.className = "msg-tool-chips";
    for (const t of toolCalls) {
      const c = document.createElement("span");
      c.className = "tool-chip" + (t.is_error ? " error" : "");
      c.textContent = t.name + (t.is_error ? " ✗" : "");
      chips.appendChild(c);
    }
    el.appendChild(chips);
  }
  messagesEl.appendChild(el);
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
    renderMessage("assistant", resp.assistant || "(no reply)", resp.tool_calls);
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
// After a dataset is loaded for the first time we seed the conversation
// so the agent immediately runs describe_dataset + suggests a goal metric.

function autopilotKickoff() {
  sendMessage("I just loaded a dataset. Take a look and tell me what you'd analyze.");
}


// boot
checkSession();
