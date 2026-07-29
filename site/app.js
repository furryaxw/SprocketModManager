const REGISTRY_REPOSITORY = "furryaxw/sprocket-mods";
const LANGUAGE_STORAGE_KEY = "sprocket-registry-language";
const SUBMISSION_LANGUAGES = [
  ["en", "English (en)"],
  ["zh", "中文 (zh)"],
  ["zh-Hans", "简体中文 (zh-Hans)"],
  ["zh-Hant", "繁體中文 (zh-Hant)"],
  ["ja", "日本語 (ja)"],
  ["ko", "한국어 (ko)"],
  ["de", "Deutsch (de)"],
  ["fr", "Français (fr)"],
  ["es", "Español (es)"],
  ["ru", "Русский (ru)"],
  ["pt-BR", "Português (Brasil) (pt-BR)"],
];

const I18N = {
  en: {
    pageTitle: "Sprocket Mod Registry",
    primaryNav: "Primary navigation",
    language: "Language",
    githubRepository: "GitHub repository",
    downloadClient: "Download client",
    submitMod: "Submit mod",
    registryOnline: "Registry online",
    refreshingRegistry: "Refreshing registry",
    registryError: "Registry unavailable",
    catalogFilters: "Catalog filters",
    browseBy: "Browse by",
    category: "Category",
    categoryAll: "All mods",
    categoryGameplay: "Gameplay",
    categoryUtility: "Utility",
    categoryLibrary: "Libraries",
    categoryVisual: "Visual",
    categoryAudio: "Audio",
    categoryOther: "Other",
    registrySummary: "Registry summary",
    packages: "Packages",
    releases: "Releases",
    loadingRegistry: "Loading Registry...",
    registryUpdated: "Updated {time}",
    loadFailed: "Load failed: {message}",
    openSourceDistribution: "OPEN-SOURCE DISTRIBUTION",
    catalogTitle: "Mod catalog",
    catalogIntro: "Versioned packages sourced directly from verified GitHub Releases.",
    metadataValidated: "Metadata validated",
    searchPlaceholder: "Search name, author or tag",
    sortBy: "Sort",
    sortPackages: "Sort packages",
    sortName: "Name",
    sortRelease: "Latest release",
    sortCategory: "Category",
    refreshReleases: "Refresh releases",
    results: "results",
    selectForDetails: "Select a package to inspect its release and dependencies",
    noMatchingMods: "No matching mods",
    tryAnotherFilter: "Try another category or search term.",
    packageDetails: "PACKAGE DETAILS",
    releaseRecord: "Release record",
    close: "Close",
    registryVerified: "Registry verified",
    dependencies: "Dependencies",
    resolvedAtInstall: "Resolved at install time",
    sourceCode: "Source code",
    viewRelease: "View release",
    version: "Version",
    authors: "Authors",
    license: "License",
    assets: "Assets",
    packageId: "Package ID",
    checking: "checking",
    unavailable: "unavailable",
    verified: "verified",
    none: "None",
    dependencyCount: "{count} dependencies",
    registrySubmission: "REGISTRY SUBMISSION",
    submitOpenSourceMod: "Submit an open-source mod",
    submissionSteps: "Submission steps",
    stepBasics: "Basics",
    stepBasicsHint: "Package identity",
    stepListing: "Listing",
    stepListingHint: "Flexible languages",
    stepRelease: "Release",
    stepReleaseHint: "Assets and dependencies",
    stepPreview: "Preview",
    stepPreviewHint: "Review metadata",
    packageIdentity: "Package identity",
    packageIdentityIntro: "Canonical identifiers used by the registry and dependency solver.",
    assemblyName: "Assembly name",
    tags: "Tags",
    localizedListing: "Localized listing",
    localizedListingIntro: "Add at least one display name. Descriptions are optional.",
    displayNames: "Display names",
    descriptions: "Descriptions",
    required: "Required",
    optional: "Optional",
    languageTag: "Language tag",
    chooseLanguage: "Choose language",
    displayName: "Display name",
    description: "Description",
    addDisplayName: "Add display name",
    addDescription: "Add description",
    removeTranslation: "Remove translation",
    invalidLanguageTag: "Use a language tag such as en, zh-Hans, or pt-BR",
    duplicateLanguage: "Language {tag} is already used in this section.",
    releaseRules: "Release rules",
    releaseRulesIntro: "Select installable GitHub Release assets and declare package dependencies.",
    releaseAssets: "Release assets",
    commaSeparatedGlobs: "Comma-separated glob patterns",
    dependencyRules: "Dependency rules",
    dependencyRulesHint: "Package ID, required version, applicable version",
    addDependency: "Add dependency",
    dependencyPackage: "Package",
    requiredVersion: "Required version",
    whenVersion: "When package version",
    noDependenciesAdded: "No dependencies added",
    reviewMetadata: "Review metadata",
    reviewMetadataIntro: "Generate the final registry file before opening GitHub.",
    copyJson: "Copy JSON",
    copied: "Copied",
    previous: "Previous",
    next: "Next",
    regenerate: "Regenerate",
    continueGithub: "Continue to GitHub",
    dependencyPackageId: "Dependency package ID",
    dependencyVersionRange: "Dependency version range",
    currentPackageVersionRange: "Current package version range",
    removeDependency: "Remove dependency",
  },
  zh: {
    pageTitle: "Sprocket 模组目录",
    primaryNav: "主导航",
    language: "语言",
    githubRepository: "GitHub 仓库",
    downloadClient: "下载客户端",
    submitMod: "提交模组",
    registryOnline: "Registry 在线",
    refreshingRegistry: "正在刷新 Registry",
    registryError: "Registry 不可用",
    catalogFilters: "目录筛选",
    browseBy: "浏览方式",
    category: "分类",
    categoryAll: "全部模组",
    categoryGameplay: "玩法",
    categoryUtility: "工具",
    categoryLibrary: "依赖库",
    categoryVisual: "视觉",
    categoryAudio: "音频",
    categoryOther: "其他",
    registrySummary: "Registry 概览",
    packages: "模组",
    releases: "可用版本",
    loadingRegistry: "正在载入 Registry...",
    registryUpdated: "更新于 {time}",
    loadFailed: "载入失败：{message}",
    openSourceDistribution: "开源模组分发",
    catalogTitle: "模组目录",
    catalogIntro: "版本化软件包直接取自经过校验的 GitHub Releases。",
    metadataValidated: "元数据已校验",
    searchPlaceholder: "搜索名称、作者或标签",
    sortBy: "排序",
    sortPackages: "模组排序",
    sortName: "名称",
    sortRelease: "最新版本",
    sortCategory: "分类",
    refreshReleases: "刷新 Releases",
    results: "项结果",
    selectForDetails: "选择模组以查看 Release、资产与依赖",
    noMatchingMods: "没有匹配的模组",
    tryAnotherFilter: "请尝试其他分类或搜索词。",
    packageDetails: "模组详情",
    releaseRecord: "Release 记录",
    close: "关闭",
    registryVerified: "Registry 已校验",
    dependencies: "依赖",
    resolvedAtInstall: "安装时自动解析",
    sourceCode: "查看源码",
    viewRelease: "查看 Release",
    version: "版本",
    authors: "作者",
    license: "许可证",
    assets: "安装资产",
    packageId: "包 ID",
    checking: "检查中",
    unavailable: "暂无版本",
    verified: "已校验",
    none: "无",
    dependencyCount: "{count} 项依赖",
    registrySubmission: "REGISTRY 投稿",
    submitOpenSourceMod: "提交开源模组",
    submissionSteps: "投稿步骤",
    stepBasics: "基础信息",
    stepBasicsHint: "软件包标识",
    stepListing: "目录内容",
    stepListingHint: "自由选择语言",
    stepRelease: "发布规则",
    stepReleaseHint: "资产与依赖",
    stepPreview: "预览",
    stepPreviewHint: "核对元数据",
    packageIdentity: "软件包标识",
    packageIdentityIntro: "Registry 和依赖解析器使用的固定标识。",
    assemblyName: "程序集名",
    tags: "标签",
    localizedListing: "多语言目录内容",
    localizedListingIntro: "至少填写一种语言的显示名称；简介可选。",
    displayNames: "显示名称",
    descriptions: "简介",
    required: "必填",
    optional: "可选",
    languageTag: "语言标签",
    chooseLanguage: "选择语言",
    displayName: "显示名称",
    description: "简介",
    addDisplayName: "添加显示名称",
    addDescription: "添加简介",
    removeTranslation: "移除这项翻译",
    invalidLanguageTag: "请使用 en、zh-Hans 或 pt-BR 这类语言标签",
    duplicateLanguage: "此区域已使用语言 {tag}。",
    releaseRules: "Release 规则",
    releaseRulesIntro: "选择可安装的 GitHub Release 资产，并声明软件包依赖。",
    releaseAssets: "Release 资产",
    commaSeparatedGlobs: "使用英文逗号分隔通配符",
    dependencyRules: "依赖规则",
    dependencyRulesHint: "包 ID、所需版本、适用版本",
    addDependency: "添加依赖",
    dependencyPackage: "依赖包",
    requiredVersion: "所需版本",
    whenVersion: "当前包适用版本",
    noDependenciesAdded: "尚未添加依赖",
    reviewMetadata: "核对元数据",
    reviewMetadataIntro: "前往 GitHub 前，生成并检查最终 Registry 文件。",
    copyJson: "复制 JSON",
    copied: "已复制",
    previous: "上一步",
    next: "下一步",
    regenerate: "重新生成",
    continueGithub: "前往 GitHub",
    dependencyPackageId: "依赖包 ID",
    dependencyVersionRange: "依赖版本范围",
    currentPackageVersionRange: "当前包版本范围",
    removeDependency: "移除依赖",
  },
};

