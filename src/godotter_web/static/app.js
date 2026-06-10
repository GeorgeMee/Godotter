let currentProject = null;
let currentSession = null;
let latestReview = null;
let currentRun = null;
let runPollTimer = null;
let nextRunEventIndex = 0;
let activeView = "chat";
let taskRuntimeStatus = {};
let taskRuntimeRunId = null;
let currentTreePath = "";

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

function normalizeView(view) {
  if (view === "plan") {
    return "task";
  } else if (view === "run") {
    return "log";
  }
  const allowed = new Set(["chat", "task", "log", "build", "git", "files"]);
  return allowed.has(view) ? view : "chat";
}

function showView(view, options = {}) {
  activeView = normalizeView(view);
  for (const button of document.querySelectorAll(".gtab-view[data-view]")) {
    const isActive = button.dataset.view === activeView;
    button.classList.toggle("active", isActive);
  }
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.classList.toggle("is-hidden", panel.dataset.panel !== activeView);
  }
  if (options.updateHash !== false && window.location.hash !== `#${activeView}`) {
    history.pushState(null, "", `#${activeView}`);
  }
}

function setupViewTabs() {
  for (const button of document.querySelectorAll(".gtab-view[data-view]")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  window.addEventListener("hashchange", () => showView(window.location.hash.slice(1), {updateHash: false}));
  showView(window.location.hash.slice(1) || activeView, {updateHash: false});
}

function setupBurger() {
  const overlay = document.getElementById("nav-overlay");
  const burger = document.getElementById("burger-btn");
  const closeBtn = document.getElementById("nav-close");
  if (!overlay || !burger) return;

  burger.addEventListener("click", () => { overlay.hidden = false; });
  if (closeBtn) closeBtn.addEventListener("click", () => { overlay.hidden = true; });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.hidden = true;
  });
  // Close sidebar when a nav link is clicked
  for (const link of overlay.querySelectorAll("a, button")) {
    link.addEventListener("click", () => { overlay.hidden = true; });
  }
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
    await loadSessionList();
    await loadBuilds();
    await loadGitStatus();
    await loadProjectTree();
  } catch (error) {
    workspace.textContent = "无法读取工作区";
    status.textContent = `错误：${error.message}`;
  }
}

async function loadBuilds() {
  const status = document.getElementById("build-status");
  const list = document.getElementById("build-list");
  if (!status || !list || !currentProject) {
    return;
  }
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/builds`);
    renderBuilds(result.builds || []);
  } catch (error) {
    status.textContent = "读取失败";
    list.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

async function runBuildDoctor() {
  const message = document.getElementById("build-message");
  if (!currentProject) {
    message.textContent = "请先选择工作区。";
    return;
  }
  message.textContent = "正在检查导出配置...";
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/builds/doctor`);
    const doctor = result.doctor || {};
    const suggestions = result.suggestions || {};
    const presets = (doctor.presets || []).map((preset) => `${preset.name}(${preset.platform || "unknown"})`).join(", ");
    const warnings = (doctor.warnings || []).join("；");
    const errors = (doctor.errors || []).join("；");
    message.textContent = [
      `export_presets=${doctor.export_presets_exists ? "yes" : "no"}`,
      `templates=${doctor.templates_detected ? "yes" : "no"}`,
      presets ? `presets=${presets}` : "",
      warnings ? `warnings=${warnings}` : "",
      errors ? `errors=${errors}` : "",
    ].filter(Boolean).join(" · ");
    if ((doctor.presets || []).length) {
      const select = document.getElementById("build-preset");
      const currentValue = select.value;
      select.innerHTML = '<option value="">选择 Preset...</option>';
      for (const p of doctor.presets) {
        const opt = document.createElement("option");
        opt.value = p.name || "";
        opt.textContent = `${p.name || "未命名"} (${p.platform || "unknown"})`;
        if (p.name === currentValue) opt.selected = true;
        select.appendChild(opt);
      }
    }
  } catch (error) {
    message.textContent = `检查失败：${error.message}`;
  }
}


