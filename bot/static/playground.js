"use strict";
// Vera Playground — talks to the same endpoints the judge uses (/v1/reply) plus a few
// playground-only helpers (/playground/api/*). All dynamic text is set via textContent.

const CATEGORY_LABELS = {
  dentists: "Dentists", salons: "Salons", restaurants: "Restaurants", gyms: "Gyms", pharmacies: "Pharmacies",
};
const QUICK_MERCHANT = [
  ["Yes, go ahead", "Yes, go ahead"],
  ["Ask a question", "How many calls did I get this month?"],
  ["Off-topic", "Can you also help me file my GST?"],
  ["Later", "Busy right now, message me later"],
  ["Auto-reply", "Thank you for contacting us! Our team will respond shortly."],
  ["Not interested", "Not interested"],
  ["Annoyed", "This is useless, you people are wasting my time"],
  ["STOP", "STOP"],
];
const QUICK_CUSTOMER = [
  ["Pick slot 1", "1"],
  ["Pick slot 2", "2"],
  ["Change time", "CHANGE"],
  ["Yes", "Yes please"],
  ["Cancel", "CANCEL"],
  ["STOP", "STOP"],
];
const CTA_LABELS = {
  binary_yes_no: "Yes / No", binary_confirm_cancel: "Confirm / Cancel",
  multi_choice_slot: "Pick a slot", open_ended: "Open question", none: "None",
};

const state = {
  merchants: [],
  filter: "all",
  query: "",
  merchant: null,
  activeTrigger: null,
  convo: null, // {id, merchantId, customerId, recipient, turn, ended}
  busy: false,
};

const $ = (id) => document.getElementById(id);

function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.cls) node.className = opts.cls;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
  for (const child of children) if (child) node.append(child);
  return node;
}

function initials(name) {
  return (name || "?").replace(/^Dr\.?\s+/i, "").split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("");
}

function humanize(s) {
  return (s || "").replace(/_/g, " ");
}

function simulatedNow() {
  const v = $("sim-date").value || "2026-04-26";
  return `${v}T10:30:00Z`;
}

// ---------------------------------------------------------------- network

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const reason = res.status === 429 ? "Slow down — too many requests" : (data && (data.detail || data.error)) || res.statusText;
    throw new Error(reason);
  }
  return data;
}

let toastTimer = null;
function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.className = isError ? "toast toast-error" : "toast";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}

// ---------------------------------------------------------------- header

async function refreshInfo() {
  const info = await api("/playground/api/info");
  const pill = $("mode-pill");
  if (info.mode === "llm") {
    pill.textContent = `LLM · ${info.model}`;
    pill.className = "pill pill-ok";
    pill.title = `Messages are phrased by ${info.provider} (${info.model}) and checked against the data.`;
  } else {
    pill.textContent = "Template mode";
    pill.className = "pill pill-warn";
    pill.title = "No LLM configured — messages come from the built-in templates. Add a key in .env to enable the LLM.";
  }
  const c = info.contexts_loaded;
  $("counts-pill").textContent = `${c.merchant} merchants · ${c.customer} customers · ${c.trigger} triggers`;
  return info;
}

// ---------------------------------------------------------------- sidebar

function renderChips() {
  const chips = $("category-chips");
  chips.replaceChildren();
  const cats = ["all", ...Object.keys(CATEGORY_LABELS)];
  for (const cat of cats) {
    const b = el("button", {
      cls: "chip", text: cat === "all" ? "All" : CATEGORY_LABELS[cat],
      attrs: { type: "button", role: "tab", "aria-selected": String(state.filter === cat) },
    });
    b.addEventListener("click", () => { state.filter = cat; renderChips(); renderMerchantList(); });
    chips.append(b);
  }
}

function visibleMerchants() {
  const q = state.query.trim().toLowerCase();
  return state.merchants.filter((m) => {
    if (state.filter !== "all" && m.category !== state.filter) return false;
    if (!q) return true;
    return [m.name, m.owner, m.locality, m.city, m.category].some((v) => (v || "").toLowerCase().includes(q));
  });
}

