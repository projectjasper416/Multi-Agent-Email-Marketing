// Onboarding app. Auth gates the wizard; the signed-in account IS the customer,
// so there is no customer_id in the client — the server derives it from the
// session cookie. Progress lives server-side: on load we ask /api/onboarding/
// status which step this account reached and resume there.

const state = {
  rawSignals: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ── HTTP helpers (cookies carry the session; nothing to attach by hand) ──
async function api(url, { method = "GET", body } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  return fetch(url, opts);
}

async function postJSON(url, body) {
  const res = await api(url, { method: "POST", body });
  if (!res.ok) {
    if (res.status === 401) {
      // Session expired and could not be refreshed — back to the auth gate.
      showScreen("auth");
    }
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = j.detail ? JSON.stringify(j.detail) : detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

// ── Screens: boot → auth → app ───────────────────────────────────────────
function showScreen(name) {
  $("#boot").hidden = name !== "boot";
  $("#auth-screen").hidden = name !== "auth";
  $("#app").hidden = name !== "app";
  $("#account").hidden = name !== "app";
}

async function boot() {
  showScreen("boot");
  const res = await api("/api/auth/me");
  if (res.ok) {
    const { user } = await res.json();
    onAuthenticated(user);
  } else {
    showScreen("auth");
  }
}

// ── Auth ─────────────────────────────────────────────────────────────────
let authMode = "login";

function setAuthMode(mode) {
  authMode = mode;
  const login = mode === "login";
  $("#auth-title").textContent = login ? "Sign in" : "Create your account";
  $("#auth-sub").textContent = login
    ? "Sign in to pick up your onboarding where you left off."
    : "Your account is your workspace — onboarding saves to it automatically.";
  $("#auth-submit").textContent = login ? "Sign in →" : "Create account →";
  $("#auth-toggle-text").textContent = login ? "New here?" : "Already have an account?";
  $("#auth-toggle-btn").textContent = login ? "Create an account" : "Sign in";
  $("#auth-error").hidden = true;
}

function showAuthError(msg) {
  const el = $("#auth-error");
  el.textContent = msg;
  el.hidden = false;
}

$("#auth-toggle-btn").addEventListener("click", () =>
  setAuthMode(authMode === "login" ? "signup" : "login")
);

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const btn = $("#auth-submit");
  const email = f.email.value.trim();
  const password = f.password.value;
  $("#auth-error").hidden = true;
  btn.disabled = true;

  const url = authMode === "login" ? "/api/auth/login" : "/api/auth/signup";
  try {
    const data = await postJSON(url, { email, password });
    if (data.needs_confirmation) {
      setAuthMode("login");
      showAuthError(`Check ${data.email} to confirm your account, then sign in.`);
      return;
    }
    onAuthenticated(data.user);
  } catch (err) {
    showAuthError(err.message);
  } finally {
    btn.disabled = false;
  }
});

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
});

// ── Resume: route to the furthest step this account has completed ────────
async function onAuthenticated(user) {
  $("#account-email").textContent = user.email || "";
  showScreen("app");
  await resume();
}

async function resume() {
  const res = await api("/api/onboarding/status");
  if (!res.ok) {
    if (res.status === 401) showScreen("auth");
    return; // any other error leaves the default Step 1 visible
  }
  const status = await res.json();
  if (status.has_behaviors) {
    $("#done-summary").textContent =
      "Your approved signals are saved. Onboarding is complete.";
    goTo("done");
  } else if (status.has_brief) {
    goTo(2);
    runAnalyze();
  } else {
    goTo(1);
  }
}

function goTo(step) {
  ["1", "2", "3", "done"].forEach((s) => {
    const panel = $(`#panel-${s}`);
    if (panel) panel.hidden = String(s) !== String(step);
  });
  $$("#stepper .step").forEach((el) => {
    const n = Number(el.dataset.step);
    const cur = step === "done" ? 4 : Number(step);
    el.classList.toggle("is-active", n === cur);
    el.classList.toggle("is-done", n < cur);
  });
}

