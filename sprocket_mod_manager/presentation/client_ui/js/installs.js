"use strict";

/**
 * 解析并展示安装计划；用户在计划里改版本时重新解析那一个包。
 *
 * 版本选择器只给根包（用户点的那些）；依赖的版本由求解器按环境定，不给挑。
 */
async function beginInstall(packageIds, presetVersions = null) {
    if (!packageIds.length) return;
    const versions = {...installVersions(packageIds), ...(presetVersions || {})};
    setStatus(tr("resolving"));
    const result = await callApi("plan_install", packageIds, versions);
    if (!result.ok) {
        if (result.code === "game_path_required") {
            showMessage(tr("gamePathRequired"), tr("operationFailed"), () => showPage("settings"));
        } else resultError(result);
        return;
    }
    const skipped = result.skipped || [];
    // 界面挑的那版就等于装着的那版时，后端把计划「跳过」了；索引里还有别的版本可挑的话，
    // 这里得再要一份计划 —— 版本选择器是强行装新版唯一的路，不能被这一步挡掉。
    if (!result.plans.length && skipped.some((packageId) => planVersionOptions(packageId).length > 1)) {
        const reopened = await callApi("plan_install", skipped, versions, true);
        if (reopened.ok) {
            result.plans = reopened.plans || [];
            result.failed = reopened.failed || [];
        }
    }
    if (!result.plans.length) {
        toast(tr("nothingToInstall"));
        setStatus("", "ready");
        state.batch.clear();
        renderCatalog();
        return;
    }

    const planState = {
        plans: result.plans,
        recommendations: result.recommendations || [],
        failed: result.failed || [],
        versions,
        recommendedSelection: new Set(),
    };
    const planBody = document.createElement("div");
    const renderPlan = () => {
        planBody.replaceChildren(...createPlanBody(planState, selectPlanVersion).children);
    };
    const selectPlanVersion = async (packageId, version) => {
        planState.versions[packageId] = version;
        const replanned = await callApi("plan_install", [packageId], {[packageId]: version});
        const message = replanned.ok
            ? (replanned.failed?.[0]?.message || tr("nothingToInstall"))
            : (replanned.message || tr("operationFailed"));
        if (replanned.ok && replanned.plans.length) {
            planState.plans = planState.plans.map((plan) =>
                plan.id === packageId ? replanned.plans[0] : plan,
            );
            planState.failed = planState.failed.filter((item) => item.id !== packageId);
        } else {
            // 这个版本装不了（例如依赖跟不上）：把它从计划里拿掉，并在「跳过」区写清原因。
            planState.plans = planState.plans.filter((plan) => plan.id !== packageId);
            planState.failed = [
                ...planState.failed.filter((item) => item.id !== packageId),
                {id: packageId, message},
            ];
        }
        renderPlan();
    };
    renderPlan();

    const confirmed = await showModal({
        kicker: tr("installPlan"),
        title: planState.plans.length === 1 ? tr("confirmInstall") : tr("confirmBatchInstall"),
        body: planBody,
        confirmText: tr("confirm"),
    });
    if (!confirmed) return;
    if (!planState.plans.length) {
        toast(tr("nothingToInstall"));
        setStatus("", "ready");
        return;
    }
    const loaderDecision = await ensureMelonLoader(Boolean(result.melonloader_installed));
    if (!loaderDecision.proceed) return;
    const queued = await callApi(
        "enqueue_install",
        [
            ...planState.plans.map((plan) => plan.id),
            ...planState.recommendedSelection,
        ],
        loaderDecision.allowWithout,
        false,
        planState.versions,
    );
    if (!queued.ok) {
        resultError(queued);
        return;
    }
    state.batch.clear();
    renderCatalog();
    const message = queued.failed?.length
        ? `${tr("queued", {count: queued.count})} | ${tr("skippedMods", {count: queued.failed.length})}`
        : tr("queued", {count: queued.count});
    toast(message);
    setStatus("", "ready");
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
        state.localMods = result.local_mods || [];
        state.localSummary = result.local_summary || null;
        state.hasAnyMods = Boolean(result.has_any_mods);
        const installedById = new Map(state.installed.map((item) => [item.id, item]));
        for (const pkg of state.packages) pkg.installed = installedById.get(pkg.id) || null;
        renderInstalled();
        renderCatalog();
        // 认领要访问 GitHub Release，放到渲染之后异步跑，绝不挡住列表。
        void claimExistingMods();
        pendingVerify = true;
        void verifyInstalled();
    } catch (error) {
        resultError({message: String(error)});
    }
}

let verifyInFlight = false;
let pendingVerify = false;

