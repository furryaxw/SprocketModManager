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
    textScale: "文字大小",
    browse: "浏览",
    melonloaderStatus: "加载器状态",
    melonloaderChecking: "正在检查本机与官方 Release",
    melonloaderMissing: "未检测到 MelonLoader。安装模组前需要先安装加载器。",
    melonloaderInstalled: "已安装 {installed}，官方最新版本 {latest}",
    melonloaderInstalledUnknown: "已安装 MelonLoader，但无法读取版本；官方最新版本 {latest}",
    melonloaderUpdateReady: "已安装 {installed}，可更新至 {latest}",
    melonloaderUnavailable: "无法读取 MelonLoader 状态",
    melonloaderPathRequired: "保存有效的 Sprocket 路径后即可管理 MelonLoader",
    installMelonLoader: "安装 MelonLoader",
    updateMelonLoader: "更新 MelonLoader",
    reinstallMelonLoader: "重新安装",
    melonloaderInstalling: "正在从官方 GitHub Release 安装 MelonLoader",
    melonloaderInstalledDone: "MelonLoader {version} 安装完成，共写入 {count} 个文件",
    melonloaderRequiredTitle: "尚未安装 MelonLoader",
    melonloaderRequiredMessage: "安装模组需要 MelonLoader。是否现在从官方 GitHub Release 下载 MelonLoader.x64.zip 并安装到 Sprocket？",
    installNow: "现在安装",
    continueWithout: "暂不安装并继续",
    officialRelease: "官方 Release",
    checking: "检查中",
    checkStatus: "重新检查",
    installedLabel: "已安装",
    missingLabel: "未安装",
    updateLabel: "可更新",
    errorLabel: "不可用",
    versionUnknown: "版本未知",
    indexUrl: "索引 URL 或本地路径",
    networkProxy: "网络代理",
    proxyEnabled: "启用 HTTP/HTTPS 代理",
    proxyUrl: "代理地址",
    githubProxyEnabled: "启用 GitHub 下载加速",
    githubProxyUrl: "加速前缀",
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
    recommendations: "推荐模组",
    starterRecommended: "新安装推荐",
    recommendedMods: "推荐一起安装",
    none: "无",
    repositoryAction: "查看仓库",
    loadingReadme: "正在读取 README",
    readmeFailed: "README 读取失败",
    retry: "重试",
    install: "安装",
    update: "更新",
    remove: "卸载",
    detectedMods: "检测到 {count} 个模组",
    noInstalled: "没有检测到模组",
    unrecognized: "无法识别",
    requested: "用户安装",
    dependency: "依赖安装",
    adopted: "从 Mods 自动接管",
    adoptedPackages: "已自动接管 {count} 个现有模组",
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
    textScale: "Text size",
    browse: "Browse",
    melonloaderStatus: "Loader status",
    melonloaderChecking: "Checking the local installation and official Release",
    melonloaderMissing: "MelonLoader was not detected. Install the loader before adding mods.",
    melonloaderInstalled: "Installed {installed}; latest official version {latest}",
    melonloaderInstalledUnknown: "MelonLoader is installed, but its version is unknown; latest official version {latest}",
    melonloaderUpdateReady: "Installed {installed}; update {latest} is available",
    melonloaderUnavailable: "MelonLoader status is unavailable",
    melonloaderPathRequired: "Save a valid Sprocket path to manage MelonLoader",
    installMelonLoader: "Install MelonLoader",
    updateMelonLoader: "Update MelonLoader",
    reinstallMelonLoader: "Reinstall",
    melonloaderInstalling: "Installing MelonLoader from the official GitHub Release",
    melonloaderInstalledDone: "MelonLoader {version} installed; wrote {count} files",
    melonloaderRequiredTitle: "MelonLoader is not installed",
    melonloaderRequiredMessage: "Mods require MelonLoader. Download MelonLoader.x64.zip from the official GitHub Release and install it into Sprocket now?",
    installNow: "Install now",
    continueWithout: "Continue without it",
    officialRelease: "Official Release",
    checking: "Checking",
    checkStatus: "Check again",
    installedLabel: "Installed",
    missingLabel: "Not installed",
    updateLabel: "Update available",
    errorLabel: "Unavailable",
    versionUnknown: "Unknown version",
    indexUrl: "Index URL or local path",
    networkProxy: "Network proxy",
    proxyEnabled: "Enable HTTP/HTTPS proxy",
    proxyUrl: "Proxy address",
    githubProxyEnabled: "Enable GitHub download proxy",
    githubProxyUrl: "Proxy prefix",
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
    recommendations: "Recommended mods",
    starterRecommended: "Recommended for new installs",
    recommendedMods: "Install recommended mods too",
    none: "None",
    repositoryAction: "Repository",
    loadingReadme: "Loading README",
    readmeFailed: "README failed to load",
    retry: "Retry",
    install: "Install",
    update: "Update",
    remove: "Remove",
    detectedMods: "{count} detected mods",
    noInstalled: "No mods detected",
    unrecognized: "Unrecognized",
    requested: "User-installed",
    dependency: "Installed dependency",
    adopted: "Adopted from Mods",
    adoptedPackages: "Adopted {count} existing mod(s)",
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
  unrecognized: [],
  hasAnyMods: false,
  queue: [],
  selectedId: null,
  batch: new Set(),
  settings: {
    language: "auto", game_path: "", index_url: "", index_placeholder: "",
    proxy_enabled: false, proxy_url: "", github_proxy_enabled: false,
    github_proxy_url: "", text_scale: 100,
  },
  links: { repository: "", registry: "" },
  melonloader: null,
  melonloaderLoading: false,
  readmes: new Map(),
  readmeLoading: new Set(),
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

