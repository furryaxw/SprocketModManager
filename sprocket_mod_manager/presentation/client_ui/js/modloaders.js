"use strict";

// 加载器 = 带 `loader: true` 的普通注册表包：这一层只管显示什么、按钮点了调哪个接口，
// 装/卸/校验都在后端。左下角环境区也在这里：它要显示加载器的名字，而名字只在加载器目录里。

/** 加载器的显示名：注册表里的本地化名字，认不出来就退回包 id。 */
function loaderLabel(loaderId) {
    const known = (state.modloaders || []).find((item) => item.id === loaderId);
    return known ? localized(known.display_name, known.name || loaderId) : loaderId;
}

/** 一个包 id 的显示名：加载器目录里有就用它，否则去模组目录找，都没有就写 id。 */
function packageOrLoaderLabel(packageId) {
    const known = (state.modloaders || []).find((item) => item.id === packageId);
    if (known) return localized(known.display_name, known.name || packageId);
    const pkg = (state.packages || []).find((item) => item.id === packageId);
    return pkg ? packageLabel(pkg) : packageId;
}

/** 环境轴拼出来的一句话：哪些轴有值就说哪些，一个都没有时返回空串。 */
function environmentAxesText() {
    const parts = [];
    const sprocket = state.environment?.sprocket?.version || state.environment?.sprocket?.raw || "";
    if (sprocket) parts.push(tr("environmentAxisSprocket", {version: sprocket}));
    const loaders = state.environment?.loaders || {};
    for (const loaderId of Object.keys(loaders).sort()) {
        const info = loaders[loaderId] || {};
        if (!info.installed) continue;
        const version = info.used_version || info.version || "";
        if (!version) continue;
        parts.push(tr("environmentAxisLoader", {loader: loaderLabel(loaderId), version}));
    }
    return parts.join(tr("environmentAxisAnd"));
}

/** 环境自身矛盾（某个加载器跟不上游戏版本）那一句；不是矛盾就返回空串。 */
function environmentConflictText() {
    const environment = state.environment;
    if (environment?.environment?.state !== "conflict") return "";
    const loaderId = environment.environment.loader || "";
    const version = loaderId ? ((environment.environment.loaders || {})[loaderId] || "") : "";
    return tr("environmentConflict", {
        loader: loaderId ? loaderLabel(loaderId) : tr("versionUnknown"),
        version: version || tr("versionUnknown"),
        sprocket: environment.sprocket?.version || environment.sprocket?.raw || "-",
    });
}

/**
 * 拉加载器目录并重画。
 *
 * 目录是「注册表里有哪些加载器包、装没装、能不能更新、当前环境下能不能跑」的完整答案，
 * 环境轮询给不了这些（它只报版本）。
 */
async function refreshModloaders() {
    state.modloadersLoading = true;
    state.modloadersRequested = true;
    renderModloaders();
    renderEnvironment();
    try {
        const result = await callApi("get_modloaders");
        if (!result.ok) {
            resultError(result);
            return false;
        }
        state.modloaders = result.modloaders || [];
        return true;
    } catch (error) {
        resultError({message: String(error)});
        return false;
    } finally {
        state.modloadersLoading = false;
        renderModloaders();
        renderEnvironment();
    }
}

/** 左下角要显示加载器的名字；名字取不到就退回 id，不因这一次读取而报错。 */
async function ensureModloaderNames() {
    if (state.modloadersRequested || (state.modloaders || []).length || !state.ready) return;
    state.modloadersRequested = true;
    try {
        const result = await callApi("get_modloaders");
        if (!result.ok) return;
        state.modloaders = result.modloaders || [];
        renderEnvironment();
    } catch (_error) {
        // 取不到名字就用 id：侧栏不该因为这一次读取而报错。
    }
}