function renderMerchantList() {
  const list = $("merchant-list");
  list.replaceChildren();
  const items = visibleMerchants();
  for (const m of items) {
    const btn = el("button", { cls: "merchant-item", attrs: { type: "button" } }, [
      el("span", { cls: `avatar cat-${m.category}`, text: initials(m.name), attrs: { "aria-hidden": "true" } }),
      el("span", { cls: "merchant-item-text" }, [
        el("span", { cls: "merchant-item-name", text: m.name }),
        el("span", { cls: "merchant-item-sub", text: [m.locality, m.city].filter(Boolean).join(", ") }),
      ]),
      el("span", { cls: "merchant-item-count", text: String(m.triggers.length), attrs: { title: "Triggers" } }),
    ]);
    if (state.merchant && state.merchant.merchant_id === m.merchant_id) btn.setAttribute("aria-current", "true");
    btn.addEventListener("click", () => selectMerchant(m.merchant_id));
    list.append(el("li", {}, [btn]));
  }
  if (!items.length) list.append(el("li", { cls: "hint", text: "No merchants match." }));
  $("merchant-count").textContent = `${items.length} of ${state.merchants.length} merchants`;
}

// ---------------------------------------------------------------- merchant view

function selectMerchant(id) {
  state.merchant = state.merchants.find((m) => m.merchant_id === id) || null;
  state.activeTrigger = null;
  resetConversation();
  renderMerchantList();
  if (!state.merchant) return;
  const m = state.merchant;
  $("empty-state").hidden = true;
  $("merchant-view").hidden = false;
  const av = $("m-avatar");
  av.className = `avatar cat-${m.category}`;
  av.textContent = initials(m.name);
  $("m-name").textContent = m.name;
  const langs = (m.languages || []).map((l) => l.toUpperCase()).join(" · ");
  $("m-sub").textContent = [CATEGORY_LABELS[m.category] || m.category,
    [m.locality, m.city].filter(Boolean).join(", "), m.owner ? `Owner: ${m.owner}` : "", langs ? `Speaks ${langs}` : ""]
    .filter(Boolean).join("  ·  ");
  renderTriggers();
}

function urgencyBars(level) {
  const u = el("span", { cls: "urgency", attrs: { "data-level": String(level), title: `Urgency ${level} of 5`, "aria-label": `Urgency ${level} of 5` } });
  for (let i = 0; i < 5; i++) u.append(el("i"));
  return u;
}

function renderTriggers() {
  const list = $("trigger-list");
  list.replaceChildren();
  const trigs = state.merchant.triggers;
  if (!trigs.length) list.append(el("li", { cls: "hint", text: "This merchant has no triggers in the dataset." }));
  for (const t of trigs) {
    const send = el("button", { cls: "btn btn-primary btn-sm", text: "Send", attrs: { type: "button" } });
    send.addEventListener("click", () => sendTrigger(t));
    const meta = el("span", { cls: "trigger-meta" }, [
      t.scope === "customer"
        ? el("span", { cls: "tag tag-customer", text: `To ${t.customer_name || "customer"}` })
        : el("span", { cls: "tag", text: "To merchant" }),
      t.source ? el("span", { cls: "tag", text: t.source }) : null,
    ]);
    const card = el("li", { cls: "trigger", attrs: { "data-active": String(state.activeTrigger === t.id) } }, [
      el("div", { cls: "trigger-top" }, [el("span", { cls: "trigger-label", text: t.label }), urgencyBars(t.urgency)]),
      el("div", { cls: "trigger-bottom" }, [meta, send]),
    ]);
    list.append(card);
  }
}

// ---------------------------------------------------------------- conversation

function resetConversation() {
  state.convo = null;
  $("thread").replaceChildren();
  $("thread-title").textContent = "Conversation";
  $("thread-sub").textContent = "Send a trigger to start a conversation.";
  $("thread-state").hidden = true;
  setComposer(false);
  renderQuickReplies();
  $("inspector").hidden = true;
  $("inspector-empty").hidden = false;
}

function setComposer(enabled) {
  const on = enabled && !state.busy;
  $("composer-input").disabled = !on;
  $("composer-send").disabled = !on;
  for (const b of $("quick-replies").querySelectorAll("button")) b.disabled = !on;
  if (state.convo) {
    $("composer-input").placeholder = state.convo.ended
      ? "Conversation ended — send another trigger to start again"
      : `Reply as ${state.convo.recipient}…`;
  }
}

function renderQuickReplies() {
  const box = $("quick-replies");
  box.replaceChildren();
  const set = state.convo && state.convo.customerId ? QUICK_CUSTOMER : QUICK_MERCHANT;
  for (const [label, text] of set) {
    const b = el("button", { text: label, attrs: { type: "button", title: text } });
    b.addEventListener("click", () => sendReply(text));
    box.append(b);
  }
}