/**
 * 逐文件校验 SHA-256，拿回带 `corrupted` 的安装列表。
 *
 * 只被 `refreshInstalled` 排一次队（`pendingVerify`），而且直接替换 `state.installed` 后重画，
 * 不触发整页刷新——否则会自己把自己再排一次队，页面永远在转。
 */
async function verifyInstalled() {
    if (!pendingVerify || verifyInFlight || !state.ready) return;
    if (!(state.installed || []).length) return;
    pendingVerify = false;
    verifyInFlight = true;
    try {
        const result = await callApi("verify_installed");
        if (!result.ok || !Array.isArray(result.installed)) return;
        const signature = (items) => JSON.stringify(items.map((item) => [item.id, Boolean(item.corrupted)]));
        const changed = signature(result.installed) !== signature(state.installed || []);
        state.installed = result.installed;
        const installedById = new Map(state.installed.map((item) => [item.id, item]));
        for (const pkg of state.packages) pkg.installed = installedById.get(pkg.id) || null;
        if (changed) renderInstalled();
    } catch (_error) {
        // 校验失败不影响列表（下次打开页面还会再试）。
    } finally {
        verifyInFlight = false;
    }
}

let claimInFlight = false;

async function claimExistingMods() {
    if (claimInFlight || !state.ready) return;
    claimInFlight = true;
    try {
        const result = await callApi("adopt_existing");
        if (!result.ok || !result.changed) return;
        await refreshInstalled();
    } catch (_error) {
        // 认领失败不影响列表：下次进入页面还会再试。
    } finally {
        claimInFlight = false;
    }
}

/** 找到与列表条目对应的本地 DLL 记录（静态元数据 + 禁用状态 + 真实路径）。 */
function localModFor(item) {
    const mods = state.localMods || [];
    if (item.unrecognized) {
        return mods.find((mod) => mod.path === item.path) || null;
    }
    return mods.find((mod) => mod.installed_package_id && mod.installed_package_id === item.id) || null;
}

async function toggleLocalMod(path, enabled) {
    const result = await callApi("toggle_mod", path, Boolean(enabled));
    if (!result.ok) {
        resultError(result);
        return;
    }
    toast(tr(enabled ? "modEnabledRestart" : "modDisabledRestart", {name: result.toggled}));
    setStatus("", "ready");
    await refreshInstalled();
}

/**
 * 完整性状态芯片：只有「不匹配任何发布版本」才出芯片。
 * 被抑制的文件不出芯片 —— 它靠按钮上的「取消抑制」区分于正常行。
 */
function appendIntegrityChip(actions, record) {
    if (!record || !record.corrupted) return;
    const corrupted = document.createElement("span");
    corrupted.className = "state-chip corrupted";
    corrupted.textContent = tr("corrupted");
    actions.append(corrupted);
}

/**
 * 抑制/取消抑制按钮：判断本身永远是实时算的（磁盘 hash vs 发布版本 hash），
 * 这里改的只是"要不要对我报红"，名单存游戏目录的 `SprocketModManager/suppression.json`，不进安装记录。
 */function appendIntegrityButton(actions, record, path) {
    if (!record || (!record.corrupted && !record.suppressed)) return;
    const suppress = document.createElement("button");
    suppress.className = "secondary-button";
    suppress.type = "button";
    suppress.textContent = tr(record.corrupted ? "suppressCorruption" : "unsuppressCorruption");
    suppress.disabled = queueActive();
    suppress.addEventListener("click", () => setIntegritySuppressed(path, Boolean(record.corrupted)));
    actions.append(suppress);
}

/**
 * 抑制/取消抑制某个文件的「不匹配任何发布版本」提示。
 *
 * 判断本身永远是实时算的（磁盘 hash vs 发布版本 hash），这里改的只是"要不要对我报红"，
 * 名单存游戏目录的 `SprocketModManager/suppression.json`，不进安装记录。
 */
async function setIntegritySuppressed(path, suppressed) {
    const result = await callApi("set_integrity_suppressed", String(path || ""), Boolean(suppressed));
    if (!result.ok) {
        resultError(result);
        return;
    }
    toast(tr(suppressed ? "integritySuppressed" : "integrityUnsuppressed", {name: path}));
    await refreshInstalled();
}

/** 行的选择键：扫描行按磁盘路径，兜底行按包 id。 */
function installedRowKey(item) {
    return String(item.path || item.id || "");
}

/** 该行在磁盘上的路径（扫描行自带；兜底行从本地 DLL 记录里找回）。 */
function installedRowPath(item) {
    return String(item.path || localModFor(item)?.path || "");
}

