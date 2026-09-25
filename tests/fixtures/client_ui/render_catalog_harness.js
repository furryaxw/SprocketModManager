// 在 Node 里执行真实的目录页/环境区渲染逻辑（无浏览器）。
//
// 用法：node render_catalog_harness.js <client_ui_dir> <payload.json>
//
// 覆盖三件契约：兼容性隐藏与「显示不兼容」开关、版本号颜色、左下角（未装加载器=链接、
// 环境矛盾=标红并写清原因）。加载的是仓库里未修改的 i18n/core/compatibility/catalog/modloaders
// 五个文件（合成一个脚本 —— 页面里它们是分开的 `<script>`，但顶层 `const` 不跨脚本共享），
// 所以文案也来自真实的 i18n 表。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_catalog_harness.js <client_ui_dir> <payload.json>");
    process.exit(2);
}

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

class FakeElement {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.className = "";
        this.disabled = false;
        this.type = "";
        this.hidden = false;
        this.value = "";
        this.checked = false;
        this.listeners = {};
        this.attributes = {};
        this._text = "";
    }

    get textContent() {
        return this._text;
    }

    set textContent(value) {
        this._text = value === undefined || value === null ? "" : String(value);
    }

    append(...nodes) {
        for (const node of nodes) this.children.push(node);
    }

    replaceChildren(...nodes) {
        this.children = nodes.slice();
    }

    addEventListener(type, handler) {
        (this.listeners[type] = this.listeners[type] || []).push(handler);
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    remove() {
        this.removed = true;
    }

    /** 够用的选择器：`.class` / `[attr="value"]`（多选栏那几处用到）。 */
    querySelector(selector) {
        return findIn(this, selector);
    }

    get classList() {
        const owner = this;
        return {
            add: (name) => {
                if (!owner.className.split(" ").includes(name)) owner.className = `${owner.className} ${name}`.trim();
            },
            remove: (name) => { owner.className = owner.className.replace(name, "").trim(); },
            toggle: (name, force) => {
                const has = owner.className.split(" ").includes(name);
                const wanted = force === undefined ? !has : Boolean(force);
                if (wanted && !has) owner.className = `${owner.className} ${name}`.trim();
                if (!wanted && has) owner.className = owner.className.replace(name, "").trim();
            },
        };
    }
}

const elements = {};

// 最小可用的选择器匹配：`.class` 或 `[attr="value"]`（多选栏那几处用到）。
function findIn(root, selector) {
    for (const child of root.children || []) {
        if (matches(child, selector)) return child;
        const nested = findIn(child, selector);
        if (nested) return nested;
    }
    return null;
}

function matches(element, selector) {
    const attribute = /^\[([\w-]+)="([^"]*)"\]$/.exec(selector);
    if (attribute) return element.attributes[attribute[1]] === attribute[2];
    const presence = /^\[([\w-]+)\]$/.exec(selector);
    if (presence) return Object.prototype.hasOwnProperty.call(element.attributes, presence[1]);
    if (selector.startsWith(".")) return element.className.split(" ").includes(selector.slice(1));
    return false;
}

/** 按 index.html 的结构造一份多选栏（含两个页面各一份）。 */
function selectionBar(scope) {
    const anchor = new FakeElement("div");
    anchor.className = "catalog-selection-anchor";
    anchor.attributes["data-selection-scope"] = scope;
    anchor.hidden = true;
    const bar = new FakeElement("div");
    bar.className = "selection-bar";
    const count = new FakeElement("span");
    count.className = "selection-count";
    count.attributes["data-selection-count"] = "";
    bar.append(count);
    for (const action of ["install", "remove", "all", "invert", "clear"]) {
        const button = new FakeElement("button");
        button.attributes["data-selection-action"] = action;
        bar.append(button);
    }
    anchor.append(bar);
    return anchor;
}
const TAGS = {
    "#environment-install-loader": "button",
    "#refresh-catalog": "button",
};
for (const selector of [
    "#catalog-search", "#category-select", "#sort-select", "#translation-search", "#translation-sort",
    "#package-list", "#translation-list", "#catalog-count", "#translation-count", "#catalog-notice",
    "#detail-panel", "#translation-detail",
    "#environment", "#environment-sprocket", "#environment-loaders",
    "#environment-install-loader", "#environment-note", "#toast-region",
    "#status-text", ".status-mark",
    "#refresh-catalog", "#registry-state", "#registry-state-text",
]) {
    elements[selector] = new FakeElement(TAGS[selector] || (selector.includes("list") ? "section" : "div"));
}
// 两个列表页各挂一份多选栏（页面元素里带着它，`[data-selection-scope]` 才能被找到）。
for (const [selector, scope] of [["#page-catalog", "catalog"], ["#page-translations", "translations"]]) {
    elements[selector] = new FakeElement("section");
    elements[selector].append(selectionBar(scope));
}
elements["#category-select"].value = "all";
elements["#sort-select"].value = "name";
elements["#translation-sort"].value = "name";

