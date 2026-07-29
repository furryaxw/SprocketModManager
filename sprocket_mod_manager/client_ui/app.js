"use strict";

const TEXT = {
  zh: {
    primaryNav: "主导航",
    catalog: "模组目录",
    installed: "已安装",
    downloads: "下载队列",
    settings: "设置",
    about: "关于",
    language: "界面语言",
    languageAuto: "跟随系统",
    connecting: "正在连接",
    connected: "Registry 已连接",
    connectionFailed: "Registry 连接失败",
    catalogTitle: "模组目录",
    installedTitle: "安装管理",
    downloadsTitle: "下载队列",
    settingsTitle: "应用设置",
    aboutTitle: "关于模组管理器",
    refresh: "刷新",
    batchInstall: "批量安装",
    search: "搜索",
    searchPlaceholder: "搜索名称、作者或标签",
    category: "分类",
    categoryAll: "全部分类",
    categoryGameplay: "玩法",
    categoryUtility: "工具",
    categoryLibrary: "依赖库",
    categoryVisual: "视觉",
    categoryAudio: "音频",
    categoryOther: "其他",
    sort: "排序",
    sortName: "名称",
    sortRelease: "版本",
    sortCategory: "分类",
    loadingCatalog: "正在加载目录",
    nothingSelected: "未选择模组",
    updateAll: "全部更新",
    clearFinished: "清除已结束",
    gameLocation: "游戏位置",
    gamePath: "Sprocket 路径",
    browse: "浏览",
    indexUrl: "索引 URL 或本地路径",
    saveSettings: "保存设置",
    currentVersion: "当前版本",
    latestVersion: "最新版本",
    repository: "代码仓库",
    checkUpdate: "检查更新",
    getUpdate: "获取更新",
    upToDate: "已是最新",
    updateUnavailable: "暂不可用",
    starting: "正在启动",
    loading: "正在读取 Registry",
    ready: "目录已就绪，共 {count} 个模组",
    noResults: "没有匹配的模组",
    available: "可安装",
    installedState: "已安装",
    updateAvailable: "可更新",
    unavailable: "无可用版本",
    version: "版本",
    authors: "作者",
    license: "许可证",
    categoryLabel: "分类",
    assets: "安装资产",
    dependencies: "依赖",
    none: "无",
    repositoryAction: "查看仓库",
    install: "安装",
    update: "更新",
    remove: "卸载",
    managedPackages: "{count} 个受管软件包",
    noInstalled: "没有由管理器安装的软件包",
    requested: "用户安装",
    dependency: "依赖安装",
    queueItems: "队列中有 {count} 项",
    queueEmpty: "下载队列为空",
    waiting: "等待中",
    installing: "安装中",
    completed: "已完成",
    failed: "失败",
    canceled: "已取消",
    cancelItem: "取消",
    confirm: "确认",
    cancel: "取消",
    close: "关闭",
    confirmInstall: "确认安装",
    confirmBatchInstall: "确认批量安装",
    confirmRemove: "确认卸载",
    removeMessage: "卸载 {name}？不再使用的依赖也会一并卸载。",
    resolving: "正在解析安装计划",
    queued: "已加入 {count} 个安装任务",
    nothingToInstall: "所选模组已经是最新版本",
    gamePathRequired: "请先在设置中填写有效的 Sprocket 游戏路径。",
    settingsSaved: "设置已保存",
    removed: "已卸载：{names}",
    noUpdates: "没有可用更新",
    updateQueued: "已加入 {count} 个更新任务",
    catalogError: "目录加载失败",
    operationFailed: "操作失败",
    checkingUpdate: "正在检查更新",
    updateFound: "模组管理器有新版本",
    updateMessage: "新版本 {latest} 已发布，当前版本为 {current}。",
    later: "稍后",
    closeWaiting: "当前安装完成后将自动关闭",
    steamNotFound: "未能通过 Steam 找到 Sprocket",
  },
  en: {
    primaryNav: "Primary navigation",
    catalog: "Catalog",
    installed: "Installed",
    downloads: "Download queue",
    settings: "Settings",
    about: "About",
    language: "Interface language",
    languageAuto: "System default",
    connecting: "Connecting",
    connected: "Registry connected",
    connectionFailed: "Registry connection failed",
    catalogTitle: "Mod catalog",
    installedTitle: "Managed installation",
    downloadsTitle: "Download queue",
    settingsTitle: "Application settings",
    aboutTitle: "About the manager",
    refresh: "Refresh",
    batchInstall: "Batch install",
    search: "Search",
    searchPlaceholder: "Search name, author or tag",
    category: "Category",
    categoryAll: "All categories",
    categoryGameplay: "Gameplay",
    categoryUtility: "Utility",
    categoryLibrary: "Libraries",
    categoryVisual: "Visual",
    categoryAudio: "Audio",
    categoryOther: "Other",
    sort: "Sort",
    sortName: "Name",
    sortRelease: "Version",
    sortCategory: "Category",
    loadingCatalog: "Loading catalog",
    nothingSelected: "No mod selected",
    updateAll: "Update all",
    clearFinished: "Clear finished",
    gameLocation: "Game location",
    gamePath: "Sprocket path",
    browse: "Browse",
    indexUrl: "Index URL or local path",
    saveSettings: "Save settings",
    currentVersion: "Current version",
    latestVersion: "Latest version",
    repository: "Repository",
    checkUpdate: "Check update",
    getUpdate: "Get update",
    upToDate: "Up to date",
    updateUnavailable: "Unavailable",
    starting: "Starting",
    loading: "Loading Registry",
    ready: "Catalog ready - {count} mods",
    noResults: "No matching mods",
    available: "Available",
    installedState: "Installed",
    updateAvailable: "Update available",
    unavailable: "Unavailable",
    version: "Version",
    authors: "Authors",
    license: "License",
    categoryLabel: "Category",
    assets: "Install assets",
    dependencies: "Dependencies",
    none: "None",
    repositoryAction: "Repository",
    install: "Install",
    update: "Update",
    remove: "Remove",
    managedPackages: "{count} managed packages",
    noInstalled: "No manager-installed packages",
    requested: "User-installed",
    dependency: "Installed dependency",
    queueItems: "{count} queue items",
    queueEmpty: "Download queue is empty",
    waiting: "Waiting",
    installing: "Installing",
    completed: "Completed",
    failed: "Failed",
    canceled: "Canceled",
    cancelItem: "Cancel",
    confirm: "Confirm",
    cancel: "Cancel",
    close: "Close",
    confirmInstall: "Confirm install",
    confirmBatchInstall: "Confirm batch install",
    confirmRemove: "Confirm removal",
    removeMessage: "Remove {name}? Unused dependencies will also be removed.",
    resolving: "Resolving install plan",
    queued: "Queued {count} install task(s)",
    nothingToInstall: "Selected mods are already up to date",
    gamePathRequired: "Set a valid Sprocket game path in Settings first.",
    settingsSaved: "Settings saved",
    removed: "Removed: {names}",
    noUpdates: "No updates available",
    updateQueued: "Queued {count} update task(s)",
    catalogError: "Catalog failed to load",
    operationFailed: "Operation failed",
    checkingUpdate: "Checking for updates",
    updateFound: "Manager update available",
    updateMessage: "Version {latest} is available. You are using {current}.",
    later: "Later",
    closeWaiting: "The window will close after the current install finishes",
    steamNotFound: "Sprocket was not found through Steam",
  },
};