const state = {
  packages: [],
  releases: new Map(),
  category: "all",
  query: "",
  sort: "name",
  language: readInitialLanguage(),
  registryStatus: { key: "loadingRegistry", values: {} },
  currentStep: 0,
  maxStep: 0,
  selectedPackageId: null,
};

const elements = {};
const LANGUAGE_TAG_PATTERN = /^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|[xX](?:-[A-Za-z0-9]{1,8})+)$/;

document.addEventListener("DOMContentLoaded", () => {
  Object.assign(elements, {
    status: document.querySelector("#registry-status"),
    topStatus: document.querySelector("#top-status"),
    packageCount: document.querySelector("#package-count"),
    releaseCount: document.querySelector("#release-count"),
    resultCount: document.querySelector("#result-count"),
    grid: document.querySelector("#mod-grid"),
    empty: document.querySelector("#empty-state"),
    search: document.querySelector("#search"),
    sort: document.querySelector("#sort"),
    categories: document.querySelector("#category-filter"),
    refresh: document.querySelector("#refresh"),
    detail: document.querySelector("#mod-detail"),
    submit: document.querySelector("#submit-dialog"),
    form: document.querySelector("#submit-form"),
    steps: [...document.querySelectorAll("[data-step]")],
    stepButtons: [...document.querySelectorAll("[data-step-target]")],
    previousStep: document.querySelector("#previous-step"),
    nextStep: document.querySelector("#next-step"),
    submitGithub: document.querySelector("#submit-github"),
    dependencyRows: document.querySelector("#dependency-rows"),
    dependencyTemplate: document.querySelector("#dependency-template"),
    dependencyEmpty: document.querySelector("#dependency-empty"),
    displayNameRows: document.querySelector("#display-name-rows"),
    descriptionRows: document.querySelector("#description-rows"),
    displayNameTemplate: document.querySelector("#display-name-template"),
    descriptionTemplate: document.querySelector("#description-template"),
    preview: document.querySelector("#meta-preview"),
    output: document.querySelector("#meta-output"),
  });

  bindEvents();
  addLocalizedRow("display_name", { language: state.language });
  applyLanguage();
  showStep(0);
  refreshIcons();
  loadRegistry(false);
});