/** 该行是否已被禁用。 */
function installedRowDisabled(item) {
    return item.fromScan ? Boolean(item.disabled) : Boolean(localModFor(item)?.disabled);
}

/** 只有 `Mods` / `Plugins` 下的 DLL 能就地改名；`UserLibs` 是被别的模组引用的库。 */
function installedRowToggleable(item) {
    return /^(Mods|Plugins)\//i.test(installedRowPath(item).replace(/\\/g, "/"));
}

/** 该行对应的包 id：没有安装记录归属的纯本地模组为空（既不能更新也不能卸载）。 */
function installedRowPackageId(item) {
    if (item.fromScan) return String(item.installed_package_id || "");
    return item.unrecognized ? "" : String(item.id || "");
}

/** 比安装记录更新的 Registry 发布版本，没有就返回 null。**环境拦下来的也算** —— 它只负责行上那枚感叹号。
 *
 * 比的是**安装记录里的版本**（与 Registry 同源的 `x.y.z[-pre]`），不是 DLL 自报版本：
 * 程序集版本可能是 `1.6.2.0` 这种 4 段式，拿它比会把所有库都误判成有新版本。
 */
function newerRelease(item) {
    const id = installedRowPackageId(item);
    if (!id) return null;
    const pkg = (state.packages || []).find((candidate) => candidate.id === id);
    const record = (state.installed || []).find((entry) => entry.id === id);
    const latest = String(pkg?.release?.version || "");
    const installedVersion = String(record?.version || "");
    if (!latest || !installedVersion) return null;
    if (compareVersions(latest, installedVersion) <= 0) return null;
    return packageReleases(pkg).find((release) => release.version === latest)
        || {version: latest, verdict: packageVerdict(pkg)};
}

/**
 * 可安装的更新：比装着的版本新、并且本机环境跑得了的**最高**那一版。
 *
 * 判定为「不兼容」的版本不算更新 —— 装上去也跑不起来，所以它不点亮「更新」按钮，
 * 只在行上留一枚感叹号说明为什么没有更新可装。判定未知（没声明）的照常算更新。
 */
function installableUpdate(item) {
    const id = installedRowPackageId(item);
    if (!id) return null;
    const pkg = (state.packages || []).find((candidate) => candidate.id === id);
    const record = (state.installed || []).find((entry) => entry.id === id);
    const installedVersion = String(record?.version || "");
    if (!pkg || !installedVersion) return null;
    const releases = packageReleases(pkg);
    return (releases.length ? releases : [pkg.release])
        .filter((release) => release?.version && release.verdict !== VERDICT_INCOMPATIBLE)
        .filter((release) => compareVersions(release.version, installedVersion) > 0)
        .reduce(
            (best, release) => (!best || compareVersions(release.version, best.version) > 0 ? release : best),
            null,
        );
}

/** 已装那个版本自己的判定：拿不到（比如纯本地的手工 DLL）就算没这回事。 */
function installedVersionVerdict(record) {
    if (!record?.id) return "";
    return releaseVerdict(record.id, record.version);
}

/** 列表数据源：磁盘扫描优先，拿不到扫描结果时退回「安装记录 + 未识别列表」。 */
function installedItems() {
    // 纯扫描模型：磁盘上有什么就显示什么（含 *.dll.disable），安装记录只用来标注归属。
    const scanned = state.localMods || [];
    if (scanned.length) return scanned.map((mod) => ({...mod, fromScan: true}));
    return [
        ...(state.installed || []).map((item) => ({...item, unrecognized: false, fromScan: false})),
        ...(state.unrecognized || []).map((item) => ({...item, unrecognized: true, fromScan: false})),
    ];
}

/** 多选集合：键是行键，列表刷新后由 `pruneInstalledSelection` 对齐。 */
function installedSelection() {
    if (!state.installedSelection) state.installedSelection = new Set();
    return state.installedSelection;
}

function setRowSelected(key, selected) {
    const selection = installedSelection();
    if (selected) selection.add(key);
    else selection.delete(key);
    renderInstalled();
}

/** 当前筛选下可见的行是否已经被全部选中。 */
function allVisibleSelected() {
    const visible = filteredInstalledItems();
    const selection = installedSelection();
    return visible.length > 0 && visible.every((item) => selection.has(installedRowKey(item)));
}

/** 全选 / 取消选择：只作用于当前筛选下可见的行。 */
function toggleInstalledSelection() {
    const selection = installedSelection();
    if (allVisibleSelected()) {
        selection.clear();
    } else {
        for (const item of filteredInstalledItems()) selection.add(installedRowKey(item));
    }
    renderInstalled();
}

