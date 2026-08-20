"use strict";

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
    root.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = tr(node.dataset.i18n);
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
        node.placeholder = tr(node.dataset.i18nPlaceholder);
    });
    root.querySelectorAll("[data-i18n-title]").forEach((node) => {
        node.title = tr(node.dataset.i18nTitle);
    });
    root.querySelectorAll("[data-i18n-aria]").forEach((node) => {
        node.setAttribute("aria-label", tr(node.dataset.i18nAria));
    });
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
    buttons.forEach((button) => {
        button.disabled = buttons.length === 1;
    });
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
            const {control, value: raw} = localizedLanguage(row);
            if (!raw) return;
            if (!LANGUAGE_TAG_PATTERN.test(raw)) {
                control.setCustomValidity(tr("invalidLanguageTag"));
                return;
            }
            const tag = normalizeLanguageTag(raw);
            const duplicate = seen.get(tag.toLocaleLowerCase());
            if (duplicate) {
                const message = tr("duplicateLanguage", {tag});
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
    const category = data.get("category");
    if (category === "translation" && !dependencies.some((item) => item.id === "bbepis.xunity-auto-translator-melonmod-il2cpp")) {
        dependencies.push({
            id: "bbepis.xunity-auto-translator-melonmod-il2cpp",
            version: "*",
            when: "*",
        });
    }
    const recommendations = [...new Set(split(data.get("recommendations")))];
    return {
        $schema: "../../schemas/sprocket-mod.schema.json",
        schema_version: 1,
        id: String(data.get("id") || "").trim(),
        name: String(data.get("name") || "").trim(),
        authors: split(data.get("authors")),
        repository: String(data.get("repository") || "").trim(),
        license: data.get("license"),
        display_name: displayNames,
        ...(Object.keys(descriptions).length ? {description: descriptions} : {}),
        release: {
            include_prerelease: false,
            version_pattern: "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
            assets: {include: split(data.get("asset_include")), exclude: ["*debug*", "*symbols*", "*source*"]},
        },
        dependencies,
        ...(recommendations.length ? {recommendations} : {}),
        ...(data.has("featured") ? {featured: true} : {}),
        install: category === "translation"
            ? {mode: "xunity-translation", scan_dlls: false, exclude: [], overrides: []}
            : {scan_dlls: true, exclude: [], overrides: []},
        category,
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
    window.setTimeout(() => {
        button.title = original;
    }, 1200);
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