function showStarterRecommendations() {
  return !state.hasAnyMods;
}

function setLanguage(language) {
  state.language = language === "zh" ? "zh" : "en";
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  applyTranslations();
  renderCatalog();
  renderInstalled();
  renderQueue();
  renderDetail();
  renderMelonLoader();
  updatePageHeader();
}

function applyTextScale(value) {
  const parsed = Number.parseInt(value, 10);
  const scale = Number.isFinite(parsed) ? Math.min(160, Math.max(100, parsed)) : 100;
  document.documentElement.style.fontSize = `${scale}%`;
  document.documentElement.classList.toggle("large-text", scale >= 140);
  const input = $("#text-scale");
  const output = $("#text-scale-value");
  if (input) input.value = String(scale);
  if (output) output.textContent = `${scale}%`;
  return scale;
}

function syncProxyControls() {
  $("#proxy-url").disabled = !$("#proxy-enabled").checked;
  $("#github-proxy-url").disabled = !$("#github-proxy-enabled").checked;
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
  if (page === "settings") {
    await reloadSettings();
    await refreshMelonLoaderStatus(false);
  }
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
    if (showStarterRecommendations()) {
      const featured = Number(Boolean(right.featured)) - Number(Boolean(left.featured));
      if (featured) return featured;
    }
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
    if (pkg.featured && showStarterRecommendations()) {
      const star = document.createElement("span");
      star.className = "featured-star";
      star.textContent = "★";
      star.title = tr("starterRecommended");
      star.setAttribute("role", "img");
      star.setAttribute("aria-label", tr("starterRecommended"));
      title.append(star, document.createTextNode(packageLabel(pkg)));
    } else {
      title.textContent = packageLabel(pkg);
    }
    const metadata = document.createElement("span");
    metadata.textContent = localized(pkg.description, pkg.id);
    metadata.title = `${pkg.id} | ${categoryText(pkg.category)}`;
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
  void loadPackageReadme(packageId);
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
    const title = document.createElement("strong");
    title.textContent = tr("nothingSelected");
    empty.append(title);
    panel.append(empty);
    return;
  }

  const topline = document.createElement("div");
  topline.className = "detail-topline";
  const record = document.createElement("span");
  const starterRecommended = pkg.featured && showStarterRecommendations();
  record.textContent = starterRecommended ? `★ ${tr("starterRecommended")}` : "PACKAGE RECORD";
  record.classList.toggle("featured-record", starterRecommended);
  const chip = document.createElement("span");
  const currentState = packageState(pkg);
  chip.className = `state-chip ${currentState.className}`;
  chip.textContent = currentState.label;
  topline.append(record, chip);

  const heading = document.createElement("div");
  heading.className = "detail-heading";
  const title = document.createElement("h2");
  title.textContent = packageLabel(pkg);
  const id = document.createElement("p");
  id.className = "detail-id";
  id.textContent = pkg.id;
  const version = document.createElement("b");
  version.className = "detail-version";
  version.textContent = pkg.release?.version || "-";
  const authors = document.createElement("span");
  authors.className = "detail-authors";
  authors.textContent = (pkg.authors || []).join(", ") || "-";
  heading.append(title, version, id, authors);

  const facts = document.createElement("dl");
  facts.className = "detail-facts";
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

  const recommendationSection = document.createElement("section");
  recommendationSection.className = "dependency-section";
  const recommendationTitle = document.createElement("strong");
  recommendationTitle.textContent = tr("recommendations").toUpperCase();
  const recommendations = document.createElement("div");
  recommendations.className = "dependency-list";
  if (!pkg.recommendations?.length) {
    const none = document.createElement("span");
    none.className = "detail-id";
    none.textContent = tr("none");
    recommendations.append(none);
  } else {
    for (const packageId of pkg.recommendations) {
      const recommended = state.packages.find((candidate) => candidate.id === packageId);
      const line = document.createElement("div");
      line.className = "dependency-line";
      const name = document.createElement("span");
      name.textContent = recommended ? packageLabel(recommended) : packageId;
      const id = document.createElement("span");
      id.textContent = packageId;
      line.append(name, id);
      recommendations.append(line);
    }
  }
  recommendationSection.append(recommendationTitle, recommendations);

  const readmeSection = document.createElement("section");
  readmeSection.className = "detail-readme";
  const readme = state.readmes.get(pkg.id);
  if (state.readmeLoading.has(pkg.id)) {
    const loading = document.createElement("div");
    loading.className = "readme-status";
    const spinner = document.createElement("span");
    spinner.className = "loader";
    const label = document.createElement("span");
    label.textContent = tr("loadingReadme");
    loading.append(spinner, label);
    readmeSection.append(loading);
  } else if (readme?.error) {
    const failure = document.createElement("div");
    failure.className = "readme-status error";
    const message = document.createElement("span");
    message.textContent = readme.error;
    const retry = document.createElement("button");
    retry.className = "secondary-button";
    retry.type = "button";
    retry.textContent = tr("retry");
    retry.addEventListener("click", () => loadPackageReadme(pkg.id, true));
    failure.append(message, retry);
    readmeSection.append(failure);
  } else if (readme?.html) {
    readmeSection.append(sanitizeReadmeHtml(readme.html, pkg.id));
  } else {
    const pending = document.createElement("div");
    pending.className = "readme-status";
    pending.textContent = tr("loadingReadme");
    readmeSection.append(pending);
  }

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

  panel.append(topline, heading, actions, readmeSection, facts, dependencySection, recommendationSection);
}