function bindEvents() {
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.addEventListener("click", () => setLanguage(button.dataset.language));
  });
  elements.search.addEventListener("input", (event) => {
    state.query = event.target.value.trim().toLocaleLowerCase();
    renderPackages();
  });
  elements.sort.addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderPackages();
  });
  elements.categories.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    elements.categories.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    renderPackages();
  });
  elements.refresh.addEventListener("click", () => loadRegistry(true));
  document.querySelector("#open-submit").addEventListener("click", () => {
    showStep(0);
    elements.submit.showModal();
  });
  document.querySelectorAll(".close-dialog").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  document.querySelector("#add-dependency").addEventListener("click", addDependencyRow);
  document.querySelector("#add-display-name").addEventListener("click", () => addLocalizedRow("display_name"));
  document.querySelector("#add-description").addEventListener("click", () => addLocalizedRow("description"));
  document.querySelector("#preview-meta").addEventListener("click", previewMeta);
  document.querySelector("#copy-meta").addEventListener("click", copyMeta);
  elements.previousStep.addEventListener("click", () => showStep(state.currentStep - 1));
  elements.nextStep.addEventListener("click", () => {
    if (!validateStep(state.currentStep)) return;
    state.maxStep = Math.max(state.maxStep, state.currentStep + 1);
    showStep(state.currentStep + 1);
  });
  elements.stepButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = Number(button.dataset.stepTarget);
      if (target <= state.maxStep) showStep(target);
    });
  });
  elements.form.addEventListener("submit", openPullRequest);
  elements.form.addEventListener("input", (event) => {
    if (event.target.matches(".localized-row [data-field='language']")) {
      validateLocalizedLanguages(event.target.closest(".localized-editor"));
    }
  });
  elements.grid.addEventListener("click", (event) => {
    const card = event.target.closest("[data-package-id]");
    if (card) openDetails(card.dataset.packageId);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  });
}