function renderModloaders() {
    const container = $("#modloader-list");
    if (!container) return;
    const items = state.modloaders || [];
    const count = $("#modloader-count");
    if (count) count.textContent = tr("modloaderCount", {count: items.length});
    container.replaceChildren();
    if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty-list";
        const title = document.createElement("strong");
        title.textContent = state.modloadersLoading ? tr("modloaderChecking") : tr("modloaderNone");
        empty.append(title);
        container.append(empty);
        return;
    }
    for (const item of items) container.append(renderModloaderCard(item));
}

/** 主按钮该做什么：没装就装，装了有新版就更新，否则重装。 */
function modloaderActionKind(item) {
    if (!item.installed) return "install";
    if (item.update_available) return "update";
    return "reinstall";
}

/** 状态芯片：不兼容优先于其它状态，它决定「装了也跑不起来」这件事能不能被看见。 */
function modloaderChip(item) {
    if (item.compatible === VERDICT_INCOMPATIBLE) {
        return {label: tr("incompatibleState"), className: "incompatible"};
    }
    if (!item.installed) return {label: tr("notInstalled"), className: ""};
    if (item.update_available) return {label: tr("updateLabel"), className: "update"};
    return {label: tr("installedLabel"), className: "installed"};
}

function modloaderVersionsText(item) {
    const latest = tr("modloaderLatestVersion", {latest: item.latest_version || tr("versionUnknown")});
    if (!item.installed) return latest;
    return [
        tr("modloaderInstalledVersion", {version: item.installed_version || tr("versionUnknown")}),
        latest,
    ].join(" · ");
}

function modloaderSection(titleKey, lines) {
    const section = document.createElement("section");
    section.className = "dependency-section";
    const heading = document.createElement("strong");
    heading.textContent = tr(titleKey).toUpperCase();
    const list = document.createElement("div");
    list.className = "dependency-list";
    if (!lines.length) {
        const none = document.createElement("span");
        none.className = "detail-id";
        none.textContent = tr("none");
        list.append(none);
    } else {
        list.append(...lines);
    }
    section.append(heading, list);
    return section;
}

function dependencyLine(left, right) {
    const line = document.createElement("div");
    line.className = "dependency-line";
    const name = document.createElement("span");
    name.textContent = left;
    line.append(name);
    if (right) {
        const value = document.createElement("span");
        value.textContent = right;
        line.append(value);
    }
    return line;
}

function renderModloaderCard(item) {
    const card = document.createElement("article");
    card.className = "modloader-card";

    const header = document.createElement("header");
    header.className = "modloader-header";

    const summary = document.createElement("div");
    summary.className = "modloader-summary";
    const title = document.createElement("div");
    title.className = "modloader-title";
    const name = document.createElement("strong");
    name.textContent = localized(item.display_name, item.name || item.id);
    const chipState = modloaderChip(item);
    const chip = document.createElement("span");
    chip.className = `state-chip ${chipState.className}`.trim();
    chip.textContent = chipState.label;
    title.append(name, chip);
    const versions = document.createElement("span");
    versions.className = "modloader-versions";
    versions.textContent = modloaderVersionsText(item);
    summary.append(title, versions);
    const description = localized(item.description, "");
    if (description) {
        const note = document.createElement("p");
        note.className = "modloader-description";
        note.textContent = description;
        summary.append(note);
    }
    if (item.files) {
        const files = document.createElement("span");
        files.className = "modloader-files";
        files.textContent = tr("modloaderFiles", {count: item.files});
        summary.append(files);
    }

    const actions = document.createElement("div");
    actions.className = "modloader-actions";
    const page = document.createElement("button");
    page.className = "secondary-button";
    page.type = "button";
    page.textContent = tr("repositoryAction");
    page.disabled = !item.page_url;
    page.addEventListener("click", () => {
        if (item.page_url) void openUrl(item.page_url);
    });
    actions.append(page);

    const busy = queueActive() || state.modloadersLoading;
    const kind = modloaderActionKind(item);
    const primary = document.createElement("button");
    primary.className = "primary-button";
    primary.type = "button";
    primary.textContent = tr(kind === "update" ? "update" : kind === "reinstall" ? "reinstall" : "install");
    primary.disabled = busy;
    primary.addEventListener("click", () => void installLoader(item));
    actions.append(primary);

    if (item.installed) {
        const remove = document.createElement("button");
        remove.className = "danger-button";
        remove.type = "button";
        remove.textContent = tr("remove");
        remove.disabled = busy;
        remove.addEventListener("click", () => void removeLoader(item));
        actions.append(remove);
    }

    header.append(summary, actions);

    const sections = document.createElement("div");
    sections.className = "detail-sections modloader-sections";
    sections.append(
        modloaderSection("dependencies", (item.dependencies || []).map((dependency) =>
            dependencyLine(
                packageOrLoaderLabel(dependency.id),
                dependency.when && dependency.when !== "*"
                    ? `${dependency.version} · ${dependency.when}`
                    : dependency.version,
            ))),
        modloaderSection("recommendations", (item.recommendations || []).map((packageId) =>
            dependencyLine(packageOrLoaderLabel(packageId), ""))),
    );
    const supply = modloaderSection("modloaderSupply", (item.supply || []).map((entry) =>
        dependencyLine(entry.type, entry.directory)));

    card.append(header, sections, supply);
    return card;
}