function notifyAdopted(items) {
  if (!items?.length) return;
  const message = tr("adoptedPackages", { count: items.length });
  toast(message);
  setStatus(message, "ready");
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
    state.unrecognized = result.unrecognized || [];
    state.hasAnyMods = Boolean(result.has_any_mods);
    state.batch.clear();
    if (!state.packages.some((pkg) => pkg.id === state.selectedId)) state.selectedId = null;
    setRegistryState("ready", tr("connected"));
    setStatus(tr("ready", { count: state.packages.length }), "ready", result.source || "");
    renderCatalog();
    renderInstalled();
    notifyAdopted(result.adopted);
  } catch (error) {
    state.catalogLoading = false;
    renderCatalog();
    setRegistryState("error", tr("connectionFailed"));
    resultError({ message: String(error) }, "catalogError");
  } finally {
    button.disabled = false;
  }
}

function createPlanBody(plans, recommendations = []) {
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
  if (recommendations.length) {
    const group = document.createElement("section");
    group.className = "plan-group recommendation-group";
    const heading = document.createElement("strong");
    heading.textContent = tr("recommendedMods");
    group.append(heading);
    for (const plan of recommendations) {
      const label = document.createElement("label");
      label.className = "recommendation-line";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = false;
      checkbox.dataset.packageId = plan.id;
      const copy = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = localized(plan.display_name, plan.name || plan.id);
      const id = document.createElement("span");
      id.textContent = plan.id;
      copy.append(name, id);
      const version = document.createElement("span");
      const root = (plan.packages || []).find((item) => item.id === plan.id);
      version.textContent = root?.version || "-";
      label.append(checkbox, copy, version);
      group.append(label);
    }
    body.append(group);
  }
  return body;
}

