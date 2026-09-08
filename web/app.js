const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const titles = {
  overview: "运行总览",
  review: "发起审查",
  tasks: "任务中心",
  skills: "Skill 注册中心",
  labels: "标注台",
  flywheel: "飞轮控制台",
  memory: "记忆库",
  governance: "治理",
  evolution: "演进实验室",
};

const stateLabels = {
  PENDING: "等待中",
  PLANNING: "规划中",
  EXECUTING: "执行中",
  REVIEWING: "汇总中",
  SUCCESS: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

let selectedTask = null;
let selectedTaskData = null;
let accessToken = localStorage.getItem("aegis_token") || "";
let toastTimer = null;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function formatTime(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(date);
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("json") ? await response.json() : await response.text();

  if (response.status === 401) {
    $("#login-overlay").classList.remove("hidden");
    $("#logout").classList.add("hidden");
  }
  if (!response.ok) {
    const plainText = typeof data === "string" && !/<[a-z][\s\S]*>/i.test(data) ? data.trim() : "";
    const message = typeof data === "object"
      ? data.error || data.detail
      : plainText || `请求失败 (${response.status})`;
    throw new Error(message || response.statusText || "请求失败");
  }
  return data;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2600);
}

function setButtonBusy(button, busy, busyText) {
  if (!button) return;
  button.setAttribute("aria-busy", String(busy));
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.textContent = busyText;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

function show(view, updateHash = true) {
  if (!titles[view]) {
    view = "overview";
    history.replaceState(null, "", "#overview");
  }
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$(".nav-item").forEach((element) => {
    const active = element.dataset.view === view;
    element.classList.toggle("active", active);
    element.setAttribute("aria-current", active ? "page" : "false");
  });
  $(`#view-${view}`).classList.add("active");
  $("#page-title").textContent = titles[view];
  document.title = `${titles[view]} · AegisAgent`;
  if (updateHash) history.replaceState(null, "", `#${view}`);

  if (view === "tasks") loadTasks();
  if (view === "skills") loadSkills();
  if (view === "labels") loadLabelTasks();
  if (view === "flywheel") loadFlywheel();
  if (view === "memory") resetMemoryView();
  if (view === "governance") loadGovernanceReports();
  if (view === "evolution") { loadFailures(); loadExperience(); }
  window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => show(button.dataset.view)));
$$("[data-jump]").forEach((button) => button.addEventListener("click", () => show(button.dataset.jump)));
window.addEventListener("hashchange", () => show(location.hash.slice(1), false));

function taskRows(tasks) {
  if (!tasks?.length) {
    return '<div class="empty-state"><span><b>还没有审查任务</b>提交一个 Diff 开始首次审查</span></div>';
  }
  return tasks.map((task) => {
    const state = String(task.state || "PENDING").toUpperCase();
    const repository = escapeHtml(task.repository || "未命名仓库");
    const pr = task.pull_request ? `PR #${escapeHtml(task.pull_request)}` : "手动审查";
    return `
      <button class="task-row" data-task="${escapeHtml(task.id)}" type="button">
        <span class="task-main">
          <span class="task-glyph">PR</span>
          <span class="task-copy">
            <span class="task-name">${repository}</span>
            <span class="task-meta"><span>${pr}</span><span>${escapeHtml(formatTime(task.created_at))}</span></span>
          </span>
        </span>
        <span class="status state-${state.toLowerCase()}">${stateLabels[state] || escapeHtml(state)}</span>
      </button>`;
  }).join("");
}

function bindTasks(root) {
  $$("[data-task]", root).forEach((row) => row.addEventListener("click", () => openTask(row.dataset.task)));
}

function statCard(label, value, note, style, icon) {
  return `<article class="stat ${style}">
    <div class="stat-head"><span>${label}</span><i>${icon}</i></div>
    <b>${value}</b><small>${note}</small>
  </article>`;
}