/** 主按钮：走模组那条安装路（解析 → 安装确认里挑版本、看依赖 → 入队）。 */
async function installLoader(item) {
    if (queueActive() || state.modloadersLoading) return false;
    await beginInstall([item.id]);
    return true;
}

async function removeLoader(item) {
    if (queueActive() || state.modloadersLoading) return;
    const name = localized(item.display_name, item.name || item.id);
    const confirmed = await showModal({
        kicker: tr("modRuntime"),
        title: tr("confirmRemove"),
        body: tr("modloaderRemoveMessage", {name}),
        confirmText: tr("remove"),
        destructive: true,
    });
    if (!confirmed) return;
    state.modloadersLoading = true;
    renderModloaders();
    renderEnvironment();
    let removed = false;
    try {
        const result = await callApi("remove_modloader", item.id);
        if (!result.ok) {
            resultError(result);
            return;
        }
        toast(tr("modloaderRemoved", {name}));
        setStatus("", "ready");
        removed = true;
    } catch (error) {
        resultError({message: String(error)});
    } finally {
        state.modloadersLoading = false;
        if (removed) {
            await refreshModloaders();
            await refreshEnvironment(true);
        } else {
            renderModloaders();
            renderEnvironment();
        }
    }
}

async function refreshEnvironment(includeLatest = false) {
    try {
        const result = await callApi("get_environment", includeLatest);
        state.environment = result.ok ? result : null;
    } catch (_error) {
        // 环境信息读不到就不显示版本，别让左下角冒红：本机情况在列表里有更准确的呈现。
        state.environment = null;
    }
    if (state.environment) state.environmentRevision = state.environment.revision;
    renderEnvironment();
    void ensureModloaderNames();
}

/** 环境里真正影响判定的那几项：只有它们变了才值得重新拉目录。 */
function environmentKey(environment) {
    if (!environment) return "";
    const loaders = environment.loaders || {};
    const loaderKey = Object.keys(loaders).sort()
        .map((loaderId) => `${loaderId}:${loaders[loaderId]?.used_version || ""}`)
        .join(",");
    return [
        environment.sprocket?.version || environment.sprocket?.state || "",
        loaderKey,
        environment.environment?.state || "",
    ].join("|");
}

/**
 * 左下角重画用的指纹：Sprocket 版本，加上每个加载器「装没装、是哪版」。
 *
 * `revision` 只跟着游戏目录里的文件走，注册表加载完不会动它；而加载器清单恰恰是注册表给的，
 * 所以「要不要重画」不能只看 `revision`，这份指纹也要算进来。
 */