const README_TAGS = new Set([
  "A", "ARTICLE", "BLOCKQUOTE", "BR", "CODE", "DEL", "DETAILS", "DIV", "EM",
  "H1", "H2", "H3", "H4", "H5", "H6", "HR", "IMG", "KBD", "LI", "OL", "P",
  "PRE", "S", "SECTION", "SPAN", "STRONG", "SUB", "SUMMARY", "SUP", "TABLE",
  "TBODY", "TD", "TH", "THEAD", "TR", "UL",
]);
const README_DROP_TAGS = new Set([
  "BASE", "BUTTON", "EMBED", "FORM", "IFRAME", "INPUT", "LINK", "META", "OBJECT",
  "SCRIPT", "STYLE", "SVG", "TEMPLATE", "TEXTAREA",
]);

function readmeHttpsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url : null;
  } catch (_error) {
    return null;
  }
}

function readmeImageUrl(value) {
  const url = readmeHttpsUrl(value);
  if (!url) return null;
  const host = url.hostname.toLowerCase();
  return host === "github.com" || host.endsWith(".githubusercontent.com") ? url : null;
}

function sanitizeReadmeHtml(html, packageId) {
  const parsed = new DOMParser().parseFromString(String(html || ""), "text/html");
  const output = document.createElement("article");
  output.className = "readme-content";

  function clean(node) {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return document.createDocumentFragment();
    if (README_DROP_TAGS.has(node.tagName)) return document.createDocumentFragment();

    const children = document.createDocumentFragment();
    for (const child of [...node.childNodes]) children.append(clean(child));
    if (!README_TAGS.has(node.tagName)) return children;

    const element = document.createElement(node.tagName.toLowerCase());
    if (node.hasAttribute("dir") && ["auto", "ltr", "rtl"].includes(node.getAttribute("dir"))) {
      element.setAttribute("dir", node.getAttribute("dir"));
    }
    if (node.hasAttribute("id") && /^user-content-[A-Za-z0-9_.:-]+$/.test(node.id)) {
      element.id = node.id;
    }
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (href.startsWith("#") || readmeHttpsUrl(href)) element.setAttribute("href", href);
      if (node.hasAttribute("title")) element.title = node.getAttribute("title").slice(0, 512);
    }
    if (node.tagName === "IMG") {
      const source = readmeImageUrl(node.getAttribute("src") || "");
      if (!source) return children;
      element.src = source.href;
      element.alt = (node.getAttribute("alt") || "").slice(0, 1024);
      element.loading = "lazy";
      element.referrerPolicy = "no-referrer";
    }
    if (["TD", "TH"].includes(node.tagName)) {
      for (const attribute of ["colspan", "rowspan"]) {
        const value = Number(node.getAttribute(attribute));
        if (Number.isInteger(value) && value > 0 && value <= 100) element.setAttribute(attribute, String(value));
      }
    }
    if (node.tagName === "DETAILS" && node.hasAttribute("open")) element.open = true;
    element.append(children);
    return element;
  }

  for (const child of [...parsed.body.childNodes]) output.append(clean(child));
  output.addEventListener("click", (event) => {
    const anchor = event.target.closest?.("a[href]");
    if (!anchor || !output.contains(anchor)) return;
    event.preventDefault();
    const href = anchor.getAttribute("href");
    if (href.startsWith("#")) {
      const targetId = href.slice(1);
      if (targetId) output.querySelector(`#${CSS.escape(targetId)}`)?.scrollIntoView({ block: "start" });
      return;
    }
    void callApi("open_readme_link", packageId, href).then((result) => {
      if (!result.ok) resultError(result);
    });
  });
  return output;
}

