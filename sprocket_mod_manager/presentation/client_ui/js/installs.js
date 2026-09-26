"use strict";

/**
 * 解析并展示安装计划；用户在计划里改版本时重新解析那一个包。
 *
 * 版本选择器只给根包（用户点的那些）；依赖的版本由求解器按环境定，不给挑。
 *
 * `includeInstalled` 同时是「重装」的意思：计划照给（不看它有几个版本可挑），入队也不因为
 * 「解析出来的版本就是装着的那版」被跳过。`force` 再多给一层：连被外部改动过的文件也一并覆盖。
 */
async function beginInstall(packageIds, presetVersions = null, includeInstalled = false, force = false) {
    if (!packageIds.length) return;
    const versions = {...installVersions(packageIds), ...(presetVersions || {})};
    setStatus(tr("resolving"));
    // 点名的那一项要「版本相同也给」的计划：版本选择器是它在装好的情况下换版本、重装的唯一入口，
    // 不看它有几个版本可挑。
    const result = await callApi("plan_install", packageIds, versions, includeInstalled);
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
    const queued = await callApi(
        "enqueue_install",
        [
            ...planState.plans.map((plan) => plan.id),
            ...planState.recommendedSelection,
        ],
        force,
        planState.versions,
        includeInstalled,
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

/**
 * 让数据层刷一次「已安装」：命令只回 ack，读数经推送回来。
 *
 * 挂在这条路上的两件后续（认领、完整性校验）只在**用户动作或页面进来**时排一次，
 * 不挂在推送回调上 —— 否则校验自己触发的推送会把自己再排一次，页面永远在转。
 * 读数变了之后怎么画，在 `business.js` 里。
 */
async function refreshInstalled() {
    if (!state.ready) return;
    try {
        const result = await callApi("data_request", "installed");
        if (!result.ok) {
            resultError(result);
            return;
        }
    } catch (error) {
        resultError({message: String(error)});
        return;
    }
    pendingVerify = true;
    void verifyInstalled();
    // 认领要访问 GitHub Release，放到渲染之后异步跑，绝不挡住列表。
    void claimExistingMods();
}

let verifyInFlight = false;
let pendingVerify = false;

/**
 * 逐文件校验 SHA-256。
 *
 * 校验改的是「已安装」这份数据的完整性状态，所以结果不在返回值里传回来 —— 跑完让数据层重算一次，
 * 界面按推送重画。这样就不存在"这条路径的读数"和"那条路径的读数"两个版本。
 */
async function verifyInstalled() {
    if (!pendingVerify || verifyInFlight || !state.ready) return;
    if (!(state.installed || []).length) return;
    pendingVerify = false;
    verifyInFlight = true;
    try {
        const result = await callApi("verify_installed");
        if (!result.ok) {
            // 撞上别的模组操作（"另一个模组操作正在进行"）只是这一轮排不上队：下次刷新再审一次，
            // 不当成校验失败。
            pendingVerify = true;
            return;
        }
        const refreshed = await callApi("data_request", "installed");
        if (!refreshed.ok) pendingVerify = true;
    } catch (_error) {
        pendingVerify = true;
    } finally {
        verifyInFlight = false;
    }
}

let claimInFlight = false;

async function claimExistingMods() {
    if (claimInFlight || !state.ready) return;
    // 认领要按注册表里的包来算：目录还没到（启动时它排在后面）就等下一轮，别去撞
    // 「registry is not loaded」。
    if (!(state.packages || []).length) return;
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
    const row = installedItems().find((item) => installedRowPath(item) === path);
    const dependents = !enabled && row ? enabledDependentsClosure([row]) : [];
    if (dependents.length) {
        // 依赖者还开着的时候后端不准先动库本身：先问一句，确认后从最外层往里关。
        const confirmed = await showModal({
            kicker: tr("disableMod"),
            title: tr("confirmDisableDependents"),
            body: tr("disableDependentsMessage", {
                count: dependents.length,
                names: dependents.map(installedRowLabel).join(", "),
            }),
            confirmText: tr("disableMod"),
            destructive: true,
        });
        if (!confirmed) return;
        for (const dependent of orderedForDisable(dependents)) {
            await callApi("toggle_mod", installedRowPath(dependent), false);
        }
    }
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
 */
function appendIntegrityChip(actions, record) {
    if (!record || !record.corrupted) return;
    const corrupted = document.createElement("span");
    corrupted.className = "state-chip corrupted";
    corrupted.textContent = tr("corrupted");
    actions.append(corrupted);
}

/** 行的选择键：扫描行按磁盘路径，兜底行按包 id。 */
function installedRowKey(item) {
    return String(item.path || item.id || "");
}

/** 该行在磁盘上的路径（扫描行自带；兜底行从本地 DLL 记录里找回）。 */
function installedRowPath(item) {
    return String(item.path || localModFor(item)?.path || "");
}

/** 定位用的路径：再兜一层安装记录里的文件名，免得只有记录、没有本地扫描的行点不动。 */
function installedRowRevealPath(item) {
    return String(installedRowPath(item) || (item.files || [])[0] || "");
}

/** 该行是否已被禁用。 */
function installedRowDisabled(item) {
    return item.fromScan ? Boolean(item.disabled) : Boolean(localModFor(item)?.disabled);
}

/** 能不能就地改名：由依赖链当场解析，不落库。
 *
 * 行是扫描来的（只走加载器供给的模组目录），所以只剩三条：不是加载器自己的文件、
 * 自身不是库、没有别的行依赖它 —— 改掉被依赖的库会连累依赖者。
 */
/** 能不能就地改名：只要磁盘上有这个文件就给开关。
 *
 * 唯一拦人的是依赖链，而且拦的不是"按钮"：还开着的依赖者要先一起处理（见 `toggleLocalMod`）。
 */
function installedRowToggleable(item) {
    const mod = item.fromScan ? item : localModFor(item);
    return Boolean(mod?.path);
}

/** 提示文案里的行名：显示名优先，退回文件名。 */
function installedRowLabel(item) {
    const mod = item.fromScan ? item : localModFor(item);
    return String(mod?.display_name || mod?.name || installedRowPath(item) || "");
}

/** 还开着、并且依赖这个文件的模组行：动它之前要先把这些一起处理掉，否则它们会坏。 */
function enabledDependentsOf(item) {
    const mod = item.fromScan ? item : localModFor(item);
    const name = String(mod?.assembly_name || "").toLowerCase();
    if (!name) return [];
    return installedItems().filter(
        (other) => other !== mod
            && !installedRowDisabled(other)
            && (other.required_dependencies || []).some((dep) => String(dep).toLowerCase() === name),
    );
}

/** 上面那串的传递闭包：隔了两三层的依赖者也要一起禁，否则中间那层会被后端拦下。 */
function enabledDependentsClosure(rows) {
    const found = [];
    const seen = new Set(rows.map(installedRowPath));
    const queue = [...rows];
    while (queue.length) {
        for (const dependent of enabledDependentsOf(queue.shift())) {
            const path = installedRowPath(dependent);
            if (!path || seen.has(path)) continue;
            seen.add(path);
            found.push(dependent);
            queue.push(dependent);
        }
    }
    return found;
}

/** 定序：依赖别人的排在前面。后端不许"依赖者还开着"时动被依赖的那个，先后错了整批会半途失败。 */
function orderedForDisable(rows) {
    const pending = [...rows];
    const ordered = [];
    const placed = new Set();
    while (pending.length) {
        const index = pending.findIndex((item) => enabledDependentsOf(item).every(
            (other) => placed.has(installedRowPath(other)),
        ));
        if (index < 0) break;   // 互相依赖：剩下的原样交给后端各自的判断
        placed.add(installedRowPath(pending[index]));
        ordered.push(...pending.splice(index, 1));
    }
    return [...ordered, ...pending];
}

/** 该行对应的包 id：没有安装记录归属的纯本地模组为空（既不能更新也不能卸载）。 */
function installedRowPackageId(item) {
    if (item.fromScan) return String(item.installed_package_id || "");
    return item.unrecognized ? "" : String(item.id || "");
}

/** 重装要用哪个包：安装记录的归属优先，其次扫描时匹配到的注册表条目。 */
function installedRowReinstallId(item) {
    if (item.fromScan) {
        return String(item.installed_package_id || item.registry_id || "");
    }
    return item.unrecognized ? "" : String(item.id || "");
}

/** 重装按钮只给找得到来源的行：注册表（含私有服务器目录）里没有这个包就没得重装。 */
function installedRowReinstallSource(item) {
    const id = installedRowReinstallId(item);
    if (!id) return "";
    return (state.packages || []).some((candidate) => candidate.id === id) ? id : "";
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

/**
 * 列表数据源：磁盘扫描结果，加上扫描看不到的补丁包。
 *
 * 模组按加载器供给的目录扫描（`Mods`、`BepInEx/plugins` …），但补丁落在 `BepInEx/core` 这类
 * 不是模组目录的位置：那些包只能按安装记录补一行，否则装完在页面上没有入口。基础运行时
 * （`modloader`）不在这里 —— 它们在加载器页与左下角环境区。
 */
function installedItems() {
    const scanned = (state.localMods || []).map((mod) => ({...mod, fromScan: true}));
    const covered = new Set(
        scanned.map((mod) => mod.installed_package_id).filter(Boolean),
    );
    const patches = (state.installed || [])
        .filter((record) => record.kind === "patch" && !covered.has(record.id))
        .map((record) => ({...record, unrecognized: false, fromScan: false}));
    return [
        ...scanned,
        ...patches,
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
    incompatible: {label: "installedFilterIncompatible", match: (item) => installedRowIncompatible(item)},
};

/**
 * 这一行跟本机环境对不上：装着的那个版本跑不了，或者能看到的更新跑不了。
 *
 * 与行上那两枚不兼容标记一一对应（版本芯片、更新那枚感叹号）——筛选出来的行都说得清为什么。
 */
function installedRowIncompatible(item) {
    const record = item.fromScan
        ? (state.installed || []).find((entry) => entry.id && entry.id === item.installed_package_id)
        : item;
    if (!record?.id) return false;
    if (installedVersionVerdict(record) === VERDICT_INCOMPATIBLE) return true;
    return newerRelease(item)?.verdict === VERDICT_INCOMPATIBLE;
}

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
 * 行上的两种点击：双击跳到模组目录里这个包的位置，右键做多选（与目录页同一套习惯）。
 *
 * 单击不做事 —— 跳页会丢掉当前视野，得双击才够明确。点在按钮、选择框、链接上时不接管。
 */
function wireInstalledRow(row, item, key) {
    row.addEventListener("dblclick", (event) => {
        if (event?.target?.closest?.("button, input, a, label")) return;
        void openInstalledRow(item);
    });
    row.addEventListener("contextmenu", (event) => {
        // 先挡掉 WebView 自带的右键菜单，再把这一行翻过来。
        event.preventDefault();
        if (event?.target?.closest?.("button, input, a, label")) return;
        setRowSelected(key, !installedSelection().has(key));
    });
}

/**
 * 双击的去处：目录里有这个包就跳到目录页并选中它；目录里没有（纯本地 DLL、`UserLibs` 库）
 * 就退到资源管理器里定位文件 —— 两种都比「双击了却没反应」强。
 */
async function openInstalledRow(item) {
    const id = installedRowPackageId(item);
    if (id && (state.packages || []).some((pkg) => pkg.id === id)) {
        await focusPackage(id);
        return;
    }
    await openModLocation(item);
}

/** 目录里查无此包时的兜底：在资源管理器里定位该行的文件。 */
async function openModLocation(item) {
    const path = installedRowRevealPath(item);
    if (!path) {
        toast(tr("modLocationUnknown"));
        return;
    }
    const result = await callApi("open_mod_location", path);
    if (!result.ok) resultError(result);
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
 * 「重装」按钮：用来源里的包把文件覆盖回去，装哪一版在确认框里挑（可以选旧版，也可以选环境
 * 判定不兼容的版本）。
 *
 * 只有找得到来源的行才有这个按钮（见 `installedRowReinstallSource`）：纯本地的手工 DLL 没有
 * 来源，不摆一个按不动的按钮。重装不走卸载，所以被别的包依赖也不拦。
 *
 * `force` 是"这一行已被外部改动过"的意思：那种文件本来就要覆盖回去，再让用户点一次「强制重试」
 * 只是多一道手续。
 */
function reinstallButton(packageId, force) {
    const button = document.createElement("button");
    button.className = force ? "primary-button" : "secondary-button";
    button.type = "button";
    button.textContent = tr("reinstall");
    button.disabled = queueActive();
    button.addEventListener("click", () => {
        if (queueActive()) return;
        void beginInstall([packageId], null, true, Boolean(force));
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
 * 「有更新但不兼容」那句话：按已知的环境轴拼出来 —— 哪一轴有值就说哪一轴，
 * 一轴都没有就退到不点名环境的说法。
 *
 * 拼法（与设计一致）：`有更新（{版本}），但不兼容你的 ` + `Sprocket {}` + `和` + 各个加载器。
 */
function incompatibleUpdateText(version) {
    const axes = environmentAxesText();
    if (!axes) return tr("incompatibleUpdateUnknown", {version});
    return tr("incompatibleUpdateHead", {version}) + axes;
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
    // 名字从数据层的注册表条目取；包 id 依次看安装记录、扫描时的注册表匹配、DLL 自己声明的 id。
    // 扫描结果自带的那份多语言名只在注册表还没到时补位，最后才回退到 DLL 的英文元数据。
    name.textContent = registryPackageLabel(
        mod.installed_package_id || mod.registry_id || mod.declared_id
    )
        || localized(mod.registry_display_name, "")
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

    const toggleable = installedRowToggleable(mod);
    if (toggleable) {
        const toggle = document.createElement("button");
        toggle.className = mod.disabled ? "secondary-button" : "danger-button";
        toggle.type = "button";
        toggle.textContent = tr(mod.disabled ? "enableMod" : "disableMod");
        toggle.disabled = queueActive();
        toggle.addEventListener("click", () => toggleLocalMod(mod.path, Boolean(mod.disabled)));
        actions.append(toggle);
    }

    const reinstallId = installedRowReinstallSource(mod);
    if (reinstallId) {
        actions.append(reinstallButton(reinstallId, Boolean(record?.corrupted)));
    }

    if (record) {
        const removablePackage = state.packages.find((candidate) => candidate.id === record.id)
            || {
                id: record.id,
                name: record.name || record.id,
                display_name: {en: record.name || record.id, zh: record.name || record.id},
            };
        const remove = document.createElement("button");
        remove.className = "danger-button";
        remove.type = "button";
        remove.textContent = tr("remove");
        remove.disabled = queueActive();
        remove.addEventListener("click", () => confirmRemove(removablePackage));
        actions.append(remove);
    }

    row.append(selectionCheckbox(mod, key), title, actions);
    wireInstalledRow(row, mod, key);
    return row;
}

/** 记录行：没有磁盘条目的包（补丁）与未识别的本地 DLL。 */
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
    // 没有磁盘条目的行（补丁）拿安装记录里的种类：那是数据层给出的事实，界面不猜。
    const kindLabel = local?.kind || item.kind || "";
    if (kindLabel) {
        const kind = document.createElement("span");
        kind.className = "state-chip";
        kind.textContent = kindLabel;
        actions.append(kind);
    }
    if (!item.unrecognized) {
        appendIntegrityChip(actions, item);
        const installedVerdict = installedVersionVerdict(item);
        if (verdictNeedsChip(installedVerdict)) actions.append(compatibilityChip(installedVerdict));
    }
    actions.append(...updateChips(item));
    if (installedRowToggleable(item)) {
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
        const reinstallId = installedRowReinstallSource(item);
        if (reinstallId) {
            actions.append(reinstallButton(reinstallId, Boolean(item.corrupted)));
        }
        const remove = document.createElement("button");
        remove.className = "danger-button";
        remove.type = "button";
        remove.textContent = tr("remove");
        remove.disabled = !removablePackage || queueActive();
        remove.addEventListener("click", () => removablePackage && confirmRemove(removablePackage));
        actions.append(remove);
    }
    row.append(selectionCheckbox(item, key), title, actions);
    wireInstalledRow(row, item, key);
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
    const result = await callApi("enqueue_install", ids, false, versions);
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

async function toggleSelectedMods(enabled) {
    if (queueActive()) return;
    const selected = selectedInstalledItems().filter(
        (item) => installedRowToggleable(item) && installedRowDisabled(item) === Boolean(enabled),
    );
    if (!selected.length) {
        toast(tr("selectionNotApplicable"));
        return;
    }
    // 禁用时把还开着的依赖者整串带上（不止一层），再定序：先关依赖别人的。
    const extra = enabled ? [] : enabledDependentsClosure(selected);
    if (extra.length) {
        const confirmed = await showModal({
            kicker: tr("disableMod"),
            title: tr("confirmDisableDependents"),
            body: tr("disableDependentsMessage", {
                count: extra.length,
                names: extra.map(installedRowLabel).join(", "),
            }),
            confirmText: tr("disableMod"),
            destructive: true,
        });
        if (!confirmed) return;
    }
    const rows = enabled ? selected : orderedForDisable([...extra, ...selected]);
    const paths = [...new Set(rows.map(installedRowPath).filter(Boolean))];
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
                const result = await callApi("enqueue_install", [entry.package_id], true);
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
                const result = await callApi("enqueue_install", [entry.package_id]);
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

/** 让数据层刷一次队列：命令只回 ack，表经推送回来。 */
async function pollQueue(_force = false) {
    if (!state.ready) return;
    try {
        const result = await callApi("data_request", "queue");
        if (!result.ok) resultError(result);
    } catch (_error) {
        // A closing WebView can reject an in-flight call. There is nothing left to update.
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