function addBotMessage(author, body, tags = []) {
  const foot = el("div", { cls: "msg-foot" }, tags.map((t) => el("span", { cls: "tag", text: t })));
  $("thread").append(el("li", { cls: "msg msg-bot" }, [
    el("span", { cls: "msg-author", text: author }), el("div", { cls: "bubble", text: body }), tags.length ? foot : null,
  ]));
  scrollThread();
}

function addUserMessage(author, body) {
  $("thread").append(el("li", { cls: "msg msg-user" }, [
    el("span", { cls: "msg-author", text: author }), el("div", { cls: "bubble", text: body }),
  ]));
  scrollThread();
}

function addEvent(text, tone = "") {
  $("thread").append(el("li", { cls: `event ${tone ? "event-" + tone : ""}`.trim(), text }));
  scrollThread();
}

function scrollThread() {
  const t = $("thread");
  t.scrollTop = t.scrollHeight;
}

function setThreadState(text, tone) {
  const s = $("thread-state");
  s.textContent = text;
  s.className = `pill ${tone ? "pill-" + tone : "pill-quiet"}`;
  s.hidden = false;
}

async function sendTrigger(trigger) {
  if (state.busy) return;
  state.busy = true;
  state.activeTrigger = trigger.id;
  renderTriggers();
  resetConversation();
  const typing = el("li", { cls: "typing", text: "Vera is composing…" });
  $("thread").append(typing);
  try {
    const res = await api("/playground/api/send", { method: "POST", body: { trigger_id: trigger.id, now: simulatedNow() } });
    typing.remove();
    const m = state.merchant;
    if (!res.action) {
      const why = { low_value: "nothing specific enough to say for a low-urgency trigger",
        opted_out: "this recipient has opted out (use Reset to clear)", skipped: "the trigger was skipped" }[res.reason] || res.reason;
      addEvent(`Vera chose not to send: ${why}`, "warn");
      renderInspector(res, null);
      return;
    }
    const a = res.action;
    const toCustomer = a.send_as === "merchant_on_behalf";
    state.convo = {
      id: a.conversation_id, merchantId: a.merchant_id, customerId: a.customer_id,
      recipient: toCustomer ? (trigger.customer_name || "customer") : (m.owner || m.name), turn: 1, ended: false,
    };
    $("thread-title").textContent = toCustomer
      ? `${m.name} → ${trigger.customer_name || "customer"}`
      : `Vera → ${m.owner || m.name}`;
    $("thread-sub").textContent = toCustomer
      ? `Sent on the merchant's behalf · ${trigger.label}`
      : `Sent as Vera · ${trigger.label}`;
    setThreadState("Open", "ok");
    addBotMessage(toCustomer ? `${m.name} (via Vera)` : "Vera", a.body,
      [CTA_LABELS[a.cta] || a.cta, res.writer === "llm" ? "LLM" : "Template"]);
    renderQuickReplies();
    renderInspector(res, a);
  } catch (err) {
    typing.remove();
    addEvent(`Could not send: ${err.message}`, "danger");
    toast(err.message, true);
  } finally {
    state.busy = false;
    setComposer(Boolean(state.convo && !state.convo.ended));
  }
}

async function sendReply(text) {
  const c = state.convo;
  if (!c || c.ended || state.busy || !text.trim()) return;
  state.busy = true;
  setComposer(false);
  c.turn += 1;
  addUserMessage(c.recipient, text);
  const typing = el("li", { cls: "typing", text: "Vera is deciding…" });
  $("thread").append(typing);
  try {
    const r = await api("/v1/reply", {
      method: "POST",
      body: {
        conversation_id: c.id, merchant_id: c.merchantId, customer_id: c.customerId,
        from_role: c.customerId ? "customer" : "merchant", message: text,
        received_at: simulatedNow(), turn_number: c.turn,
      },
    });
    typing.remove();
    const author = c.customerId ? `${state.merchant.name} (via Vera)` : "Vera";
    if (r.action === "send") {
      addBotMessage(author, r.body, r.cta && r.cta !== "none" ? [CTA_LABELS[r.cta] || r.cta] : []);
      setThreadState("Open", "ok");
    } else if (r.action === "wait") {
      const hours = Math.round((r.wait_seconds || 0) / 3600);
      addEvent(`Vera is waiting ${hours >= 1 ? hours + "h" : Math.round((r.wait_seconds || 0) / 60) + " min"} before following up`, "warn");
      setThreadState("Waiting", "warn");
    } else {
      addEvent("Vera ended the conversation", "danger");
      setThreadState("Ended", "danger");
      c.ended = true;
    }
    renderReplyDecision(r);
  } catch (err) {
    typing.remove();
    addEvent(`Reply failed: ${err.message}`, "danger");
    toast(err.message, true);
  } finally {
    state.busy = false;
    setComposer(!c.ended);
    if (!c.ended) $("composer-input").focus();
  }
}