async function loadPackageReadme(packageId, refresh = false) {
  if (state.readmeLoading.has(packageId)) return;
  if (!refresh && state.readmes.get(packageId)?.html) return;
  state.readmeLoading.add(packageId);
  if (state.selectedId === packageId) renderDetail();
  try {
    const result = await callApi("get_package_readme", packageId, refresh);
    if (!result.ok) {
      state.readmes.set(packageId, {
        error: result.message || tr("readmeFailed"),
      });
      return;
    }
    state.readmes.set(packageId, {
      html: result.html,
      pageUrl: result.page_url,
    });
  } catch (error) {
    state.readmes.set(packageId, { error: String(error) });
  } finally {
    state.readmeLoading.delete(packageId);
    if (state.selectedId === packageId) renderDetail();
  }
}

async function ensureMelonLoader(installedHint = null) {
  let installed = installedHint;
  if (installed === null) {
    const status = await callApi("get_melonloader_status", false, false);
    if (!status.ok) {
      if (status.code === "game_path_required") {
        showMessage(tr("gamePathRequired"), tr("operationFailed"), () => showPage("settings"));
      } else resultError(status);
      return { proceed: false, allowWithout: false };
    }
    installed = Boolean(status.melonloader?.installed);
  }
  if (installed) return { proceed: true, allowWithout: false };

  const installNow = await showModal({
    kicker: "MOD RUNTIME",
    title: tr("melonloaderRequiredTitle"),
    body: tr("melonloaderRequiredMessage"),
    confirmText: tr("installNow"),
    cancelText: tr("continueWithout"),
  });
  if (!installNow) return { proceed: true, allowWithout: true };
  const installedNow = await installMelonLoader();
  return { proceed: installedNow, allowWithout: false };
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
  const planBody = createPlanBody(result.plans, result.recommendations || []);
  const confirmed = await showModal({
    kicker: "INSTALL PLAN",
    title: result.plans.length === 1 ? tr("confirmInstall") : tr("confirmBatchInstall"),
    body: planBody,
    confirmText: tr("confirm"),
  });
  if (!confirmed) return;
  const loaderDecision = await ensureMelonLoader(Boolean(result.melonloader_installed));
  if (!loaderDecision.proceed) return;
  const queued = await callApi(
    "enqueue_install",
    [
      ...result.plans.map((plan) => plan.id),
      ...$$('input[type="checkbox"][data-package-id]:checked', planBody)
        .map((input) => input.dataset.packageId),
    ],
    loaderDecision.allowWithout,
  );
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
    state.unrecognized = result.unrecognized || [];
    state.hasAnyMods = Boolean(result.has_any_mods);
    const installedById = new Map(state.installed.map((item) => [item.id, item]));
    for (const pkg of state.packages) pkg.installed = installedById.get(pkg.id) || null;
    renderInstalled();
    renderCatalog();
    notifyAdopted(result.adopted);
  } catch (error) {
    resultError({ message: String(error) });
  }
}

