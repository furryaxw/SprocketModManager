// 在 Node 里真实执行安装计划的渲染与「改版本重解析」（无浏览器）。
//
// 用法：node render_plan_harness.js <client_ui_dir> <payload.json>
//
// 加载仓库里未修改的 i18n/core/compatibility/catalog/installs 五个文件，注入最小 DOM，
// 然后**真的调用** beginInstall()：检查计划里根包的版本选择器、改选之后是否按新版本重新
// 解析并重画依赖树、以及最终入队时带的是不是用户选的那个版本。
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
let lastInstall = {};
let modalOptions = null;
let resolveModal = null;
const snapshot = (value) => (value === undefined ? null : JSON.parse(JSON.stringify(value)));

// 勾选「推荐一起装」那几个复选框：按 `dataset.packageId` 找那个 input。
function checkboxFor(node, packageId) {
    if (!node || node.tagName === "#text") return null;
    if (node.tagName === "input" && node.dataset?.packageId === packageId) return node;
    for (const child of node.children || []) {
        const found = checkboxFor(child, packageId);
        if (found) return found;
    }
    return null;
}

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
        plan_changes: async (ids, choices, reinstall, force) => {
            // 存快照而不是引用：`choices` 是活对象，改版本会把它一起改掉。
            apiCalls.push({
                kind: "call",
                args: ["plan_changes", snapshot(ids), snapshot(choices), reinstall === true, force === true],
            });
            if (payload.plan_failure) {
                return {ok: false, code: "install_plan_failed", message: payload.plan_failure};
            }
            const call = apiCalls.filter((entry) => entry.args[0] === "plan_changes").length;
            const sequence = Array.isArray(payload.lines_sequence) ? payload.lines_sequence : null;
            const lines = sequence
                ? (sequence[call - 1] ?? sequence[sequence.length - 1]) || []
                : (call === 1 ? payload.lines : payload.replan_lines) || [];
            const failures = sequence
                ? (payload.failed_sequence?.[call - 1] ?? []) || []
                : ((call === 1 ? payload.failed : payload.replan_failed) || []);
            const noSolution = Array.isArray(payload.no_solution_sequence)
                ? Boolean(payload.no_solution_sequence[call - 1])
                : Boolean(payload.no_solution);
            const response = planResponse(lines, failures, noSolution);
            lastInstall = response.install;
            return response;
        },
        apply_changes: async (...args) => {
            apiCalls.push({kind: "call", args: ["apply_changes", ...args.map(snapshot)]});
            return {ok: true, added: [], count: 1, removed: []};
        },
    },
};

/**
 * 一份计划：给出来的每一行就是会装的那一行，解不出来的那些另给一行（标红、说明原因）。
 *
 * `install` 是这一批会落到磁盘上的「包 -> 版本」：`install_extra` 用它补上依赖，好断言
 * 「改版本之后依赖跟着换」。
 */
function planResponse(lines, failures, noSolution = false) {
    // 候选版本由目录读数给：这一行只报选中了哪一版，列表是后端摆好的那一份。
    const out = lines.map((entry) => {
        const base = lineFor(entry.id);
        return {...base, ...entry, versions: entry.versions?.length ? entry.versions : base.versions};
    });
    for (const failure of failures) {
        const base = lineFor(failure.id);
        out.push({
            ...base,
            action: "skip",
            version: failure.version || base.version,
            problems: [{kind: "conflict", others: [], text: failure.message || "no version found"}],
        });
    }
    const install = {};
    for (const line of out) {
        if (line.action === "install" && line.version) install[line.id] = String(line.version);
    }
    const extra = {...(payload.install_extra || {}), ...(lines === payload.lines ? {} : payload.replan_install_extra || {})};
    for (const [id, version] of Object.entries(extra)) {
        install[id] = String(version);
    }
    return {
        ok: true,
        no_solution: Boolean(noSolution),
        message: noSolution ? (payload.no_solution_message || "") : "",
        roots: payload.no_solution_roots || [],
        lines: out,
        install,
        remove: out.filter((line) => line.action === "remove").map((line) => line.id),
        excluded: [],
        recommendations: payload.recommendations || [],
        replaced_by: payload.replaced_by || [],
    };
}

/** 一行计划：候选版本对着目录读数取，和线上一样是后端给的。 */
function lineFor(packageId) {
    const pkg = (payload.packages || []).find((item) => item.id === packageId) || {};
    return {
        id: packageId,
        name: packageId,
        display_name: pkg.display_name || {},
        installed_version: pkg.installed?.version || "",
        named: true,
        action: "skip",
        version: "",
        reinstall: false,
        verdict: "",
        problems: [],
        depends_on: [],
        required_by: [],
        remove_with: [],
        versions: (pkg.releases || []).map((release) => ({
            version: String(release.version),
            verdict: String(release.verdict || ""),
        })),
    };
}

const injected = {
    ready: true,
    page: "catalog",
    language: "en",
    languageMode: "en",
    catalogLoading: false,
    privatePackages: [],
    selectedId: null,
    // `batch` / 集合类字段不要放进这里：JSON 会把 Set 变成 {}，留着 core.js 里的原样。
    showIncompatible: false,
};

const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "data.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    "globalThis.__state = state;",
    `dataDeliver(${JSON.stringify({key: "environment", value: payload.environment || null, revision: 1})});`,
    `dataDeliver(${JSON.stringify({key: "catalog", value: {packages: payload.packages || [], source: ""}, revision: 1})});`,
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

// 弹窗里所有的下拉，按渲染顺序：`change_select` 用它挑第几个来改。
function selectElements(node, found = []) {
    if (!node || node.tagName === "#text") return found;
    if (node.tagName === "select") found.push(node);
    for (const child of node.children || []) selectElements(child, found);
    return found;
}