const documentStub = {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => {
        const node = new FakeElement("#text");
        node.textContent = text;
        return node;
    },
    querySelector: (selector) => elements[selector] || null,
    querySelectorAll: () => [],
};

const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    // installs.js 才定义 queueActive；这里只渲染目录页，按钮可用性用一个固定值糊住。
    queueActive: () => false,    // core.js 会覆盖下面这些同名的桩（真代码优先）；这些只是它加载时的兜底。
    reportClientLog: () => {},
    toast: () => {},
    setStatus: () => {},
    // 认领、已安装页的渲染、私有服务器那一块都在别的文件里：这个 harness 只管目录页那一段。
    renderInstalled: () => {},
    claimExistingMods: async () => {},
    loadDeveloperServers: async () => {},
    resultError: () => {},
    loadPackageReadme: async () => {},
    confirmRemove: () => {},
    beginInstall: () => {},
    openUrl: () => {},
    callApi: async () => ({ ok: true }),
    showPage: async () => {},
    showModal: async () => true,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
// `core.js` 自己声明了 `callApi`（走 `window.pywebview.api`），所以真调用要有假桥。
sandbox.pywebview = {
    api: {
        load_catalog: async () => ({ok: true, source: "test-index", count: 1}),
    },
};

// core.js 里的 `state` 是脚本内的 `const`，所以 payload 必须在同一个脚本里并进去。
const injected = {
    ready: true,
    page: payload.page === "translations" ? "translations" : "catalog",
    language: "en",
    languageMode: "en",
    catalogLoading: false,
    privatePackages: [],
    unrecognized: [],
    localMods: [],
    selectedId: payload.selected || null,
    showIncompatible: Boolean(payload.show_incompatible),
    modloadersRequested: true,
};
const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "data.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    // 安装记录与环境读数和线上一样由**数据层推来**（页面再不自己存一份）。
    `dataDeliver(${JSON.stringify({key: "installed", value: {installed: payload.installed || []}, revision: 1})});`,
    `dataDeliver(${JSON.stringify({key: "environment", value: payload.environment || null, revision: 1})});`,
    `dataDeliver(${JSON.stringify({key: "loaders", value: {modloaders: payload.modloaders || []}, revision: 1})});`,
    `dataDeliver(${JSON.stringify({key: "catalog", value: {packages: payload.packages || [], source: payload.source || ""}, revision: 1})});`,
    fs.readFileSync(path.join(clientUiDir, "js", "compatibility.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "catalog.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "modloaders.js"), "utf8"),
    // `state` 是 core.js 脚本内的 `const`，脚本外面看不见：拷一份到全局，方便断言开关状态。
    "globalThis.__state = state;",
].join("\n");

const context = vm.createContext(sandbox);
vm.runInContext(source, context, { filename: "catalog.js" });
vm.runInContext("renderCatalog(); renderEnvironment();", context);

// 多选：先按 `select_rows` 右键点几条（左键留给详情面板），再跑 `selection_action`。
const selection = {rightClicked: []};
function rightClickRow(index) {
    const row = elements[listSelector].children.filter((child) => child.className.includes("package-row"))[index];
    for (const handler of row?.listeners?.contextmenu || []) {
        handler({preventDefault: () => {}, target: null});
    }
}
const bar = () => {
    const page = elements[translations ? "#page-translations" : "#page-catalog"];
    return findIn(page, "[data-selection-scope]");
};
function selectionState() {
    const anchor = bar();
    const buttons = {};
    for (const action of ["install", "remove", "all", "invert", "clear"]) {
        const button = findIn(anchor, `[data-selection-action="${action}"]`);
        buttons[action] = Boolean(button?.disabled);
    }
    return {
        hidden: Boolean(anchor?.hidden),
        count: findIn(anchor, "[data-selection-count]")?.textContent || "",
        buttons,
    };
}

// 两个列表页共用同一套渲染函数：目录页与翻译页各取自己的容器。
const translations = sandbox.__state.page === "translations";
const listSelector = translations ? "#translation-list" : "#package-list";
const countSelector = translations ? "#translation-count" : "#catalog-count";
const detailSelector = translations ? "#translation-detail" : "#detail-panel";

function serialize(element) {
    if (!element || element.tagName === "#text") return null;
    return {
        tag: element.tagName,
        className: element.className,
        text: element.textContent,
        hidden: Boolean(element.hidden),
        open: Boolean(element.open),
        disabled: Boolean(element.disabled),
        checked: Boolean(element.checked),
        children: element.children.map(serialize).filter(Boolean),
    };
}

function flatten(node, found = []) {
    if (!node || node.tagName === "#text") return found;
    found.push({ tag: node.tagName, className: node.className, text: node.textContent });
    for (const child of node.children || []) flatten(child, found);
    return found;
}