function renderInstalled() {
  const container = $("#installed-list");
  if (!container) return;
  const items = [
    ...state.installed.map((item) => ({ ...item, unrecognized: false })),
    ...state.unrecognized.map((item) => ({ ...item, unrecognized: true })),
  ];
  $("#installed-count").textContent = tr("detectedMods", { count: items.length });
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    const title = document.createElement("strong");
    title.textContent = tr("noInstalled");
    empty.append(title);
    container.append(empty);
    return;
  }
  for (const item of items) {
    const pkg = item.unrecognized
      ? null
      : state.packages.find((candidate) => candidate.id === item.id);
    const row = document.createElement("article");
    row.className = "data-row";
    const title = document.createElement("div");
    title.className = "row-title";
    const name = document.createElement("strong");
    name.textContent = item.unrecognized
      ? item.name
      : pkg ? packageLabel(pkg) : item.name || item.id;
    const metadata = document.createElement("span");
    if (item.unrecognized) {
      metadata.textContent = item.path;
    } else {
      const source = item.adopted ? tr("adopted") : item.requested ? tr("requested") : tr("dependency");
      metadata.textContent = `${item.id}  |  ${item.version || "-"}  |  ${source}`;
    }
    title.append(name, metadata);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    if (item.unrecognized) {
      const status = document.createElement("span");
      status.className = "state-chip unrecognized";
      status.textContent = tr("unrecognized");
      actions.append(status);
    } else {
      const remove = document.createElement("button");
      remove.className = "danger-button";
      remove.type = "button";
      remove.textContent = tr("remove");
      remove.disabled = !pkg || queueActive();
      remove.addEventListener("click", () => pkg && confirmRemove(pkg));
      actions.append(remove);
    }
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
    const hasUpdates = state.installed.some((item) => {
      if (!item.requested) return false;
      const pkg = state.packages.find((candidate) => candidate.id === item.id);
      return Boolean(pkg?.release && item.version !== pkg.release.version);
    });
    if (!hasUpdates) {
      toast(tr("noUpdates"));
      setStatus(tr("noUpdates"), "ready");
      return;
    }
    const loaderDecision = await ensureMelonLoader(null);
    if (!loaderDecision.proceed) return;
    const result = await callApi("update_all", loaderDecision.allowWithout);
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
    renderMelonLoader();
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
  $("#proxy-enabled").checked = result.settings.proxy_enabled;
  $("#proxy-url").value = result.settings.proxy_url;
  $("#proxy-url").placeholder = result.settings.proxy_placeholder;
  $("#github-proxy-enabled").checked = result.settings.github_proxy_enabled;
  $("#github-proxy-url").value = result.settings.github_proxy_url;
  $("#github-proxy-url").placeholder = result.settings.github_proxy_placeholder;
  syncProxyControls();
  applyTextScale(result.settings.text_scale);
  updatePageHeader();
}