/** 反选：只反转当前筛选下可见的行。 */
function invertInstalledSelection() {
    const selection = installedSelection();
    for (const item of filteredInstalledItems()) {
        const key = installedRowKey(item);
        if (selection.has(key)) selection.delete(key);
        else selection.add(key);
    }
    renderInstalled();
}

/**
 * 筛选口径：一枚药丸 = 一个状态，计数是全集里的条数。
 * 筛选只影响显示与可选中范围，计数不会随当前口径缩水。
 */
const INSTALLED_FILTERS = {
    all: {label: "installedFilterAll", match: () => true},
    enabled: {label: "installedFilterEnabled", match: (item) => !installedRowDisabled(item)},
    disabled: {label: "installedFilterDisabled", match: (item) => installedRowDisabled(item)},
    outdated: {label: "installedFilterOutdated", match: (item) => Boolean(installableUpdate(item))},
};

function installedFilterKey() {
    return INSTALLED_FILTERS[state.installedFilter] ? state.installedFilter : "all";
}

function filteredInstalledItems() {
    const filter = INSTALLED_FILTERS[installedFilterKey()];
    return installedItems().filter((item) => filter.match(item));
}

function setInstalledFilter(key) {
    if (!INSTALLED_FILTERS[key] || key === installedFilterKey()) return;
    state.installedFilter = key;
    renderInstalled();
}

function renderInstalledFilters(items) {
    const active = installedFilterKey();
    for (const [key, filter] of Object.entries(INSTALLED_FILTERS)) {
        const chip = $(`#installed-filter-${key}`);
        if (!chip) continue;
        chip.textContent = `${tr(filter.label)} (${items.filter((item) => filter.match(item)).length})`;
        chip.className = key === active ? "filter-chip active" : "filter-chip";
    }
}

function selectedInstalledItems() {
    const selection = installedSelection();
    return filteredInstalledItems().filter((item) => selection.has(installedRowKey(item)));
}

/** 选择状态跟着列表走：被筛掉或磁盘上已经不在了的行不能继续被选中。 */
function pruneInstalledSelection(items) {
    const keys = new Set(items.map(installedRowKey));
    const selection = installedSelection();
    for (const key of [...selection]) {
        if (!keys.has(key)) selection.delete(key);
    }
}

function selectionCheckbox(item, key) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "package-check";
    checkbox.checked = installedSelection().has(key);
    checkbox.setAttribute("aria-label", `${tr("selectRow")}: ${item.display_name || item.name || key}`);
    checkbox.addEventListener("change", () => setRowSelected(key, checkbox.checked));
    return checkbox;
}

/**
 * 整行可点：点空白处等于勾选这一行，所以进了多选就不必去够那个小方框。
 * 点在按钮、选择框、链接上时不接管——那些控件有自己的动作。
 */
function selectOnRowClick(row, key) {
    row.addEventListener("click", (event) => {
        if (event?.target?.closest?.("button, input, a, label")) return;
        setRowSelected(key, !installedSelection().has(key));
    });
}

/**
 * 底部操作栏只在有选中行时出现；栏内按钮只在「选中项里真的有活可干」时可用：
 * 更新看有没有新版本、禁用/启用看有没有 `Mods`/`Plugins` 下的 DLL、卸载看有没有安装记录归属。
 */
function renderInstalledToolbar(items) {
    const selected = selectedInstalledItems();
    const bar = $("#installed-selection-bar");
    if (bar) bar.hidden = selected.length === 0;
    const label = $("#installed-selection");
    if (label) label.textContent = selected.length ? tr("selectionCount", {count: selected.length}) : "";

    // 全选这一格自己承担两个动作：都选中了就变成「取消选择」。
    const toggle = $("#toggle-selection");
    if (toggle) {
        toggle.textContent = tr(allVisibleSelected() ? "clearSelection" : "selectAll");
        toggle.disabled = items.length === 0;
    }
    const invert = $("#invert-selection");
    if (invert) invert.disabled = items.length === 0;

    const actionable = {
        "#update-selected": selected.filter((item) => installableUpdate(item)).length,
        "#disable-selected": selected.filter((item) => installedRowToggleable(item) && !installedRowDisabled(item)).length,
        "#enable-selected": selected.filter((item) => installedRowToggleable(item) && installedRowDisabled(item)).length,
        "#remove-selected": selected.filter((item) => installedRowPackageId(item)).length,
    };
    const busy = queueActive();
    for (const [selector, count] of Object.entries(actionable)) {
        const button = $(selector);
        if (button) button.disabled = busy || count === 0;
    }
}