const state = {
  ready: false,
  version: "-",
  page: "catalog",
  languageMode: "auto",
  language: "zh",
  packages: [],
  installed: [],
  queue: [],
  selectedId: null,
  batch: new Set(),
  settings: { language: "auto", game_path: "", index_url: "", index_placeholder: "" },
  links: { repository: "", registry: "" },
  update: null,
  queueSignature: "",
  queueStates: new Map(),
  modalAction: null,
  catalogLoading: true,
};

const PAGE_META = {
  catalog: { kicker: "REGISTRY", title: "catalogTitle", subtitle: () => "sprocketmods.furryaxw.top" },
  installed: { kicker: "INSTALLATION", title: "installedTitle", subtitle: gamePathSummary },
  downloads: { kicker: "TRANSFERS", title: "downloadsTitle", subtitle: queueSummary },
  settings: { kicker: "CONFIGURATION", title: "settingsTitle", subtitle: () => "LOCAL SETTINGS" },
  about: { kicker: "APPLICATION", title: "aboutTitle", subtitle: () => `VERSION ${state.version}` },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function tr(key, values = {}) {
  let value = TEXT[state.language]?.[key] ?? TEXT.en[key] ?? key;
  for (const [name, replacement] of Object.entries(values)) {
    value = value.replaceAll(`{${name}}`, String(replacement));
  }
  return value;
}

function localized(values, fallback = "") {
  if (!values || typeof values !== "object") return fallback;
  const entries = Object.entries(values);
  if (!entries.length) return fallback;
  const exact = entries.find(([key]) => key.toLowerCase() === state.language.toLowerCase());
  if (exact) return exact[1];
  const base = state.language.split("-", 1)[0].toLowerCase();
  const related = entries.find(([key]) => key.toLowerCase().split("-", 1)[0] === base);
  if (related) return related[1];
  const english = entries.find(([key]) => key.toLowerCase().split("-", 1)[0] === "en");
  return english?.[1] ?? entries[0][1] ?? fallback;
}

function packageLabel(pkg) {
  return localized(pkg.display_name, pkg.name || pkg.id);
}

function setLanguage(language) {
  state.language = language === "zh" ? "zh" : "en";
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  applyTranslations();
  renderCatalog();
  renderInstalled();
  renderQueue();
  renderDetail();
  updatePageHeader();
}

function applyTranslations() {
  $$('[data-i18n]').forEach((element) => { element.textContent = tr(element.dataset.i18n); });
  $$('[data-i18n-placeholder]').forEach((element) => { element.placeholder = tr(element.dataset.i18nPlaceholder); });
  $$('[data-i18n-aria]').forEach((element) => { element.setAttribute("aria-label", tr(element.dataset.i18nAria)); });
  $("#language-select").value = state.languageMode;
}

async function callApi(method, ...args) {
  if (!window.pywebview?.api?.[method]) throw new Error(`API unavailable: ${method}`);
  return window.pywebview.api[method](...args);
}

function setStatus(message, tone = "normal", source = "") {
  $("#status-text").textContent = message;
  $("#status-source").textContent = source;
  const mark = $(".status-mark");
  mark.classList.toggle("ready", tone === "ready");
  mark.classList.toggle("error", tone === "error");
}

function setRegistryState(kind, text) {
  const element = $("#registry-state");
  element.classList.toggle("ready", kind === "ready");
  element.classList.toggle("error", kind === "error");
  $("#registry-state-text").textContent = text;
}

function toast(message, tone = "normal") {
  const element = document.createElement("div");
  element.className = `toast ${tone}`;
  element.textContent = message;
  $("#toast-region").append(element);
  window.setTimeout(() => element.remove(), 4300);
}

function resultError(result, fallbackKey = "operationFailed") {
  const message = result?.message || tr(fallbackKey);
  toast(message, "error");
  setStatus(message, "error");
  return message;
}

function gamePathSummary() {
  return state.settings.game_path || $("#game-path")?.placeholder || tr("gamePath");
}

function queueSummary() {
  return tr("queueItems", { count: state.queue.length });
}

function updatePageHeader() {
  const meta = PAGE_META[state.page];
  $("#page-kicker").textContent = meta.kicker;
  $("#page-title").textContent = tr(meta.title);
  $("#page-subtitle").textContent = meta.subtitle();
  $("#header-actions").hidden = state.page !== "catalog";
}

async function showPage(page) {
  if (!PAGE_META[page]) return;
  state.page = page;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.pageTarget === page));
  $$(".page").forEach((element) => {
    const active = element.dataset.page === page;
    element.hidden = !active;
    element.classList.toggle("active", active);
  });
  updatePageHeader();
  if (page === "settings") await reloadSettings();
  if (page === "installed") await refreshInstalled();
  if (page === "downloads") await pollQueue(true);
  if (page === "about" && !state.update) await checkManagerUpdate(false);
}