function tr(key, values = {}) {
  const table = I18N[state.language] || I18N.en;
  let value = table[key] ?? I18N.en[key] ?? key;
  Object.entries(values).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}

function readInitialLanguage() {
  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === "en" || stored === "zh") return stored;
  } catch (_) {}
  return navigator.language.toLocaleLowerCase().startsWith("zh") ? "zh" : "en";
}

function setLanguage(language) {
  if (language !== "en" && language !== "zh") return;
  state.language = language;
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch (_) {}
  applyLanguage();
}

function applyLanguage() {
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  document.title = tr("pageTitle");
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = tr(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = tr(node.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((node) => {
    node.title = tr(node.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    node.setAttribute("aria-label", tr(node.dataset.i18nAria));
  });
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.classList.toggle("active", button.dataset.language === state.language);
    button.setAttribute("aria-pressed", String(button.dataset.language === state.language));
  });
  elements.status.textContent = tr(state.registryStatus.key, state.registryStatus.values);
  renderPackages();
  if (state.selectedPackageId && elements.detail.open) renderDetails(state.selectedPackageId);
  validateLocalizedLanguages(elements.form);
  refreshIcons();
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
}

function setRegistryStatus(key, values = {}) {
  state.registryStatus = { key, values };
  elements.status.textContent = tr(key, values);
}

function setSystemState(key, isError = false) {
  elements.topStatus.textContent = tr(key);
  elements.topStatus.closest(".system-state").classList.toggle("error", isError);
}

async function loadRegistry(forceRefresh) {
  setRegistryStatus("loadingRegistry");
  setSystemState("refreshingRegistry");
  elements.refresh.disabled = true;
  elements.refresh.querySelector("svg")?.classList.add("spin");
  try {
    const response = await fetch("./index.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Registry HTTP ${response.status}`);
    const registry = await response.json();
    state.packages = Array.isArray(registry.packages) ? registry.packages : [];
    state.releases.clear();
    state.packages.forEach((pkg) => {
      const release = Array.isArray(pkg.releases) ? pkg.releases[0] : null;
      state.releases.set(pkg.id, normalizeEmbeddedRelease(release));
    });
    updateCategoryCounts();
    elements.packageCount.textContent = String(state.packages.length);
    elements.releaseCount.textContent = String([...state.releases.values()].filter(Boolean).length);
    setRegistryStatus("registryUpdated", { time: formatTime(registry.generated_at) });
    setSystemState("registryOnline");
    renderPackages();
  } catch (error) {
    setRegistryStatus("loadFailed", { message: error.message });
    setSystemState("registryError", true);
    state.packages = [];
    updateCategoryCounts();
    renderPackages();
  } finally {
    elements.refresh.disabled = false;
    elements.refresh.querySelector("svg")?.classList.remove("spin");
  }
}

function normalizeEmbeddedRelease(release) {
  if (!release || typeof release !== "object") return null;
  const parsedVersion = parseSemver(release.version);
  if (!parsedVersion || !Array.isArray(release.assets) || !release.assets.length) return null;
  return {
    ...release,
    tag_name: release.tag,
    html_url: release.page_url,
    parsedVersion,
    selectedAssets: release.assets,
  };
}

function parseSemver(value) {
  const match = String(value).match(/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/);
  return match ? { major: +match[1], minor: +match[2], patch: +match[3], prerelease: match[4] ? match[4].split(".") : [] } : null;
}

function compareSemver(left, right) {
  for (const field of ["major", "minor", "patch"]) if (left[field] !== right[field]) return left[field] - right[field];
  if (!left.prerelease.length && right.prerelease.length) return 1;
  if (left.prerelease.length && !right.prerelease.length) return -1;
  for (let index = 0; index < Math.max(left.prerelease.length, right.prerelease.length); index += 1) {
    if (left.prerelease[index] === undefined) return -1;
    if (right.prerelease[index] === undefined) return 1;
    const a = left.prerelease[index];
    const b = right.prerelease[index];
    if (a === b) continue;
    const an = /^\d+$/.test(a);
    const bn = /^\d+$/.test(b);
    if (an && bn) return Number(a) - Number(b);
    if (an !== bn) return an ? -1 : 1;
    return a.localeCompare(b);
  }
  return 0;
}

function updateCategoryCounts() {
  const counts = { all: state.packages.length };
  state.packages.forEach((pkg) => { counts[pkg.category] = (counts[pkg.category] || 0) + 1; });
  document.querySelectorAll("[data-category]").forEach((button) => {
    const output = button.querySelector("b");
    if (output) output.textContent = String(counts[button.dataset.category] || 0);
  });
}

function filteredPackages() {
  const packages = state.packages.filter((pkg) => {
    if (state.category !== "all" && pkg.category !== state.category) return false;
    if (!state.query) return true;
    const text = [
      pkg.id, pkg.name, pkg.repository, ...pkg.authors, ...pkg.tags,
      ...Object.values(pkg.display_name || {}), ...Object.values(pkg.description || {}),
    ].join(" ").toLocaleLowerCase();
    return text.includes(state.query);
  });
  return packages.sort((left, right) => {
    if (state.sort === "release") {
      const a = state.releases.get(left.id)?.parsedVersion;
      const b = state.releases.get(right.id)?.parsedVersion;
      if (a && b) return compareSemver(b, a);
      if (a) return -1;
      if (b) return 1;
    }
    if (state.sort === "category") {
      const category = left.category.localeCompare(right.category);
      if (category) return category;
    }
    return localized(left.display_name).localeCompare(localized(right.display_name), state.language);
  });
}

function renderPackages() {
  if (!elements.grid) return;
  const packages = filteredPackages();
  elements.resultCount.textContent = String(packages.length);
  elements.grid.replaceChildren(...packages.map(renderCard));
  elements.empty.hidden = packages.length !== 0;
  refreshIcons();
}

function renderCard(pkg) {
  const release = state.releases.get(pkg.id);
  const article = document.createElement("article");
  article.className = "mod-card";
  article.dataset.packageId = pkg.id;
  article.tabIndex = 0;
  article.setAttribute("role", "button");
  article.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDetails(pkg.id);
    }
  });
  const avatar = `https://github.com/${pkg.repository.split("/")[0]}.png?size=128`;
  const releaseText = release === undefined ? tr("checking") : release ? release.tag_name : tr("unavailable");
  const pending = release ? "" : " pending";
  const dependencies = tr("dependencyCount", { count: pkg.dependencies.length });
  const description = localized(pkg.description);
  article.innerHTML = `
    <img class="mod-avatar" src="${escapeAttribute(avatar)}" alt="${escapeAttribute(pkg.authors.join(", "))}" loading="lazy" />
    <div class="mod-identity">
      <h2>${escapeHtml(localized(pkg.display_name))}</h2>
      <span class="category-badge">${escapeHtml(categoryLabel(pkg.category))}</span>
    </div>
    <p class="mod-description"${description ? "" : " hidden"}>${escapeHtml(description)}</p>
    <div class="mod-metadata">
      <span class="repository"><i data-lucide="github"></i>${escapeHtml(pkg.repository)}</span>
      <span><i data-lucide="scale"></i>${escapeHtml(pkg.license)}</span>
      <span><i data-lucide="split"></i>${escapeHtml(dependencies)}</span>
    </div>
    <div class="release-column">
      <span class="release-badge${pending}">${escapeHtml(releaseText)}</span>
      <span class="verified-label"><i data-lucide="shield-check"></i>${escapeHtml(tr("verified"))}</span>
      <span class="card-arrow"><i data-lucide="chevron-right"></i></span>
    </div>`;
  return article;
}