// ── Step 1: brief ──────────────────────────────────────────────────────
$("#brief-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  btn.textContent = "Generating…";

  const emailSettings = {
    brand_name: f.brand_name.value.trim(),
    from_address: f.from_address.value.trim(),
    unsubscribe_base_url: f.unsubscribe_base_url.value.trim(),
  };
  const formInputs = {
    product_description: f.product_description.value.trim(),
    target_users: f.target_users.value.trim(),
    conversion_model: f.conversion_model.value.trim(),
    brand_tone: f.brand_tone.value.trim(),
    email_hard_rules: f.email_hard_rules.value.trim(),
    exclusion_rules: f.exclusion_rules.value.trim(),
    extra_context: f.extra_context.value.trim(),
  };

  try {
    const { brief } = await postJSON("/api/onboarding/brief", {
      form_inputs: formInputs,
      email_settings: emailSettings,
    });
    $("#brief-doc").textContent = brief;
    $("#brief-result").hidden = false;
    f.querySelectorAll("input, textarea").forEach((el) => (el.disabled = true));
    btn.hidden = true;
    $("#brief-result").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert("Could not generate brief:\n\n" + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate brief →";
  }
});

$("#brief-edit").addEventListener("click", () => {
  const f = $("#brief-form");
  f.querySelectorAll("input, textarea").forEach((el) => (el.disabled = false));
  f.querySelector("button[type=submit]").hidden = false;
  $("#brief-result").hidden = true;
});

$("#to-analyze").addEventListener("click", () => {
  goTo(2);
  runAnalyze();
});

// ── Step 2: analyze schema ─────────────────────────────────────────────
async function runAnalyze() {
  $("#analyze-loading").hidden = false;
  $("#analyze-result").hidden = true;
  $("#analyze-error").hidden = true;
  try {
    const { raw_signals } = await postJSON("/api/onboarding/analyze");
    state.rawSignals = raw_signals || [];
    $("#analyze-count").textContent =
      `Found ${state.rawSignals.length} candidate signal${state.rawSignals.length === 1 ? "" : "s"}.`;
    $("#analyze-loading").hidden = true;
    $("#analyze-result").hidden = false;
  } catch (err) {
    $("#analyze-loading").hidden = true;
    $("#analyze-error").hidden = false;
    $("#analyze-error .err").textContent = err.message;
  }
}

$("#analyze-retry").addEventListener("click", runAnalyze);

$("#to-review").addEventListener("click", () => {
  renderSignals();
  goTo(3);
});

// ── Step 3: review + save ──────────────────────────────────────────────
function updateApproveCount() {
  const n = $$("#signals .toggle input:checked").length;
  $("#approve-count").textContent = `${n} of ${state.rawSignals.length} approved`;
}

function renderSignals() {
  const wrap = $("#signals");
  wrap.innerHTML = "";
  state.rawSignals.forEach((sig, i) => {
    const row = document.createElement("div");
    row.className = "signal";
    row.innerHTML = `
      <label class="toggle">
        <input type="checkbox" checked data-i="${i}" />
        <span class="track"></span>
      </label>
      <div class="body">
        <div class="name"></div>
        <div class="desc"></div>
        <div class="deriv"></div>
      </div>`;
    row.querySelector(".name").textContent = sig.name || "(unnamed signal)";
    row.querySelector(".desc").textContent = sig.description || "";
    row.querySelector(".deriv").textContent = sig.derivation ? `↳ ${sig.derivation}` : "";
    const input = row.querySelector("input");
    input.addEventListener("change", () => {
      row.classList.toggle("off", !input.checked);
      updateApproveCount();
    });
    wrap.appendChild(row);
  });
  updateApproveCount();
}

$("#save-signals").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Saving…";

  const approvals = state.rawSignals.map((sig, i) => ({
    ...sig,
    approved: $(`#signals .toggle input[data-i="${i}"]`).checked,
  }));

  try {
    const { approved } = await postJSON("/api/onboarding/approve", { approvals });
    $("#done-summary").textContent =
      `${approved.length} signal${approved.length === 1 ? "" : "s"} approved.`;
    goTo("done");
  } catch (err) {
    alert("Could not save signals:\n\n" + err.message);
    btn.disabled = false;
    btn.textContent = "Finish setup →";
  }
});

// ── Start ────────────────────────────────────────────────────────────────
setAuthMode("login");
boot();