function renderInstalled() {
    const container = $("#installed-list");
    if (!container) return;
    const items = installedItems();
    const visible = filteredInstalledItems();
    pruneInstalledSelection(visible);
    $("#installed-count").textContent = countLabel(items.length, state.localSummary);
    container.replaceChildren();
    renderInstalledFilters(items);
    renderInstalledToolbar(visible);
    if (!visible.length) {
        const empty = document.createElement("div");
        empty.className = "empty-list";
        const title = document.createElement("strong");
        title.textContent = items.length ? tr("noFilteredMods") : tr("noInstalled");
        empty.append(title);
        container.append(empty);
        return;
    }
    for (const item of visible) {
        container.append(item.fromScan ? renderScannedModRow(item) : renderLegacyModRow(item));
    }
}

/**
 * 「重装」按钮：文件损坏或被改动时，用注册表里的包覆盖回去。
 *
 * 只有注册表（或私有服务器目录）里能找到这个包时才可用——纯本地的手工 DLL 没有来源，
 * 这时按钮禁用并给出原因，而不是假装能重装。
 */
function reinstallButton(record) {
    const known = state.packages.some((candidate) => candidate.id === record.id);
    const button = document.createElement("button");
    button.className = "primary-button";
    button.type = "button";
    button.textContent = tr("reinstall");
    button.disabled = !known || queueActive();
    if (!known) button.title = tr("reinstallUnavailable");
    button.addEventListener("click", () => {
        if (!known || queueActive()) {
            if (!known) setStatus(tr("reinstallUnavailable"), "error");
            return;
        }
        void beginInstall([record.id]);
    });
    return button;
}

/** 列表标题：数量 + （有的话）依赖缺口数量。依赖来自 DLL 元数据，不是安装记录。 */
function countLabel(total, summary) {
    const text = tr("detectedMods", {count: total});
    const missing = summary && Number(summary.missing_dependencies || 0);
    return missing > 0 ? `${text}  ·  ${tr("missingDepsCount", {count: missing})}` : text;
}

/** 新版本提示：装上之后 Registry 又发了更新。版本号来自已缓存的目录数据。 */
function newVersionChip(version) {
    const chip = document.createElement("span");
    chip.className = "state-chip update";
    chip.textContent = tr("newVersionAvailable", {version});
    return chip;
}

/**
 * 「有更新但不兼容」那句话：按已知的轴拼出来 —— 只知道游戏就只说游戏，两个都知道才用「和」。
 *
 * 拼法（与设计一致）：`有更新（{版本}），但不兼容你的 ` + `Sprocket {}` + `和` + `MelonLoader {}`。
 */
function incompatibleUpdateText(version) {
    const sprocket = state.environment?.sprocket?.version || state.environment?.sprocket?.raw || "";
    const loader = state.environment?.melonloader?.used_version || "";
    const parts = [];
    if (sprocket) parts.push(tr("environmentAxisSprocket", {version: sprocket}));
    if (loader) parts.push(tr("environmentAxisMelonLoader", {version: loader}));
    if (!parts.length) return tr("incompatibleUpdateUnknown", {version});
    return tr("incompatibleUpdateHead", {version}) + parts.join(tr("environmentAxisAnd"));
}

/**
 * 有更新、但那个版本跟本机环境不兼容：行里只放一枚感叹号，原因（哪一轴拦下来的）走 tooltip。
 *
 * 整句「有更新（0.2.3），但不兼容你的 Sprocket …」塞进行里会把版本号和按钮挤走；
 * 判定信息本来就只该按需展开，所以默认收成 18px 的一枚标记。
 */
function incompatibleUpdateChip(version) {
    const reason = incompatibleUpdateText(version);
    const chip = document.createElement("span");
    chip.className = "state-chip unknown update-alert";
    chip.textContent = "!";
    chip.title = reason;
    chip.setAttribute("role", "img");
    chip.setAttribute("aria-label", reason);
    return chip;
}

/**
 * 行上关于「新版本」的标记：能装的更新写成版本号，装不了（被环境拦下来）的只留一枚感叹号。
 *
 * 两枚可能同时出现：最新的那版跑不了、但中间还有一版能跑时，既要说明能更新到哪版，
 * 也要说明再新的那版为什么装不了。
 */
function updateChips(item) {
    const chips = [];
    const update = installableUpdate(item);
    if (update) chips.push(newVersionChip(update.version));
    const latest = newerRelease(item);
    if (latest?.verdict === VERDICT_INCOMPATIBLE) chips.push(incompatibleUpdateChip(latest.version));
    return chips;
}

/** 当前装的这个版本跟环境对不上：只标记，不拦（磁盘上的东西永远照原样显示）。 */
function compatibilityChip(verdict) {
    const chip = document.createElement("span");
    chip.className = `state-chip ${verdictClass(verdict)}`.trim();
    chip.textContent = verdictLabel(verdict);
    return chip;
}