function showEnvSetDialog(key, suggest, currentValue) {
  const backdrop = document.createElement("div");
  backdrop.className = "dialog-backdrop";
  backdrop.innerHTML = `
    <div class="dialog-card">
      <strong>设置 ${key}</strong>
      <p class="muted">当前值: ${escapeHtml(currentValue || "(未设置)")}</p>
      ${suggest ? `<p class="muted">检测到: ${escapeHtml(suggest)}</p>` : ""}
      <input type="text" id="env-set-input" placeholder="输入路径..." value="${escapeHtml(currentValue || suggest || "")}" />
      <div class="dialog-actions">
        <button type="button" id="env-set-cancel">取消</button>
        <button type="button" id="env-set-save">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);

  backdrop.querySelector("#env-set-cancel").addEventListener("click", () => backdrop.remove());
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.remove(); });

  backdrop.querySelector("#env-set-save").addEventListener("click", async () => {
    const value = document.getElementById("env-set-input").value.trim();
    if (!value) return;
    try {
      await fetchJson("/api/config", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({key, value}),
      });
      backdrop.remove();
      runBuildDoctor();
    } catch (error) {
      alert(`保存失败: ${error.message}`);
    }
  });
}

async function submitBuild(event) {
  event.preventDefault();
  const message = document.getElementById("build-message");
  const button = document.getElementById("build-submit");
  if (!currentProject) {
    message.textContent = "请先选择工作区。";
    return;
  }
  const preset = document.getElementById("build-preset").value.trim();
  if (!preset) {
    message.textContent = "请填写导出 Preset。";
    return;
  }
  const output = document.getElementById("build-output").value.trim();
  const debug = document.getElementById("build-debug").checked;
  button.disabled = true;
  message.textContent = `正在构建 ${preset}...`;
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/builds`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({preset, output, debug}),
    });
    const build = result.build || {};
    message.textContent = `构建结束：${build.status || "unknown"} · ${build.build_id || ""}`;
    await loadBuilds();
  } catch (error) {
    message.textContent = `构建失败：${error.message}`;
    await loadBuilds();
  } finally {
    button.disabled = false;
  }
}

function renderBuilds(builds) {
  const status = document.getElementById("build-status");
  const list = document.getElementById("build-list");
  status.textContent = builds.length ? `${builds.length} 个构建` : "暂无构建";
  if (!builds.length) {
    list.innerHTML = '<p class="muted">暂无构建产物。先运行 `uv run godotter export build --preset <preset>`。</p>';
    return;
  }
  list.innerHTML = builds.map((build) => buildHtml(build)).join("");
  for (const btn of list.querySelectorAll(".build-delete-btn")) {
    btn.addEventListener("click", () => deleteBuild(btn.dataset.buildId));
  }
}

function buildHtml(build) {
  const artifacts = build.artifacts || [];
  const links = artifacts.length
    ? artifacts.map((artifact) => {
      const artifactPath = artifact.path || "";
      const prefix = `.godotter/builds/${build.build_id}/`;
      const downloadPath = artifactPath.startsWith(prefix) ? artifactPath.slice(prefix.length) : artifact.name;
      const href = `/api/projects/${encodeURIComponent(currentProject)}/builds/${encodeURIComponent(build.build_id)}/download/${encodeURI(downloadPath)}`;
      return `<a class="button-link" href="${href}">${escapeHtml(artifact.name || downloadPath)} (${formatBytes(artifact.size_bytes || 0)})</a>`;
    }).join("")
    : '<span class="muted">没有可下载产物</span>';
  return `
    <article class="build-card">
      <div>
        <strong>${escapeHtml(build.preset || "unknown preset")}</strong>
        <span class="status-pill run-${escapeHtml(build.status || "unknown")}">${escapeHtml(build.status || "unknown")}</span>
        <button type="button" class="build-delete-btn" data-build-id="${escapeHtml(build.build_id || "")}" title="删除此构建">&times;</button>
      </div>
      <p class="muted">${escapeHtml(build.build_id || "")} · ${escapeHtml(build.created_at || "")}</p>
      <div class="build-downloads">${links}</div>
    </article>
  `;
}

