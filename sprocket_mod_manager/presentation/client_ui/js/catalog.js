"use strict";

function renderCatalog() {
    const view = packageBrowserView();
    const {container} = view;
    if (!container) return;
    const packages = filteredPackages(view);
    view.count.textContent = String(packages.length);
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
    let privateServer = null;
    for (const pkg of packages) {
        if (pkg.private && pkg.server_id !== privateServer && !view.translations) {
            const header = document.createElement("div");
            header.className = "private-section-header";
            header.textContent = `${tr("privateLabel")} · ${pkg.server_name}`;
            container.append(header);
            privateServer = pkg.server_id;
        }
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
    const pkg = state.packages.find((item) => item.id === packageId);
    if (!pkg?.private) void loadPackageReadme(packageId);
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
    const view = packageBrowserView();
    const {panel} = view;
    if (!panel) return;
    const pkg = state.packages.find(
        (item) => item.id === state.selectedId
            && (item.category === "translation") === view.translations,
    );
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
    record.textContent = pkg.private ? `${tr("privateLabel")} · ${pkg.server_name}` : (starterRecommended ? `★ ${tr("starterRecommended")}` : tr("packageRecord"));
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
    if (pkg.private) appendFact(facts, tr("developerServers"), `${pkg.server_name} · ${pkg.server_url}`);

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
    if (pkg.private) {
        const privateNotice = document.createElement("div");
        privateNotice.className = "readme-status";
        privateNotice.textContent = `${tr("privateDistribution")} · ${pkg.server_name}`;
        readmeSection.append(privateNotice);
    } else if (state.readmeLoading.has(pkg.id)) {
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
    repo.hidden = Boolean(pkg.private);
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
    const message = tr("adoptedPackages", {count: items.length});
    toast(message);
    setStatus(message, "ready");
}

async function loadCatalog(refresh = false) {
    const button = $("#refresh-catalog");
    button.disabled = true;
    const privateRefresh = loadDeveloperServers();
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
        state.publicPackages = result.packages || [];
        state.packages = [...state.publicPackages, ...state.privatePackages];
        state.catalogLoading = false;
        state.installed = result.installed || [];
        state.unrecognized = result.unrecognized || [];
        state.hasAnyMods = Boolean(result.has_any_mods);
        state.batch.clear();
        if (!state.packages.some((pkg) => pkg.id === state.selectedId)) state.selectedId = null;
        setRegistryState("ready", tr("connected"));
        setStatus(tr("ready", {count: state.packages.length}), "ready", result.source || "");
        renderCatalog();
        renderInstalled();
        notifyAdopted(result.adopted);
    } catch (error) {
        state.catalogLoading = false;
        renderCatalog();
        setRegistryState("error", tr("connectionFailed"));
        resultError({message: String(error)}, "catalogError");
    } finally {
        await privateRefresh;
        button.disabled = false;
    }
}

function createPlanBody(plans, recommendations = []) {
    const body = document.createElement("div");
    body.className = "modal-plan";
    if (plans.some((plan) => plan.replaces_autotranslator)) {
        const warning = document.createElement("div");
        warning.className = "translation-replace-warning";
        warning.textContent = tr("translationReplaceWarning");
        body.append(warning);
    }
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
            if (targetId) output.querySelector(`#${CSS.escape(targetId)}`)?.scrollIntoView({block: "start"});
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
        state.readmes.set(packageId, {error: String(error)});
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
            return {proceed: false, allowWithout: false};
        }
        installed = Boolean(status.melonloader?.installed);
    }
    if (installed) return {proceed: true, allowWithout: false};

    const installNow = await showModal({
        kicker: tr("modRuntime"),
        title: tr("melonloaderRequiredTitle"),
        body: tr("melonloaderRequiredMessage"),
        confirmText: tr("installNow"),
        cancelText: tr("continueWithout"),
    });
    if (!installNow) return {proceed: true, allowWithout: true};
    const installedNow = await installMelonLoader();
    return {proceed: installedNow, allowWithout: false};
}
