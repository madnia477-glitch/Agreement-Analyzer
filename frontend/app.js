// ============================================================
// Agreement Review workspace - vanilla JS, no build step.
// ============================================================
const API = ""; // same origin

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const state = {
  docId: null,
  filename: "",
  analysis: null,
  pdfDoc: null,
  currentPage: 1,
  activeTab: "overview",
};

const el = (id) => document.getElementById(id);
const show = (id) => el(id).removeAttribute("hidden");
const hide = (id) => el(id).setAttribute("hidden", "");

// ---------------- Upload ----------------
const dropzone = el("dropzone");
const fileInput = el("file-input");

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) handleUpload(file);
});
fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) handleUpload(file);
});

async function handleUpload(file) {
  hide("upload-error");
  if (file.type !== "application/pdf") {
    el("upload-error").textContent = "Please choose a PDF file.";
    show("upload-error");
    return;
  }

  const form = new FormData();
  form.append("file", file);

  try {
    hide("upload-screen");
    show("processing-screen");
    el("processing-title").textContent = "Uploading your agreement…";

    const res = await fetch(`${API}/api/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.docId = data.doc_id;
    state.filename = data.filename;

    await loadPdf(file);
    pollStatus();
  } catch (err) {
    console.error(err);
    hide("processing-screen");
    show("upload-screen");
    el("upload-error").textContent = "Upload failed: " + err.message;
    show("upload-error");
  }
}

async function loadPdf(file) {
  const buf = await file.arrayBuffer();
  state.pdfDoc = await pdfjsLib.getDocument({ data: buf }).promise;
}

async function pollStatus() {
  try {
    const res = await fetch(`${API}/api/status/${state.docId}`);
    const data = await res.json();
    el("processing-title").textContent =
      data.status === "error" ? "Something went wrong" : "Reading your agreement…";
    el("processing-message").textContent = data.message || "";
    markChecklistStage(data.message);

    if (data.status === "done") {
      await loadAnalysis();
      enterWorkspace();
    } else if (data.status === "error") {
      el("processing-message").textContent =
        (data.message || "Unknown error") + " — check the server logs and that GROQ_API_KEY is set.";
    } else {
      setTimeout(pollStatus, 1500);
    }
  } catch (err) {
    console.error(err);
    setTimeout(pollStatus, 2500);
  }
}

const PIPELINE_STAGES = [
  "Document uploaded", "Text extracted", "OCR completed", "Document structure detected",
  "Important terms detected", "Financial terms detected", "Obligations detected",
  "Signatures detected", "Checkboxes detected", "RAG verification completed",
  "Benefits analyzed", "Potential concerns analyzed", "Report generated", "Voice summary ready",
];
const seenStages = new Set();

function buildChecklist() {
  const ul = el("processing-checklist");
  ul.innerHTML = PIPELINE_STAGES.map((s) =>
    `<li data-stage="${esc(s)}"><span class="tick"></span>${esc(s)}</li>`
  ).join("");
}
buildChecklist();

function markChecklistStage(stageMessage) {
  if (!stageMessage) return;
  const idx = PIPELINE_STAGES.indexOf(stageMessage);
  if (idx === -1) return;
  for (let i = 0; i <= idx; i++) seenStages.add(PIPELINE_STAGES[i]);
  document.querySelectorAll("#processing-checklist li").forEach((li) => {
    if (seenStages.has(li.dataset.stage)) {
      li.classList.add("done");
      li.querySelector(".tick").textContent = "\u2713";
    }
  });
}

async function loadAnalysis() {
  const res = await fetch(`${API}/api/analysis/${state.docId}`);
  state.analysis = await res.json();
}

// ---------------- Workspace entry ----------------
function enterWorkspace() {
  hide("processing-screen");
  show("workspace-screen");

  el("doc-title").textContent = state.analysis.document_overview.agreement_type || "Agreement Review";
  el("doc-subtitle").textContent =
    [state.analysis.document_overview.institution, `${state.analysis.document_overview.page_count} pages`]
      .filter(Boolean).join(" · ");

  const score = state.analysis.attention_score;
  if (score) {
    const badge = el("attention-badge");
    badge.textContent = `${score.level} attention`;
    badge.className = "badge " + score.level;
    show("attention-badge");
  }

  if (!state.analysis.voice_summary) {
    el("listen-btn").disabled = true;
    el("listen-btn").title = "No voice summary was generated for this document.";
  }

  buildLedger();
  renderPage(1);
  buildTabs();
  renderTab("overview");
  setupMobileToggle();
}

function setupMobileToggle() {
  const toggle = el("mobile-toggle");
  const grid = el("workspace-grid");
  grid.classList.add("show-analysis");
  toggle.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      toggle.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      grid.classList.remove("show-viewer", "show-analysis");
      grid.classList.add("show-" + btn.dataset.view);
    });
  });
}

el("new-doc-btn").addEventListener("click", () => location.reload());
el("export-btn").addEventListener("click", () => {
  window.open(`${API}/api/export/${state.docId}`, "_blank");
});

// ---------------- Ledger strip ----------------
function buildLedger() {
  const track = el("ledger-track");
  track.innerHTML = "";
  const pageCount = state.analysis.document_overview.page_count || (state.pdfDoc ? state.pdfDoc.numPages : 1);

  const byPage = {};
  const mark = (page, kind) => {
    if (!page) return;
    byPage[page] = byPage[page] || [];
    byPage[page].push(kind);
  };
  (state.analysis.signatures || []).forEach((s) => mark(s.page, "signature"));
  (state.analysis.checkboxes || []).forEach((c) => mark(c.page, "checkbox"));
  (state.analysis.important_terms || []).forEach((t) => {
    if (t.importance === "high") mark(t.page, "important");
  });
  (state.analysis.attention_areas || []).forEach((a) => {
    if (a.level === "Important" && a.source) mark(a.source.page, "important");
  });

  for (let p = 1; p <= pageCount; p++) {
    const btn = document.createElement("button");
    btn.className = "ledger-tick";
    btn.dataset.page = p;
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = p;
    const dots = document.createElement("div");
    dots.className = "ledger-dots";
    (byPage[p] || []).slice(0, 4).forEach((kind) => {
      const d = document.createElement("span");
      d.className = "dot " + kind;
      dots.appendChild(d);
    });
    btn.appendChild(num);
    btn.appendChild(dots);
    btn.addEventListener("click", () => renderPage(p));
    track.appendChild(btn);
  }
}

function setActiveTick(page) {
  document.querySelectorAll(".ledger-tick").forEach((t) => {
    t.classList.toggle("active", Number(t.dataset.page) === page);
  });
}

// ---------------- PDF rendering ----------------
async function renderPage(pageNum) {
  if (!state.pdfDoc) return;
  state.currentPage = pageNum;
  el("page-indicator").textContent = `Page ${pageNum} of ${state.pdfDoc.numPages}`;
  setActiveTick(pageNum);

  const page = await state.pdfDoc.getPage(pageNum);
  const viewport = page.getViewport({ scale: 1.35 });
  const canvas = el("pdf-canvas");
  const ctx = canvas.getContext("2d");
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvasContext: ctx, viewport }).promise;

  renderHighlightOverlay(pageNum, viewport);
}

function renderHighlightOverlay(pageNum, viewport) {
  const overlay = el("page-overlay");
  overlay.innerHTML = "";
  overlay.style.width = viewport.width + "px";
  overlay.style.height = viewport.height + "px";
  overlay.style.left = el("pdf-canvas").offsetLeft + "px";
  overlay.style.top = el("pdf-canvas").offsetTop + "px";

  const items = [];
  (state.analysis.important_terms || []).forEach((t) => {
    if (t.bbox && t.page === pageNum) items.push({ kind: "term", data: t });
  });
  (state.analysis.signatures || []).forEach((s) => {
    if (s.bbox && s.page === pageNum) items.push({ kind: "signature", data: s });
  });
  (state.analysis.checkboxes || []).forEach((cb) => {
    if (cb.bbox && cb.page === pageNum) items.push({ kind: "checkbox", data: cb });
  });

  items.forEach(({ kind, data }) => {
    const bbox = data.bbox;
    const scaleX = viewport.width / bbox.page_width;
    const scaleY = viewport.height / bbox.page_height;
    const div = document.createElement("div");
    div.className = "term-highlight" + (data.importance === "high" ? " importance-high" : "");
    div.style.left = (bbox.x0 * scaleX - 2) + "px";
    div.style.top = (bbox.top * scaleY - 2) + "px";
    div.style.width = ((bbox.x1 - bbox.x0) * scaleX + 4) + "px";
    div.style.height = ((bbox.bottom - bbox.top) * scaleY + 4) + "px";

    div.addEventListener("click", (e) => {
      e.stopPropagation();
      showTermTooltip(div, kind, data);
    });
    overlay.appendChild(div);
  });
}

let activeTooltip = null;
function showTermTooltip(anchor, kind, data) {
  if (activeTooltip) activeTooltip.remove();
  const tip = document.createElement("div");
  tip.className = "term-tooltip";
  if (kind === "term") {
    tip.innerHTML = `<div class="tt-title">${esc(data.term)}</div>
      <div class="tt-meta">${esc(data.category)} · ${esc(data.importance)} importance</div>
      <div>${esc(data.explanation || "")}</div>`;
  } else if (kind === "signature") {
    tip.innerHTML = `<div class="tt-title">${esc(data.field)}</div>
      <div class="tt-meta">${esc(data.status)}</div>
      <div>${esc(data.meaning || "")}</div>`;
  } else {
    tip.innerHTML = `<div class="tt-title">${esc(data.classification)}</div>
      <div>${esc(data.what_it_means || "")}</div>`;
  }
  tip.style.left = (anchor.offsetLeft) + "px";
  tip.style.top = (anchor.offsetTop + anchor.offsetHeight + 4) + "px";
  el("page-overlay").appendChild(tip);
  activeTooltip = tip;
  setTimeout(() => {
    document.addEventListener("click", function closeTip() {
      if (activeTooltip) { activeTooltip.remove(); activeTooltip = null; }
      document.removeEventListener("click", closeTip);
    }, { once: true });
  }, 0);
}

el("prev-page").addEventListener("click", () => {
  if (state.currentPage > 1) renderPage(state.currentPage - 1);
});
el("next-page").addEventListener("click", () => {
  if (state.pdfDoc && state.currentPage < state.pdfDoc.numPages) renderPage(state.currentPage + 1);
});

// ---------------- Tabs ----------------
const TABS = [
  { id: "overview", label: "Overview" },
  { id: "terms", label: "Important Terms" },
  { id: "financial", label: "Financial" },
  { id: "obligations", label: "Obligations & Rights" },
  { id: "default_term", label: "Default & Exit" },
  { id: "attention", label: "Attention Areas" },
  { id: "signatures", label: "Signatures & Consent" },
  { id: "checklist", label: "Checklist" },
];

function buildTabs() {
  const tabsEl = el("tabs");
  tabsEl.innerHTML = "";
  TABS.forEach((t) => {
    const btn = document.createElement("button");
    btn.className = "tab-btn" + (t.id === state.activeTab ? " active" : "");
    btn.textContent = t.label;
    btn.addEventListener("click", () => {
      state.activeTab = t.id;
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderTab(t.id);
    });
    tabsEl.appendChild(btn);
  });
}

function sourceTag(source) {
  if (!source || !source.page) return "";
  return `<span class="card-meta">PAGE ${source.page}${source.section ? " · " + esc(source.section) : ""}</span>`;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

function jumpablePage(page) {
  return page ? ` data-jump="${page}"` : "";
}

function renderTab(id) {
  const a = state.analysis;
  const c = el("tab-content");
  let html = "";

  if (id === "overview") {
    const ov = a.document_overview;
    html += `<h3>Document Overview</h3>`;
    html += `<div class="card"><div class="card-body">
      <div><strong>${esc(ov.agreement_type || "Type not determined")}</strong></div>
      <div>${esc(ov.institution || "")} ${ov.product ? "&middot; " + esc(ov.product) : ""}</div>
      <div>${esc((ov.parties || []).join(", "))}</div>
      <div>${ov.effective_date ? "Effective: " + esc(ov.effective_date) : ""}</div>
    </div></div>`;
    if (a.executive_summary) {
      html += `<h3>Executive Summary</h3><p>${esc(a.executive_summary)}</p>`;
    }
    if (a.potential_benefits && a.potential_benefits.length) {
      html += `<h3>Potential Benefits</h3>`;
      html += renderCards(a.potential_benefits, (b) => ({
        title: b.title, meta: sourceTag({ page: b.page, section: b.section }), body: b.explanation, page: b.page,
      }));
    }
    if (a.potential_concerns && a.potential_concerns.length) {
      html += `<h3>Potential Concerns</h3>`;
      html += renderCards(a.potential_concerns, (cn) => ({
        title: cn.title, meta: sourceTag({ page: cn.page, section: cn.section }), body: cn.explanation, page: cn.page, cls: "attention",
      }));
    }
    if (a.questions_to_consider && a.questions_to_consider.length) {
      html += `<h3>Questions to Consider</h3><ul>${a.questions_to_consider.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    }
    if (a.processing_notes && a.processing_notes.length) {
      html += `<p class="section-sub">${a.processing_notes.map(esc).join(" ")}</p>`;
    }
  }

  if (id === "terms") {
    html += renderImportantTermsPanel();
  }

  if (id === "financial") {
    html += `<h3>Financial Obligations, Fees &amp; Penalties</h3>`;
    html += renderCards(a.financial_terms, (f) => ({
      title: f.label,
      meta: sourceTag(f.source),
      body: f.amount_or_rule || "Amount/rule not clearly specified in the document.",
      extra: f.needs_review ? `<span class="pill">Needs review</span>` : "",
      page: f.source ? f.source.page : null,
    }));
    if (a.early_settlement && a.early_settlement.present) {
      html += `<h3>Early Settlement</h3><div class="card"${jumpablePage(a.early_settlement.source && a.early_settlement.source.page)}>
        ${sourceTag(a.early_settlement.source)}
        <div class="card-body">
          <div><strong>Conditions:</strong> ${esc(a.early_settlement.conditions || "Not specified")}</div>
          <div><strong>Charges:</strong> ${esc(a.early_settlement.charges || "Not specified")}</div>
          <div><strong>Notice required:</strong> ${esc(a.early_settlement.notice_requirement || "Not specified")}</div>
        </div></div>`;
    }
  }

  if (id === "obligations") {
    html += `<h3>Your Obligations</h3>`;
    html += renderCards(a.obligations, (o) => ({
      title: o.obligation, meta: sourceTag(o.source), body: o.meaning || "",
      extra: o.consequence ? `<div class="card-body"><em>If not met:</em> ${esc(o.consequence)}</div>` : "",
      page: o.source ? o.source.page : null,
    }));
    html += `<h3>Bank / Institution Rights</h3>`;
    html += renderCards(a.bank_rights, (r) => ({
      title: r.right, meta: sourceTag(r.source), body: r.conditions || "",
      page: r.source ? r.source.page : null,
    }));
  }

  if (id === "default_term") {
    html += `<h3>What Can Put You in Default</h3>`;
    html += renderCards(a.default_clauses, (d) => ({
      title: d.trigger, meta: sourceTag(d.source),
      body: d.stated_consequence || "The document does not clearly state the consequence.",
      page: d.source ? d.source.page : null,
    }));
    html += `<h3>How This Agreement Can End</h3>`;
    html += renderCards(a.termination_clauses, (t) => ({
      title: t.who_can_terminate ? `Termination by: ${t.who_can_terminate}` : "Termination clause",
      meta: sourceTag(t.source),
      body: [t.when && `When: ${t.when}`, t.notice_required && `Notice: ${t.notice_required}`,
             t.fee && `Fee: ${t.fee}`, t.consequences && `Consequences: ${t.consequences}`]
        .filter(Boolean).join("<br/>"),
      page: t.source ? t.source.page : null,
    }));
  }

  if (id === "attention") {
    html += `<h3>Attention Areas</h3>`;
    const areas = [...(a.attention_areas || [])].sort((x, y) => {
      const order = { Important: 0, Attention: 1, Informational: 2 };
      return order[x.level] - order[y.level];
    });
    html += renderCards(areas, (item) => ({
      title: item.topic,
      meta: sourceTag(item.source),
      body: item.what_document_says + (item.why_it_matters ? `<div class="section-sub">${esc(item.why_it_matters)}</div>` : ""),
      cls: item.level.toLowerCase(),
      page: item.source ? item.source.page : null,
    }));
  }

  if (id === "signatures") {
    html += `<h3>Signature &amp; Consent Requirements</h3>`;
    html += renderCards(a.signatures, (s) => ({
      title: `${s.field}${s.appears_blank ? " (appears blank)" : ""}`,
      meta: `<span class="card-meta">PAGE ${s.page} · ${esc(s.status)}</span>`,
      body: (s.meaning || "") + (s.attention_note ? `<div class="section-sub">${esc(s.attention_note)}</div>` : ""),
      page: s.page,
    }));
    if (a.checkboxes && a.checkboxes.length) {
      html += `<h3>Checkboxes &amp; Consent Lines</h3>`;
      html += renderCards(a.checkboxes, (cb) => ({
        title: cb.text.length > 80 ? cb.text.slice(0, 80) + "…" : cb.text,
        meta: `<span class="card-meta">PAGE ${cb.page} · ${esc(cb.classification)}</span>`,
        body: (cb.what_it_means || "") +
          (cb.what_happens_if_selected ? `<div class="section-sub"><strong>If selected:</strong> ${esc(cb.what_happens_if_selected)}</div>` : ""),
        page: cb.page,
      }));
    }
    if (a.missing_fields && a.missing_fields.length) {
      html += `<h3>Information That May Need Completion</h3>`;
      html += renderCards(a.missing_fields, (m) => ({
        title: m.field, meta: `<span class="card-meta">PAGE ${m.page}</span>`, body: m.reason, page: m.page,
      }));
    }
  }

  if (id === "checklist") {
    html += `<h3>Before You Sign</h3>`;
    if (a.before_you_sign_checklist && a.before_you_sign_checklist.length) {
      html += a.before_you_sign_checklist.map((item, i) => `
        <label class="checklist-item">
          <input type="checkbox" id="chk-${i}" />
          <span>${esc(item)}</span>
        </label>`).join("");
    } else {
      html += `<div class="empty-state">No checklist items were generated.</div>`;
    }
    if (a.attention_score) {
      html += `<h3>Agreement Attention Score</h3>
        <div class="card">
          <div class="card-title">${esc(a.attention_score.level)}</div>
          <ul>${a.attention_score.factors.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
          <div class="section-sub">${esc(a.attention_score.methodology_note || "")}</div>
          <div class="section-sub"><em>${esc(a.attention_score.disclaimer)}</em></div>
        </div>`;
    }
  }

  c.innerHTML = html || `<div class="empty-state">Nothing detected for this section.</div>`;
  c.querySelectorAll("[data-jump]").forEach((node) => {
    node.style.cursor = "pointer";
    node.addEventListener("click", () => renderPage(Number(node.dataset.jump)));
  });
  if (id === "terms") wireTermsFilters();
}

function renderImportantTermsPanel() {
  const terms = state.analysis.important_terms || [];
  state.termsFilter = state.termsFilter || { q: "", category: "All", importance: "All" };
  const f = state.termsFilter;

  const categories = ["All", ...new Set(terms.map((t) => t.category))];
  let filtered = terms.filter((t) => {
    if (f.q && !t.term.toLowerCase().includes(f.q.toLowerCase())) return false;
    if (f.category !== "All" && t.category !== f.category) return false;
    if (f.importance !== "All" && t.importance !== f.importance) return false;
    return true;
  });

  let html = `<h3>Important Terms</h3>
    <div class="section-sub">${terms.length} term${terms.length === 1 ? "" : "s"} detected. Click any term to jump to its page.</div>
    <div class="terms-filters">
      <input type="text" id="terms-search" placeholder="Search terms..." value="${esc(f.q)}" />
      <select id="terms-cat-filter">${categories.map((c) => `<option value="${esc(c)}" ${c === f.category ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <select id="terms-imp-filter">
        ${["All", "high", "medium", "low"].map((v) => `<option value="${v}" ${v === f.importance ? "selected" : ""}>${v === "All" ? "All importance" : v}</option>`).join("")}
      </select>
    </div>`;

  if (!filtered.length) {
    html += `<div class="empty-state">No important terms match this filter.</div>`;
  } else {
    ["high", "medium", "low"].forEach((level) => {
      const group = filtered.filter((t) => t.importance === level);
      if (!group.length) return;
      html += `<div class="term-group-label">${level} importance</div>`;
      group.forEach((t) => {
        html += `<div class="term-row"${jumpablePage(t.page)}>
          <div><span class="imp-dot ${level}"></span><span class="term-name">${esc(t.term)}</span>
            <div class="term-cat">${esc(t.category)}</div></div>
          <div class="term-page">${t.page ? "PAGE " + t.page : ""}</div>
        </div>`;
      });
    });
  }
  return html;
}

function wireTermsFilters() {
  const search = el("terms-search");
  const catSel = el("terms-cat-filter");
  const impSel = el("terms-imp-filter");
  if (!search) return;
  const rerender = () => {
    state.termsFilter = {
      q: search.value, category: catSel.value, importance: impSel.value,
    };
    renderTab("terms");
  };
  search.addEventListener("input", rerender);
  catSel.addEventListener("change", rerender);
  impSel.addEventListener("change", rerender);
  if (document.activeElement === document.body) search.focus();
}

function renderCards(items, mapper) {
  if (!items || !items.length) return `<div class="empty-state">Nothing detected in this category.</div>`;
  return items.map((raw) => {
    const it = mapper(raw);
    return `<div class="card ${it.cls || ""}"${jumpablePage(it.page)}>
      ${it.meta || ""}
      <div class="card-title">${esc(it.title || "")}</div>
      <div class="card-body">${it.body || ""}</div>
      ${it.extra || ""}
    </div>`;
  }).join("");
}

// ================================================================
// VOICE PLAYER - FULLY SYNCHRONISED, EXCELLENT QUALITY
// ================================================================

const voiceState = {
  utterance: null,
  playing: false,
  paused: false,
  text: "",
  sentences: [],
  currentIndex: 0,
  language: "en",
  speed: 1,
};

// Split text into sentences (supports English and Urdu punctuation)
function splitIntoSentences(text) {
  // Match sentences ending with . ! ? ۔ ؟ … and also newlines
  const raw = text.split(/(?<=[.!?۔؟…])\s+|(?<=[.!?۔؟…])\n+/)
                  .map(s => s.trim())
                  .filter(s => s.length > 0);
  if (raw.length === 0) return [text.trim()];
  return raw;
}

// Get a suitable voice for the language
function getVoiceForLanguage(lang) {
  const voices = window.speechSynthesis.getVoices();
  // Try exact match first (e.g., "ur-PK", "en-US")
  let voice = voices.find(v => v.lang === lang);
  if (voice) return voice;
  // Try base language (e.g., "ur", "en")
  const base = lang.split('-')[0];
  voice = voices.find(v => v.lang.startsWith(base));
  if (voice) return voice;
  // Fallback to first available voice
  return voices[0] || null;
}

// Fetch voice summary from backend
async function fetchVoiceSummary(lang) {
  const res = await fetch(`${API}/api/voice-summary/${state.docId}?lang=${lang}`);
  if (!res.ok) throw new Error("Voice summary not available");
  const data = await res.json();
  return data.voice_summary;
}

// Update the progress bar based on currentIndex
function updateProgress() {
  const total = voiceState.sentences.length || 1;
  const pct = Math.min(100, (voiceState.currentIndex / total) * 100);
  el("voice-progress").style.width = pct + "%";
}

// Actually speak from the current sentence index
function startSpeech() {
  if (voiceState.currentIndex >= voiceState.sentences.length) {
    el("voice-status").textContent = "Finished";
    voiceState.playing = false;
    voiceState.paused = false;
    el("voice-progress").style.width = "100%";
    return;
  }

  // Cancel any ongoing speech
  window.speechSynthesis.cancel();

  const textToSpeak = voiceState.sentences.slice(voiceState.currentIndex).join(" ");
  if (!textToSpeak.trim()) {
    el("voice-status").textContent = "No text to speak";
    voiceState.playing = false;
    return;
  }

  const utter = new SpeechSynthesisUtterance(textToSpeak);
  const lang = voiceState.language === "ur" ? "ur-PK" : "en-US";
  utter.lang = lang;
  const voice = getVoiceForLanguage(lang);
  if (voice) utter.voice = voice;

  const speed = parseFloat(el("voice-speed").value) || 1;
  utter.rate = speed;
  voiceState.speed = speed;

  let totalChars = textToSpeak.length;
  let started = false;

  utter.onboundary = (e) => {
    if (e.name === 'word' || e.name === 'sentence') {
      // Calculate progress: base from currentIndex + fraction within current chunk
      const baseProgress = (voiceState.currentIndex / voiceState.sentences.length) * 100;
      const remainingProgress = (e.charIndex / totalChars) * (100 / voiceState.sentences.length);
      const pct = Math.min(100, baseProgress + remainingProgress);
      el("voice-progress").style.width = pct + "%";
    }
  };

  utter.onend = () => {
    // When finished, set index to end
    voiceState.currentIndex = voiceState.sentences.length;
    voiceState.playing = false;
    voiceState.paused = false;
    el("voice-progress").style.width = "100%";
    el("voice-status").textContent = "Finished";
  };

  utter.onerror = (err) => {
    voiceState.playing = false;
    voiceState.paused = false;
    if (err.error !== 'canceled') {
      el("voice-status").textContent = "Error: " + err.error;
    }
    console.warn("Speech error:", err);
  };

  voiceState.utterance = utter;
  voiceState.playing = true;
  voiceState.paused = false;
  el("voice-status").textContent = "Playing...";
  window.speechSynthesis.speak(utter);
}

// Play / Resume
function playVoice() {
  if (!("speechSynthesis" in window)) {
    el("voice-status").textContent = "Browser does not support speech synthesis.";
    return;
  }

  // If paused, resume
  if (voiceState.paused) {
    window.speechSynthesis.resume();
    voiceState.paused = false;
    voiceState.playing = true;
    el("voice-status").textContent = "Playing...";
    return;
  }

  // If already playing, do nothing
  if (voiceState.playing) return;

  // If no text loaded, fetch it
  if (!voiceState.text) {
    el("voice-status").textContent = "Loading summary...";
    fetchVoiceSummary(voiceState.language)
      .then(summary => {
        voiceState.text = summary;
        voiceState.sentences = splitIntoSentences(summary);
        voiceState.currentIndex = 0;
        updateProgress();
        startSpeech();
      })
      .catch(err => {
        el("voice-status").textContent = "Error loading voice: " + err.message;
      });
    return;
  }

  // If we have text but finished, restart from beginning if at end
  if (voiceState.currentIndex >= voiceState.sentences.length) {
    voiceState.currentIndex = 0;
    el("voice-progress").style.width = "0%";
  }

  startSpeech();
}

// -------- Event listeners for voice controls --------
// Play
el("voice-play").addEventListener("click", playVoice);

// Pause
el("voice-pause").addEventListener("click", () => {
  if ("speechSynthesis" in window && voiceState.playing) {
    window.speechSynthesis.pause();
    voiceState.paused = true;
    voiceState.playing = false;
    el("voice-status").textContent = "Paused";
  }
});

// Stop - preserves current position
el("voice-stop").addEventListener("click", () => {
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  voiceState.playing = false;
  voiceState.paused = false;
  // Do NOT reset currentIndex
  updateProgress();
  const total = voiceState.sentences.length;
  el("voice-status").textContent = `Stopped at sentence ${Math.min(voiceState.currentIndex + 1, total)}`;
});

// Forward - skip 2 sentences
el("voice-forward").addEventListener("click", () => {
  if (!voiceState.text || voiceState.sentences.length === 0) return;
  const newIdx = Math.min(voiceState.currentIndex + 2, voiceState.sentences.length - 1);
  if (newIdx === voiceState.currentIndex) return;
  voiceState.currentIndex = newIdx;
  updateProgress();
  if (voiceState.playing || voiceState.paused) {
    // Cancel and restart from new index
    window.speechSynthesis.cancel();
    voiceState.playing = false;
    voiceState.paused = false;
    startSpeech();
  } else {
    el("voice-status").textContent = `Skipped to sentence ${voiceState.currentIndex + 1}`;
  }
});

// Backward - skip back 2 sentences
el("voice-backward").addEventListener("click", () => {
  if (!voiceState.text || voiceState.sentences.length === 0) return;
  const newIdx = Math.max(voiceState.currentIndex - 2, 0);
  if (newIdx === voiceState.currentIndex) return;
  voiceState.currentIndex = newIdx;
  updateProgress();
  if (voiceState.playing || voiceState.paused) {
    window.speechSynthesis.cancel();
    voiceState.playing = false;
    voiceState.paused = false;
    startSpeech();
  } else {
    el("voice-status").textContent = `Skipped to sentence ${voiceState.currentIndex + 1}`;
  }
});

// Speed control
el("voice-speed").addEventListener("change", () => {
  const speed = parseFloat(el("voice-speed").value);
  voiceState.speed = speed;
  if (voiceState.playing || voiceState.paused) {
    // Restart from current index with new speed
    window.speechSynthesis.cancel();
    voiceState.playing = false;
    voiceState.paused = false;
    startSpeech();
  }
});

// Language switching
el("voice-lang").addEventListener("change", async () => {
  const lang = el("voice-lang").value;
  if (lang === voiceState.language && voiceState.text) return;

  voiceState.language = lang;
  voiceState.playing = false;
  voiceState.paused = false;
  voiceState.currentIndex = 0;
  voiceState.text = "";
  voiceState.sentences = [];
  el("voice-progress").style.width = "0%";
  el("voice-status").textContent = `Loading ${lang.toUpperCase()} summary...`;

  try {
    const summary = await fetchVoiceSummary(lang);
    voiceState.text = summary;
    voiceState.sentences = splitIntoSentences(summary);
    voiceState.currentIndex = 0;
    updateProgress();
    el("voice-status").textContent = `Loaded ${lang.toUpperCase()} summary. Auto-playing...`;
    // Auto-play after loading
    playVoice();
  } catch (err) {
    el("voice-status").textContent = `Failed to load ${lang.toUpperCase()} summary: ${err.message}`;
  }
});

// Close voice player
el("voice-close").addEventListener("click", () => {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  hide("voice-player");
});

// Main "Listen to Analysis" button
el("listen-btn").addEventListener("click", async () => {
  if (!state.analysis) return;
  // Reset voice player but keep language
  hide("voice-player");
  voiceState.playing = false;
  voiceState.paused = false;
  voiceState.currentIndex = 0;
  voiceState.text = "";
  voiceState.sentences = [];
  el("voice-progress").style.width = "0%";

  const lang = el("voice-lang").value;
  el("voice-status").textContent = `Loading ${lang.toUpperCase()} summary...`;

  try {
    const summary = await fetchVoiceSummary(lang);
    voiceState.text = summary;
    voiceState.sentences = splitIntoSentences(summary);
    voiceState.currentIndex = 0;
    show("voice-player");
    // Auto start playing
    playVoice();
  } catch (err) {
    el("voice-status").textContent = `Unable to generate audio: ${err.message}. You can read the written analysis.`;
    show("voice-player");
  }
});

// ---------------- Ask Q&A ----------------
el("ask-btn").addEventListener("click", askQuestion);
el("ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") askQuestion(); });

async function askQuestion() {
  const input = el("ask-input");
  const question = input.value.trim();
  if (!question || !state.docId) return;

  const answerBox = el("ask-answer");
  answerBox.textContent = "Checking the document…";
  show("ask-answer");

  try {
    const res = await fetch(`${API}/api/ask/${state.docId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    let text = data.answer || "No answer returned.";
    if (data.pages_referenced && data.pages_referenced.length) {
      text += ` (pages ${data.pages_referenced.join(", ")})`;
    }
    if (data.confident === false) {
      text += " — the document does not clearly settle this; consider asking the institution directly.";
    }
    answerBox.textContent = text;
  } catch (err) {
    answerBox.textContent = "Couldn't get an answer: " + err.message;
  }
}