const packageRows = () =>
    elements[listSelector].children.filter((child) => child.className.includes("package-row"));
const emptyState = () =>
    elements[listSelector].children.some((child) => child.className.includes("empty-list"));

const rows = packageRows().map(serialize);
// 开关之前的快照：`count`/`notice` 必须在点击前取，否则读到的是点击后的那一版。
const firstRender = {
    rows,
    emptyState: emptyState(),
    count: elements[countSelector].textContent,
    notice: serialize(elements["#catalog-notice"]),
    toggle: (() => {
        const button = elements["#catalog-notice"].children.find((child) => child.tagName === "button");
        return button ? { text: button.textContent, tag: button.tagName } : null;
    })(),
    diagnostics: {
        showIncompatible: sandbox.__state.showIncompatible,
        known: sandbox.__state.packages.length,
        visible: vm.runInContext("filteredPackages(packageBrowserView()).length", context),
        hidden: vm.runInContext(
            "state.packages.filter((pkg) => packageHidden(pkg)).map((pkg) => pkg.id)",
            context,
        ),
    },
};

// 「显示不兼容」开关真的接在那一行上：点一下看隐藏的包是否回来（再点一下反向验证）。
const noticeButton = elements["#catalog-notice"].children.find((child) => child.tagName === "button");
let afterToggle = null;
if (noticeButton) {
    for (const handler of noticeButton.listeners.click || []) handler({});
    afterToggle = {
        showIncompatible: sandbox.__state.showIncompatible,
        rows: packageRows().length,
    };
}

// 目录加载：`load_catalog` 只回 ack，界面必须靠这次 ack 收尾 ——
// 目录没变的时候不会有推送，等推送就会永远停在「正在连接」。
let catalogLoad = null;
let catalogLoadPending = null;
if (payload.action === "load_catalog") {
    catalogLoadPending = Promise.resolve(
        vm.runInContext("loadCatalog(false)", context),
    ).then(() => ({
        state: elements["#registry-state-text"].textContent,
        loading: Boolean(sandbox.__state.catalogLoading),
    }));
}

// 多选：右键行＝快速勾选（左键仍留给详情面板），然后可选地跑一个批量动作。
for (const index of payload.select_rows || []) rightClickRow(index);
const selectionAfterRows = selectionState();
// 直接勾某一行的选择框：真实浏览器里 disabled 的框不会派发 change，所以这条路径能测出「能不能勾」。
if (Number.isInteger(payload.check_row)) {
    const checkbox = packageRows()[payload.check_row]?.children?.[0];
    if (checkbox) {
        checkbox.checked = true;
        for (const handler of checkbox.listeners.change || []) handler({target: checkbox});
    }
}
const selectionAfterCheck = selectionState();
if (payload.selection_action) {
    vm.runInContext(`void handleCatalogSelection(${JSON.stringify(payload.selection_action)});`, context);
}
const selectionAfterAction = selectionState();

// 说明区：默认收起，`open_readme` 直接给 details 加 open，等价于用户点开那一下。
const detailElement = elements[detailSelector];
const detailSnapshot = serialize(detailElement);
const readmeDetails = findIn(detailElement, ".detail-readme") || null;
const readmeText = () => flatten(readmeDetails).map((item) => item.text).join(" ");
const readme = readmeDetails ? {
    tag: readmeDetails.tagName,
    open: Boolean(readmeDetails.open),
    summary: readmeDetails.children.find((child) => child.tagName === "summary")?.textContent || "",
    text: readmeText(),
} : null;
if (readmeDetails && payload.open_readme) readmeDetails.open = true;
const readmeOpened = readmeDetails ? {
    tag: readmeDetails.tagName,
    open: Boolean(readmeDetails.open),
    text: readmeText(),
} : null;

const report = () => process.stdout.write(JSON.stringify({
        rows: firstRender.rows,
        emptyState: firstRender.emptyState,
        count: firstRender.count,
        notice: firstRender.notice,
        toggle: firstRender.toggle,
        diagnostics: firstRender.diagnostics,
        afterToggle,
        catalogLoad,
        selectionAfterRows,
        selectionAfterCheck,
        selectionAfterAction,
        environment: {
            sprocket: serialize(elements["#environment-sprocket"]),
            loaders: serialize(elements["#environment-loaders"]),
            install: serialize(elements["#environment-install-loader"]),
            note: serialize(elements["#environment-note"]),
        },
        statusbar: {
            text: elements["#status-text"].textContent,
            mark: elements[".status-mark"].className,
        },
        detail: detailSnapshot,
        readme: {default: readme, opened: readmeOpened},
}));
if (catalogLoadPending) {
    catalogLoadPending
        .catch((error) => ({error: String((error && error.stack) || error)}))
        .then((value) => {
            catalogLoad = value;
            setImmediate(report);
        });
} else {
    setImmediate(report);
}
