"use strict";

/** 这批包各自要装哪个版本：界面自动挑的那个，交给后端按版本解析。 */
function installVersions(packageIds) {
    const versions = {};
    for (const id of packageIds) {
        const pkg = (state.packages || []).find((item) => item.id === id);
        const version = pkg ? preferredVersion(pkg) : "";
        if (version) versions[id] = version;
    }
    return versions;
}

/** 被兼容性藏起来的包：默认不显示，开关打开后照常出现（「显示不兼容」是内存态）。 */
function hiddenByCompatibility() {
    return state.packages.filter(
        (pkg) => !pkg.private && !isModloaderPackage(pkg)
            && (pkg.category === "translation") === (state.page === "translations") && packageHidden(pkg),
    );
}

function renderCatalogNotice(hidden) {
    const notice = $("#catalog-notice");
    if (!notice) return;
    const sprocket = state.environment?.sprocket;
    const unusable = ["legacy", "unreadable"].includes(sprocket?.state);
    if (!hidden.length) {
        notice.hidden = true;
        notice.replaceChildren();
        return;
    }
    notice.hidden = false;
    notice.replaceChildren();
    const message = document.createElement("span");
    message.textContent = unusable
        ? tr("catalogUnusableGame", {count: hidden.length, version: sprocket?.raw || "-"})
        : tr("catalogHidden", {count: hidden.length});
    const toggle = document.createElement("button");
    toggle.className = "link-button";
    toggle.type = "button";
    toggle.textContent = state.showIncompatible ? tr("hideIncompatible") : tr("showIncompatible");
    toggle.addEventListener("click", () => {
        state.showIncompatible = !state.showIncompatible;
        renderCatalog();
    });
    notice.append(message, toggle);
}

/**
 * 目录 / 翻译页的多选。
 *
 * 行上右键＝快速勾选（左键留给详情面板），勾选情况显示在列表底部的浮动栏里，
 * 动作是「安装 / 卸载 / 全选 / 反选 / 取消选择」。
 */
function catalogSelection() {
    return state.batch;
}

function catalogSelectionScope(translations) {
    const page = $(translations ? "#page-translations" : "#page-catalog");
    return page?.querySelector?.("[data-selection-scope]") || null;
}

function catalogSelectionButton(scope, action) {
    return scope?.querySelector?.(`[data-selection-action="${action}"]`) || null;
}

/** 当前可见的包（与渲染用同一套筛选，隐藏的不算）。 */
function visiblePackages(view = packageBrowserView()) {
    return filteredPackages(view).filter(
        (pkg) => state.showIncompatible || !packageHidden(pkg) || pkg.private,
    );
}

function togglePackageSelection(packageId) {
    const selection = catalogSelection();
    if (selection.has(packageId)) selection.delete(packageId);
    else selection.add(packageId);
    renderCatalog();
}

/** 刷新底部浮动栏：数量、显隐、以及每个动作当前有没有活可干。 */
function updateCatalogSelection() {
    const view = packageBrowserView();
    const packages = visiblePackages(view);
    const visibleIds = new Set(packages.map((pkg) => pkg.id));
    for (const id of [...catalogSelection()]) {
        // 切页/换筛选后，看不到的选中项不该继续算数。
        if (!visibleIds.has(id)) catalogSelection().delete(id);
    }
    const selected = packages.filter((pkg) => catalogSelection().has(pkg.id));
    const scope = catalogSelectionScope(view.translations);
    if (!scope) return;
    scope.hidden = selected.length === 0;
    const count = scope.querySelector?.("[data-selection-count]");
    if (count) count.textContent = selected.length ? tr("selectionCount", {count: selected.length}) : "";
    const busy = queueActive();
    const states = {
        install: selected.filter((pkg) => packageEligible(pkg)).length,
        remove: selected.filter((pkg) => Boolean(packageInstalled(pkg))).length,
        all: packages.length,
        invert: packages.length,
        clear: selected.length,
    };
    for (const [action, available] of Object.entries(states)) {
        const button = catalogSelectionButton(scope, action);
        if (button) button.disabled = busy || available === 0;
    }
}

