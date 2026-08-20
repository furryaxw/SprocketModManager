"use strict";

const state = {
    ready: false,
    initializing: false,
    version: "-",
    page: "catalog",
    languageMode: "auto",
    language: "zh",
    packages: [],
    publicPackages: [],
    privatePackages: [],
    developerServers: [],
    installed: [],
    unrecognized: [],
    hasAnyMods: false,
    queue: [],
    selectedId: null,
    batch: new Set(),
    settings: {
        debug: false, debug_active: false, language: "auto", game_path: "", index_url: "", index_placeholder: "",
        proxy_enabled: false, proxy_url: "", github_proxy_enabled: false,
        github_proxy_url: "", github_user_id: "", text_scale: 100,
    },
    links: {repository: "", registry: ""},
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
    catalog: {kicker: "pageRegistry", title: "catalogTitle", subtitle: () => "sprocketmods.furryaxw.top"},
    translations: {
        kicker: "pageLocalization",
        title: "translationsTitle",
        subtitle: () => "XUnity Translation Packages"
    },
    installed: {kicker: "pageInstallation", title: "installedTitle", subtitle: gamePathSummary},
    downloads: {kicker: "pageTransfers", title: "downloadsTitle", subtitle: queueSummary},
    settings: {kicker: "pageConfiguration", title: "settingsTitle", subtitle: () => tr("localSettings")},
    about: {
        kicker: "pageApplication",
        title: "aboutTitle",
        subtitle: () => tr("versionPrefix", {version: state.version})
    },
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
    renderDeveloperServers();
    renderGithubLogin();
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
    $$('[data-i18n]').forEach((element) => {
        element.textContent = tr(element.dataset.i18n);
    });
    $$('[data-i18n-placeholder]').forEach((element) => {
        element.placeholder = tr(element.dataset.i18nPlaceholder);
    });
    $$('[data-i18n-aria]').forEach((element) => {
        element.setAttribute("aria-label", tr(element.dataset.i18nAria));
    });
    $("#language-select").value = state.languageMode;
}

async function callApi(method, ...args) {
    if (!window.pywebview?.api?.[method]) throw new Error(`API unavailable: ${method}`);
    reportClientLog("debug", `API call: ${method}`);
    try {
        const result = await window.pywebview.api[method](...args);
        if (result?.ok === false) {
            reportClientLog("warning", `API returned ${result.code || "operation_failed"}: ${method}`);
        }
        return result;
    } catch (error) {
        reportClientLog("error", `API bridge failed: ${method}: ${String(error)}`);
        throw error;
    }
}

function reportClientLog(level, message) {
    if (!window.pywebview?.api?.client_log) return;
    void window.pywebview.api.client_log(level, String(message)).catch(() => {
    });
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
    reportClientLog("error", `${result?.code || "operation_failed"}: ${message}`);
    toast(message, "error");
    setStatus(message, "error");
    return message;
}

function gamePathSummary() {
    return state.settings.game_path || $("#game-path")?.placeholder || tr("gamePath");
}

function queueSummary() {
    return tr("queueItems", {count: state.queue.length});
}

function updatePageHeader() {
    const meta = PAGE_META[state.page];
    $("#page-kicker").textContent = tr(meta.kicker);
    $("#page-title").textContent = tr(meta.title);
    $("#page-subtitle").textContent = meta.subtitle();
    $("#header-actions").hidden = !["catalog", "translations"].includes(state.page);
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
    if (["catalog", "translations"].includes(page)) {
        const selected = state.packages.find((item) => item.id === state.selectedId);
        if (selected && (selected.category === "translation") !== (page === "translations")) {
            state.selectedId = null;
        }
        renderCatalog();
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
        translation: "categoryTranslation",
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

function packageBrowserView() {
    const translations = state.page === "translations";
    return {
        translations,
        keyword: $(translations ? "#translation-search" : "#catalog-search").value.trim().toLocaleLowerCase(),
        category: translations ? "translation" : $("#category-select").value,
        sort: $(translations ? "#translation-sort" : "#sort-select").value,
        container: $(translations ? "#translation-list" : "#package-list"),
        count: $(translations ? "#translation-count" : "#catalog-count"),
        panel: $(translations ? "#translation-detail" : "#detail-panel"),
    };
}

function filteredPackages(view) {
    const {keyword, category, sort, translations} = view;
    const result = state.packages.filter((pkg) => {
        if ((pkg.category === "translation") !== translations) return false;
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
        const sourceOrder = Number(Boolean(left.private)) - Number(Boolean(right.private));
        if (sourceOrder) return sourceOrder;
        if (left.private && right.private) {
            const serverOrder = String(left.server_name || "").localeCompare(String(right.server_name || ""));
            if (serverOrder) return serverOrder;
        }
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
    if (pkg.cached) return {label: tr("unavailable"), className: ""};
    if (!pkg.release || !(pkg.install_assets || []).length) return {label: tr("unavailable"), className: ""};
    if (!pkg.installed) return {label: tr("available"), className: "available"};
    if (pkg.installed.version !== pkg.release.version) return {label: tr("updateAvailable"), className: "update"};
    return {label: tr("installedState"), className: "installed"};
}

function packageEligible(pkg) {
    return Boolean(pkg.release && (pkg.install_assets || []).length && !pkg.cached && (!pkg.private || pkg.archive) && (!pkg.installed || pkg.installed.version !== pkg.release.version));
}
