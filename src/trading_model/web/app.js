const form = document.getElementById("run-form");
const sceneEl = document.getElementById("scene");
const rawText = document.getElementById("raw-text");
const useLlm = document.getElementById("use-llm");
const runBtn = document.getElementById("run-btn");
const formStatus = document.getElementById("form-status");
const conclusion = document.getElementById("conclusion");
const metaLine = document.getElementById("meta-line");
const embedderChip = document.getElementById("embedder-chip");
const contractHits = document.getElementById("contract-hits");
const regHits = document.getElementById("reg-hits");
const reviewCard = document.getElementById("review-card");
const reviewChunks = document.getElementById("review-chunks");
const contractLabelEl = document.getElementById("contract-label");
const gospdCard = document.getElementById("gospd-card");
const gospdFields = document.getElementById("gospd-fields");
const chatFab = document.getElementById("chat-fab");
const chatDialog = document.getElementById("chat-dialog");
const chatClose = document.getElementById("chat-close");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatLog = document.getElementById("chat-log");
const scanFile = document.getElementById("scan-file");
const scanBtn = document.getElementById("scan-btn");

let paragraphs = [];
let lastHits = [];
let lastConclusion = "";

function errorDetail(data, fallback) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || item.message || JSON.stringify(item)).join("; ");
  }
  return fallback || "请求失败";
}

function setStatus(text, isError) {
  formStatus.textContent = text;
  formStatus.classList.toggle("error", Boolean(isError));
}

function setProgress(steps) {
  const byId = Object.fromEntries((steps || []).map((s) => [s.id, s]));
  for (const li of document.querySelectorAll(".progress li")) {
    const step = byId[li.dataset.step];
    li.className = step ? step.status : "pending";
  }
}