function categoryText(category) {
  const key = {
    gameplay: "categoryGameplay",
    utility: "categoryUtility",
    library: "categoryLibrary",
    visual: "categoryVisual",
    audio: "categoryAudio",
    other: "categoryOther",
  }[category] || "categoryOther";
  return tr(key);
}

function semverParts(value) {
  const match = String(value || "").match(/^(\d+)\.(\d+)\.(\d+)(?:-(.*))?$/);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] || ""] : [0, 0, 0, ""];
}

function compareVersions(left, right) {
  const a = semverParts(left);
  const b = semverParts(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  if (a[3] === b[3]) return 0;
  if (!a[3]) return 1;
  if (!b[3]) return -1;
  return String(a[3]).localeCompare(String(b[3]));
}

function filteredPackages() {
  const keyword = $("#catalog-search").value.trim().toLocaleLowerCase();
  const category = $("#category-select").value;
  const sort = $("#sort-select").value;
  const result = state.packages.filter((pkg) => {
    if (category !== "all" && pkg.category !== category) return false;
    const haystack = [
      pkg.id,
      pkg.name,
      pkg.repository,
      packageLabel(pkg),
      ...(pkg.authors || []),
      ...(pkg.tags || []),
    ].join(" ").toLocaleLowerCase();
    return !keyword || haystack.includes(keyword);
  });
  result.sort((left, right) => {
    if (sort === "release") {
      return compareVersions(right.release?.version, left.release?.version) || packageLabel(left).localeCompare(packageLabel(right));
    }
    if (sort === "category") {
      return left.category.localeCompare(right.category) || packageLabel(left).localeCompare(packageLabel(right));
    }
    return packageLabel(left).localeCompare(packageLabel(right));
  });
  return result;
}

function packageState(pkg) {
  if (!pkg.release) return { label: tr("unavailable"), className: "" };
  if (!pkg.installed) return { label: tr("available"), className: "available" };
  if (pkg.installed.version !== pkg.release.version) return { label: tr("updateAvailable"), className: "update" };
  return { label: tr("installedState"), className: "available" };
}

function packageEligible(pkg) {
  return Boolean(pkg.release && (!pkg.installed || pkg.installed.version !== pkg.release.version));
}

function renderCatalog() {
  const container = $("#package-list");
  if (!container) return;
  const packages = filteredPackages();
  $("#catalog-count").textContent = String(packages.length);
  container.replaceChildren();
  if (state.catalogLoading && !state.packages.length) {
    const loading = document.createElement("div");
    loading.className = "loading-state";
    const spinner = document.createElement("span");
    spinner.className = "loader";
    const label = document.createElement("span");
    label.textContent = tr("loadingCatalog");
    loading.append(spinner, label);
    container.append(loading);
    return;
  }
  if (!packages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    const title = document.createElement("strong");
    title.textContent = tr("noResults");
    empty.append(title);
    container.append(empty);
  }
  for (const pkg of packages) {
    const row = document.createElement("article");
    row.className = "package-row";
    row.tabIndex = 0;
    row.classList.toggle("selected", state.selectedId === pkg.id);
    row.addEventListener("click", () => selectPackage(pkg.id));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectPackage(pkg.id);
      }
    });

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "package-check";
    checkbox.checked = state.batch.has(pkg.id);
    checkbox.disabled = !packageEligible(pkg);
    checkbox.setAttribute("aria-label", `${tr("batchInstall")}: ${packageLabel(pkg)}`);
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.batch.add(pkg.id);
      else state.batch.delete(pkg.id);
      updateBatchButton();
    });

    const copy = document.createElement("div");
    copy.className = "package-copy";
    const title = document.createElement("strong");
    title.textContent = packageLabel(pkg);
    const metadata = document.createElement("span");
    metadata.textContent = `${pkg.id}  |  ${categoryText(pkg.category)}`;
    copy.append(title, metadata);

    const version = document.createElement("div");
    version.className = "package-version";
    const number = document.createElement("b");
    number.textContent = pkg.release?.version || "-";
    const chip = document.createElement("span");
    const currentState = packageState(pkg);
    chip.className = `state-chip ${currentState.className}`;
    chip.textContent = currentState.label;
    version.append(number, chip);

    row.append(checkbox, copy, version);
    container.append(row);
  }
  updateBatchButton();
  renderDetail();
}