async function saveSettings(values = null) {
  const previousGamePath = state.settings.game_path || "";
  const payload = values || {
    language: state.languageMode,
    game_path: $("#game-path").value.trim(),
    index_url: $("#index-url").value.trim(),
    proxy_enabled: $("#proxy-enabled").checked,
    proxy_url: $("#proxy-url").value.trim(),
    github_proxy_enabled: $("#github-proxy-enabled").checked,
    github_proxy_url: $("#github-proxy-url").value.trim(),
    text_scale: applyTextScale($("#text-scale").value),
  };
  const result = await callApi("save_settings", payload);
  if (!result.ok) {
    resultError(result);
    return false;
  }
  state.settings = result.settings;
  if (previousGamePath !== (result.settings.game_path || "")) state.melonloader = null;
  state.languageMode = result.settings.language;
  applyTextScale(result.settings.text_scale);
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

function renderMelonLoader() {
  const status = $("#melonloader-state");
  const detail = $("#melonloader-detail");
  const action = $("#melonloader-action");
  const release = $("#open-melonloader-release");
  if (!status || !detail || !action || !release) return;

  status.className = "component-state";
  release.disabled = !state.links.melonloader && !state.melonloader?.page_url;
  if (state.melonloaderLoading) {
    status.textContent = tr("checking");
    detail.textContent = tr("melonloaderChecking");
    action.textContent = tr("checking");
    action.disabled = true;
    return;
  }
  if (!state.melonloader) {
    status.textContent = tr("checking");
    detail.textContent = tr("melonloaderChecking");
    action.textContent = tr("checkStatus");
    action.disabled = false;
    return;
  }
  if (state.melonloader.error) {
    status.classList.add("error");
    status.textContent = tr("errorLabel");
    detail.textContent = state.melonloader.errorCode === "game_path_required"
      ? tr("melonloaderPathRequired")
      : state.melonloader.error;
    action.textContent = tr("checkStatus");
    action.disabled = false;
    return;
  }

  const installed = state.melonloader.installed_version || tr("versionUnknown");
  const latest = state.melonloader.latest_version || tr("versionUnknown");
  if (!state.melonloader.installed) {
    status.classList.add("missing");
    status.textContent = tr("missingLabel");
    detail.textContent = tr("melonloaderMissing");
    action.textContent = tr("installMelonLoader");
  } else if (state.melonloader.update_available) {
    status.classList.add("update");
    status.textContent = tr("updateLabel");
    detail.textContent = tr("melonloaderUpdateReady", { installed, latest });
    action.textContent = tr("updateMelonLoader");
  } else {
    status.classList.add("ready");
    status.textContent = tr("installedLabel");
    detail.textContent = state.melonloader.installed_version
      ? tr("melonloaderInstalled", { installed, latest })
      : tr("melonloaderInstalledUnknown", { latest });
    action.textContent = tr("reinstallMelonLoader");
  }
  action.disabled = queueActive();
}

async function refreshMelonLoaderStatus(refresh = false) {
  state.melonloaderLoading = true;
  renderMelonLoader();
  try {
    const result = await callApi("get_melonloader_status", true, refresh);
    if (!result.ok) {
      state.melonloader = {
        error: result.message || tr("melonloaderUnavailable"),
        errorCode: result.code || "melonloader_status_failed",
      };
      return false;
    }
    state.melonloader = result.melonloader;
    return true;
  } catch (error) {
    state.melonloader = { error: String(error), errorCode: "melonloader_status_failed" };
    return false;
  } finally {
    state.melonloaderLoading = false;
    renderMelonLoader();
  }
}

async function installMelonLoader() {
  state.melonloaderLoading = true;
  renderMelonLoader();
  setStatus(tr("melonloaderInstalling"));
  try {
    const result = await callApi("install_melonloader", true);
    if (!result.ok) {
      resultError(result);
      return false;
    }
    state.melonloader = result.melonloader;
    const message = tr("melonloaderInstalledDone", {
      version: result.melonloader.latest_version || tr("versionUnknown"),
      count: result.files_installed,
    });
    toast(message);
    setStatus(message, "ready");
    return true;
  } catch (error) {
    resultError({ message: String(error) });
    return false;
  } finally {
    state.melonloaderLoading = false;
    renderMelonLoader();
  }
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
    if (await saveSettings()) {
      await loadCatalog(false);
      await refreshMelonLoaderStatus(false);
    }
  });
  $("#browse-game-path").addEventListener("click", chooseGamePath);
  $("#proxy-enabled").addEventListener("change", syncProxyControls);
  $("#github-proxy-enabled").addEventListener("change", syncProxyControls);
  $("#melonloader-action").addEventListener("click", async () => {
    const editedPath = $("#game-path").value.trim();
    if (editedPath !== (state.settings.game_path || "") && !(await saveSettings())) return;
    if (!state.melonloader || state.melonloader.error) await refreshMelonLoaderStatus(true);
    else await installMelonLoader();
  });
  $("#open-melonloader-release").addEventListener("click", () => {
    openUrl(state.melonloader?.page_url || state.links.melonloader);
  });
  $("#language-select").addEventListener("change", async (event) => {
    state.languageMode = event.target.value;
    const saved = {
      language: state.languageMode,
      game_path: state.settings.game_path || "",
      index_url: state.settings.index_url || "",
      proxy_enabled: state.settings.proxy_enabled === true,
      proxy_url: state.settings.proxy_url || "",
      github_proxy_enabled: state.settings.github_proxy_enabled === true,
      github_proxy_url: state.settings.github_proxy_url || "",
      text_scale: state.settings.text_scale || 100,
    };
    await saveSettings(saved);
  });
  $("#text-scale").addEventListener("input", (event) => {
    applyTextScale(event.target.value);
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
    $("#proxy-enabled").checked = result.settings.proxy_enabled;
    $("#proxy-url").value = result.settings.proxy_url;
    $("#proxy-url").placeholder = result.settings.proxy_placeholder;
    $("#github-proxy-enabled").checked = result.settings.github_proxy_enabled;
    $("#github-proxy-url").value = result.settings.github_proxy_url;
    $("#github-proxy-url").placeholder = result.settings.github_proxy_placeholder;
    syncProxyControls();
    applyTextScale(result.settings.text_scale);
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
