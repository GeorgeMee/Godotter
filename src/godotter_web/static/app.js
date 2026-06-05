let currentProject = null;
let currentSession = null;
let latestReview = null;
let currentRun = null;
let runPollTimer = null;
let nextRunEventIndex = 0;
let activeView = "chat";
let taskRuntimeStatus = {};
let taskRuntimeRunId = null;

async function fetchJson(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function selectedSessionKey(projectName) {
  return `godotter:selectedSession:${projectName}`;
}

function isActiveRun(run) {
  return run && (run.status === "queued" || run.status === "running");
}

function updateRunControls() {
  const cancelButton = document.getElementById("cancel-run");
  if (cancelButton) {
    cancelButton.disabled = !isActiveRun(currentRun);
  }
}

function showView(view) {
  if (view === "plan") {
    view = "task";
  } else if (view === "run") {
    view = "log";
  }
  const allowed = new Set(["chat", "task", "log"]);
  activeView = allowed.has(view) ? view : "chat";
  for (const button of document.querySelectorAll("[data-view]")) {
    const isActive = button.dataset.view === activeView;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-current", isActive ? "page" : "false");
  }
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.classList.toggle("is-hidden", panel.dataset.panel !== activeView);
  }
  const label = document.getElementById("current-view-label");
  if (label) {
    label.textContent = activeView[0].toUpperCase() + activeView.slice(1);
  }
}

function setupViewTabs() {
  window.addEventListener("hashchange", () => showView(window.location.hash.slice(1)));
  showView(window.location.hash.slice(1) || activeView);
}

async function loadState() {
  const workspace = document.getElementById("workspace");
  const status = document.getElementById("session-status");
  try {
    const projects = await fetchJson("/api/projects");
    const savedProject = localStorage.getItem("godotter:selectedProject");
    const selected =
      projects.projects.find((item) => item.name === savedProject) ||
      projects.projects.find((item) => item.is_default) ||
      projects.projects[0];

    if (!selected) {
      workspace.textContent = "未配置工作区";
      status.textContent = "请先到项目页创建或选择工作区。";
      return;
    }

    currentProject = selected.name;
    localStorage.setItem("godotter:selectedProject", selected.name);
    workspace.textContent = `当前工作区：${selected.name}`;
    workspace.title = selected.workspace_root || "";
    await loadSavedSession();
  } catch (error) {
    workspace.textContent = "无法读取工作区";
    status.textContent = `错误：${error.message}`;
  }
}

async function loadSavedSession() {
  const status = document.getElementById("session-status");
  const savedSession = localStorage.getItem(selectedSessionKey(currentProject));
  if (!savedSession) {
    currentSession = null;
    renderMessages([]);
    status.textContent = "未创建对话。发送第一条消息时会自动创建。";
    renderTaskSummary(null);
    return;
  }

  try {
    const detail = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(savedSession)}`);
    currentSession = detail.session;
    latestReview = detail.latest_review;
    renderMessages(detail.messages);
    renderReview(latestReview);
    await loadLatestRun();
    status.textContent = `当前对话：${currentSession.title}`;
  } catch (error) {
    localStorage.removeItem(selectedSessionKey(currentProject));
    currentSession = null;
    latestReview = null;
    renderMessages([]);
    renderReview(null);
    renderRunHistory([]);
    status.textContent = "之前的对话不存在，发送消息时会创建新对话。";
  }
}

async function loadLatestRun() {
  if (!currentProject || !currentSession) {
    return;
  }
  try {
    const result = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/runs`,
    );
    renderRunHistory(result.runs || []);
    currentRun = (result.runs || [])[0] || null;
    refreshTaskRuntimeStatus();
    if (currentRun) {
      document.getElementById("run-status").textContent = runStatusLabel(currentRun.status);
      nextRunEventIndex = 0;
      document.getElementById("run-log").textContent = "";
      updateRunControls();
      renderReview(latestReview);
      pollRunEvents();
    }
  } catch (error) {
    renderRunHistory([]);
  }
}