function updateBatchButton() {
  for (const id of [...state.batch]) {
    const pkg = state.packages.find((item) => item.id === id);
    if (!pkg || !packageEligible(pkg)) state.batch.delete(id);
  }
  $("#batch-count").textContent = String(state.batch.size);
  $("#batch-install").disabled = state.batch.size === 0;
}

function selectPackage(packageId) {
  state.selectedId = packageId;
  renderCatalog();
}

function appendFact(list, label, value) {
  const group = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value || tr("none");
  group.append(term, description);
  list.append(group);
}

function renderDetail() {
  const panel = $("#detail-panel");
  if (!panel) return;
  const pkg = state.packages.find((item) => item.id === state.selectedId);
  panel.replaceChildren();
  if (!pkg) {
    const empty = document.createElement("div");
    empty.className = "empty-detail";
    const glyph = document.createElement("span");
    glyph.className = "empty-glyph";
    glyph.textContent = "+";
    const title = document.createElement("strong");
    title.textContent = tr("nothingSelected");
    empty.append(glyph, title);
    panel.append(empty);
    return;
  }

  const topline = document.createElement("div");
  topline.className = "detail-topline";
  const record = document.createElement("span");
  record.textContent = "PACKAGE RECORD";
  const chip = document.createElement("span");
  const currentState = packageState(pkg);
  chip.className = `state-chip ${currentState.className}`;
  chip.textContent = currentState.label;
  topline.append(record, chip);

  const title = document.createElement("h2");
  title.textContent = packageLabel(pkg);
  const id = document.createElement("p");
  id.className = "detail-id";
  id.textContent = pkg.id;
  const description = document.createElement("p");
  description.className = "detail-description";
  description.textContent = localized(pkg.description, "");

  const facts = document.createElement("dl");
  facts.className = "detail-facts";
  appendFact(facts, tr("version"), pkg.release?.version || "-");
  appendFact(facts, tr("authors"), (pkg.authors || []).join(", "));
  appendFact(facts, tr("license"), pkg.license);
  appendFact(facts, tr("categoryLabel"), categoryText(pkg.category));
  appendFact(facts, tr("assets"), (pkg.install_assets || []).join(", "));
  appendFact(facts, tr("repository"), pkg.repository);

  const dependencySection = document.createElement("section");
  dependencySection.className = "dependency-section";
  const dependencyTitle = document.createElement("strong");
  dependencyTitle.textContent = tr("dependencies").toUpperCase();
  const dependencies = document.createElement("div");
  dependencies.className = "dependency-list";
  if (!pkg.dependencies?.length) {
    const none = document.createElement("span");
    none.className = "detail-id";
    none.textContent = tr("none");
    dependencies.append(none);
  } else {
    for (const item of pkg.dependencies) {
      const line = document.createElement("div");
      line.className = "dependency-line";
      const name = document.createElement("span");
      name.textContent = item.id;
      const range = document.createElement("span");
      range.textContent = item.version;
      line.append(name, range);
      dependencies.append(line);
    }
  }
  dependencySection.append(dependencyTitle, dependencies);

  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const repo = document.createElement("button");
  repo.className = "secondary-button";
  repo.type = "button";
  repo.textContent = tr("repositoryAction");
  repo.addEventListener("click", () => openUrl(pkg.repository_url));
  const remove = document.createElement("button");
  remove.className = "danger-button";
  remove.type = "button";
  remove.textContent = tr("remove");
  remove.disabled = !pkg.installed || queueActive();
  remove.addEventListener("click", () => confirmRemove(pkg));
  const install = document.createElement("button");
  install.className = "primary-button";
  install.type = "button";
  install.textContent = pkg.installed ? tr("update") : tr("install");
  install.disabled = !packageEligible(pkg);
  install.addEventListener("click", () => beginInstall([pkg.id]));
  actions.append(repo, remove, install);

  panel.append(topline, title, id, description, facts, dependencySection, actions);
}