function renderLlmRuntime(llm = {}, runMode = {}) {
  const enabled = Boolean(llm.enabled);
  const failed = Boolean(llm.error);
  const provider = String(llm.provider || "local");
  const model = String(llm.model || "");
  const detail = failed
    ? "暂时无法读取模型配置"
    : enabled
      ? `${provider} / ${model || "默认模型"}，参与上下文审查与风险判断`
      : "未配置模型；agentic 审查暂不可用";
  const state = failed ? "读取失败" : enabled ? "已启用" : "待配置";
  const runtime = failed
    ? "运行时状态未知"
    : enabled
      ? `${provider} / ${model || "模型已配置"}`
      : "agentic / 需要模型配置";

  const chain = $("#execution-chain");
  if (chain) {
    const scanner = '<div class="agent-step"><b>01</b><span><strong>Tool / Scanner</strong><small>规则、AST 与代码搜索提供事实</small></span><em>事实</em></div>';
    const gate = '<i class="flow-line"></i><div class="agent-step"><b>03</b><span><strong>Gate</strong><small>格式、证据、置信度与发布门禁</small></span><em class="done">门禁</em></div>';
    const llmStep = `<i class="flow-line"></i><div class="agent-step is-active" id="llm-agent-step"><b>02</b><span><strong>4-role LLM Agents</strong><small id="llm-agent-detail">${escapeHtml(detail)}</small></span><em id="llm-agent-state">${escapeHtml(state)}</em></div>`;
    chain.innerHTML = scanner + llmStep + gate;
  }

  const step = $("#llm-agent-step");
  if (step) {
    step.classList.remove("is-pending");
    step.classList.toggle("is-active", enabled);
    step.classList.toggle("is-disabled", !enabled && !failed);
    step.classList.toggle("is-error", failed);
    const detailNode = $("#llm-agent-detail");
    const stateNode = $("#llm-agent-state");
    if (detailNode) detailNode.textContent = detail;
    if (stateNode) stateNode.textContent = state;
  }

  const status = $("#llm-runtime-status");
  status.className = `runtime-status ${failed ? "is-error" : enabled ? "is-active" : "is-disabled"}`;
  status.textContent = state;
  const capability = $("#llm-capability");
  capability.classList.toggle("is-active", enabled);
  capability.classList.toggle("is-disabled", !enabled && !failed);
  capability.classList.toggle("is-error", failed);
  $("#llm-capability-detail").textContent = detail;
  $("#llm-runtime-model").textContent = runtime;
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    renderLlmRuntime(data.llm, data.run_mode);
    const modeSelect = $("#review-mode");
    if (modeSelect) {
      modeSelect.value = "agentic";
      modeSelect.disabled = !data.llm?.enabled;
    }
    $("#system-status").textContent = `${data.queue} · ${data.orchestrator}`;
    const stats = data.stats || {};
    const rate = Math.round(Number(stats.success_rate || 0) * 100);
    $("#stats").innerHTML = [
      statCard("总任务", stats.tasks_total ?? 0, "累计审查任务", "", "ALL"),
      statCard("已完成", stats.tasks_success ?? 0, "通过质量门禁", "success", "OK"),
      statCard("失败", stats.tasks_failed ?? 0, "需要进一步处理", "failed", "ERR"),
      statCard("成功率", `${rate}%`, "全部任务成功率", "rate", "RATE"),
      statCard("待处理案例", stats.unresolved_failure_cases ?? 0, "未解决反馈", "feedback", "OPEN"),
      statCard("活跃 Skills", stats.active_skill_versions ?? 0, "当前生效版本", "skills", "SK"),
    ].join("");
    $("#recent-tasks").innerHTML = taskRows((data.tasks || []).slice(0, 5));
    bindTasks($("#recent-tasks"));
  } catch (error) {
    renderLlmRuntime({ error: true }, {});
    $("#system-status").textContent = "服务连接异常";
    $("#stats").innerHTML = '<div class="empty-state"><span><b>暂时无法读取数据</b>请检查服务状态后重试</span></div>';
    $("#recent-tasks").innerHTML = '<div class="empty-state"><span>数据加载失败</span></div>';
    toast(error.message);
  }
}

async function loadTasks() {
  const root = $("#all-tasks");
  root.innerHTML = '<div class="list-loading"></div><div class="list-loading"></div>';
  try {
    const data = await api("/api/tasks");
    root.innerHTML = taskRows(data.tasks || []);
    bindTasks(root);
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>任务加载失败</span></div>';
    toast(error.message);
  }
}

async function openTask(id) {
  show("tasks");
  $("#task-report").textContent = "正在加载任务报告…";
  $("#feedback-panel").classList.add("hidden");
  try {
    const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
    selectedTask = id;
    selectedTaskData = task;
    $("#task-report").textContent = formatJson(task);
    $("#create-fix").classList.toggle("hidden", !(task.report && task.pull_request));
    const feedbackReady = task.state === "SUCCESS" && task.report;
    $("#feedback-panel").classList.toggle("hidden", !feedbackReady);
    if (feedbackReady) {
      populateFeedbackFindings(task.report.findings || []);
      await loadTaskFeedback(id);
    }
  } catch (error) {
    $("#task-report").textContent = error.message;
    selectedTaskData = null;
  }
}