function openDetails(packageId) {
  state.selectedPackageId = packageId;
  renderDetails(packageId);
  if (!elements.detail.open) elements.detail.showModal();
}

function renderDetails(packageId) {
  const pkg = state.packages.find((item) => item.id === packageId);
  if (!pkg) return;
  const release = state.releases.get(pkg.id);
  const owner = pkg.repository.split("/")[0];
  document.querySelector("#detail-avatar").src = `https://github.com/${owner}.png?size=160`;
  document.querySelector("#detail-avatar").alt = pkg.authors.join(", ");
  document.querySelector("#detail-title").textContent = localized(pkg.display_name);
  document.querySelector("#detail-repository").textContent = pkg.repository;
  const description = localized(pkg.description);
  const descriptionNode = document.querySelector("#detail-description");
  descriptionNode.textContent = description;
  descriptionNode.hidden = !description;
  const details = [
    [tr("version"), release?.tag_name || tr("unavailable")],
    [tr("authors"), pkg.authors.join(", ")],
    [tr("license"), pkg.license],
    [tr("category"), categoryLabel(pkg.category)],
    [tr("assets"), release?.selectedAssets.map((asset) => asset.name).join(", ") || "-"],
    [tr("packageId"), pkg.id],
  ];
  document.querySelector("#detail-grid").innerHTML = details
    .map(([term, value]) => `<div><dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  document.querySelector("#detail-dependencies").innerHTML = pkg.dependencies.length
    ? pkg.dependencies.map((item) => `<span class="dependency-pill">${escapeHtml(item.id)} ${escapeHtml(item.version)}</span>`).join("")
    : `<span class="dependency-pill">${escapeHtml(tr("none"))}</span>`;
  document.querySelector("#detail-repo-link").href = `https://github.com/${pkg.repository}`;
  const releaseLink = document.querySelector("#detail-release-link");
  releaseLink.href = release?.html_url || `https://github.com/${pkg.repository}/releases`;
  releaseLink.toggleAttribute("aria-disabled", !release);
  refreshIcons();
}

function categoryLabel(category) {
  const key = `category${category.charAt(0).toUpperCase()}${category.slice(1)}`;
  return tr(key);
}

function showStep(step) {
  state.currentStep = Math.max(0, Math.min(3, step));
  elements.steps.forEach((section) => {
    const active = Number(section.dataset.step) === state.currentStep;
    section.hidden = !active;
    section.classList.toggle("active", active);
  });
  elements.stepButtons.forEach((button) => {
    const target = Number(button.dataset.stepTarget);
    button.classList.toggle("active", target === state.currentStep);
    button.disabled = target > state.maxStep;
  });
  elements.previousStep.hidden = state.currentStep === 0;
  elements.nextStep.hidden = state.currentStep === 3;
  document.querySelector("#preview-meta").hidden = state.currentStep !== 3;
  elements.submitGithub.hidden = state.currentStep !== 3;
  if (state.currentStep === 3) previewMeta(false);
  refreshIcons();
}

function validateStep(step) {
  const section = elements.steps[step];
  validateLocalizedLanguages(section);
  const invalid = [...section.querySelectorAll("input, textarea, select")].find((field) => !field.checkValidity());
  if (!invalid) return true;
  invalid.reportValidity();
  return false;
}

function validateAll() {
  for (let step = 0; step < 3; step += 1) {
    if (validateStep(step)) continue;
    state.maxStep = Math.max(state.maxStep, step);
    showStep(step);
    const invalid = [...elements.steps[step].querySelectorAll("input, textarea, select")]
      .find((field) => !field.checkValidity());
    invalid?.reportValidity();
    return false;
  }
  return true;
}

function addDependencyRow() {
  const row = elements.dependencyTemplate.content.firstElementChild.cloneNode(true);
  row.querySelector(".remove-dependency").addEventListener("click", () => {
    row.remove();
    updateDependencyEmpty();
  });
  elements.dependencyRows.append(row);
  applyLanguageTo(row);
  updateDependencyEmpty();
  refreshIcons();
}

function applyLanguageTo(root) {
  root.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = tr(node.dataset.i18n); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((node) => { node.placeholder = tr(node.dataset.i18nPlaceholder); });
  root.querySelectorAll("[data-i18n-title]").forEach((node) => { node.title = tr(node.dataset.i18nTitle); });
  root.querySelectorAll("[data-i18n-aria]").forEach((node) => { node.setAttribute("aria-label", tr(node.dataset.i18nAria)); });
}

function updateDependencyEmpty() {
  elements.dependencyEmpty.hidden = elements.dependencyRows.children.length !== 0;
}

function normalizeLanguageTag(value) {
  const parts = String(value || "").trim().split("-");
  if (parts[0].toLocaleLowerCase() === "x") return parts.map((part) => part.toLocaleLowerCase()).join("-");
  return parts.map((part, index) => {
    if (index === 0) return part.toLocaleLowerCase();
    if (/^[A-Za-z]{4}$/.test(part)) return part[0].toLocaleUpperCase() + part.slice(1).toLocaleLowerCase();
    if (/^(?:[A-Za-z]{2}|[0-9]{3})$/.test(part)) return part.toLocaleUpperCase();
    return part.toLocaleLowerCase();
  }).join("-");
}

function localizedEditor(field) {
  const displayName = field === "display_name";
  return {
    rows: displayName ? elements.displayNameRows : elements.descriptionRows,
    template: displayName ? elements.displayNameTemplate : elements.descriptionTemplate,
  };
}

function updateDisplayNameButtons() {
  const buttons = elements.displayNameRows.querySelectorAll(".remove-localized");
  buttons.forEach((button) => { button.disabled = buttons.length === 1; });
}

function localizedLanguage(row) {
  const control = row.querySelector('[data-field="language"]');
  return {
    control,
    value: control.value,
  };
}

function populateLanguageOptions(select) {
  SUBMISSION_LANGUAGES.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  });
}