async function deleteBuild(buildId) {
  if (!confirm(`确定删除构建 ${buildId}？`)) return;
  try {
    await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/builds/${encodeURIComponent(buildId)}`,
      { method: "DELETE" },
    );
    loadBuilds();
  } catch (error) {
    alert(`删除失败：${error.message}`);
  }
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

async function loadGitStatus() {
  const status = document.getElementById("git-status");
  const summary = document.getElementById("git-summary");
  const files = document.getElementById("git-files");
  const log = document.getElementById("git-log");
  const branchSelect = document.getElementById("git-branch-select");
  if (!status || !summary || !files || !log || !branchSelect || !currentProject) {
    return;
  }
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/git/status`);
    renderGitStatus(result.git);
  } catch (error) {
    status.textContent = "Git unavailable";
    summary.textContent = error.message;
    files.innerHTML = "";
    log.innerHTML = "";
    branchSelect.innerHTML = "";
  }
}

function renderGitStatus(git) {
  const status = document.getElementById("git-status");
  const summary = document.getElementById("git-summary");
  const files = document.getElementById("git-files");
  const log = document.getElementById("git-log");
  const branchCurrent = document.getElementById("git-branch-current");
  const branchSelect = document.getElementById("git-branch-select");
  const branches = document.getElementById("git-branches");
  if (!git.is_repo) {
    status.textContent = "Not a Git repo";
    summary.textContent = "当前游戏项目还没有 .git。可以点击 Init Git 初始化项目仓库。";
    files.innerHTML = '<p class="muted">No Git repository.</p>';
    log.innerHTML = "";
    branchCurrent.textContent = "当前分支：无仓库";
    branchSelect.innerHTML = "";
    branches.innerHTML = '<p class="muted">No Git repository.</p>';
    return;
  }
  const changes = git.files || [];
  status.textContent = changes.length ? `${changes.length} changes` : "Clean";
  summary.textContent = [
    `branch=${git.branch || "(unknown)"}`,
    git.upstream ? `upstream=${git.upstream}` : "",
    git.branch_line || "",
  ].filter(Boolean).join(" · ");
  files.innerHTML = changes.length
    ? changes.map((file) => gitFileHtml(file)).join("")
    : '<p class="muted">No working tree changes.</p>';
  renderGitBranches(git);
  renderGitCommits(git.commits || []);
  for (const button of files.querySelectorAll("[data-diff-path]")) {
    button.addEventListener("click", () => loadGitDiff(button.dataset.diffPath || ""));
  }
}

function renderGitBranches(git) {
  const branchCurrent = document.getElementById("git-branch-current");
  const branchSelect = document.getElementById("git-branch-select");
  const branches = document.getElementById("git-branches");
  const items = git.branches || [];
  branchCurrent.textContent = `当前分支：${git.branch || "(detached)"}`;
  branchSelect.innerHTML = items.map((branch) => {
    const selected = branch.current ? " selected" : "";
    const label = `${branch.name}${branch.upstream ? ` -> ${branch.upstream}` : ""}`;
    return `<option value="${escapeHtml(branch.name)}"${selected}>${escapeHtml(label)}</option>`;
  }).join("");
  branches.innerHTML = items.length
    ? items.map((branch) => gitBranchHtml(branch)).join("")
    : '<p class="muted">No branches.</p>';
}

function gitBranchHtml(branch) {
  const remote = branch.remote ? "remote" : "local";
  return `
    <article class="git-branch-card ${branch.current ? "active" : ""}">
      <strong>${escapeHtml(branch.name)}</strong>
      <span class="status-pill">${branch.current ? "current" : remote}</span>
      <small>${escapeHtml(branch.commit || "")} ${escapeHtml(branch.subject || "")}</small>
    </article>
  `;
}

function renderGitCommits(commits) {
  const log = document.getElementById("git-log");
  log.innerHTML = commits.length
    ? commits.map((commit) => gitCommitHtml(commit)).join("")
    : '<p class="muted">No commits.</p>';
}

function gitCommitHtml(commit) {
  return `
    <article class="git-commit-row">
      <code title="${escapeHtml(commit.hash || "")}">${escapeHtml(commit.short || "")}</code>
      <div>
        <strong>${escapeHtml(commit.subject || "")}</strong>
        <small>${escapeHtml(commit.author || "")} · ${escapeHtml(commit.relative_date || "")}</small>
      </div>
    </article>
  `;
}

function gitFileHtml(file) {
  const path = file.path || "";
  const code = file.code || "";
  return `
    <label class="git-file-row">
      <input type="checkbox" value="${escapeHtml(path)}" />
      <span class="status-pill">${escapeHtml(code)}</span>
      <code title="${escapeHtml(path)}">${escapeHtml(path)}</code>
      <button type="button" class="secondary" data-diff-path="${escapeHtml(path)}">Diff</button>
    </label>
  `;
}