async function ensureSession(initialTitle = "") {
  if (currentSession) {
    return currentSession;
  }
  const created = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/sessions`, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({title: initialTitle.slice(0, 48)}),
  });
  currentSession = created.session;
  localStorage.setItem(selectedSessionKey(currentProject), currentSession.session_id);
  document.getElementById("session-status").textContent = `当前对话：${currentSession.title}`;
  return currentSession;
}

function renderMessages(messages) {
  const timeline = document.getElementById("timeline");
  timeline.innerHTML = "";
  if (!messages.length) {
    appendBubble("assistant", "你可以先给一句话需求。我会保存到当前工作区的会话里，默认不执行。", false);
    return;
  }
  for (const message of messages) {
    appendBubble(message.role === "user" ? "user" : "assistant", message.content, false);
  }
}

function renderReview(review) {
  const status = document.getElementById("review-status");
  const list = document.getElementById("review-items");
  const actions = document.getElementById("review-actions");
  list.innerHTML = "";
  actions.innerHTML = "";
  if (!review) {
    status.textContent = "暂无任务";
    list.innerHTML = '<li class="muted">生成计划后，这里会显示任务审批与执行状态。</li>';
    renderTaskSummary(null);
    return;
  }

  status.textContent = review.status || "in_review";
  renderTaskSummary(review);
  for (const item of review.items || []) {
    const node = document.createElement("li");
    const commentId = `comment-${review.review_id}-${item.item_id}`;
    const runtime = taskRuntimeStatus[item.item_id] || {status: "not_started", label: "未执行"};
    const runtimeDetails = runtimeDetailsHtml(runtime);
    node.innerHTML = `
      <div class="task-title-row">
        <label><span class="task-id">${escapeHtml(item.item_id)}</span>${escapeHtml(item.title)}</label>
        <div class="task-badges">
          <span class="status-pill review-${escapeHtml(item.status)}">${escapeHtml(reviewStatusLabel(item.status))}</span>
          <span class="status-pill run-${escapeHtml(runtime.status)}">${escapeHtml(runtime.label)}</span>
        </div>
      </div>
      <p class="muted">${escapeHtml(item.goal || "")}</p>
      ${runtimeDetails}
      <textarea id="${commentId}" placeholder="需要修改时填写评论；批准可留空。">${escapeHtml(item.comment || "")}</textarea>
      <div class="approval-bar">
        <button type="button" data-action="approved">批准</button>
        <button type="button" class="secondary" data-action="needs_revision">要求修改</button>
        <button type="button" class="secondary" data-action="rejected">拒绝</button>
      </div>
    `;
    for (const button of node.querySelectorAll("button[data-action]")) {
      button.addEventListener("click", async () => {
        await updateReviewItem(review.review_id, item.item_id, button.dataset.action, document.getElementById(commentId).value);
      });
    }
    if (item.status === "approved") {
      const runOneButton = document.createElement("button");
      runOneButton.type = "button";
      runOneButton.className = "secondary";
      runOneButton.textContent = `只运行 ${item.item_id}`;
      runOneButton.disabled = isActiveRun(currentRun);
      runOneButton.addEventListener("click", () => runApprovedReview(review.review_id, [item.item_id]));
      node.querySelector(".approval-bar").appendChild(runOneButton);
    }
    list.appendChild(node);
  }
  const approvedItems = (review.items || []).filter((item) => item.status === "approved");
  if (approvedItems.length) {
    const runButton = document.createElement("button");
    runButton.type = "button";
    runButton.textContent = `运行已批准任务（${approvedItems.length}）`;
    runButton.disabled = isActiveRun(currentRun);
    if (runButton.disabled) {
      runButton.textContent = `正在运行：${runDisplayName(currentRun)}`;
    }
    runButton.addEventListener("click", () => runApprovedReview(review.review_id));
    actions.appendChild(runButton);
  }
}

function renderTaskSummary(review) {
  const summary = document.getElementById("task-summary");
  if (!summary) {
    return;
  }
  if (!review) {
    summary.innerHTML = `<strong>当前状态：</strong><span>暂无计划</span><small>先在 Chat 里生成 PlanPack。</small>`;
    return;
  }
  const items = review.items || [];
  const approvedCount = items.filter((item) => item.status === "approved").length;
  const revisionCount = items.filter((item) => item.status === "needs_revision").length;
  const rejectedCount = items.filter((item) => item.status === "rejected").length;
  const latestCommand = latestCommandResult();
  const runstate = latestCommand?.runstate;
  const verify = latestCommand?.verify_report;
  const attempts = runstate?.attempts || [];
  const latestAttempt = attempts.length ? attempts[attempts.length - 1] : null;
  const phase = currentRun
    ? (isActiveRun(currentRun) ? "执行中" : runStatusLabel(currentRun.status))
    : (approvedCount ? "可执行" : "待审批");
  const details = [
    `审批 ${approvedCount}/${items.length}`,
    revisionCount ? `${revisionCount} 项需修改` : "",
    rejectedCount ? `${rejectedCount} 项已拒绝` : "",
    currentRun ? `批次 ${currentRun.run_id}` : "",
    latestAttempt ? `Attempt ${latestAttempt.index}: ${latestAttempt.status}` : "",
    verify ? `验证 ${verify.result || "unknown"}${verify.failed_check ? `，失败点 ${verify.failed_check}` : ""}` : "",
  ].filter(Boolean);
  summary.innerHTML = `
    <strong>当前状态：${escapeHtml(phase)}</strong>
    <span>${details.map(escapeHtml).join(" · ")}</span>
    <small>${escapeHtml(summaryHint(review, currentRun, verify))}</small>
  `;
}

function summaryHint(review, run, verify) {
  if (!review) {
    return "尚未生成计划。";
  }
  if (!run) {
    return "批准一个或多个任务后，可以开始执行。";
  }
  if (isActiveRun(run)) {
    return "正在执行，详细输出见 Log 页面。";
  }
  if (verify && verify.result !== "pass") {
    return "验证失败，展开任务卡查看 RunState 和 VerifyReport 摘要。";
  }
  return "执行已结束。";
}

function latestCommandResult() {
  const commands = currentRun?.commands || [];
  return commands.length ? commands[commands.length - 1] : null;
}

function runtimeDetailsHtml(runtime) {
  const parts = [];
  if (runtime.runstate) {
    const attempts = runtime.runstate.attempts || [];
    const latestAttempt = attempts.length ? attempts[attempts.length - 1] : null;
    parts.push(`RunState: ${attempts.length} attempt(s), ${latestAttempt?.status || runtime.runstate.status || "unknown"}`);
  }
  if (runtime.verify_report) {
    const summary = runtime.verify_report.summary || {};
    const failed = runtime.verify_report.failed_check || "none";
    parts.push(`VerifyReport: ${runtime.verify_report.result || "unknown"}, failed=${failed}, checks=${summary.passed || 0}/${summary.total || 0}`);
  }
  if (!parts.length) {
    return "";
  }
  return `<div class="runtime-details">${parts.map((part) => `<span>${escapeHtml(part)}</span>`).join("")}</div>`;
}

async function updateReviewItem(reviewId, itemId, status, comment) {
  const sessionStatus = document.getElementById("session-status");
  try {
    const result = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/reviews/${encodeURIComponent(reviewId)}/items/${encodeURIComponent(itemId)}/approval`,
      {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({status, comment}),
      },
    );
    latestReview = result.review;
    renderReview(latestReview);
    sessionStatus.textContent = `计划状态：${latestReview.status}`;
  } catch (error) {
    sessionStatus.textContent = `审批失败：${error.message}`;
  }
}

