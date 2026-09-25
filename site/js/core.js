"use strict";

const state = {
    packages: [],
    releases: new Map(),
    category: "all",
    query: "",
    sort: "name",
    language: readInitialLanguage(),
    registryStatus: {key: "loadingRegistry", values: {}},
    selectedPackageId: null,
};

const elements = {};

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
    });

    bindEvents();
    applyLanguage();
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
    document.querySelectorAll(".close-dialog").forEach((button) => {
        button.addEventListener("click", () => button.closest("dialog").close());
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
    } catch (_) {
    }
    return navigator.language.toLocaleLowerCase().startsWith("zh") ? "zh" : "en";
}

function setLanguage(language) {
    if (language !== "en" && language !== "zh") return;
    state.language = language;
    try {
        localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch (_) {
    }
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
    refreshIcons();
}

function refreshIcons() {
    if (window.lucide) window.lucide.createIcons({attrs: {"stroke-width": 1.8}});
}

function setRegistryStatus(key, values = {}) {
    state.registryStatus = {key, values};
    elements.status.textContent = tr(key, values);
}

function setSystemState(key, isError = false) {
    elements.topStatus.textContent = tr(key);
    elements.topStatus.closest(".system-state").classList.toggle("error", isError);
}