function options(element) {
    return (element?.children || []).map((option) => ({
        value: option.value,
        text: option.textContent,
        selected: Boolean(option.selected),
    }));
}

// 计划里的依赖树：每条 `plan-line` 的「名字 + 版本」，按渲染顺序。
function planLines(node) {
    const found = [];
    const collect = (element) => {
        if (!element || element.tagName === "#text") return;
        if (String(element.className || "").split(" ").includes("plan-line")) found.push(lineText(element));
        for (const child of element.children || []) collect(child);
    };
    collect(node);
    return found;
}

// 计划里的每一行（`.plan-line`）。
function planLineElements(node, found = []) {
    if (!node || node.tagName === "#text") return found;
    if (String(node.className || "").split(" ").includes("plan-line")) found.push(node);
    for (const child of node.children || []) planLineElements(child, found);
    return found;
}

// 对话框里每个框（`.plan-group`）的结构：标题行、标记、原因行、以及这个框里那行版本选择器。
// 解析不了的包也要有自己的框 —— 它不该只剩一句报错。
function planGroups(node) {
    const found = [];
    const klass = (element) => String(element.className || "").split(" ");
    const markOf = (element) => (
        flatten(element).find((item) => klass(item).includes("plan-skipped-mark"))?.text || ""
    );
    // 标记与下拉在同一行里谁在前：重装标记要挨着下拉左边。
    const markBeforeSelect = (element) => {
        const row = planLineElements(element)[0];
        if (!row) return false;
        const children = row.children || [];
        const markIndex = children.findIndex((item) => klass(item).includes("plan-skipped-mark"));
        const selectIndex = children.findIndex((item) => item.tagName === "select");
        return markIndex >= 0 && selectIndex >= 0 && markIndex < selectIndex;
    };
    const collect = (element) => {
        if (!element || element.tagName === "#text") return;
        if (klass(element).includes("plan-group")) {
            const row = planLineElements(element)[0];
            found.push({
                className: element.className,
                heading: flatten(element).find((item) => item.tag === "strong")?.text || "",
                titleInLine: Boolean(
                    (row?.children || []).some((item) => item.tagName === "strong")
                ),
                mark: markOf(element),
                markBeforeSelect: markBeforeSelect(element),
                reason: (element.children || [])
                    .find((item) => klass(item).includes("plan-reason"))?.textContent || "",
                reasons: (element.children || [])
                    .filter((item) => klass(item).includes("plan-reason"))
                    .map((item) => item.textContent),
                lines: planLines(element),
                hasSelect: Boolean(selectElement(element)),
            });
            return;
        }
        for (const item of element.children || []) collect(item);
    };
    collect(node);
    return found;
}

function lineText(element) {
    const parts = [];
    const walk = (element) => {
        if (!element) return;
        if (element.tagName === "#text") {
            if (element.textContent) parts.push(element.textContent);
            return;
        }
        if (element.tagName === "select") {
            const selected = (element.children || []).find((option) => option.selected);
            parts.push(selected ? selected.value : "");
            return;
        }
        if (!(element.children || []).length && element.textContent) parts.push(element.textContent);
        for (const child of element.children || []) walk(child);
    };
    walk(element);
    return parts.join(" ");
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function main() {
    const result = {planCalls: [], enqueue: null, first: null, after: null, toasts: []};
    const started = vm.runInContext(
        `beginInstall(${JSON.stringify(payload.install_ids || [])}, `
        + `${JSON.stringify(payload.choices || null)}, `
        + `${payload.reinstall === true}, ${payload.force === true});`,
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
                .filter((entry) => entry.kind === "call" && entry.args[0] === "plan_changes")
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
        lines: planLines(body),
        groups: planGroups(body),
        install: lastInstall,
        messages: flatten(body)
            .filter((item) => item.className === "translation-replace-warning")
            .map((item) => item.text),
        warnings: flatten(body)
            .filter((item) => item.className === "loader-displace-warning")
            .map((item) => item.text),
    };

    // `changes` 按顺序改几次版本（每次都在重画后的新选择器上改）；`change_to` 是它的单值写法。
    // `change_select` 挑第几个下拉（默认第一个）—— 依赖那一行的下拉也能改。
    const steps = Array.isArray(payload.changes)
        ? payload.changes
        : (payload.change_to ? [payload.change_to] : []);
    const selectIndex = Number(payload.change_select || 0);
    result.steps = [];
    for (const version of steps) {
        const control = selectElements(body)[selectIndex];
        if (!control) break;
        control.value = version;
        for (const handler of control.listeners.change || []) handler({});
        for (let attempt = 0; attempt < 10; attempt += 1) await tick();
        result.steps.push({
            version,
            options: options(selectElement(body)),
            lines: planLines(body),
            groups: planGroups(body),
            install: lastInstall,
            messages: flatten(body)
                .filter((item) => item.className === "translation-replace-warning")
                .map((item) => item.text),
        });
    }
    result.after = result.steps.length ? result.steps[result.steps.length - 1] : null;

    // `check_recommendations` 勾上「推荐一起装」里的那几个，然后才确认。
    for (const packageId of payload.check_recommendations || []) {
        const box = checkboxFor(body, packageId);
        if (!box) continue;
        box.checked = true;
        for (const handler of box.listeners.change || []) handler({});
    }

    resolveModal(payload.confirm !== false);
    await tick();
    await tick();
    result.planCalls = apiCalls
        .filter((entry) => entry.kind === "call" && entry.args[0] === "plan_changes")
        .map((entry) => entry.args.slice(1));
    const queued = apiCalls.find((entry) => entry.kind === "call" && entry.args[0] === "apply_changes");
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