/** 扫描行：一条 = 磁盘上的一个 DLL（或 .dll.disable），身份来自静态元数据。 */
function renderScannedModRow(mod) {
    const row = document.createElement("article");
    const key = installedRowKey(mod);
    const selected = installedSelection().has(key);
    row.className = selected ? "data-row selectable selected" : "data-row selectable";
    const title = document.createElement("div");
    title.className = "row-title";

    const name = document.createElement("strong");
    // 识别到 Registry 条目时用已缓存的多语言名称（i18n），否则回退到 DLL 自己的英文元数据。
    name.textContent = localized(mod.registry_display_name, "")
        || mod.display_name
        || mod.name
        || mod.path;

    const record = (state.installed || []).find((item) => item.id && item.id === mod.installed_package_id);
    const source = record
        ? (record.requested ? tr("requested") : tr("dependency"))
        : tr("localOnly");
    const parts = [mod.registry_id || mod.declared_id || mod.assembly_name || mod.path];
    if (mod.version) parts.push(`v${mod.version}`);
    parts.push(source);
    const requires = (mod.required_dependencies || []).join(", ");
    if (requires) parts.push(`${tr("requiresLabel")}: ${requires}`);
    // 缺失依赖同样来自 DLL 元数据（MelonAdditionalDependencies 对比磁盘上的程序集）。
    const missing = (mod.missing_dependencies || []).join(", ");
    if (missing) parts.push(`${tr("missingLabel")}: ${missing}`);
    const incompatible = (mod.incompatible_assemblies || []).join(", ");
    if (incompatible) parts.push(`${tr("incompatibleLabel")}: ${incompatible}`);
    if (mod.error) parts.push(mod.error);

    const metadata = document.createElement("span");
    metadata.textContent = parts.join("  |  ");
    title.append(name, metadata);

    const actions = document.createElement("div");
    actions.className = "row-actions";
    // 先状态、后形态：避免"形态"落在按钮后面看起来像补充说明。
    // 禁用状态由按钮的反向动作（显示"启用"）表达。
    appendIntegrityChip(actions, record);
    const installedVerdict = installedVersionVerdict(record);
    if (verdictNeedsChip(installedVerdict)) actions.append(compatibilityChip(installedVerdict));
    actions.append(...updateChips(mod));
    if (mod.kind) {
        const kind = document.createElement("span");
        kind.className = "state-chip";
        kind.textContent = mod.kind;
        actions.append(kind);
    }

    // 只有 Mods / Plugins 下的 DLL 能切换（UserLibs 是被引用的库，就地改名会连累依赖它的模组）。
    const toggleable = /^(Mods|Plugins)\//i.test(String(mod.path || "").replace(/\\/g, "/"));
    if (toggleable) {
        const toggle = document.createElement("button");
        toggle.className = mod.disabled ? "secondary-button" : "danger-button";
        toggle.type = "button";
        toggle.textContent = tr(mod.disabled ? "enableMod" : "disableMod");
        toggle.disabled = queueActive();
        toggle.addEventListener("click", () => toggleLocalMod(mod.path, Boolean(mod.disabled)));
        actions.append(toggle);
    }

    if (record) {
        const removablePackage = state.packages.find((candidate) => candidate.id === record.id)
            || {
                id: record.id,
                name: record.name || record.id,
                display_name: {en: record.name || record.id, zh: record.name || record.id},
            };
        if (record.corrupted) {
            actions.append(reinstallButton(record));
        }
        if (mod.path) {
            appendIntegrityButton(actions, record, mod.path);
        }
        const remove = document.createElement("button");
        remove.className = "danger-button";
        remove.type = "button";
        remove.textContent = tr("remove");
        remove.disabled = queueActive();
        remove.addEventListener("click", () => confirmRemove(removablePackage));
        actions.append(remove);
    }

    row.append(selectionCheckbox(mod, key), title, actions);
    selectOnRowClick(row, key);
    return row;
}