function addLocalizedRow(field, initial = {}) {
  const editor = localizedEditor(field);
  const row = editor.template.content.firstElementChild.cloneNode(true);
  const languageSelect = row.querySelector('[data-field="language"]');
  populateLanguageOptions(languageSelect);
  const initialLanguage = initial.language ? normalizeLanguageTag(initial.language) : "";
  const hasOption = [...languageSelect.options].some((option) => option.value === initialLanguage);
  if (initialLanguage && !hasOption) {
    const option = document.createElement("option");
    option.value = initialLanguage;
    option.textContent = initialLanguage;
    languageSelect.append(option);
  }
  languageSelect.value = initialLanguage;
  row.querySelector('[data-field="value"]').value = initial.value || "";
  languageSelect.addEventListener("change", () => {
    validateLocalizedLanguages(row.closest(".localized-editor"));
  });
  row.querySelector(".remove-localized").addEventListener("click", () => {
    if (field === "display_name" && editor.rows.children.length === 1) return;
    row.remove();
    updateDisplayNameButtons();
    validateLocalizedLanguages(elements.form);
  });
  editor.rows.append(row);
  applyLanguageTo(row);
  updateDisplayNameButtons();
  refreshIcons();
  if (!initial.language) languageSelect.focus();
}

function validateLocalizedLanguages(root) {
  if (!root) return;
  const editors = root.matches?.(".localized-editor") ? [root] : root.querySelectorAll(".localized-editor");
  editors.forEach((editor) => {
    const seen = new Map();
    const rows = [...editor.querySelectorAll(".localized-row")];
    rows.forEach((row) => {
      localizedLanguage(row).control.setCustomValidity("");
    });
    rows.forEach((row) => {
      const { control, value: raw } = localizedLanguage(row);
      if (!raw) return;
      if (!LANGUAGE_TAG_PATTERN.test(raw)) {
        control.setCustomValidity(tr("invalidLanguageTag"));
        return;
      }
      const tag = normalizeLanguageTag(raw);
      const duplicate = seen.get(tag.toLocaleLowerCase());
      if (duplicate) {
        const message = tr("duplicateLanguage", { tag });
        duplicate.setCustomValidity(message);
        control.setCustomValidity(message);
      } else {
        seen.set(tag.toLocaleLowerCase(), control);
      }
    });
  });
}