function renderHits(node, items, emptyText, lineFn) {
  node.replaceChildren();
  if (!items || !items.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = emptyText;
    node.appendChild(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    const meta = document.createElement("span");
    meta.className = "hit-meta";
    meta.textContent = lineFn(item).meta;
    const body = document.createElement("div");
    body.textContent = lineFn(item).body;
    li.append(meta, body);
    node.appendChild(li);
  }
}

function renderGospd(g) {
  if (!gospdCard || !gospdFields) return;
  if (!g || (!g.fillable && !g.X_exception && !g.C23_cutoff_period_note)) {
    gospdCard.classList.add("hidden");
    gospdFields.replaceChildren();
    return;
  }
  gospdCard.classList.remove("hidden");
  const rows = [
    ["E13 / M 运输条款", g.E13_transport_terms || "（空：不要留模板默认「签收确认」）"],
    ["N 交货单据类型", g.N_delivery_document_type || "—"],
    ["O 交货单据编号", g.O_delivery_document_no || "（未抽到单号）"],
    ["P 控制权日期", g.P_control_date ? `${g.P_control_date}（${g.P_date_meaning || ""}）` : "（缺日期，P 列暂空）"],
    ["C23 截止期", g.C23_cutoff_period_note || "—"],
    ["X 异常", g.X_exception || "无"],
    ["已有交货单据", (g.available_delivery_evidence || []).join("、") || "无"],
    ["仍缺交货单据", (g.missing_delivery_evidence || []).join("、") || "无"],
  ];
  gospdFields.replaceChildren();
  for (const [dt, dd] of rows) {
    const term = document.createElement("dt");
    term.textContent = dt;
    const def = document.createElement("dd");
    def.textContent = dd;
    gospdFields.append(term, def);
  }
}

function applyResult(data) {
  setProgress(data.steps);
  const view = data.view || {};
  lastConclusion = view.trading_mode_conclusion || "无结论";
  conclusion.textContent = lastConclusion;
  conclusion.className = `status-${view.status || "insufficient_evidence"}`;
  const embedder = (data.rag && data.rag.embedder) || "";
  const label = data.contract_label || (data.classification && data.classification.contract_label) || "";
  metaLine.textContent = `状态 ${view.status || "—"} · 置信度 ${view.confidence || "—"} · 嵌入 ${embedder}`;
  contractLabelEl.textContent = label ? `合同名义标签（对照，不是答案）：${label}` : "";
  renderGospd(data.gospd01030);
  const advisory = data.llm_advisory;
  if (advisoryCard && advisory && (advisory.actual_scenario || advisory.can_conclude === false)) {
    advisoryCard.classList.remove("hidden");
    const can = advisory.can_conclude === false ? "模型认为无法判断" : "模型参考情景";
    const excerpt = advisory.excerpt_ok === false ? " · 摘录未通过校验，仅供对照" : "";
    advisoryBody.textContent = `${can}${excerpt}\n${advisory.actual_scenario || "（无情景文本）"}`;
  } else if (advisoryCard) {
    advisoryCard.classList.add("hidden");
    if (advisoryBody) advisoryBody.textContent = "";
  }
  if (embedder) embedderChip.textContent = `向量模型：${embedder}`;
  const review = (data.rag && data.rag.review_chunks) || [];
  if (data.can_conclude === false && review.length) {
    reviewCard.classList.remove("hidden");
    renderHits(
      reviewChunks,
      review,
      "没有切段",
      (hit) => ({
        meta: `${hit.source_file || ""} · 段 ${hit.seq ?? "—"}`,
        body: hit.raw_text || "",
      })
    );
  } else {
    reviewCard.classList.add("hidden");
    reviewChunks.replaceChildren();
  }
  renderHits(
    contractHits,
    (data.rag && data.rag.contract_hits) || [],
    "没有合同段落命中",
    (hit) => ({
      meta: `RRF ${Number(hit.rrf_score || 0).toFixed(4)} · 段 ${hit.seq ?? "—"}`,
      body: hit.raw_text || "",
    })
  );
  renderHits(
    regHits,
    (data.rag && data.rag.hits) || [],
    "没有准则语料命中",
    (hit) => ({
      meta: `${hit.backend || "fts"} · ${hit.source || ""} · ${hit.title || ""}`,
      body: hit.excerpt || "",
    })
  );
  paragraphs = data.paragraphs || [];
  lastHits = (data.rag && data.rag.hits) || [];
  const ocrText = ((data.classified || []).map((d) => d.raw_text).filter(Boolean).join("\n\n"));
  if (ocrText) rawText.value = ocrText;
  if (chatFab) chatFab.disabled = false;
}

async function loadStatus() {
  try {
    const res = await fetch("/v1/status");
    const data = await res.json();
    embedderChip.textContent = `向量模型：${data.embedder || "未知"} · dim ${data.dim ?? "—"} · ${data.profile || "demo"}`;
  } catch (err) {
    embedderChip.textContent = "向量模型：无法连接本机服务";
  }
}

async function loadScenes() {
  try {
    const res = await fetch("/v1/sim/scenes");
    const data = await res.json();
    sceneEl.replaceChildren();
    for (const scene of data.scenes || []) {
      const opt = document.createElement("option");
      opt.value = scene.id;
      opt.textContent = `${scene.title} — ${scene.summary}`;
      sceneEl.appendChild(opt);
    }
    const custom = document.createElement("option");
    custom.value = "custom";
    custom.textContent = "自定义粘贴（已有识别文本）";
    sceneEl.appendChild(custom);
  } catch (err) {
    setStatus("样例列表加载失败：" + String(err.message || err), true);
  }
}

if (scanBtn && scanFile) {
  scanBtn.addEventListener("click", async () => {
    const file = scanFile.files && scanFile.files[0];
    if (!file) {
      setStatus("请先选择 PDF 或图片", true);
      return;
    }
    scanBtn.disabled = true;
    setProgress([
      { id: "scene", status: "done" },
      { id: "ocr", status: "running" },
      { id: "rag", status: "pending" },
      { id: "judge", status: "pending" },
    ]);
    setStatus("正在导入扫描件（不调用 DeepSeek）…");
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("document_id", file.name.replace(/\.[^.]+$/, "") || "SCAN-1");
      const res = await fetch("/v1/ingest/file", { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorDetail(data, res.statusText));
      if (data.preview) rawText.value = data.preview;
      if (sceneEl) sceneEl.value = "custom";
      setProgress([
        { id: "scene", status: "done" },
        { id: "ocr", status: "done" },
        { id: "rag", status: "done" },
        { id: "judge", status: "pending" },
      ]);
      const how = data.extract_method === "ocr" ? "PaddleOCR" : data.extract_method === "native" ? "PDF 文字层" : "文本";
      setStatus(`已入库 ${data.ingested || 0} 段（${how}）。未调用 DeepSeek。可再运行判断。`);
    } catch (err) {
      setStatus(String(err.message || err), true);
      setProgress([
        { id: "scene", status: "pending" },
        { id: "ocr", status: "error" },
        { id: "rag", status: "pending" },
        { id: "judge", status: "pending" },
      ]);
    } finally {
      scanBtn.disabled = false;
    }
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  runBtn.disabled = true;
  setProgress([
    { id: "scene", status: "running" },
    { id: "ocr", status: "pending" },
    { id: "rag", status: "pending" },
    { id: "judge", status: "pending" },
  ]);
  setStatus("正在跑模拟工作流…");
  try {
    const res = await fetch("/v1/sim/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene_id: sceneEl.value,
        raw_text: rawText.value,
        use_llm: useLlm.checked,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorDetail(data, res.statusText));
    applyResult(data);
    setStatus("完成");
  } catch (err) {
    setStatus(String(err.message || err), true);
  } finally {
    runBtn.disabled = false;
  }
});

if (chatFab) {
  chatFab.addEventListener("click", () => {
    chatDialog.showModal();
    if (!paragraphs.length) {
      chatLog.replaceChildren();
      addBubble("assistant", "请先运行模拟工作流，助手才能根据当前文件切段回答。", []);
    }
    chatInput.focus();
  });
}
if (chatClose) {
  chatClose.addEventListener("click", () => chatDialog.close());
}

function addBubble(role, text, citations) {
  const wrap = document.createElement("div");
  wrap.className = `bubble ${role}`;
  const body = document.createElement("div");
  body.textContent = text;
  wrap.appendChild(body);
  if (role === "assistant" && citations && citations.length) {
    const dots = document.createElement("div");
    dots.className = "cite-dots";
    const panel = document.createElement("div");
    panel.className = "cite-panel";
    panel.hidden = true;
    for (const cite of citations) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cite-dot";
      btn.textContent = String(cite.n);
      btn.setAttribute("aria-label", `注释 ${cite.n}`);
      btn.addEventListener("click", () => {
        const on = btn.classList.contains("active");
        dots.querySelectorAll(".cite-dot").forEach((el) => el.classList.remove("active"));
        if (on) {
          panel.hidden = true;
          return;
        }
        btn.classList.add("active");
        panel.hidden = false;
        panel.textContent = `${cite.source_file} · 第 ${cite.page} 页 · 第 ${cite.seq} 段\n${cite.excerpt}`;
      });
      dots.appendChild(btn);
    }
    wrap.append(dots, panel);
  }
  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = chatInput.value.trim();
  if (!question) return;
  if (!paragraphs.length) {
    addBubble("assistant", "请先运行模拟工作流，助手才能根据当前文件切段回答。", []);
    return;
  }
  addBubble("user", question);
  chatInput.value = "";
  try {
    const res = await fetch("/v1/auditor/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, paragraphs, hits: lastHits }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorDetail(data, res.statusText));
    addBubble("assistant", data.answer || "（无回答）", data.citations || []);
    if (conclusion.textContent !== lastConclusion) {
      conclusion.textContent = lastConclusion;
    }
  } catch (err) {
    addBubble("assistant", String(err.message || err), []);
  }
});

loadStatus();
loadScenes();