/** 浮动栏上的动作；`install` / `remove` 会走各自那条链路，其余只改选择。 */
async function handleCatalogSelection(action) {
    const packages = visiblePackages();
    const selection = catalogSelection();
    switch (action) {
        case "install":
            await beginInstall([...selection]);
            return;
        case "remove":
            await removeSelectedPackages();
            return;
        case "all":
            packages.forEach((pkg) => selection.add(pkg.id));
            break;
        case "invert":
            packages.forEach((pkg) => {
                if (selection.has(pkg.id)) selection.delete(pkg.id);
                else selection.add(pkg.id);
            });
            break;
        case "clear":
            selection.clear();
            break;
        default:
            return;
    }
    renderCatalog();
}

/** 批量卸载：只处理选中的、且确实在安装记录里的那些（逐个调用，不假装整批原子）。 */
async function removeSelectedPackages() {
    const installed = (state.installed || []).filter((item) => catalogSelection().has(item.id));
    if (!installed.length || queueActive()) return;
    const confirmed = await showModal({
        kicker: tr("removePackage"),
        title: tr("confirmBatchRemove"),
        body: tr("removeSelectedMessage", {count: installed.length}),
        confirmText: tr("remove"),
        destructive: true,
    });
    if (!confirmed) return;
    const failed = [];
    for (const item of installed) {
        const result = await callApi("remove", item.id);
        if (!result.ok) failed.push(item.id);
    }
    catalogSelection().clear();
    const message = tr("batchRemoved", {count: installed.length - failed.length});
    const summary = failed.length ? `${message} | ${tr("batchPartial", {done: installed.length - failed.length, failed: failed.length})}` : message;
    toast(summary, failed.length ? "error" : "normal");
    setStatus(summary, failed.length ? "error" : "ready");
    await loadCatalog(false);
}

function renderCatalog() {
    const view = packageBrowserView();
    const {container} = view;
    if (!container) return;
    const hidden = hiddenByCompatibility();
    renderCatalogNotice(hidden);
    const packages = visiblePackages(view);
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
        title.textContent = hidden.length && !state.showIncompatible
            ? tr("catalogAllHidden")
            : tr("noResults");
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
        // 勾选只表示「这一行被选中」：能不能装、能不能卸由浮动栏按各自的账本自己判。
        checkbox.checked = state.batch.has(pkg.id);
        checkbox.setAttribute("aria-label", `${tr("selectRow")}: ${packageLabel(pkg)}`);
        checkbox.addEventListener("click", (event) => event.stopPropagation());
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) catalogSelection().add(pkg.id);
            else catalogSelection().delete(pkg.id);
            updateCatalogSelection();
        });
        // 左键留给详情面板，所以快速勾选用右键。
        row.addEventListener("contextmenu", (event) => {
            event.preventDefault();
            togglePackageSelection(pkg.id);
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
        // 显示「点安装会装哪个」，不是「最新是哪个」；两者不一致时带 `↓`。
        number.textContent = installTargetVersion(pkg);
        // `not_applicable`（翻译包）没有颜色类，别把空字符串塞进 classList。
        const targetClass = verdictClass(targetVerdict(pkg));
        if (targetClass) number.classList.add(targetClass);
        const chip = document.createElement("span");
        const currentState = packageState(pkg);
        chip.className = `state-chip ${currentState.className}`;
        chip.textContent = currentState.label;
        version.append(number, chip);

        row.append(checkbox, copy, version);
        container.append(row);
    }
    updateCatalogSelection();
    renderDetail();
}

function selectPackage(packageId) {
    state.selectedId = packageId;
    renderCatalog();
    const pkg = state.packages.find((item) => item.id === packageId);
    if (!pkg?.private) void loadPackageReadme(packageId);
}

/**
 * 跳到某个包在目录里的位置：切到它所在的页（翻译包在「翻译」页），并把它选中。
 *
 * 会挡住这一行的筛选一律让开（搜索词、分类、以及默认折叠的不兼容包）——「跳转」要真的落在
 * 那一行上，否则只是换了个页面、还是看不见它。
 */
async function focusPackage(packageId) {
    const pkg = (state.packages || []).find((item) => item.id === packageId);
    if (!pkg) return false;
    const page = pkg.category === "translation" ? "translations" : "catalog";
    if (page === "translations") {
        $("#translation-search").value = "";
    } else {
        $("#catalog-search").value = "";
        $("#category-select").value = "all";
    }
    if (packageHidden(pkg)) state.showIncompatible = true;
    state.selectedId = packageId;
    await showPage(page);
    selectPackage(packageId);
    packageBrowserView().container?.querySelector?.(".package-row.selected")
        ?.scrollIntoView({block: "nearest"});
    return true;
}