// ---------------------------------------------------------------- inspector

function kvRow(dl, label, value, asCode = false) {
  const dd = el("dd");
  if (asCode) dd.append(el("code", { text: value })); else dd.textContent = value;
  dl.append(el("dt", { text: label }), dd);
}

function factItem(f) {
  return el("li", { cls: "fact" }, [el("span", { cls: "fact-text", text: f.text }), el("span", { cls: "fact-source", text: f.source })]);
}

function renderInspector(res, action) {
  $("inspector-empty").hidden = true;
  $("inspector").hidden = false;
  $("reply-panel").hidden = true;
  const dl = $("decision");
  dl.replaceChildren();
  if (action) {
    kvRow(dl, "Sent as", action.send_as === "merchant_on_behalf" ? "Merchant, to their customer" : "Vera, to the merchant");
    kvRow(dl, "Call to action", CTA_LABELS[action.cta] || action.cta);
    kvRow(dl, "Angle", humanize(res.angle));
    kvRow(dl, "Language", res.language || "English");
    kvRow(dl, "Written by", res.writer === "llm" ? "LLM (validated)" : res.writer === "llm_stripped" ? "LLM (trimmed)" : "Template");
    kvRow(dl, "Template", action.template_name, true);
    kvRow(dl, "Dedup key", action.suppression_key, true);
  } else {
    kvRow(dl, "Outcome", "Not sent");
    kvRow(dl, "Reason", humanize(res.reason));
    kvRow(dl, "Angle", humanize(res.angle));
  }
  const used = res.facts.filter((f) => f.used);
  const other = res.facts.filter((f) => !f.used);
  $("facts-used").replaceChildren(...(used.length ? used.map(factItem) : [el("li", { cls: "hint", text: "No facts used." })]));
  $("facts-other").replaceChildren(...other.map(factItem));
  $("used-count").textContent = `(${used.length})`;
  $("other-count").textContent = `(${other.length})`;
  $("rationale").textContent = action ? action.rationale.replace(/ Evidence: .*$/, "").replace(/ Writer: .*$/, "") : "—";
}

function renderReplyDecision(r) {
  const panel = $("reply-panel");
  panel.hidden = false;
  const dl = $("reply-decision");
  dl.replaceChildren();
  kvRow(dl, "Action", { send: "Replied", wait: `Wait ${Math.round((r.wait_seconds || 0) / 3600)}h`, end: "Ended" }[r.action] || r.action);
  if (r.cta) kvRow(dl, "Call to action", CTA_LABELS[r.cta] || r.cta);
  kvRow(dl, "Why", r.rationale || "—");
}

// ---------------------------------------------------------------- boot

async function loadCatalog() {
  const cat = await api("/playground/api/catalog");
  state.merchants = cat.merchants;
  renderChips();
  renderMerchantList();
}

async function resetAll() {
  const btn = $("reset-btn");
  btn.disabled = true;
  try {
    await api("/playground/api/reset", { method: "POST" });
    const keep = state.merchant && state.merchant.merchant_id;
    await Promise.all([refreshInfo(), loadCatalog()]);
    if (keep) selectMerchant(keep); else resetConversation();
    toast("Reset — dataset reloaded and conversations cleared");
  } catch (err) {
    toast(`Reset failed: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

async function boot() {
  $("search").addEventListener("input", (e) => { state.query = e.target.value; renderMerchantList(); });
  $("reset-btn").addEventListener("click", resetAll);
  $("composer-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("composer-input");
    const text = input.value;
    input.value = "";
    sendReply(text);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("search").focus(); }
  });
  renderQuickReplies();
  try {
    const info = await refreshInfo();
    $("sim-date").value = (info.default_now || "2026-04-26").slice(0, 10);
    if (!info.contexts_loaded.merchant) {
      await api("/playground/api/reset", { method: "POST" });
      await refreshInfo();
    }
    await loadCatalog();
  } catch (err) {
    $("mode-pill").textContent = "Offline";
    $("mode-pill").className = "pill pill-danger";
    toast(`Could not reach the bot: ${err.message}`, true);
  }
}

document.addEventListener("DOMContentLoaded", boot);