function environmentRenderKey(environment) {
    if (!environment) return "";
    const sprocket = environment.sprocket || {};
    const loaders = environment.loaders || {};
    const loaderKey = Object.keys(loaders).sort()
        .map((loaderId) => {
            const info = loaders[loaderId] || {};
            return `${loaderId}:${info.installed ? "1" : "0"}:${info.version || info.used_version || ""}`;
        })
        .join(",");
    return [
        sprocket.state || "",
        sprocket.version || "",
        loaderKey,
        environment.environment?.state || "",
    ].join("|");
}

/**
 * 每秒问一次环境。
 *
 * `revision` 变了说明游戏目录里动过东西（游戏更新、加载器装/卸、Mods 里增删文件）：
 * 版本或判定口径变了就重拉目录（每个 release 的判定是后端按环境算的），只是文件变了就刷新列表。
 * 加载器清单本身变了也要重画 —— 注册表加载不会动 `revision`，只看它左下角就会一直停在旧读数。
 */
async function pollEnvironment() {
    if (!state.ready) return;
    let result;
    try {
        result = await callApi("get_environment", false);
    } catch (_error) {
        return;
    }
    if (!result.ok) return;
    const previousKey = environmentKey(state.environment);
    const changed = state.environmentRevision !== result.revision;
    state.environmentRevision = result.revision;
    state.environment = result;
    renderStatusbar();
    if (!changed && environmentRenderKey(result) === state.environmentRenderKey) return;
    renderEnvironment();
    if (environmentKey(result) !== previousKey) await loadCatalog(false);
    else await refreshInstalled();
}

/**
 * 左下角：Sprocket 版本、每个已装加载器的一行、以及「为什么对不上」那一行。
 *
 * 状态栏（statusbar）那边只报健康状态，细节都在这里 —— 这区不弹 toast。
 */
function renderEnvironment() {
    const sprocket = $("#environment-sprocket");
    const loaders = $("#environment-loaders");
    const install = $("#environment-install-loader");
    const note = $("#environment-note");
    if (!sprocket || !loaders || !install || !note) return;

    const environment = state.environment;
    const sprocketInfo = environment?.sprocket || {};
    const problem = environmentProblem();

    sprocket.classList.toggle("error",
        sprocketInfo.state === "legacy" || sprocketInfo.state === "unreadable" || Boolean(problem));
    const sprocketText = sprocketInfo.state === "legacy"
        ? (sprocketInfo.raw || "-")
        : sprocketInfo.state === "ok" ? (sprocketInfo.version || "-") : "-";
    sprocket.textContent = `Sprocket ${sprocketText}`;

    loaders.replaceChildren();
    let installedCount = 0;
    for (const loaderId of Object.keys(environment?.loaders || {}).sort()) {
        const info = environment.loaders[loaderId] || {};
        if (!info.installed) continue;
        installedCount += 1;
        const line = document.createElement("div");
        line.className = "environment-line ready";
        line.textContent = `${loaderLabel(loaderId)} ${info.version || tr("versionUnknown")}`;
        loaders.append(line);
    }
    install.hidden = !environment || installedCount > 0 || sprocketInfo.state === "unconfigured";
    install.disabled = queueActive() || state.modloadersLoading;

    note.hidden = !problem;
    note.className = "environment-line environment-note error";
    note.textContent = problem;
    // 记下这次画的是哪一份读数：轮询靠它判断加载器清单有没有变。
    state.environmentRenderKey = environmentRenderKey(environment);
    // 环境是状态栏要看的活状态之一，顺手重算一次。
    renderStatusbar();
}

/**
 * 环境哪里不对：返回一句给 toast 的说明，没问题就返回空串。
 *
 * 两类：游戏版本太老/读不出来、以及环境自身矛盾（加载器跟不上游戏）。
 */
function environmentProblem() {
    const environment = state.environment;
    if (!environment) return "";
    const sprocket = environment.sprocket || {};
    if (sprocket.state === "legacy" || sprocket.state === "unreadable") {
        return tr("environmentUnusable", {raw: sprocket.raw || "-"});
    }
    return environmentConflictText();
}