async function loadGitDiff(path = "") {
  const diff = document.getElementById("git-diff");
  if (!currentProject || !diff) {
    return;
  }
  diff.textContent = "Loading diff...";
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/git/diff${query}`);
    diff.textContent = result.diff?.stdout || "(empty diff)";
  } catch (error) {
    diff.textContent = `diff_error ${error.message}`;
  }
}

async function runGitAction(action) {
  const summary = document.getElementById("git-summary");
  if (!currentProject || !summary) {
    return;
  }
  summary.textContent = `Running git ${action}...`;
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/git/${action}`, {method: "POST"});
    renderGitStatus(result.git);
    document.getElementById("git-diff").textContent = [
      result.result?.stdout || "",
      result.result?.stderr || "",
    ].filter(Boolean).join("\n") || `git ${action} finished`;
  } catch (error) {
    summary.textContent = `git_${action}_error ${error.message}`;
  }
}

async function checkoutGitBranch() {
  const summary = document.getElementById("git-summary");
  const select = document.getElementById("git-branch-select");
  const branch = select?.value || "";
  if (!currentProject || !summary || !branch) {
    return;
  }
  summary.textContent = `Running git checkout ${branch}...`;
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/git/checkout`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({branch}),
    });
    renderGitStatus(result.git);
    document.getElementById("git-diff").textContent = [
      result.result?.stdout || "",
      result.result?.stderr || "",
    ].filter(Boolean).join("\n") || `checked out ${branch}`;
    await loadProjectTree("");
  } catch (error) {
    summary.textContent = `git_checkout_error ${error.message}`;
  }
}

async function initGitRepo() {
  const summary = document.getElementById("git-summary");
  if (!currentProject || !summary) {
    return;
  }
  summary.textContent = "Running git init...";
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/git/init`, {method: "POST"});
    renderGitStatus(result.git);
    document.getElementById("git-diff").textContent = (result.results || []).map((item) => [
      `$ ${item.args.join(" ")}`,
      item.stdout || "",
      item.stderr || "",
    ].filter(Boolean).join("\n")).join("\n\n") || "git repo already exists";
  } catch (error) {
    summary.textContent = `git_init_error ${error.message}`;
  }
}