/** 兜底渲染：拿不到扫描结果时，退回「安装记录 + 未识别列表」。 */
function renderLegacyModRow(item) {
    const pkg = item.unrecognized
        ? null
        : state.packages.find((candidate) => candidate.id === item.id);
    const local = localModFor(item);
    const row = document.createElement("article");
    const key = installedRowKey(item);
    const selected = installedSelection().has(key);
    row.className = selected ? "data-row selectable selected" : "data-row selectable";
    const title = document.createElement("div");
    title.className = "row-title";
    const name = document.createElement("strong");
    name.textContent = local?.display_name
        || (item.unrecognized ? item.name : pkg ? packageLabel(pkg) : item.name || item.id);
    const metadata = document.createElement("span");
    const requires = (local?.required_dependencies || []).join(", ");
    if (item.unrecognized) {
        const version = local?.version ? `v${local.version}  |  ` : "";
        metadata.textContent = `${version}${item.path}${requires ? `  |  ${tr("requiresLabel")}: ${requires}` : ""}`;
    } else {
        const source = item.requested ? tr("requested") : tr("dependency");
        const version = local?.version || item.version || "-";
        metadata.textContent = `${item.id}  |  ${version}  |  ${source}${requires ? `  |  ${tr("requiresLabel")}: ${requires}` : ""}`;
    }
    title.append(name, metadata);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    if (local?.kind) {
        const kind = document.createElement("span");
        kind.className = "state-chip";
        kind.textContent = local.kind;
        actions.append(kind);
    }
    if (!item.unrecognized) {
        appendIntegrityChip(actions, item);
        const installedVerdict = installedVersionVerdict(item);
        if (verdictNeedsChip(installedVerdict)) actions.append(compatibilityChip(installedVerdict));
    }
    actions.append(...updateChips(item));
    if (local?.path) {
        const toggle = document.createElement("button");
        toggle.className = local.disabled ? "secondary-button" : "danger-button";
        toggle.type = "button";
        toggle.textContent = tr(local.disabled ? "enableMod" : "disableMod");
        toggle.disabled = queueActive();
        toggle.addEventListener("click", () => toggleLocalMod(local.path, local.disabled));
        actions.append(toggle);
    }
    if (item.unrecognized && !local?.disabled) {
        const status = document.createElement("span");
        status.className = "state-chip unrecognized";
        status.textContent = tr("unrecognized");
        actions.append(status);
    } else if (!item.unrecognized) {
        const removablePackage = pkg || (item.id.includes(":") ? {
            id: item.id,
            name: item.name || item.id,
            display_name: {en: item.name || item.id, zh: item.name || item.id},
        } : null);
        const remove = document.createElement("button");
        remove.className = "danger-button";
        remove.type = "button";
        remove.textContent = tr("remove");
        remove.disabled = !removablePackage || queueActive();
        remove.addEventListener("click", () => removablePackage && confirmRemove(removablePackage));
        if (item.corrupted) {
            actions.append(reinstallButton(item));
        }
        appendIntegrityButton(actions, item, local?.path || (item.files || [])[0] || "");
        actions.append(remove);
    }
    row.append(selectionCheckbox(item, key), title, actions);
    selectOnRowClick(row, key);
    return row;
}

async function confirmRemove(pkg) {
    const confirmed = await showModal({
        kicker: tr("removePackage"),
        title: tr("confirmRemove"),
        body: tr("removeMessage", {name: packageLabel(pkg)}),
        confirmText: tr("remove"),
        destructive: true,
    });
    if (!confirmed) return;
    const result = await callApi("remove", pkg.id);
    if (!result.ok) {
        resultError(result);
        return;
    }
    const message = tr("removed", {names: result.removed.join(", ")});
    toast(result.warnings?.length ? `${message} | ${result.warnings.join("; ")}` : message);
    setStatus("", "ready");
    await refreshInstalled();
}

/**
 * 多选批量操作：更新 / 禁用 / 启用 / 卸载。
 *
 * 抑制与取消抑制不进多选：那是「这一个文件的提示要不要报」，逐行操作才有意义。
 * 三个操作都复用单行用的端点，逐个调用并把结果汇总成一条提示，不假装整批是原子的。
 */

/** 选中项里可安装的更新：包 id → 要装的那一版（去重：一个包可能对应多个 DLL）。
 *
 * 被环境拦下来的版本不在里面 —— 所以它既不点亮「更新」，也不会被点名装上去。
 */
function selectedUpdateVersions() {
    const versions = {};
    for (const item of selectedInstalledItems()) {
        const id = installedRowPackageId(item);
        if (!id || id in versions) continue;
        const update = installableUpdate(item);
        if (update) versions[id] = update.version;
    }
    return versions;
}

async function updateSelectedMods() {
    if (queueActive()) return;
    // 把版本一起交上去：列表上写的「新版本 X」和实际会装的必须是同一版。
    const versions = selectedUpdateVersions();
    const ids = Object.keys(versions);
    if (!ids.length) {
        toast(tr("noUpdates"));
        setStatus("", "ready");
        return;
    }
    const loaderDecision = await ensureMelonLoader(null);
    if (!loaderDecision.proceed) return;
    const result = await callApi("enqueue_install", ids, loaderDecision.allowWithout, false, versions);
    if (!result.ok) {
        resultError(result);
        return;
    }
    const message = result.failed?.length
        ? `${tr("updateQueued", {count: result.count})} | ${tr("skippedMods", {count: result.failed.length})}`
        : tr("updateQueued", {count: result.count});
    toast(message);
    setStatus("", "ready");
    await pollQueue(true);
    await showPage("downloads");
}

