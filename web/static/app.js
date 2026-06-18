// Onboarding wizard — drives the three /api/onboarding endpoints in order.
// State carried across steps: the customer_id and the raw signal list.

const state = {
  customerId: null,
  rawSignals: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = j.detail ? JSON.stringify(j.detail) : detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
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

  const customerId = f.customer_id.value.trim();
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
      customer_id: customerId,
      form_inputs: formInputs,
      email_settings: emailSettings,
    });
    state.customerId = customerId;
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

// Resume: skip Step 1 for a customer whose brief is already stored. Sets the
// customer id and jumps straight to schema analysis — product_context is read
// from the Store server-side, so the brief is never regenerated/overwritten.
$("#resume-btn").addEventListener("click", () => {
  const id = $("#resume-id").value.trim();
  if (!id) {
    alert("Enter the customer ID you onboarded (e.g. taskflow).");
    return;
  }
  state.customerId = id;
  goTo(2);
  runAnalyze();
});

// ── Step 2: analyze schema ─────────────────────────────────────────────
async function runAnalyze() {
  $("#analyze-loading").hidden = false;
  $("#analyze-result").hidden = true;
  $("#analyze-error").hidden = true;
  try {
    const { raw_signals } = await postJSON("/api/onboarding/analyze", {
      customer_id: state.customerId,
    });
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
    const { approved } = await postJSON("/api/onboarding/approve", {
      customer_id: state.customerId,
      approvals,
    });
    $("#done-summary").textContent =
      `${approved.length} signal${approved.length === 1 ? "" : "s"} approved for ${state.customerId}.`;
    goTo("done");
  } catch (err) {
    alert("Could not save signals:\n\n" + err.message);
    btn.disabled = false;
    btn.textContent = "Finish setup →";
  }
});