async function submitGitCommit(event) {
  event.preventDefault();
  const messageInput = document.getElementById("git-commit-message");
  const summary = document.getElementById("git-summary");
  const files = Array.from(document.querySelectorAll("#git-files input[type='checkbox']:checked")).map((item) => item.value);
  const message = messageInput.value.trim();
  if (!message || !files.length) {
    summary.textContent = "Commit requires a message and at least one selected file.";
    return;
  }
  summary.textContent = "Running git commit...";
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/git/commit`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({message, files}),
    });
    renderGitStatus(result.git);
    document.getElementById("git-diff").textContent = [
      result.result?.stdout || "",
      result.result?.stderr || "",
    ].filter(Boolean).join("\n") || "git commit finished";
    if (result.ok) {
      messageInput.value = "";
    }
  } catch (error) {
    summary.textContent = `git_commit_error ${error.message}`;
  }
}

async function loadProjectTree(path = currentTreePath) {
  const status = document.getElementById("tree-status");
  const list = document.getElementById("tree-list");
  const pathLabel = document.getElementById("tree-path");
  const depth = 8;
  if (!status || !list || !pathLabel || !currentProject) {
    return;
  }
  currentTreePath = path || "";
  status.textContent = "Loading";
  pathLabel.textContent = currentTreePath ? `res://${currentTreePath}` : "res://";
  try {
    const result = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/tree?path=${encodeURIComponent(currentTreePath)}&max_depth=${encodeURIComponent(depth)}`,
    );
    status.textContent = result.truncated ? "Truncated" : "Loaded";
    list.innerHTML = treeNodeHtml(result.tree);
    collapseAllTreeChildren(list);
    applySizeFilters();
  } catch (error) {
    status.textContent = "Failed";
    list.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

function treeNodeHtml(node) {
  if (!node) {
    return '<p class="muted">Empty tree.</p>';
  }
  const isDir = node.kind === "directory";
  const gitStatus = node.git_status || "";
  const gitClass = gitStatus ? ` git-${gitStatus}` : "";

  if (isDir) {
    const children = (node.children || []);
    const childHtml = children.map((c) => treeNodeHtml(c)).join("");
    const truncated = node.truncated ? ' <span class="muted truncated-tag"> (truncated)</span>' : "";
    const treeId = `tree-${node.path.replace(/[^a-zA-Z0-9]/g, "_")}` || "tree-root";
    const dirSize = node.size !== undefined
      ? ` <span class="tree-size dir-size">${formatBytes(node.size)}</span>`
      : "";
    return `
      <div class="tree-node">
        <div class="tree-row tree-dir${gitClass}" data-tree-id="${treeId}" onclick="toggleTree('${treeId}', this)">
          <span class="tree-toggle" id="${treeId}-toggle">▶</span>
          <code>${escapeHtml(node.name || "(root)")}${dirSize}${truncated}</code>
        </div>
        <div class="tree-children" id="${treeId}-children">${childHtml}</div>
      </div>`;
  } else {
    const size = node.size !== undefined
      ? ` <span class="tree-size file-size">${formatBytes(node.size)}</span>`
      : "";
    return `
      <div class="tree-node">
        <div class="tree-row tree-file${gitClass}">
          <span class="tree-toggle" style="visibility:hidden">▶</span>
          <code>${escapeHtml(node.name)}${size}</code>
        </div>
      </div>`;
  }
}

function toggleTree(treeId, rowEl) {
  const children = document.getElementById(treeId + "-children");
  const toggle = document.getElementById(treeId + "-toggle");
  if (!children || !toggle) return;
  if (children.style.display === "none") {
    children.style.display = "block";
    toggle.textContent = "▼";
  } else {
    children.style.display = "none";
    toggle.textContent = "▶";
  }
}

function collapseAllTreeChildren(container) {
  for (const el of container.querySelectorAll(".tree-children")) {
    el.style.display = "none";
  }
}

function applySizeFilters() {
  const showFiles = document.getElementById("show-file-size")?.checked ?? true;
  const showDirs = document.getElementById("show-dir-size")?.checked ?? false;
  for (const el of document.querySelectorAll(".file-size")) {
    el.style.display = showFiles ? "" : "none";
  }
  for (const el of document.querySelectorAll(".dir-size")) {
    el.style.display = showDirs ? "" : "none";
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

async function loadSessionList() {
  const select = document.getElementById("session-select");
  if (!select || !currentProject) return;
  try {
    const result = await fetchJson(`/api/projects/${encodeURIComponent(currentProject)}/sessions`);
    const sessions = result.sessions || [];
    select.innerHTML = '<option value="">选择历史会话...</option>';
    for (const s of sessions) {
      const opt = document.createElement("option");
      opt.value = s.session_id;
      opt.textContent = s.title || s.session_id;
      if (currentSession && s.session_id === currentSession.session_id) {
        opt.selected = true;
      }
      select.appendChild(opt);
    }
  } catch (_) {
    select.innerHTML = '<option value="">加载失败</option>';
  }
}

document.getElementById("load-session-btn").addEventListener("click", async () => {
  const select = document.getElementById("session-select");
  const sessionId = select.value;
  if (!sessionId) return;
  try {
    const detail = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(sessionId)}`,
    );
    currentSession = detail.session;
    latestReview = detail.latest_review;
    localStorage.setItem(selectedSessionKey(currentProject), sessionId);
    renderMessages(detail.messages);
    renderReview(latestReview);
    document.getElementById("session-status").textContent = `当前对话：${currentSession.title}`;
  } catch (error) {
    alert(`加载会话失败：${error.message}`);
  }
});



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