async function loadCatalog(refresh = false) {
  const button = $("#refresh-catalog");
  button.disabled = true;
  state.catalogLoading = true;
  renderCatalog();
  setRegistryState("loading", tr("connecting"));
  setStatus(tr("loading"));
  try {
    const result = await callApi("load_catalog", refresh);
    if (!result.ok) {
      state.catalogLoading = false;
      renderCatalog();
      setRegistryState("error", tr("connectionFailed"));
      resultError(result, "catalogError");
      return;
    }
    state.packages = result.packages || [];
    state.catalogLoading = false;
    state.installed = result.installed || [];
    state.batch.clear();
    if (!state.packages.some((pkg) => pkg.id === state.selectedId)) state.selectedId = null;
    setRegistryState("ready", tr("connected"));
    setStatus(tr("ready", { count: state.packages.length }), "ready", result.source || "");
    renderCatalog();
    renderInstalled();
  } catch (error) {
    state.catalogLoading = false;
    renderCatalog();
    setRegistryState("error", tr("connectionFailed"));
    resultError({ message: String(error) }, "catalogError");
  } finally {
    button.disabled = false;
  }
}

function createPlanBody(plans) {
  const body = document.createElement("div");
  body.className = "modal-plan";
  for (const plan of plans) {
    const group = document.createElement("section");
    group.className = "plan-group";
    const heading = document.createElement("strong");
    heading.textContent = localized(plan.display_name, plan.name || plan.id);
    group.append(heading);
    for (const item of plan.packages || []) {
      const line = document.createElement("div");
      line.className = "plan-line";
      const label = document.createElement("span");
      label.textContent = localized(item.display_name, item.name || item.id);
      const version = document.createElement("span");
      version.textContent = item.version;
      line.append(label, version);
      group.append(line);
    }
    body.append(group);
  }
  return body;
}

async function beginInstall(packageIds) {
  if (!packageIds.length) return;
  setStatus(tr("resolving"));
  const result = await callApi("plan_install", packageIds);
  if (!result.ok) {
    if (result.code === "game_path_required") {
      showMessage(tr("gamePathRequired"), tr("operationFailed"), () => showPage("settings"));
    } else resultError(result);
    return;
  }
  if (!result.plans.length) {
    toast(tr("nothingToInstall"));
    setStatus(tr("nothingToInstall"), "ready");
    state.batch.clear();
    renderCatalog();
    return;
  }
  const confirmed = await showModal({
    kicker: "INSTALL PLAN",
    title: result.plans.length === 1 ? tr("confirmInstall") : tr("confirmBatchInstall"),
    body: createPlanBody(result.plans),
    confirmText: tr("confirm"),
  });
  if (!confirmed) return;
  const queued = await callApi("enqueue_install", result.plans.map((plan) => plan.id));
  if (!queued.ok) {
    resultError(queued);
    return;
  }
  state.batch.clear();
  renderCatalog();
  toast(tr("queued", { count: queued.count }));
  setStatus(tr("queued", { count: queued.count }), "ready");
  await pollQueue(true);
  await showPage("downloads");
}