function appendFact(list, label, value, tone = "") {
    const group = document.createElement("div");
    group.className = "detail-row";
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    if (tone) description.classList.add(tone);
    group.append(term, description);
    list.append(group);
    return group;
}

/** 分组标题：占满整行，依赖 / 推荐 / 兼容性因此读起来是同一块里的三段。 */
function appendGroupLabel(list, label, note = "") {
    const group = document.createElement("div");
    group.className = "detail-group";
    const term = document.createElement("dt");
    term.textContent = label;
    group.append(term);
    if (note) {
        const description = document.createElement("dd");
        description.textContent = note;
        group.append(description);
    }
    list.append(group);
    return group;
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
    const verdict = packageVerdict(pkg);
    // 状态 chip（已安装 / 可更新）留在标题行最右边；兼容判定跟着版本号走。
    topline.append(record, chip);

    const heading = document.createElement("div");
    heading.className = "detail-heading";
    const title = document.createElement("h2");
    title.textContent = packageLabel(pkg);
    const id = document.createElement("p");
    id.className = "detail-id";
    id.textContent = pkg.id;
    const version = document.createElement("b");
    version.className = `detail-version ${verdictClass(targetVerdict(pkg))}`.trim();
    version.textContent = installTargetVersion(pkg);
    const versionGroup = document.createElement("div");
    versionGroup.className = "detail-version-group";
    const verdictText = verdictLabel(verdict);
    if (verdictText) {
        // 翻译包不参与环境判定，「不适用」光秃秃一个 chip 只会让人误会，所以不挂。
        const verdictChip = document.createElement("span");
        verdictChip.className = `state-chip ${verdictClass(verdict)}`.trim();
        verdictChip.textContent = verdictText;
        versionGroup.append(verdictChip);
    }
    versionGroup.append(version);
    const newest = String(pkg.release?.version || "");
    const target = preferredVersion(pkg);
    if (newest && target && newest !== target) {
        // 最新那版当前装不了：划掉它，别让人以为划掉的是「会装的版本」。
        const superseded = document.createElement("s");
        superseded.className = "detail-version-superseded";
        superseded.textContent = versionWithSource(pkg, newest);
        versionGroup.append(superseded);
    }
    const authors = document.createElement("span");
    authors.className = "detail-authors";
    authors.textContent = (pkg.authors || []).join(", ") || "-";
    heading.append(title, versionGroup, id, authors);

    // 事实、依赖/推荐、兼容性共用一个 <dl>：一行一项，标签就是分组标题。
    const block = document.createElement("dl");
    block.className = "detail-block";
    appendFact(block, tr("license"), pkg.license || tr("none"));
    appendFact(block, tr("categoryLabel"), categoryText(pkg.category));
    appendFact(block, tr("assets"), (pkg.install_assets || []).join(", ") || tr("none"));
    appendFact(block, tr("repository"), pkg.repository || tr("none"));
    if (pkg.private) appendFact(block, tr("developerServers"), `${pkg.server_name} · ${pkg.server_url}`);

    appendGroupLabel(block, tr("dependencies").toUpperCase(), pkg.dependencies?.length ? "" : tr("none"));
    for (const item of pkg.dependencies || []) {
        appendFact(block, item.id, item.version);
    }

    appendGroupLabel(block, tr("recommendations").toUpperCase(), pkg.recommendations?.length ? "" : tr("none"));
    for (const packageId of pkg.recommendations || []) {
        const recommended = state.packages.find((candidate) => candidate.id === packageId);
        const label = recommended ? packageLabel(recommended) : packageId;
        // 认不出这个包时标签就等于 id，别再写第二遍。
        appendFact(block, label, label === packageId ? "" : packageId);
    }

    appendCompatibility(block, pkg, verdict);

    // 说明默认收起：正文照常读取，展开才看。
    const readmeDetails = document.createElement("details");
    readmeDetails.className = "detail-readme";
    const summary = document.createElement("summary");
    summary.textContent = tr("readmeTitle");
    const readmeBody = document.createElement("div");
    readmeBody.className = "readme-body";
    readmeDetails.append(summary, readmeBody);
    const cached = state.readmes.get(pkg.id);
    if (pkg.private) {
        const privateNotice = document.createElement("div");
        privateNotice.className = "readme-status";
        privateNotice.textContent = `${tr("privateDistribution")} · ${pkg.server_name}`;
        readmeBody.append(privateNotice);
    } else if (state.readmeLoading.has(pkg.id)) {
        const loading = document.createElement("div");
        loading.className = "readme-status";
        const spinner = document.createElement("span");
        spinner.className = "loader";
        const label = document.createElement("span");
        label.textContent = tr("loadingReadme");
        loading.append(spinner, label);
        readmeBody.append(loading);
    } else if (cached?.error) {
        const failure = document.createElement("div");
        failure.className = "readme-status error";
        const message = document.createElement("span");
        message.textContent = cached.error;
        const retry = document.createElement("button");
        retry.className = "secondary-button";
        retry.type = "button";
        retry.textContent = tr("retry");
        retry.addEventListener("click", () => loadPackageReadme(pkg.id, true));
        failure.append(message, retry);
        readmeBody.append(failure);
    } else if (cached?.html) {
        readmeBody.append(sanitizeReadmeHtml(cached.html, pkg.id));
    } else {
        const pending = document.createElement("div");
        pending.className = "readme-status";
        pending.textContent = tr("loadingReadme");
        readmeBody.append(pending);
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
    remove.disabled = !packageInstalled(pkg) || queueActive();
    remove.addEventListener("click", () => confirmRemove(pkg));
    const install = document.createElement("button");
    install.className = "primary-button";
    install.type = "button";
    install.textContent = packageInstalled(pkg) ? tr("update") : tr("install");
    install.disabled = !packageEligible(pkg);
    install.addEventListener("click", () => beginInstall([pkg.id]));
    actions.append(repo, remove, install);

    panel.append(topline, heading, actions, readmeDetails, block);
}

/**
 * 拉目录并重画。
 *
 * 读数归数据层（`catalog` 那个 key），这条只是**刷新命令**；怎么画在 `business.js` 里。
 * `catalogLoading` 只是这次操作期间的界面状态。
 */
async function loadCatalog(refresh = false) {
    const button = $("#refresh-catalog");
    button.disabled = true;
    const privateRefresh = loadDeveloperServers();
    state.catalogLoading = true;
    renderCatalog();
    setRegistryState("loading", tr("connecting"));
    setStatus(tr("loading"));
    try {
        // 显式刷新让后端现去拉索引；其余场合让数据层按缓存重算。两条都只回 ack。
        const result = refresh
            ? await callApi("load_catalog", true)
            : await callApi("data_request", "catalog");
        if (!result.ok) {
            state.catalogLoading = false;
            renderCatalog();
            setRegistryState("error", tr("connectionFailed"));
            resultError(result, "catalogError");
        }
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

/** 计划里「某个包有哪些版本可挑」：索引里带来的全部可安装版本（新到旧）。 */
function planVersionOptions(packageId) {
    const pkg = (state.packages || []).find((item) => item.id === packageId);
    return packageReleases(pkg).map((release) => ({
        version: release.version,
        verdict: release.verdict || "",
        compatibility: release.compatibility || null,
    }));
}

/** 根包那一行的版本选择器：列出全部版本（三色写在选项文字里），改选即重新解析这个包。 */
function planVersionControl(planId, planState, onVersionChange) {
    const options = planVersionOptions(planId);
    if (!options.length) return null;
    const current = String(planState.versions[planId] || "");
    const pkg = (state.packages || []).find((item) => item.id === planId);

    const control = document.createElement("div");
    control.className = "plan-version-control";
    const select = document.createElement("select");
    select.className = `plan-version ${verdictClass(options.find((item) => item.version === current)?.verdict)}`.trim();
    select.setAttribute("aria-label", tr("planVersion"));
    for (const option of options) {
        const element = document.createElement("option");
        element.value = option.version;
        const label = verdictLabel(option.verdict);
        const text = versionWithSource(pkg, option.version);
        element.textContent = label ? `${text} · ${label}` : text;
        element.selected = option.version === current;
        select.append(element);
    }
    select.addEventListener("change", () => {
        select.disabled = true;
        void onVersionChange(planId, select.value);
    });
    control.append(select);
    return control;
}

/** 兼容性：详情块的最后一段。逐轴结果由后端算好（`axes`），这里只显示声明、本机值、过没过。 */
function appendCompatibility(block, pkg, verdict) {
    appendGroupLabel(block, tr("compatibilityTitle").toUpperCase());
    const release = packageReleases(pkg).find((item) => item.version === pkg.release?.version);
    const addFact = (label, value, tone = "") => appendFact(block, label, value, tone);

    if (pkg.category === "translation") {
        addFact(tr("compatibilityTitle"), tr("compatibilityTranslation"));
    } else {
        for (const axis of release?.axes || []) {
            const label = axisLabel(axis.id);
            const declared = axis.declared || tr("compatibilityNotDeclared");
            const local = axis.local ? tr("compatibilityLocal", {version: axis.local}) : tr("compatibilityLocalUnknown");
            const tone = axis.satisfied === true ? "pass" : axis.satisfied === false ? "fail" : "";
            addFact(label, `${declared} · ${local}`, tone);
        }
    }

    const newest = String(pkg.release?.version || "");
    const target = preferredVersion(pkg);
    if (target && newest && target !== newest) {
        addFact(tr("installTargetLabel"), tr("installTargetSuperseded", {target, newest}));
    }
    const source = releaseCompatibility(pkg, pkg.release?.version);
    if (source?.source === "inherited") {
        addFact(tr("compatibilityVersionLabel"), tr("compatibilityStarNote"));
    }
    // 兼容是「默认状态」，不再专门写一行；只有未知/不兼容才把它摆出来。
    if (verdict === VERDICT_UNKNOWN || verdict === VERDICT_INCOMPATIBLE) {
        addFact(tr("compatibilityVerdictLabel"), verdictLabel(verdict),
            verdict === VERDICT_INCOMPATIBLE ? "fail" : "");
    }
    if (state.environment?.environment?.state === "conflict") {
        addFact(tr("compatibilityEnvironmentLabel"), environmentConflictText(), "fail");
    }
}

function createPlanBody(planState, onVersionChange = async () => {}) {
    const plans = planState.plans || [];
    const recommendations = planState.recommendations || [];
    const failed = planState.failed || [];
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
        // 供给同一项能力的加载器只能有一个：装这个之前先交还那些（各自的树先进备份区）。
        for (const gone of plan.displaces || []) {
            const displaced = document.createElement("div");
            displaced.className = "loader-displace-warning";
            displaced.textContent = tr("loaderDisplaceWarning", {
                name: localized(gone.display_name, gone.name || gone.id),
            });
            group.append(displaced);
        }
        for (const item of plan.packages || []) {
            const line = document.createElement("div");
            line.className = "plan-line";
            const label = document.createElement("span");
            label.textContent = localized(item.display_name, item.name || item.id);
            // 计划里的依赖也会被标色：求解器按环境筛过一遍，界面再把判定摆出来。
            const verdict = releaseVerdict(item.id, item.version);
            if (verdictClass(verdict)) line.classList.add(verdictClass(verdict));
            line.append(label);
            if (item.id === plan.id) {
                // 根包可以改版本；依赖的版本由求解器定，不给挑。
                const control = planVersionControl(plan.id, planState, onVersionChange);
                if (control) {
                    line.append(control);
                    group.append(line);
                    continue;
                }
            }
            const version = document.createElement("span");
            version.textContent = item.version;
            line.append(version);
            group.append(line);
        }
        body.append(group);
    }
    if (failed.length) {
        // 解析不了的模组不进计划，但不能悄悄消失：装剩下的之前先把原因摆出来。
        const group = document.createElement("section");
        group.className = "plan-group skipped-group";
        const heading = document.createElement("strong");
        heading.textContent = tr("skippedMods", {count: failed.length});
        group.append(heading);
        for (const item of failed) {
            const line = document.createElement("div");
            line.className = "skipped-line";
            const label = document.createElement("span");
            label.textContent = item.id;
            const reason = document.createElement("span");
            reason.textContent = item.message;
            line.append(label, reason);
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
            checkbox.className = "package-check";
            // 勾选状态放在 planState 里：改版本会重画这一块，别把用户勾掉的又勾回来。
            checkbox.checked = planState.recommendedSelection.has(plan.id);
            checkbox.dataset.packageId = plan.id;
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) planState.recommendedSelection.add(plan.id);
                else planState.recommendedSelection.delete(plan.id);
            });
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