const feedbackLabels = {
  false_positive: "误报",
  missed_issue: "漏报",
  bad_fix: "坏修复",
  accepted: "已接受",
};

function populateFeedbackFindings(findings) {
  const select = $("#feedback-finding");
  select.innerHTML = '<option value="">不关联已有结论</option>' + findings.map((finding, index) => {
    const identity = `${finding.rule_id || "未命名规则"} · ${finding.path || "未知文件"}:${finding.line || "?"}`;
    return `<option value="${index}">${escapeHtml(identity)}</option>`;
  }).join("");
  $("#feedback-result").textContent = "";
}

function renderTaskFeedback(cases) {
  const root = $("#task-feedback-history");
  if (!cases.length) {
    root.innerHTML = '<p class="feedback-empty">尚无反馈。提交后，它会在这里保留并进入后续评测。</p>';
    return;
  }
  root.innerHTML = `<p class="list-section-label">本任务反馈</p>${cases.map((item) => {
    const payload = item.payload || {};
    const finding = payload.finding || {};
    const reference = finding.rule_id
      ? `${finding.rule_id}${finding.path ? ` · ${finding.path}:${finding.line || "?"}` : ""}`
      : "未关联审查结论";
    return `<div class="feedback-case">
      <span class="feedback-case-type">${escapeHtml(feedbackLabels[item.category] || item.category)}</span>
      <span class="feedback-case-copy"><b>${escapeHtml(reference)}</b><small>${escapeHtml(payload.note || "未填写说明")}</small></span>
      <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待评测"}</span>
    </div>`;
  }).join("")}`;
}

async function loadTaskFeedback(taskId) {
  const root = $("#task-feedback-history");
  root.innerHTML = '<p class="feedback-empty">正在读取本任务反馈…</p>';
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(taskId)}/feedback`);
    if (selectedTask === taskId) renderTaskFeedback(data.cases || []);
  } catch (error) {
    root.innerHTML = `<p class="feedback-empty">无法读取反馈历史：${escapeHtml(error.message)}</p>`;
  }
}

async function loadSkills() {
  const root = $("#skill-list");
  root.innerHTML = '<div class="skill-card loading"></div><div class="skill-card loading"></div>';
  try {
    const data = await api("/api/skills");
    renderLlmRuntime(data.llm);
    const skills = (data.skills || []).filter((skill) => skill.name !== "llm-review");
    root.innerHTML = skills.length ? skills.map((skill) => `
      <article class="skill-card">
        <span class="skill-label">${skill.sandboxed ? "SANDBOXED SKILL" : "ACTIVE SKILL"}</span>
        <h3>${escapeHtml(skill.name)}</h3>
        <p>${escapeHtml(skill.description || "暂无能力描述")}</p>
        <span class="skill-meta">v${escapeHtml(skill.version)} · ${escapeHtml(skill.source)}</span>
      </article>`).join("") : '<div class="empty-state"><span><b>尚未加载 Skill</b>扫描目录以加载可用能力</span></div>';
  } catch (error) {
    renderLlmRuntime({ error: true });
    root.innerHTML = '<div class="empty-state"><span>Skills 加载失败</span></div>';
    toast(error.message);
  }
}

async function loadFailures() {
  try {
    const [failuresData, status, runsData] = await Promise.all([
      api("/api/failures"),
      api("/v1/evolution/status"),
      api("/v1/evolution/runs?limit=5"),
    ]);
    $("#evolution-status").textContent = formatJson(status);
    const cases = failuresData.cases || [];
    const runs = runsData.runs || [];
    const failureHtml = cases.length
      ? cases.slice(0, 8).map((item) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">FC</span><span class="task-copy">
              <span class="task-name">${escapeHtml(feedbackLabels[item.category] || item.category)}</span>
              <span class="task-meta"><span>${escapeHtml(item.task_id)}</span><span>${escapeHtml((item.payload || {}).note || "无说明")}</span></span>
            </span></span>
            <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待处理"}</span>
          </div>`).join("")
      : '<div class="empty-state"><span><b>暂无失败反馈</b>系统当前没有未处理案例</span></div>';
    const historyHtml = runs.length
      ? `<p class="list-section-label">最近评测</p>${runs.map((run) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">V${escapeHtml(run.candidate_version)}</span><span class="task-copy">
              <span class="task-name">${escapeHtml(run.decision)}</span>
              <span class="task-meta">${Number(run.candidate_score).toFixed(3)} vs ${Number(run.baseline_score).toFixed(3)}</span>
            </span></span>
          </div>`).join("")}`
      : "";
    $("#failure-list").innerHTML = failureHtml + historyHtml;
  } catch (error) {
    $("#evolution-status").textContent = "暂时无法读取评测状态。";
    $("#failure-list").innerHTML = '<div class="empty-state"><span>反馈加载失败</span></div>';
    toast(error.message);
  }
}

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const body = { repository: values.get("repository"), diff: values.get("diff"), mode: values.get("mode") };
  if (values.get("pull_request")) body.pull_request = Number(values.get("pull_request"));
  const asyncQuery = values.get("async") ? "?async=true" : "";
  const output = $("#review-result");
  output.classList.remove("empty");
  output.textContent = "正在提交审查任务…";
  setButtonBusy(button, true, "正在提交…");
  try {
    const data = await api(`/v1/reviews${asyncQuery}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    output.textContent = formatJson(data);
    toast("审查任务已成功提交");
    loadDashboard();
  } catch (error) {
    output.textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#create-fix").addEventListener("click", async () => {
  if (!selectedTask) return;
  const button = $("#create-fix");
  setButtonBusy(button, true, "正在创建…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(selectedTask)}/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    $("#task-report").textContent = formatJson(data);
    toast("修复分支已创建");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#feedback-category").addEventListener("change", (event) => {
  const missed = event.target.value === "missed_issue";
  $("#feedback-missed-fields").classList.toggle("hidden", !missed);
  $("#feedback-hint").textContent = missed
    ? "补充规则和位置可让候选评测学习更精确的检查点。"
    : "提交后可在本任务和演进实验室查看状态。";
});