async function runApprovedReview(reviewId, itemIds = null) {
  const sessionStatus = document.getElementById("session-status");
  if (isActiveRun(currentRun)) {
    sessionStatus.textContent = `已有运行中执行批次：${runDisplayName(currentRun)}`;
    showView("log");
    pollRunEvents();
    return;
  }
  sessionStatus.textContent = "正在运行已批准任务...";
  try {
    const result = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/reviews/${encodeURIComponent(reviewId)}/run`,
      {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(itemIds ? {item_ids: itemIds} : {}),
      },
    );
    currentSession = result.session;
    currentRun = result.run;
    refreshTaskRuntimeStatus();
    nextRunEventIndex = 0;
    document.getElementById("run-log").textContent = "";
    document.getElementById("run-status").textContent = runStatusLabel(currentRun.status);
    appendBubble(
      "assistant",
      `${result.reused ? "继续查看执行批次" : "执行批次已开始"}：${runDisplayName(result.run)}（${runTaskSummary(result.run)}）`,
    );
    sessionStatus.textContent = `运行状态：${result.run.status}`;
    renderReview(latestReview);
    showView("log");
    await loadLatestRun();
  } catch (error) {
    appendBubble("assistant warning", `运行失败：${error.message}`);
    sessionStatus.textContent = "运行失败。";
  }
}

async function pollRunEvents() {
  if (!currentRun) {
    return;
  }
  if (runPollTimer) {
    clearTimeout(runPollTimer);
    runPollTimer = null;
  }
  try {
    const result = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/runs/${encodeURIComponent(currentRun.run_id)}/events?after=${nextRunEventIndex}`,
    );
    currentRun = result.run;
    refreshTaskRuntimeStatus();
    updateRunControls();
    document.getElementById("run-status").textContent = runStatusLabel(currentRun.status);
    appendRunEvents(result.events || []);
    renderReview(latestReview);
    if (currentRun.status === "queued" || currentRun.status === "running") {
      runPollTimer = setTimeout(pollRunEvents, 1000);
    } else {
      appendBubble("assistant", `运行结束：${currentRun.status}`);
      document.getElementById("session-status").textContent = `执行状态：${runStatusLabel(currentRun.status)}`;
      updateRunControls();
      renderReview(latestReview);
    }
  } catch (error) {
    appendRunLine(`poll_error ${error.message}`);
    runPollTimer = setTimeout(pollRunEvents, 2000);
  }
}

