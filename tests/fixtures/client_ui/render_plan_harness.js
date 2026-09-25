// 在 Node 里真实执行安装计划的渲染与「改版本重解析」（无浏览器）。
//
// 用法：node render_plan_harness.js <client_ui_dir> <payload.json>
//
// 加载仓库里未修改的 i18n/core/compatibility/catalog/installs 五个文件，注入最小 DOM，
// 然后**真的调用** beginInstall()：检查计划里根包的版本选择器、改选之后是否按新版本重新
// 解析、以及最终入队时带的是不是用户选的那个版本。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_plan_harness.js <client_ui_dir> <payload.json>");
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
        this.dataset = {};
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

    querySelector() {
        return null;
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
for (const selector of [
    "#catalog-search", "#category-select", "#sort-select", "#translation-search", "#translation-sort",
    "#package-list", "#translation-list", "#catalog-count", "#translation-count", "#catalog-notice",
    "#batch-count", "#batch-install",
    // core.js 的真实 setStatus/toast/showPage 会写这几个节点（不是桩，得给它们元素）。
    "#status-text", "#status-source", ".status-mark", "#toast-region",
    "#page-kicker", "#page-title", "#page-subtitle", "#header-actions",
]) {
    elements[selector] = new FakeElement(selector.includes("list") ? "section" : "div");
}
elements["#category-select"].value = "all";
elements["#sort-select"].value = "name";

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

const apiCalls = [];
let modalOptions = null;
let resolveModal = null;
const snapshot = (value) => (value === undefined ? null : JSON.parse(JSON.stringify(value)));

const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    showMessage: () => {},
    pollQueue: async () => {},
    showPage: async () => {},
    ensureMelonLoader: async () => ({proceed: true, allowWithout: false}),
    showModal: (options) => {
        // 第一个弹窗（安装计划）由测试驱动；后面那些（例如「要装 MelonLoader 吗」）直接确认。
        if (modalOptions) return Promise.resolve(true);
        modalOptions = options;
        return new Promise((resolve) => { resolveModal = resolve; });
    },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

// core.js 的 `callApi` 走的是真实桥；这里给一个假桥，行为与后端一致。
sandbox.pywebview = {
    api: {
        client_log: async () => ({ok: true}),
        plan_install: async (ids, versions, includeInstalled) => {
            // 存快照而不是引用：`versions` 是活对象，改版本会把它一起改掉。
            apiCalls.push({kind: "call", args: ["plan_install", snapshot(ids), snapshot(versions), includeInstalled === true]});
            if (payload.plan_failure) {
                return {ok: false, code: "install_plan_failed", message: payload.plan_failure};
            }
            const call = apiCalls.filter((entry) => entry.args[0] === "plan_install").length;
            // `plans_sequence` 按第几次调用给计划（不够就沿用最后一个）；没给就按老规矩 first/replan。
            const sequence = Array.isArray(payload.plans_sequence) ? payload.plans_sequence : null;
            const plans = sequence
                ? (sequence[call - 1] ?? sequence[sequence.length - 1]) || []
                : (call === 1 ? payload.plan?.plans : payload.replan?.plans) || [];
            return {
                ok: true,
                plans,
                skipped: call === 1 ? payload.skipped || [] : [],
                recommendations: payload.plan?.recommendations || [],
                failed: [],
            };
        },
        enqueue_install: async (...args) => {
            apiCalls.push({kind: "call", args: ["enqueue_install", ...args.map(snapshot)]});
            return {ok: true, count: 1, failed: []};
        },
    },
};

const injected = {
    ready: true,
    page: "catalog",
    language: "en",
    languageMode: "en",
    catalogLoading: false,
    packages: payload.packages || [],
    publicPackages: payload.packages || [],
    privatePackages: [],
    installed: [],
    unrecognized: [],
    localMods: [],
    queue: [],
    selectedId: null,
    // `batch` / 集合类字段不要放进这里：JSON 会把 Set 变成 {}，留着 core.js 里的原样。
    showIncompatible: false,
    environment: payload.environment || null,
    environmentRevision: 1,
};

const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    "globalThis.__state = state;",
    fs.readFileSync(path.join(clientUiDir, "js", "compatibility.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "catalog.js"), "utf8"),
    // 状态栏的状态由环境（modloaders.js）与队列（installs.js）共同决定，两个都要加载。
    fs.readFileSync(path.join(clientUiDir, "js", "modloaders.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "installs.js"), "utf8"),
].join("\n");
const context = vm.createContext(sandbox);
vm.runInContext(source, context, {filename: "plan.js"});

function flatten(node, found = []) {
    if (!node || node.tagName === "#text") return found;
    found.push({tag: node.tagName, className: node.className, text: node.textContent, value: node.value});
    for (const child of node.children || []) flatten(child, found);
    return found;
}

function findSelect(node) {
    return flatten(node).length
        ? flatten(node).find((item) => item.tag === "select")
        : null;
}

function selectElement(node) {
    if (!node || node.tagName === "#text") return null;
    if (node.tagName === "select") return node;
    for (const child of node.children || []) {
        const found = selectElement(child);
        if (found) return found;
    }
    return null;
}

function options(element) {
    return (element?.children || []).map((option) => ({
        value: option.value,
        text: option.textContent,
        selected: Boolean(option.selected),
    }));
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function main() {
    const result = {planCalls: [], enqueue: null, first: null, after: null, toasts: []};
    const started = vm.runInContext(
        `beginInstall(${JSON.stringify(payload.install_ids || [])}, ${JSON.stringify(payload.versions || null)});`,
        context,
    );
    for (let attempt = 0; attempt < 10 && !modalOptions; attempt += 1) await tick();
    if (!modalOptions) {
        // 计划本身失败时不会开弹窗：这时候只关心报了几次、报的是什么。
        for (let attempt = 0; attempt < 5; attempt += 1) await tick();
        await started.catch(() => {});
        return {
            ...result,
            planCalls: apiCalls
                .filter((entry) => entry.kind === "call" && entry.args[0] === "plan_install")
                .map((entry) => entry.args.slice(1)),
            toasts: elements["#toast-region"].children.map((node) => node.textContent),
        };
    }

    const body = modalOptions.body;
    const select = selectElement(body);
    result.first = {
        title: modalOptions.title,
        options: options(select),
        className: select ? select.className : "",
        warnings: flatten(body)
            .filter((item) => item.className === "loader-displace-warning")
            .map((item) => item.text),
    };

    if (select && payload.change_to) {
        select.value = payload.change_to;
        for (const handler of select.listeners.change || []) handler({});
        for (let attempt = 0; attempt < 10; attempt += 1) await tick();
        result.after = {
            options: options(selectElement(body)),
        };
    }

    resolveModal(payload.confirm !== false);
    await tick();
    await tick();
    result.planCalls = apiCalls
        .filter((entry) => entry.kind === "call" && entry.args[0] === "plan_install")
        .map((entry) => entry.args.slice(1));
    const queued = apiCalls.find((entry) => entry.kind === "call" && entry.args[0] === "enqueue_install");
    result.enqueue = queued ? queued.args.slice(1) : null;
    await started;
    return result;
}

// 输出放在 `setImmediate` 里：管道的 stdout 是异步写，直接写会在进程退出时丢掉。
// 写完再 `process.exit`：否则 toast 那些 4 秒定时器会把整个测试拖成几十秒。
function finish(payload) {
    const text = JSON.stringify(payload);
    setImmediate(() => process.stdout.write(text, () => process.exit(0)));
}

main()
    .then(finish)
    .catch((error) => finish({error: String((error && error.stack) || error)}));
