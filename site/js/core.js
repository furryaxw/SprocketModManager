"use strict";

const state = {
    packages: [],
    releases: new Map(),
    category: "all",
    query: "",
    sort: "name",
    language: readInitialLanguage(),
    registryStatus: {key: "loadingRegistry", values: {}},
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
    addLocalizedRow("display_name", {language: state.language});
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
    validateLocalizedLanguages(elements.form);
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