function appendRunEvents(events) {
  for (const event of events) {
    nextRunEventIndex = Math.max(nextRunEventIndex, Number(event.index || 0) + 1);
    const prefix = event.task_id ? `[${event.task_id}] ` : "";
    updateTaskRuntimeFromEvent(event);
    appendRunLine(`${event.created_at || ""} ${event.type || "event"} ${prefix}${event.message || ""}`.trim());
    if (event.type === "command_result" && event.payload) {
      const stderr = event.payload.stderr || "";
      const stdout = event.payload.stdout || "";
      if (stderr.trim()) {
        appendRunLine(stderr.trimEnd());
      }
      if (stdout.trim() && !events.some((item) => item.type === "stdout" && item.task_id === event.task_id)) {
        appendRunLine(stdout.trimEnd());
      }
    }
    if (event.type === "error" && event.payload) {
      appendRunLine(JSON.stringify(event.payload));
    }
  }
}

function renderRunHistory(runs) {
  const history = document.getElementById("run-history");
  if (!history) {
    return;
  }
  history.innerHTML = "";
  for (const run of runs.slice(0, 5)) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "run-chip";
    node.innerHTML = `
      <strong>${escapeHtml(runStatusLabel(run.status))}</strong>
      <span>${escapeHtml(runDisplayName(run))}</span>
      <small>${escapeHtml(runTaskSummary(run))}</small>
    `;
    node.addEventListener("click", () => {
      currentRun = run;
      refreshTaskRuntimeStatus();
      nextRunEventIndex = 0;
      document.getElementById("run-log").textContent = "";
      document.getElementById("run-status").textContent = runStatusLabel(currentRun.status);
      updateRunControls();
      showView("log");
      pollRunEvents();
    });
    history.appendChild(node);
  }
}

function reviewStatusLabel(status) {
  return ({
    approved: "已批准",
    needs_review: "待审批",
    needs_revision: "需修改",
    rejected: "已拒绝",
  })[status] || status || "待审批";
}