async function refreshInstalled() {
  if (!state.ready) return;
  try {
    const result = await callApi("get_installed");
    if (!result.ok) {
      resultError(result);
      return;
    }
    state.installed = result.installed || [];
    const installedById = new Map(state.installed.map((item) => [item.id, item]));
    for (const pkg of state.packages) pkg.installed = installedById.get(pkg.id) || null;
    renderInstalled();
    renderCatalog();
  } catch (error) {
    resultError({ message: String(error) });
  }
}

function renderInstalled() {
  const container = $("#installed-list");
  if (!container) return;
  $("#installed-count").textContent = tr("managedPackages", { count: state.installed.length });
  container.replaceChildren();
  if (!state.installed.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    const title = document.createElement("strong");
    title.textContent = tr("noInstalled");
    empty.append(title);
    container.append(empty);
    return;
  }
  for (const item of state.installed) {
    const pkg = state.packages.find((candidate) => candidate.id === item.id);
    const row = document.createElement("article");
    row.className = "data-row";
    const title = document.createElement("div");
    title.className = "row-title";
    const name = document.createElement("strong");
    name.textContent = pkg ? packageLabel(pkg) : item.name || item.id;
    const metadata = document.createElement("span");
    metadata.textContent = `${item.id}  |  ${item.version || "-"}  |  ${item.requested ? tr("requested") : tr("dependency")}`;
    title.append(name, metadata);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    const remove = document.createElement("button");
    remove.className = "danger-button";
    remove.type = "button";
    remove.textContent = tr("remove");
    remove.disabled = !pkg || queueActive();
    remove.addEventListener("click", () => pkg && confirmRemove(pkg));
    actions.append(remove);
    row.append(title, actions);
    container.append(row);
  }
}

async function confirmRemove(pkg) {
  const confirmed = await showModal({
    kicker: "REMOVE PACKAGE",
    title: tr("confirmRemove"),
    body: tr("removeMessage", { name: packageLabel(pkg) }),
    confirmText: tr("remove"),
    destructive: true,
  });
  if (!confirmed) return;
  const result = await callApi("remove", pkg.id);
  if (!result.ok) {
    resultError(result);
    return;
  }
  const message = tr("removed", { names: result.removed.join(", ") });
  toast(result.warnings?.length ? `${message} | ${result.warnings.join("; ")}` : message);
  setStatus(message, "ready");
  await refreshInstalled();
}

async function updateAll() {
  const button = $("#update-all");
  button.disabled = true;
  try {
    const result = await callApi("update_all");
    if (!result.ok) {
      if (result.code === "game_path_required") {
        showMessage(tr("gamePathRequired"), tr("operationFailed"), () => showPage("settings"));
      } else resultError(result);
      return;
    }
    if (!result.count) {
      toast(tr("noUpdates"));
      setStatus(tr("noUpdates"), "ready");
      return;
    }
    toast(tr("updateQueued", { count: result.count }));
    await pollQueue(true);
    await showPage("downloads");
  } finally {
    button.disabled = false;
  }
}

function queueActive() {
  return state.queue.some((entry) => entry.state === "waiting" || entry.state === "installing");
}

function renderQueue() {
  const container = $("#download-list");
  if (!container) return;
  $("#download-count").textContent = tr("queueItems", { count: state.queue.length });
  const activeCount = state.queue.filter((entry) => entry.state === "waiting" || entry.state === "installing").length;
  const badge = $("#queue-badge");
  badge.hidden = activeCount === 0;
  badge.textContent = String(activeCount);
  container.replaceChildren();
  if (!state.queue.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    const title = document.createElement("strong");
    title.textContent = tr("queueEmpty");
    empty.append(title);
    container.append(empty);
    renderDetail();
    return;
  }
  for (const entry of state.queue) {
    const pkg = state.packages.find((candidate) => candidate.id === entry.package_id);
    const row = document.createElement("article");
    row.className = "data-row";
    const title = document.createElement("div");
    title.className = "row-title";
    const name = document.createElement("strong");
    name.textContent = pkg ? packageLabel(pkg) : entry.package_id;
    const message = document.createElement("span");
    message.className = "queue-message";
    message.textContent = entry.message || entry.package_id;
    title.append(name, message);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    const status = document.createElement("span");
    status.className = `queue-state ${entry.state}`;
    status.textContent = tr(entry.state);
    actions.append(status);
    if (entry.state === "waiting") {
      const cancel = document.createElement("button");
      cancel.className = "secondary-button";
      cancel.type = "button";
      cancel.textContent = tr("cancelItem");
      cancel.addEventListener("click", async () => {
        await callApi("cancel_queue_item", entry.task_id);
        await pollQueue(true);
      });
      actions.append(cancel);
    }
    row.append(title, actions);
    container.append(row);
  }
  renderDetail();
}