function renderTaskDetails(item) {
  const scope = (item.scope || []).filter(Boolean);
  const acceptance = (item.acceptance || []).filter(Boolean);
  const verification = (item.verification || []).filter(Boolean);
  const dependsOn = (item.depends_on || []).filter(Boolean);

  if (!scope.length && !acceptance.length && !verification.length && !dependsOn.length) {
    return "";
  }

  const detailId = `task-detail-${item.item_id}`;
  const sections = [];
  if (scope.length) sections.push(`<strong>Scope:</strong> ${scope.map((s) => `<code>${escapeHtml(s)}</code>`).join(", ")}`);
  if (acceptance.length) sections.push(`<strong>验收:</strong> ${acceptance.map((s) => escapeHtml(s)).join("；")}`);
  if (verification.length) sections.push(`<strong>验证:</strong> ${verification.map((s) => `<code>${escapeHtml(s)}</code>`).join("；")}`);
  if (dependsOn.length) sections.push(`<strong>依赖:</strong> ${dependsOn.map((s) => escapeHtml(s)).join(", ")}`);

  return `
    <div class="task-detail-fold">
      <div class="task-detail-head" onclick="document.getElementById('${detailId}').classList.toggle('is-hidden');this.querySelector('span').textContent = document.getElementById('${detailId}').classList.contains('is-hidden') ? '▶' : '▼'">
        <span>▶</span> 详情 (Scope · 验收 · 验证)
      </div>
      <div class="task-detail-body is-hidden" id="${detailId}">
        ${sections.map((s) => `<p class="muted" style="margin:4px 0;font-size:0.8rem">${s}</p>`).join("")}
      </div>
    </div>
  `;
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
  applyPlanStateToRuntime(review);
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
      ${renderTaskDetails(item)}
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

function applyPlanStateToRuntime(review) {
  const taskStatus = review?.plan_state?.task_status || {};
  for (const [taskId, status] of Object.entries(taskStatus)) {
    const current = taskRuntimeStatus[taskId];
    if (current && !["not_started", "queued"].includes(current.status)) {
      continue;
    }
    taskRuntimeStatus[taskId] = planStateRuntimeStatus(status);
  }
}

function planStateRuntimeStatus(status) {
  return ({
    pass: {status: "passed", label: "passed"},
    fail: {status: "failed", label: "failed"},
    running: {status: "running", label: "running"},
    pending: {status: "not_started", label: "not started"},
  })[status] || {status: "not_started", label: status || "not started"};
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
  const head = document.createElement("div");
  head.className = "bubble-head";
  const title = document.createElement("strong");
  title.textContent = role === "user" ? "You" : "Godotter";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "bubble-copy";
  copyBtn.title = "复制到剪贴板";
  copyBtn.textContent = "复制";
  copyBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.textContent = "已复制";
      setTimeout(() => { copyBtn.textContent = "复制"; }, 1200);
    }).catch(() => {
      copyBtn.textContent = "失败";
      setTimeout(() => { copyBtn.textContent = "复制"; }, 1200);
    });
  });
  head.append(title, copyBtn);
  const contentDiv = document.createElement("div");
  contentDiv.className = "bubble-content";

  const paragraphs = text.split(/\n\n+/);
  for (const para of paragraphs) {
    const trimmed = para.trim();
    if (!trimmed) {
      continue;
    }

    const lineCount = (trimmed.match(/^ {0,2}\d+ \| /gm) || []).length;

    const isSceneInspect = trimmed.includes("uid=uid://") && trimmed.includes("ext_resource");
    const isProjectInfo = /^(name|main_scene|autoloads|script_count|scene_count)=/.test(trimmed);
    const isFileList = /^[a-zA-Z]/.test(trimmed) && (trimmed.match(/^(game|tests|ui|\.godotter)\\?/gm) || []).length >= 3;

    const toolFold = isSceneInspect ? "场景结构" : isProjectInfo ? "项目信息" : isFileList ? "文件列表" : null;

    if (toolFold) {
      const lines = trimmed.split("\n").length;
      const wrapper = document.createElement("div");
      wrapper.className = "code-fold";
      const foldHead = document.createElement("div");
      foldHead.className = "code-fold-head";
      const label = document.createElement("span");
      label.textContent = `${toolFold} · ${lines} 行`;
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "code-fold-toggle";
      toggle.textContent = "展开";
      foldHead.append(label, toggle);
      const codeBody = document.createElement("div");
      codeBody.className = "code-fold-body";
      codeBody.style.display = "none";
      const codeEl = document.createElement("code");
      codeEl.textContent = trimmed;
      codeBody.appendChild(codeEl);
      toggle.addEventListener("click", () => {
        const hidden = codeBody.style.display === "none";
        codeBody.style.display = hidden ? "block" : "none";
        toggle.textContent = hidden ? "收起" : "展开";
      });
      wrapper.append(foldHead, codeBody);
      contentDiv.appendChild(wrapper);
    } else if (lineCount >= 8) {
      const wrapper = document.createElement("div");
      wrapper.className = "code-fold";

      const foldHead = document.createElement("div");
      foldHead.className = "code-fold-head";

      const label = document.createElement("span");
      const firstLine = trimmed.split("\n")[0].replace(/^ {0,2}\d+ \| /, "").trim().slice(0, 60);
      label.textContent = `代码块 · ${lineCount} 行 · ${firstLine}...`;

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "code-fold-toggle";
      toggle.textContent = "展开";
      foldHead.append(label, toggle);

      const codeBody = document.createElement("div");
      codeBody.className = "code-fold-body";
      codeBody.style.display = "none";
      const codeEl = document.createElement("code");
      codeEl.textContent = trimmed;
      codeBody.appendChild(codeEl);

      toggle.addEventListener("click", () => {
        const hidden = codeBody.style.display === "none";
        codeBody.style.display = hidden ? "block" : "none";
        toggle.textContent = hidden ? "收起" : "展开";
      });

      wrapper.append(foldHead, codeBody);
      contentDiv.appendChild(wrapper);
    } else {
      const p = document.createElement("p");
      p.textContent = trimmed;
      contentDiv.appendChild(p);
    }
  }
  bubble.append(head, contentDiv);
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