function refreshTaskRuntimeStatus() {
  if (!currentRun) {
    taskRuntimeRunId = null;
    taskRuntimeStatus = {};
    return;
  }
  if (taskRuntimeRunId !== currentRun.run_id) {
    taskRuntimeRunId = currentRun.run_id;
    taskRuntimeStatus = {};
  }
  for (const taskId of currentRun.task_ids || []) {
    taskRuntimeStatus[taskId] = taskRuntimeStatus[taskId] || (isActiveRun(currentRun)
      ? {status: "queued", label: "等待中"}
      : {status: "not_started", label: "未执行"});
  }
  for (const command of currentRun.commands || []) {
    const taskId = command.task_id;
    if (!taskId) {
      continue;
    }
    if (command.exit_code === 0) {
      taskRuntimeStatus[taskId] = {status: "passed", label: "已通过"};
    } else if (command.exit_code !== undefined && command.exit_code !== null) {
      taskRuntimeStatus[taskId] = {status: "failed", label: "失败"};
    }
  }
}

function updateTaskRuntimeFromEvent(event) {
  if (!event.task_id) {
    return;
  }
  if (!taskRuntimeStatus[event.task_id]) {
    taskRuntimeStatus[event.task_id] = {status: "queued", label: "等待中"};
  }
  if (event.type === "command" || event.type === "stdout") {
    taskRuntimeStatus[event.task_id] = {status: "running", label: "执行中"};
  }
  if (event.type === "command_result" && event.payload) {
    const exitCode = event.payload.exit_code;
    taskRuntimeStatus[event.task_id] = exitCode === 0
      ? {status: "passed", label: "已通过"}
      : {status: "failed", label: "失败"};
  }
}

function appendRunLine(line) {
  const log = document.getElementById("run-log");
  log.textContent += `${line}\n`;
  log.scrollTop = log.scrollHeight;
}

function runDisplayName(run) {
  if (!run) {
    return "无执行批次";
  }
  return `执行批次 ${run.run_id || "(unknown)"}`;
}

function runTaskSummary(run) {
  const taskIds = run?.task_ids || [];
  if (!taskIds.length) {
    return "无任务";
  }
  return `${taskIds.length} 个任务：${taskIds.join(", ")}`;
}

function runStatusLabel(status) {
  return ({
    queued: "等待中",
    running: "执行中",
    passed: "已完成",
    failed: "失败",
    interrupted: "已中断",
  })[status] || status || "空闲";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

function appendBubble(role, text, scroll = true) {
  const timeline = document.getElementById("timeline");
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  const title = document.createElement("strong");
  title.textContent = role === "user" ? "You" : "Godotter";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  bubble.append(title, paragraph);
  timeline.appendChild(bubble);
  if (scroll) {
    timeline.scrollTop = timeline.scrollHeight;
  }
}

function refreshTaskRuntimeStatus() {
  if (!currentRun) {
    taskRuntimeRunId = null;
    taskRuntimeStatus = {};
    return;
  }
  if (taskRuntimeRunId !== currentRun.run_id) {
    taskRuntimeRunId = currentRun.run_id;
    taskRuntimeStatus = {};
  }
  for (const taskId of currentRun.task_ids || []) {
    taskRuntimeStatus[taskId] = taskRuntimeStatus[taskId] || (isActiveRun(currentRun)
      ? {status: "queued", label: "queued"}
      : {status: "not_started", label: "not started"});
  }
  for (const command of currentRun.commands || []) {
    const taskId = command.task_id;
    if (!taskId) {
      continue;
    }
    if (command.exit_code !== undefined && command.exit_code !== null) {
      const passed = command.exit_code === 0;
      taskRuntimeStatus[taskId] = {
        status: passed ? "passed" : "failed",
        label: passed ? "passed" : "failed",
        runstate: command.runstate,
        verify_report: command.verify_report,
      };
    }
  }
}

function updateTaskRuntimeFromEvent(event) {
  if (!event.task_id) {
    return;
  }
  if (!taskRuntimeStatus[event.task_id]) {
    taskRuntimeStatus[event.task_id] = {status: "queued", label: "queued"};
  }
  if (event.type === "command" || event.type === "stdout") {
    taskRuntimeStatus[event.task_id] = {status: "running", label: "running"};
  }
  if (event.type === "command_result" && event.payload) {
    const exitCode = event.payload.exit_code;
    taskRuntimeStatus[event.task_id] = {
      status: exitCode === 0 ? "passed" : "failed",
      label: exitCode === 0 ? "passed" : "failed",
      runstate: event.payload.runstate,
      verify_report: event.payload.verify_report,
    };
  }
}

document.getElementById("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendChatMessage();
});