/** 选中项里需要改名的路径：只作用于 `Mods`/`Plugins`，已处于目标状态的不重复调用。 */
function selectedTogglePaths(enabled) {
    const rows = selectedInstalledItems().filter(
        (item) => installedRowToggleable(item) && installedRowDisabled(item) === Boolean(enabled),
    );
    return [...new Set(rows.map(installedRowPath).filter(Boolean))];
}

async function toggleSelectedMods(enabled) {
    if (queueActive()) return;
    const paths = selectedTogglePaths(enabled);
    if (!paths.length) {
        toast(tr("selectionNotApplicable"));
        return;
    }
    const failed = [];
    for (const path of paths) {
        const result = await callApi("toggle_mod", path, Boolean(enabled));
        if (!result.ok) failed.push(path);
    }
    const done = paths.length - failed.length;
    const message = tr(enabled ? "batchEnabled" : "batchDisabled", {count: done});
    const summary = failed.length ? `${message} | ${tr("batchPartial", {done, failed: failed.length})}` : message;
    toast(summary, failed.length ? "error" : "normal");
    setStatus(summary, failed.length ? "error" : "ready");
    await refreshInstalled();
}

async function removeSelectedMods() {
    if (queueActive()) return;
    const ids = [...new Set(
        selectedInstalledItems().map(installedRowPackageId).filter(Boolean),
    )];
    if (!ids.length) {
        toast(tr("selectionNotApplicable"));
        return;
    }
    const confirmed = await showModal({
        kicker: tr("removePackage"),
        title: tr("confirmBatchRemove"),
        body: tr("removeSelectedMessage", {count: ids.length}),
        confirmText: tr("remove"),
        destructive: true,
    });
    if (!confirmed) return;
    const removed = [];
    const warnings = [];
    const failed = [];
    for (const id of ids) {
        const result = await callApi("remove", id);
        if (!result.ok) {
            failed.push(id);
            continue;
        }
        removed.push(...(result.removed || []));
        warnings.push(...(result.warnings || []));
    }
    const message = tr("batchRemoved", {count: removed.length});
    const parts = [message];
    if (failed.length) parts.push(tr("batchPartial", {done: removed.length, failed: failed.length}));
    if (warnings.length) parts.push(warnings.join("; "));
    toast(parts.join(" | "), failed.length ? "error" : "normal");
    setStatus(message, failed.length ? "error" : "ready");
    await refreshInstalled();
}

function queueActive() {
    return state.queue.some((entry) => entry.state === "waiting" || entry.state === "installing");
}

function renderQueue() {
    const container = $("#download-list");
    if (!container) return;
    $("#download-count").textContent = tr("queueItems", {count: state.queue.length});
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
        } else if (entry.state === "failed" && entry.error_code === "file_conflict") {
            const force = document.createElement("button");
            force.className = "danger-button";
            force.type = "button";
            force.textContent = tr("forceRetry");
            force.addEventListener("click", async () => {
                const confirmed = await showModal({
                    kicker: tr("fileConflict"),
                    title: tr("forceConflictTitle"),
                    body: tr("forceConflictMessage"),
                    confirmText: tr("forceRetry"),
                    destructive: true,
                });
                if (!confirmed) return;
                const result = await callApi("enqueue_install", [entry.package_id], true, true);
                if (!result.ok) resultError(result);
                await pollQueue(true);
            });
            actions.append(force);
        } else if (entry.state === "failed") {
            const retry = document.createElement("button");
            retry.className = "secondary-button";
            retry.type = "button";
            retry.textContent = tr("retry");
            retry.addEventListener("click", async () => {
                retry.disabled = true;
                const result = await callApi("enqueue_install", [entry.package_id], true);
                if (!result.ok) resultError(result);
                await pollQueue(true);
            });
            actions.append(retry);
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
        // 队列跑没跑完也是状态栏要看的活状态。
        renderStatusbar();
        if (newlyCompleted) await refreshInstalled();
        // 队列里失败的那一条也算「出过事」：状态栏红着，直到下一次操作成功。
        if (newlyFailed) setStatus(newlyFailed.message || tr("operationFailed"), "error");
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
    $("#debug-mode").checked = result.settings.debug;
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
    renderGithubLogin();
    updatePageHeader();
}