function collectLocalized(rows) {
  return Object.fromEntries([...rows.querySelectorAll(".localized-row")]
    .map((row) => [
      normalizeLanguageTag(localizedLanguage(row).value),
      row.querySelector('[data-field="value"]').value.trim(),
    ])
    .filter(([language, value]) => language && value));
}

function buildMeta(validate = true) {
  if (validate && !validateAll()) return null;
  const data = new FormData(elements.form);
  const split = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
  const displayNames = collectLocalized(elements.displayNameRows);
  const descriptions = collectLocalized(elements.descriptionRows);
  const dependencies = [...elements.dependencyRows.querySelectorAll(".dependency-row")].map((row) => ({
    id: row.querySelector('[data-field="id"]').value.trim(),
    version: row.querySelector('[data-field="version"]').value.trim() || "*",
    when: row.querySelector('[data-field="when"]').value.trim() || "*",
  })).filter((item) => item.id);
  return {
    $schema: "../../schemas/sprocket-mod.schema.json",
    schema_version: 1,
    id: String(data.get("id") || "").trim(),
    name: String(data.get("name") || "").trim(),
    authors: split(data.get("authors")),
    repository: String(data.get("repository") || "").trim(),
    license: data.get("license"),
    display_name: displayNames,
    ...(Object.keys(descriptions).length ? { description: descriptions } : {}),
    release: {
      include_prerelease: false,
      version_pattern: "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
      assets: { include: split(data.get("asset_include")), exclude: ["*debug*", "*symbols*", "*source*"] },
    },
    dependencies,
    install: { scan_dlls: true, exclude: [], overrides: [] },
    category: data.get("category"),
    tags: split(data.get("tags")).map((tag) => tag.toLocaleLowerCase()),
  };
}