async function saveUserMessage(text) {
  const session = await ensureSession(text);
  const result = await fetchJson(
    `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(session.session_id)}/messages`,
    {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({content: text}),
    },
  );
  currentSession = result.session;
  appendBubble("user", result.message.content);
  return result;
}

async function sendChatMessage() {
  const prompt = document.getElementById("prompt");
  const status = document.getElementById("session-status");
  const text = prompt.value.trim();
  if (!text) {
    return;
  }
  if (!currentProject) {
    status.textContent = "请先选择工作区。";
    return;
  }

  prompt.value = "";
  try {
    const result = await saveUserMessage(text);
    status.textContent = "正在回复...";
    const reply = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(result.session.session_id)}/reply`,
      {
        method: "POST",
        headers: {"content-type": "application/json"},
      },
    );
    currentSession = reply.session;
    appendBubble("assistant", reply.message.content);
    status.textContent = "已回复。";
  } catch (error) {
    status.textContent = `发送失败：${error.message}`;
    prompt.value = text;
  }
}

async function generatePlanFromPrompt() {
  const prompt = document.getElementById("prompt");
  const status = document.getElementById("session-status");
  const text = prompt.value.trim();
  if (!currentProject) {
    status.textContent = "请先选择工作区。";
    return;
  }
  let goal = text;
  try {
    if (text) {
      prompt.value = "";
      await saveUserMessage(text);
    } else if (!currentSession) {
      status.textContent = "请先输入需求，或选择已有对话。";
      return;
    }
    status.textContent = "正在生成计划草案...";
    const plan = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/plan`,
      {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(goal ? {goal} : {}),
      },
    );
    currentSession = plan.session;
    latestReview = plan.review;
    appendBubble("assistant", plan.message.content);
    renderReview(latestReview);
    showView("task");
    status.textContent = `计划草案已生成：${plan.review.items.length} 个任务`;
  } catch (error) {
    appendBubble("assistant warning", `计划生成失败：${error.message}`);
    status.textContent = "计划生成失败。";
    if (text) {
      prompt.value = text;
    }
  }
}

document.getElementById("generate-plan").addEventListener("click", generatePlanFromPrompt);

document.getElementById("new-chat").addEventListener("click", async () => {
  if (!currentProject) {
    document.getElementById("session-status").textContent = "请先选择工作区。";
    return;
  }
  const created = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/sessions`, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({title: "New chat"}),
  });
  currentSession = created.session;
  latestReview = null;
  localStorage.setItem(selectedSessionKey(currentProject), currentSession.session_id);
  renderMessages([]);
  renderReview(null);
  document.getElementById("session-status").textContent = `当前对话：${currentSession.title}`;
});

document.getElementById("cancel-run").addEventListener("click", async () => {
  if (!isActiveRun(currentRun)) {
    return;
  }
  const sessionStatus = document.getElementById("session-status");
  sessionStatus.textContent = `正在停止：${runDisplayName(currentRun)}`;
  try {
    const result = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/runs/${encodeURIComponent(currentRun.run_id)}/cancel`,
      {method: "POST"},
    );
    currentRun = result.run;
    document.getElementById("run-status").textContent = runStatusLabel(currentRun.status);
    appendRunLine(`cancel ${result.cancelled ? "requested" : "ignored"} ${runDisplayName(currentRun)}`);
    updateRunControls();
    pollRunEvents();
  } catch (error) {
    appendRunLine(`cancel_error ${error.message}`);
    sessionStatus.textContent = "停止失败。";
  }
});

setupViewTabs();
loadState();