async function generatePlanFromGoal() {
  const goalInput = document.getElementById("plan-goal");
  const status = document.getElementById("session-status");
  const planStatus = document.getElementById("plan-status");
  const submitBtn = document.getElementById("plan-submit");
  const text = goalInput.value.trim();
  if (!currentProject) {
    status.textContent = "请先选择工作区。";
    return;
  }
  if (!text) {
    if (planStatus) planStatus.textContent = "请先输入计划目标。";
    return;
  }
  try {
    goalInput.value = "";
    const session = await ensureSession(text);
    currentSession = session;
    if (planStatus) planStatus.textContent = "正在生成计划草案...";
    if (submitBtn) submitBtn.disabled = true;
    status.textContent = "正在生成计划草案...";
    const plan = await fetchJson(
      `/api/projects/${encodeURIComponent(currentProject)}/sessions/${encodeURIComponent(currentSession.session_id)}/plan`,
      {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({goal: text}),
      },
    );
    currentSession = plan.session;
    latestReview = plan.review;
    appendBubble("assistant", plan.message.content);
    renderReview(latestReview);
    status.textContent = `计划草案已生成：${plan.review.items.length} 个任务`;
    if (planStatus) planStatus.textContent = `已生成 ${plan.review.items.length} 个任务`;
  } catch (error) {
    appendBubble("assistant warning", `计划生成失败：${error.message}`);
    status.textContent = "计划生成失败。";
    if (planStatus) planStatus.textContent = "生成失败。";
    if (text) {
      goalInput.value = text;
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

document.getElementById("plan-goal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await generatePlanFromGoal();
});

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

document.getElementById("build-form").addEventListener("submit", submitBuild);
document.getElementById("build-doctor").addEventListener("click", runBuildDoctor);
document.getElementById("build-refresh").addEventListener("click", loadBuilds);
document.getElementById("git-refresh").addEventListener("click", loadGitStatus);
document.getElementById("git-init").addEventListener("click", initGitRepo);
document.getElementById("git-fetch").addEventListener("click", () => runGitAction("fetch"));
document.getElementById("git-pull").addEventListener("click", () => runGitAction("pull"));
document.getElementById("git-push").addEventListener("click", () => runGitAction("push"));
document.getElementById("git-checkout").addEventListener("click", checkoutGitBranch);
document.getElementById("git-commit-form").addEventListener("submit", submitGitCommit);
document.getElementById("tree-refresh").addEventListener("click", () => loadProjectTree());
document.getElementById("tree-root").addEventListener("click", () => loadProjectTree(""));

document.getElementById("show-file-size").addEventListener("change", (e) => {
  for (const el of document.querySelectorAll(".file-size")) {
    el.style.display = e.target.checked ? "" : "none";
  }
});

document.getElementById("show-dir-size").addEventListener("change", (e) => {
  for (const el of document.querySelectorAll(".dir-size")) {
    el.style.display = e.target.checked ? "" : "none";
  }
});

setupViewTabs();
setupBurger();
loadState();