function previewMeta(validate = true) {
  const meta = buildMeta(validate);
  if (!meta) {
    elements.output.textContent = "";
    return;
  }
  elements.output.textContent = JSON.stringify(meta, null, 2);
}

async function copyMeta() {
  if (!elements.output.textContent) previewMeta();
  if (!elements.output.textContent) return;
  await navigator.clipboard.writeText(elements.output.textContent);
  const button = document.querySelector("#copy-meta");
  const original = button.title;
  button.title = tr("copied");
  window.setTimeout(() => { button.title = original; }, 1200);
}

function openPullRequest(event) {
  event.preventDefault();
  const meta = buildMeta(true);
  if (!meta) return;
  const value = JSON.stringify(meta, null, 2) + "\n";
  const filename = `mods/${meta.id}/sprocket-mod.json`;
  const url = `https://github.com/${REGISTRY_REPOSITORY}/new/main?filename=${encodeURIComponent(filename)}&value=${encodeURIComponent(value)}`;
  window.open(url, "_blank", "noopener,noreferrer");
}

function localized(value) {
  const entries = Object.entries(value || {});
  if (!entries.length) return "";
  const language = state.language.replace("_", "-").toLocaleLowerCase();
  const exact = entries.find(([tag]) => tag.toLocaleLowerCase() === language)?.[1];
  if (exact) return exact;
  const byBase = (base) => entries.find(([tag]) => tag.toLocaleLowerCase().split("-", 1)[0] === base)?.[1] || "";
  return byBase(language.split("-", 1)[0]) || byBase("en") || entries[0][1];
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleString(state.language === "zh" ? "zh-CN" : "en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function escapeAttribute(value) {
  return escapeHtml(value);
}