/**
 * 状态栏右侧那个按钮：结束正在跑的游戏。
 *
 * 结束后端只做路径匹配的结束（见 `utilities/processes.py`），所以这里只要确认一次；
 * 进程真正退出还要一点时间，界面上那行由每秒一次的环境轮询自己更新。
 */
async function killRunningSprocket() {
    const confirmed = await showModal({
        kicker: tr("modRuntime"),
        title: tr("killSprocketTitle"),
        body: tr("killSprocketMessage"),
        confirmText: tr("killSprocket"),
        destructive: true,
    });
    if (!confirmed) return;
    state.gameKillPending = true;
    renderGameState();
    try {
        const result = await callApi("kill_sprocket");
        if (!result.ok) {
            resultError(result);
            return;
        }
        if (!result.killed?.length) toast(tr("sprocketAlreadyStopped"));
    } catch (error) {
        resultError({message: String(error)});
    } finally {
        state.gameKillPending = false;
        await refreshEnvironment(false);
    }
}

async function openUrl(url) {
    const result = await callApi("open_url", url);
    if (!result.ok) resultError(result);
}

/**
 * 管理器自己的版本检查。
 *
 * 打包成单文件时可以直接换掉自己：确认后下载新版、交给换壳子进程重启（后端关掉这个窗口）。
 * 选「稍后」＝这次会话先不管，下次启动照样问（不落任何持久化）。
 */
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
        if (!result.newer) {
            button.textContent = tr("upToDate");
            button.disabled = true;
            return;
        }
        button.disabled = false;
        button.textContent = result.can_self_update ? tr("updateNow") : tr("getUpdate");
        if (startup && $("#modal-layer").hidden) {
            const confirmed = await showModal({
                kicker: tr("updateAvailableKicker"),
                title: tr("updateFound"),
                body: managerUpdateBody(result),
                confirmText: result.can_self_update ? tr("updateNow") : tr("getUpdate"),
                cancelText: tr("later"),
            });
            if (!confirmed) return;
            if (result.can_self_update) await applyManagerUpdate(result);
            else await openUrl(result.page_url);
        }
    } catch (error) {
        $("#latest-version").textContent = tr("updateUnavailable");
        button.textContent = tr("checkUpdate");
        button.disabled = false;
        if (!startup) resultError({message: String(error)});
    }
}

/** 更新弹窗那块内容：版本对比 + 发布说明（源码运行时再说明一句为什么不能自助更新）。 */
function managerUpdateBody(update) {
    const body = document.createElement("div");
    body.className = "update-body";
    const versions = document.createElement("p");
    versions.textContent = tr("updateMessage", {latest: update.latest, current: update.current});
    body.append(versions);
    const notes = String(update.notes || "").trim();
    if (notes) {
        const block = document.createElement("pre");
        block.className = "update-notes";
        block.textContent = notes;
        body.append(block);
    }
    if (!update.can_self_update) {
        const hint = document.createElement("p");
        hint.className = "modal-note";
        hint.textContent = tr("updateSelfUpdateUnavailable");
        body.append(hint);
    }
    return body;
}

/** 立即更新：下载新版 → 换壳重启。成功的表现是这个窗口被后端关掉。 */
async function applyManagerUpdate(update) {
    void showModal({
        kicker: tr("updateAvailableKicker"),
        title: tr("updateDownloading"),
        body: tr("updateDownloadingMessage", {version: update.latest}),
        confirmText: tr("close"),
        cancelText: null,
        closeOnBackdrop: false,
    });
    const modalStatus = $("#modal-status");
    if (modalStatus) {
        modalStatus.hidden = false;
        modalStatus.textContent = tr("updateDownloading");
    }
    const applied = await callApi("apply_manager_update");
    if (!applied.ok) {
        closeModal(false);
        resultError(applied);
        return;
    }
    const message = tr("updateRestarting", {version: applied.version});
    if (modalStatus) modalStatus.textContent = message;
    setStatus(message);
}