async function pollQueue(force = false) {
  if (!state.ready) return;
  try {
    const result = await callApi("get_queue");
    if (!result.ok) return;
    const signature = JSON.stringify([result.entries, result.close_pending]);
    if (!force && signature === state.queueSignature) return;
    const previous = state.queueStates;
    state.queue = result.entries || [];
    state.queueSignature = signature;
    state.queueStates = new Map(state.queue.map((entry) => [entry.task_id, entry.state]));
    const newlyCompleted = state.queue.some((entry) => entry.state === "completed" && previous.get(entry.task_id) !== "completed");
    const newlyFailed = state.queue.find((entry) => entry.state === "failed" && previous.get(entry.task_id) !== "failed");
    renderQueue();
    updatePageHeader();
    if (newlyCompleted) await refreshInstalled();
    if (newlyFailed) toast(newlyFailed.message || tr("operationFailed"), "error");
    if (result.close_pending) setStatus(tr("closeWaiting"));
  } catch (_error) {
    // A closing WebView can reject an in-flight poll. There is nothing left to update.
  }
}

async function reloadSettings() {
  const result = await callApi("get_settings");
  if (!result.ok) {
    resultError(result);
    return;
  }
  state.settings = result.settings;
  state.languageMode = result.settings.language;
  $("#language-select").value = state.languageMode;
  $("#game-path").value = result.settings.game_path;
  $("#index-url").value = result.settings.index_url;
  $("#index-url").placeholder = result.settings.index_placeholder;
  updatePageHeader();
}

async function saveSettings(values = null) {
  const payload = values || {
    language: state.languageMode,
    game_path: $("#game-path").value.trim(),
    index_url: $("#index-url").value.trim(),
  };
  const result = await callApi("save_settings", payload);
  if (!result.ok) {
    resultError(result);
    return false;
  }
  state.settings = result.settings;
  state.languageMode = result.settings.language;
  setLanguage(result.language);
  toast(tr("settingsSaved"));
  setStatus(tr("settingsSaved"), "ready");
  return true;
}

async function chooseGamePath() {
  const result = await callApi("choose_game_path");
  if (!result.ok) {
    resultError(result);
    return;
  }
  if (result.path) $("#game-path").value = result.path;
}

async function detectGamePathPlaceholder() {
  const result = await callApi("find_game_path");
  if (!result.ok) return;
  $("#game-path").placeholder = result.path || tr("steamNotFound");
  updatePageHeader();
}

async function openUrl(url) {
  const result = await callApi("open_url", url);
  if (!result.ok) resultError(result);
}

async function checkManagerUpdate(startup) {
  const button = $("#manager-update");
  button.disabled = true;
  button.textContent = tr("checkingUpdate");
  $("#latest-version").textContent = "...";
  try {
    const result = await callApi("get_manager_update");
    if (!result.ok) {
      $("#latest-version").textContent = tr("updateUnavailable");
      button.textContent = tr("checkUpdate");
      button.disabled = false;
      if (!startup) resultError(result);
      return;
    }
    state.update = result;
    $("#latest-version").textContent = result.latest;
    if (result.newer) {
      button.disabled = false;
      button.textContent = tr("getUpdate");
      if (startup && $("#modal-layer").hidden) {
        const confirmed = await showModal({
          kicker: "UPDATE AVAILABLE",
          title: tr("updateFound"),
          body: tr("updateMessage", { latest: result.latest, current: result.current }),
          confirmText: tr("getUpdate"),
          cancelText: tr("later"),
        });
        if (confirmed) await openUrl(result.page_url);
      }
    } else {
      button.textContent = tr("upToDate");
      button.disabled = true;
    }
  } catch (error) {
    $("#latest-version").textContent = tr("updateUnavailable");
    button.textContent = tr("checkUpdate");
    button.disabled = false;
    if (!startup) resultError({ message: String(error) });
  }
}