$("#feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedTask || !selectedTaskData?.report) return;
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const category = String(values.get("category"));
  const selectedIndex = values.get("finding_index");
  const findings = selectedTaskData.report.findings || [];
  const finding = selectedIndex === "" ? {} : { ...(findings[Number(selectedIndex)] || {}) };
  if (category === "missed_issue") {
    const ruleId = String(values.get("rule_id") || "").trim();
    const path = String(values.get("path") || "").trim();
    const line = Number(values.get("line"));
    if (ruleId) finding.rule_id = ruleId;
    if (path) finding.path = path;
    if (Number.isInteger(line) && line > 0) finding.line = line;
  }
  const output = $("#feedback-result");
  output.textContent = "正在保存反馈…";
  setButtonBusy(button, true, "正在提交…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(selectedTask)}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category,
        finding: Object.keys(finding).length ? finding : null,
        note: String(values.get("note") || "").trim(),
      }),
    });
    output.textContent = `${feedbackLabels[data.category] || data.category}已记录；可在演进实验室等待候选评测。`;
    form.reset();
    $("#feedback-missed-fields").classList.add("hidden");
    $("#feedback-hint").textContent = "提交后可在本任务和演进实验室查看状态。";
    await Promise.all([loadTaskFeedback(selectedTask), loadDashboard()]);
    toast("反馈已记录");
  } catch (error) {
    output.textContent = `提交失败：${error.message}`;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#reload-skills").addEventListener("click", async () => {
  const button = $("#reload-skills");
  setButtonBusy(button, true, "正在扫描…");
  try {
    await api("/v1/skills/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await loadSkills();
    toast("Skills 已重新加载");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#evolution-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在评测…");
  try {
    const data = await api("/v1/evolution/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: values.get("skill_name"), prompt: values.get("prompt") }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("新旧版本回放评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#auto-evolve").addEventListener("click", async () => {
  const button = $("#auto-evolve");
  setButtonBusy(button, true, "正在生成…");
  try {
    const data = await api("/v1/evolution/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: "llm-review" }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("反馈候选评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#refresh").addEventListener("click", async () => {
  const view = location.hash.slice(1) || "overview";
  if (view === "overview") await loadDashboard();
  else if (view === "tasks") await loadTasks();
  else if (view === "skills") await loadSkills();
  else if (view === "labels") await loadLabelTasks();
  else if (view === "flywheel") await loadFlywheel();
  else if (view === "memory") resetMemoryView();
  else if (view === "governance") await loadGovernanceReports();
  else if (view === "evolution") { await loadFailures(); await loadExperience(); }
  else await loadDashboard();
  toast("数据已刷新");
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在登录…");
  try {
    const data = await api("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: values.get("username"),
        password: values.get("password"),
        tenant_id: values.get("tenant_id"),
      }),
    });
    accessToken = data.access_token;
    localStorage.setItem("aegis_token", accessToken);
    $("#login-overlay").classList.add("hidden");
    $("#logout").classList.remove("hidden");
    $("#login-error").textContent = "";
    await loadDashboard();
  } catch (error) {
    $("#login-error").textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#logout").addEventListener("click", () => {
  accessToken = "";
  localStorage.removeItem("aegis_token");
  $("#login-overlay").classList.remove("hidden");
  $("#logout").classList.add("hidden");
});

// ---- 标注台 / 飞轮控制台（自进化数据飞轮） ----
let selectedLabelTask = null;
let selectedLabelFindings = [];

async function loadLabelTasks() {
  const root = $("#label-tasks");
  root.innerHTML = '<div class="list-loading"></div><div class="list-loading"></div>';
  try {
    const data = await api("/api/tasks");
    const tasks = (data.tasks || []).filter(
      (item) => String(item.state || "").toUpperCase() === "SUCCESS"
    );
    if (!tasks.length) {
      root.innerHTML = '<div class="empty-state"><span><b>还没有已完成的审查</b>先在「发起审查」跑一次任务，再回来逐条标注</span></div>';
      return;
    }
    root.innerHTML = tasks.map((task) => `
      <button class="task-row" data-label-task="${escapeHtml(task.id)}" type="button">
        <span class="task-main"><span class="task-glyph">LB</span><span class="task-copy">
          <span class="task-name">${escapeHtml(task.repository)}</span>
          <span class="task-meta"><span>${task.pull_request ? `PR #${escapeHtml(task.pull_request)}` : "手动审查"}</span><span>${escapeHtml(formatTime(task.created_at))}</span></span>
        </span></span>
        <span class="status state-success">已完成</span>
      </button>`).join("");
    $$("[data-label-task]", root).forEach((row) =>
      row.addEventListener("click", () => openLabelTask(row.dataset.labelTask)));
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>任务加载失败</span></div>';
    toast(error.message);
  }
}

async function openLabelTask(id) {
  selectedLabelTask = id;
  const root = $("#label-findings");
  root.innerHTML = '<div class="empty-state"><span>正在加载报告…</span></div>';
  $("#label-history").innerHTML = "";
  try {
    const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
    $("#label-task-meta").textContent = `${task.repository || ""} · ${task.id}`;
    const findings = ((task.report || {}).findings) || [];
    selectedLabelFindings = findings;
    if (!findings.length) {
      root.innerHTML = '<div class="empty-state"><span><b>该任务没有通过门禁的结论</b>无需标注</span></div>';
    } else {
      root.innerHTML = findings.map((finding, index) => `
        <article class="label-finding">
          <header>
            <b>${escapeHtml(finding.rule_id || "REVIEW")}</b>
            <span class="chip sev">${escapeHtml(finding.severity || "")}</span>
            ${finding.risk_class ? `<span class="chip ai">AI · ${escapeHtml(finding.risk_class)}</span>` : ""}
            <span class="label-loc">${escapeHtml(finding.path || "")}:${finding.line || "?"}</span>
          </header>
          <p class="label-title">${escapeHtml(finding.title || "")}</p>
          <p class="label-explain">${escapeHtml(String(finding.explanation || "").slice(0, 220))}</p>
          <footer>
            <span class="muted">${escapeHtml(String(finding.evidence || "").slice(0, 160))}</span>
            <span class="label-actions">
              <button class="button small accept" data-act="accept" data-idx="${index}">采纳</button>
              <button class="button small danger" data-act="reject" data-idx="${index}">误报</button>
            </span>
          </footer>
        </article>`).join("");
      $$("[data-act]", root).forEach((button) =>
        button.addEventListener("click", () =>
          postFindingLabel(Number(button.dataset.idx), button.dataset.act)));
    }
    await loadLabelHistory(id);
  } catch (error) {
    root.innerHTML = `<div class="empty-state"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

async function postFindingLabel(index, action) {
  const taskId = selectedLabelTask;
  const finding = selectedLabelFindings[index];
  if (!taskId || !finding) return;
  const accept = action === "accept";
  try {
    await api(`/v1/tasks/${encodeURIComponent(taskId)}/labels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dimension: "finding_accept",
        score: accept ? 1 : 0,
        label: accept ? "accept" : "reject",
        scored_by: "human",
        note: accept ? "人工确认采纳" : "人工标注误报",
        metadata: {
          rule_id: finding.rule_id || "", path: finding.path || "",
          line: finding.line, severity: finding.severity,
          risk_class: finding.risk_class || null,
        },
      }),
    });
    toast(accept ? "已标注采纳" : "已标注误报");
    await loadLabelHistory(taskId);
  } catch (error) {
    toast(error.message);
  }
}

async function loadLabelHistory(taskId) {
  const root = $("#label-history");
  root.innerHTML = '<p class="feedback-empty">正在读取标注历史…</p>';
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(taskId)}/labels`);
    const labels = data.labels || [];
    root.innerHTML = labels.length
      ? `<p class="list-section-label">本任务标注（${labels.length}）</p>` + labels.map((item) => `
          <span class="chip label-${escapeHtml(item.label)}">${escapeHtml(item.dimension)}:${escapeHtml(item.label)} · ${escapeHtml(item.scored_by)}</span>`).join("")
      : '<p class="feedback-empty">尚无标注；自动标注（规则）与人工标注都会在此显示。</p>';
  } catch (error) {
    root.innerHTML = `<p class="feedback-empty">无法读取标注：${escapeHtml(error.message)}</p>`;
  }
}

async function loadFlywheel() {
  const stats = $("#fw-stats");
  stats.innerHTML = '<div class="stat loading"></div>'.repeat(7);
  try {
    const status = await api("/v1/flywheel/status");
    const activePre = (status.active_adapters || {}).preflight;
    const pool = status.pool || {};
    const cards = [
      statCard("采集轨迹", status.traces ?? 0, "内容级 llm_traces", "", "TR"),
      statCard("标注", status.labels ?? 0, `覆盖 ${status.labeled_tasks ?? 0} 个任务`, "", "LB"),
      statCard("classify 样本", pool.classify ?? 0, "预筛 / 分级数据集", "", "CL"),
      statCard("instruct 样本", pool.instruct ?? 0, "finding 生成数据集", "", "IN"),
      statCard("preference 样本", pool.preference ?? 0, "偏好对数据集", "", "PF"),
      statCard("episode 样本", pool.episode ?? 0, "整段轨迹数据集", "", "EP"),
      statCard("部署模式", status.model_deployment || "api-only",
        activePre ? `preflight 活跃 v${activePre.version}` : "无活跃适配器",
        activePre ? "success" : "", activePre ? "AD" : "--"),
    ];
    stats.innerHTML = cards.join("");
    renderFwAdapters(status.adapters || {}, activePre);
    $("#fw-pool").innerHTML = "serve_url: " + (status.serve_url || "") +
      "<br>scheduler: " + escapeHtml(JSON.stringify(status.scheduler_last_attempt || {}));
    try {
      const replay = await api("/v1/flywheel/replay");
      const replayNode = $("#fw-replay");
      replayNode.classList.remove("empty");
      replayNode.textContent = "轨迹回放一致性守卫：checked=" + (replay.checked ?? 0) +
        " consistent=" + (replay.consistent ?? 0) + " inconsistent=" + (replay.inconsistent ?? 0) +
        " guard_passed=" + (replay.guard_passed ?? false) +
        (replay.mismatches?.length ? "\n" + JSON.stringify(replay.mismatches, null, 2) : "");
    } catch (error) {
      $("#fw-replay").textContent = "回放守卫读取失败：" + error.message;
    }
  } catch (error) {
    stats.innerHTML = `<div class="empty-state"><span>飞轮状态加载失败：${escapeHtml(error.message)}</span></div>`;
    toast(error.message);
  }
}

function renderFwAdapters(byLayer, activePre) {
  const root = $("#fw-adapters");
  const parts = [];
  for (const layer of ["preflight", "reviewer"]) {
    const adapters = byLayer[layer] || [];
    parts.push(`<p class="list-section-label">layer · ${escapeHtml(layer)}</p>`);
    if (!adapters.length) {
      parts.push('<p class="feedback-empty">暂无适配器版本（先运行数据流水线并训练）</p>');
      continue;
    }
    parts.push(...adapters.map((item) => `
      <div class="task-row">
        <span class="task-main"><span class="task-glyph">v${escapeHtml(item.version)}</span><span class="task-copy">
          <span class="task-name">${escapeHtml(item.serving_model_id || "")}</span>
          <span class="task-meta"><span>${escapeHtml(item.status || "")}</span><span>score ${Number(item.score || 0).toFixed(3)}</span></span>
        </span></span>
        ${(activePre && Number(item.version) === Number(activePre.version) && item.active)
          ? '<span class="status state-success">ACTIVE</span>'
          : `<button class="button small" data-activate-layer="${escapeHtml(layer)}" data-activate-version="${escapeHtml(item.version)}">激活</button>`}
      </div>`).join(""));
  }
  root.innerHTML = parts.join("");
  $$("[data-activate-layer]", root).forEach((button) =>
    button.addEventListener("click", async () => {
      try {
        const data = await api(
          `/v1/adapters/${encodeURIComponent(button.dataset.activateLayer)}/versions/${encodeURIComponent(button.dataset.activateVersion)}/activate`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
        );
        toast(data.activated ? "适配器已激活" : "激活失败（不存在或未通过门禁）");
        await loadFlywheel();
      } catch (error) {
        toast(error.message);
      }
    }));
}

(function bindFlywheelActions() {
  const pipelineButton = $("#fw-pipeline");
  const trainButton = $("#fw-train");
  const output = $("#fw-result");
  if (pipelineButton) {
    pipelineButton.addEventListener("click", async () => {
      setButtonBusy(pipelineButton, true, "正在运行…");
      try {
        const manifest = await api("/v1/flywheel/pipeline/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ options: { classify: true, instruct: true, preference: true } }),
        });
        output.classList.remove("empty");
        output.textContent = formatJson(manifest);
        toast(`数据流水线完成：${manifest.total || 0} 条样本`);
        await loadFlywheel();
      } catch (error) {
        toast(error.message);
      } finally {
        setButtonBusy(pipelineButton, false);
      }
    });
  }
  if (trainButton) {
    trainButton.addEventListener("click", async () => {
      setButtonBusy(trainButton, true, "训练中（CPU，可能数分钟）…");
      try {
        const result = await api("/v1/flywheel/train", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ options: { layer: "preflight", kind: "classify" } }),
        });
        output.classList.remove("empty");
        output.textContent = formatJson(result);
        toast(`训练完成：adapter v${result.adapter_version}`);
        await loadFlywheel();
      } catch (error) {
        toast("训练失败：" + error.message);
      } finally {
        setButtonBusy(trainButton, false);
      }
    });
  }
  const refreshButton = $("#fw-refresh");
  if (refreshButton) {
    refreshButton.addEventListener("click", async () => {
      await loadFlywheel();
      toast("飞轮状态已刷新");
    });
  }
})();

// ---- 记忆库 / 治理 / 经验自动沉淀（Hermes 启发） ----
async function loadExperience() {
  const root = $("#experience-list");
  if (!root) return;
  try {
    const data = await api("/v1/experience/suggestions?status=pending");
    const suggestions = data.suggestions || [];
    if (!suggestions.length) {
      root.innerHTML = '<p class="feedback-empty">暂无待采纳经验建议。完成含 AI 风险的评审后会自动产生。</p>';
      return;
    }
    root.innerHTML = suggestions.map((item) => `
      <div class="task-row">
        <span class="task-main"><span class="task-glyph">EX</span><span class="task-copy">
          <span class="task-name">${escapeHtml(item.rule_id || "RULE")} · ${escapeHtml(item.risk_class || "-")}</span>
          <span class="task-meta"><span>${escapeHtml(item.repository || "")}</span><span>${escapeHtml(item.title || "")}</span></span>
        </span></span>
        <span class="label-actions">
          <button class="button small accept" data-exp-accept="${escapeHtml(item.id)}">采纳为 Skill</button>
          <button class="button small danger" data-exp-dismiss="${escapeHtml(item.id)}">忽略</button>
        </span>
      </div>`).join("");
    $$("[data-exp-accept]", root).forEach((button) => button.addEventListener("click", () => decideExperience(button.dataset.expAccept, true)));
    $$("[data-exp-dismiss]", root).forEach((button) => button.addEventListener("click", () => decideExperience(button.dataset.expDismiss, false)));
  } catch (error) {
    root.innerHTML = `<p class="feedback-empty">经验建议读取失败：${escapeHtml(error.message)}</p>`;
  }
}

async function decideExperience(id, accept) {
  const path = accept ? "accept" : "dismiss";
  try {
    const data = await api(`/v1/experience/suggestions/${encodeURIComponent(id)}/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    toast(accept ? (data.applied ? "已采纳为 Skill 新版本" : `未应用：${data.reason || ""}`) : "已忽略");
    await loadExperience();
  } catch (error) {
    toast(error.message);
  }
}

function resetMemoryView() {
  const output = $("#memory-output");
  if (output) output.textContent = "检索结果 / 仓库画像将在这里显示";
}

async function runMemorySearch(repository, query) {
  const output = $("#memory-output");
  output.classList.remove("empty");
  output.textContent = "正在检索…";
  try {
    const data = await api(`/v1/memory/search?repository=${encodeURIComponent(repository)}&q=${encodeURIComponent(query)}`);
    const hits = data.hits || [];
    output.textContent = hits.length
      ? hits.map((hit) => `${hit.task_id}\t${hit.created_at}\n${hit.content}`).join("\n\n---\n\n")
      : `仓库 ${repository} 未检索到 "${query}" 相关历史。`;
  } catch (error) {
    output.textContent = "检索失败：" + error.message;
  }
}

async function runMemoryDigest(repository) {
  const output = $("#memory-output");
  output.classList.remove("empty");
  output.textContent = "正在生成仓库画像…";
  try {
    const digest = await api(`/v1/memory/digest?repository=${encodeURIComponent(repository)}`);
    output.textContent = formatJson(digest);
  } catch (error) {
    output.textContent = "画像生成失败：" + error.message;
  }
}

async function loadGovernanceReports() {
  const root = $("#governance-reports");
  if (!root) return;
  root.innerHTML = '<div class="list-loading"></div><div class="list-loading"></div>';
  try {
    const data = await api("/v1/governance/reports?limit=20");
    const reports = data.reports || [];
    if (!reports.length) {
      root.innerHTML = '<div class="empty-state"><span><b>还没有治理报告</b>用上方表单生成第一份</span></div>';
      return;
    }
    root.innerHTML = reports.map((item) => `
      <button class="task-row" data-gov-id="${escapeHtml(item.id)}" type="button">
        <span class="task-main"><span class="task-glyph">GV</span><span class="task-copy">
          <span class="task-name">${escapeHtml(item.repository)}</span>
          <span class="task-meta"><span>since ${escapeHtml(item.since || "all-time")}</span><span>${escapeHtml(formatTime(item.created_at))}</span></span>
        </span></span>
        <span class="status state-success">${escapeHtml(item.report?.prs ?? 0)} PR</span>
      </button>`).join("");
    $$("[data-gov-id]", root).forEach((row) => row.addEventListener("click", () => {
      const item = reports.find((candidate) => String(candidate.id) === String(row.dataset.govId));
      if (item) { $("#governance-output").classList.remove("empty"); $("#governance-output").textContent = formatJson(item.report); }
    }));
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>治理报告加载失败</span></div>';
    toast(error.message);
  }
}

(async function bindHermesViews() {
  const memoryForm = $("#memory-search-form");
  if (memoryForm) {
    memoryForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const values = new FormData(memoryForm);
      runMemorySearch(String(values.get("repository") || "").trim(), String(values.get("q") || "").trim());
    });
  }
  const digestButton = $("#memory-digest-btn");
  if (digestButton) {
    digestButton.addEventListener("click", () => {
      const repository = String(new FormData($("#memory-search-form")).get("repository") || "").trim();
      if (!repository) { toast("请先填写仓库"); return; }
      runMemoryDigest(repository);
    });
  }
  const governanceForm = $("#governance-form");
  if (governanceForm) {
    governanceForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $('button[type="submit"]', governanceForm);
      const values = new FormData(governanceForm);
      const output = $("#governance-output");
      output.classList.remove("empty");
      output.textContent = "正在生成治理报告…";
      setButtonBusy(button, true, "扫描中…");
      try {
        const body = { repository: String(values.get("repository") || "").trim() };
        const since = String(values.get("since") || "").trim();
        if (since) body.since = since;
        const report = await api("/v1/governance/report", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        output.textContent = formatJson(report);
        toast("治理报告已生成");
        await loadGovernanceReports();
      } catch (error) {
        output.textContent = "生成失败：" + error.message;
      } finally {
        setButtonBusy(button, false);
      }
    });
  }
})();

const diffInput = $('textarea[name="diff"]', $("#review-form"));
const diffStats = $("#diff-stats");
function updateDiffStats() {
  const value = diffInput.value;
  const lines = value ? value.split(/\r?\n/).length : 0;
  diffStats.textContent = `${lines} 行，${value.length} 字符`;
}
diffInput.addEventListener("input", updateDiffStats);
updateDiffStats();

if (accessToken) $("#logout").classList.remove("hidden");
show(location.hash.slice(1) || "overview", false);
loadDashboard();