function closeModal(result = false) {
  const layer = $("#modal-layer");
  layer.hidden = true;
  document.body.classList.remove("modal-open");
  const action = state.modalAction;
  state.modalAction = null;
  if (action) action(result);
}

function showModal({ kicker, title, body, confirmText, cancelText = null, destructive = false }) {
  if (state.modalAction) closeModal(false);
  $("#modal-kicker").textContent = kicker;
  $("#modal-title").textContent = title;
  const bodyElement = $("#modal-body");
  bodyElement.replaceChildren();
  if (body instanceof Node) bodyElement.append(body);
  else bodyElement.textContent = String(body ?? "");
  const confirm = $("#modal-confirm");
  confirm.textContent = confirmText || tr("confirm");
  confirm.className = destructive ? "danger-button" : "primary-button";
  const cancel = $("#modal-cancel");
  cancel.textContent = cancelText || tr("cancel");
  cancel.hidden = false;
  $("#modal-layer").hidden = false;
  document.body.classList.add("modal-open");
  window.setTimeout(() => confirm.focus(), 0);
  return new Promise((resolve) => { state.modalAction = resolve; });
}

function showMessage(message, title = null, onClose = null) {
  showModal({
    kicker: "NOTICE",
    title: title || tr("operationFailed"),
    body: message,
    confirmText: tr("close"),
  }).then(() => onClose?.());
  $("#modal-cancel").hidden = true;
}

function wireEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => showPage(item.dataset.pageTarget)));
  $("#catalog-search").addEventListener("input", renderCatalog);
  $("#category-select").addEventListener("change", renderCatalog);
  $("#sort-select").addEventListener("change", renderCatalog);
  $("#refresh-catalog").addEventListener("click", () => loadCatalog(true));
  $("#batch-install").addEventListener("click", () => beginInstall([...state.batch]));
  $("#update-all").addEventListener("click", updateAll);
  $("#clear-finished").addEventListener("click", async () => {
    await callApi("clear_finished");
    await pollQueue(true);
  });
  $("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (await saveSettings()) await loadCatalog(false);
  });
  $("#browse-game-path").addEventListener("click", chooseGamePath);
  $("#language-select").addEventListener("change", async (event) => {
    state.languageMode = event.target.value;
    const saved = {
      language: state.languageMode,
      game_path: state.settings.game_path || "",
      index_url: state.settings.index_url || "",
    };
    await saveSettings(saved);
  });
  $("#manager-update").addEventListener("click", () => {
    if (state.update?.newer) void openUrl(state.update.page_url);
    else void checkManagerUpdate(false);
  });
  $("#open-repository").addEventListener("click", () => openUrl(state.links.repository));
  $("#open-registry").addEventListener("click", () => openUrl(state.links.registry));
  $("#modal-close").addEventListener("click", () => closeModal(false));
  $("#modal-cancel").addEventListener("click", () => closeModal(false));
  $("#modal-confirm").addEventListener("click", () => closeModal(true));
  $("#modal-layer").addEventListener("click", (event) => {
    if (event.target === $("#modal-layer")) closeModal(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#modal-layer").hidden) closeModal(false);
  });
}

async function initialize() {
  if (state.ready) return;
  wireEvents();
  try {
    const result = await callApi("bootstrap");
    if (!result.ok) throw new Error(result.message || "bootstrap failed");
    state.ready = true;
    state.version = result.version;
    state.settings = result.settings;
    state.languageMode = result.settings.language;
    state.links = result.links;
    $("#current-version").textContent = state.version;
    $("#game-path").value = result.settings.game_path;
    $("#index-url").value = result.settings.index_url;
    $("#index-url").placeholder = result.settings.index_placeholder;
    setLanguage(result.language);
    await pollQueue(true);
    void detectGamePathPlaceholder();
    void loadCatalog(false);
    window.setTimeout(() => { void checkManagerUpdate(true); }, 350);
    window.setInterval(() => { void pollQueue(false); }, 400);
  } catch (error) {
    state.ready = false;
    setRegistryState("error", tr("connectionFailed"));
    resultError({ message: String(error) });
  }
}

window.addEventListener("pywebviewready", initialize, { once: true });
window.addEventListener("error", (event) => {
  if (!$("#modal-layer").hidden) closeModal(false);
  toast(event.message || tr("operationFailed"), "error");
});
window.addEventListener("unhandledrejection", (event) => {
  if (!$("#modal-layer").hidden) closeModal(false);
  toast(String(event.reason || tr("operationFailed")), "error");
});

if (window.pywebview?.api) void initialize();
