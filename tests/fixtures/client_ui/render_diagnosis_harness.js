// 在 Node 里执行真实的「错误修复」页渲染逻辑（无浏览器）。
//
// 用法：node render_diagnosis_harness.js <client_ui_dir> <payload.json>
//
// 加载仓库里未修改的 i18n/core/data/diagnosis 四个文件（合成一个脚本 —— 页面里它们是分开的
// `<script>`，但顶层 `const` 不跨脚本共享），注入 `run_diagnosis` 的假桥，输出两栏结论、
// 每条问题的事实与步骤、未判定那一行、以及跳转按钮交回去的目标。
//
// 不覆盖 `showPage()` 真正切页那一段：那会连带其他页面的渲染，各页面自己的夹具已经在管了。
// 这里把 `goToDiagnosisTarget` 换成一个记录器，只断言「按钮交回来的目标对不对」。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [clientUiDir, payloadPath] = process.argv.slice(2);
if (!clientUiDir || !payloadPath) {
    console.error("usage: node render_diagnosis_harness.js <client_ui_dir> <payload.json>");
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

    get classList() {
        const owner = this;
        return {
            add: (name) => {
                if (!owner.className.split(" ").includes(name)) owner.className = `${owner.className} ${name}`.trim();
            },
            remove: (name) => {
                owner.className = owner.className.replace(name, "").trim();
            },
            toggle: (name, force) => {
                const has = owner.className.split(" ").includes(name);
                const wanted = force === undefined ? !has : Boolean(force);
                if (wanted && !has) owner.className = `${owner.className} ${name}`.trim();
                if (!wanted && has) owner.className = owner.className.replace(name, "").trim();
            },
        };
    }
}

const elements = {
    "#diagnosis-report": new FakeElement("div"),
    "#diagnosis-meta": new FakeElement("span"),
    "#run-diagnosis": new FakeElement("button"),
    "#toast-region": new FakeElement("div"),
};

const documentStub = {
    createElement: (tag) => new FakeElement(tag),
    querySelector: (selector) => elements[selector] || null,
    querySelectorAll: () => [],
};

function settle(rounds = 12) {
    return Array.from({length: rounds}).reduce(
        (chain) => chain.then(() => new Promise((resolve) => setImmediate(resolve))),
        Promise.resolve(),
    );
}

const apiCalls = [];
const api = {
    client_log: async () => ({ok: true}),
    run_diagnosis: async () => {
        apiCalls.push({kind: "call", args: ["run_diagnosis"]});
        if (payload.run_ok === false) {
            return {ok: false, code: "diagnosis_failed", message: "boom"};
        }
        // 真实那条路是数据层推来的：这条桥只回 ack，现状走 `deliver()`。
        return {ok: true};
    },
};

const sandbox = {
    console,
    document: documentStub,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    pywebview: {api},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const injected = {
    ready: true,
    page: "diagnosis",
    language: payload.language || "en",
    languageMode: payload.language || "en",
    settings: {},
    links: {},
    installed: [],
    catalogLoading: false,
    modloadersLoading: false,
};

const source = [
    fs.readFileSync(path.join(clientUiDir, "js", "i18n.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "core.js"), "utf8"),
    fs.readFileSync(path.join(clientUiDir, "js", "data.js"), "utf8"),
    `Object.assign(state, ${JSON.stringify(injected)});`,
    fs.readFileSync(path.join(clientUiDir, "js", "diagnosis.js"), "utf8"),
    // 跳转只记录目标：真正切页要连带别的页面一起渲染，各页面自己的夹具在管那段。
    "let goTargets = [];",
    "goToDiagnosisTarget = (target) => { goTargets.push(target); return Promise.resolve(true); };",
].join("\n");

const context = vm.createContext(sandbox);
let error = null;
try {
    vm.runInContext(source, context, {filename: "diagnosis.js"});
    // 真实页面里按钮上那行字由 `applyTranslations()` 按 `data-i18n` 填好才可点；
    // 这里没有 DOM，照着补一次，免得「跑完把文字还回去」测的是空串。
    elements["#run-diagnosis"].textContent = vm.runInContext('tr("runDiagnosis")', context);
} catch (caught) {
    error = String((caught && caught.stack) || caught);
}

function children(node, className) {
    return node.children.filter((child) => child.className === className);
}

function serializeFact(row) {
    const description = row.children[1];
    return {
        label: row.children[0].textContent,
        value: description.children[0] ? description.children[0].textContent : "",
        quote: description.children[1] ? description.children[1].textContent : "",
    };
}

function serializeCard(card) {
    const header = card.children[0];
    const facts = children(card, "diagnosis-facts")[0];
    const fix = children(card, "diagnosis-fix")[0];
    const go = children(card, "secondary-button")[0];
    return {
        bucket: card.dataset.bucket,
        level: header.children[0].textContent,
        title: header.children[1].textContent,
        facts: (facts ? facts.children : []).map(serializeFact),
        steps: fix ? fix.children[1].children.map((item) => item.textContent) : [],
        go: go ? go.textContent : null,
    };
}

function serializeReport() {
    const container = elements["#diagnosis-report"];
    const sections = children(container, "diagnosis-section").map((section) => ({
        bucket: section.dataset.bucket,
        heading: section.children[0].textContent,
        empty: children(section, "diagnosis-empty")[0]?.textContent ?? null,
        cards: children(section, "diagnosis-card").map(serializeCard),
    }));
    const loose = container.children.filter((node) => node.className !== "diagnosis-section");
    return {
        sections,
        unjudged: children(container, "diagnosis-unjudged")[0]?.textContent ?? null,
        running: children(container, "diagnosis-running")[0]?.textContent ?? null,
        hint: children(container, "diagnosis-hint")[0]?.textContent ?? null,
        empty: children(container, "diagnosis-empty")[0]?.textContent ?? null,
        looseCount: loose.length,
    };
}

/** 数据层推一份现状过来：界面读的就是它，`dataDeliver` 是那条路的入口。 */
function deliver(value) {
    vm.runInContext(
        `dataDeliver({key: "diagnosis", value: ${JSON.stringify(value === undefined ? null : value)}});`,
        context,
    );
}

async function exercise() {
    const button = elements["#run-diagnosis"];
    if (payload.preload_report !== undefined) {
        // 上一次那份报告先摆上：这一次跑失败时应该留在原地。
        deliver(payload.preload_report);
        vm.runInContext("renderDiagnosis()", context);
    }
    if (payload.run === false) {
        try {
            deliver(payload.report === undefined ? null : payload.report);
            vm.runInContext("renderDiagnosis()", context);
        } catch (caught) {
            error = error || String((caught && caught.stack) || caught);
        }
    } else {
        try {
            deliver(payload.report === undefined ? null : payload.report);
            vm.runInContext("runDiagnosis()", context);
        } catch (caught) {
            error = error || String((caught && caught.stack) || caught);
        }
    }
    // 还没 settle：此刻按钮应该正说着「正在读日志…」并且点不动。
    const duringLabel = button.textContent;
    const duringDisabled = button.disabled;
    await settle();

    const rendered = serializeReport();
    const clicked = payload.click_go;
    if (clicked !== undefined && clicked !== null) {
        const cards = children(elements["#diagnosis-report"], "diagnosis-section")
            .flatMap((section) => children(section, "diagnosis-card"))
            .filter((card) => Boolean(children(card, "secondary-button")[0]));
        const buttonInCard = clicked < 0
            ? cards[cards.length + clicked] && children(cards[cards.length + clicked], "secondary-button")[0]
            : cards[clicked] && children(cards[clicked], "secondary-button")[0];
        for (const handler of (buttonInCard?.listeners?.click || [])) handler({});
        await settle();
    }

    const toasts = elements["#toast-region"].children.map((node) => node.textContent);
    return {
        error,
        meta: elements["#diagnosis-meta"].textContent,
        buttonLabel: button.textContent,
        buttonDisabled: button.disabled,
        duringLabel,
        duringDisabled,
        toasts,
        apiCalls,
        goTargets: vm.runInContext("goTargets", context),
        report: rendered,
    };
}

exercise().then((result) => {
    setImmediate(() => process.stdout.write(JSON.stringify(result), () => process.exit(0)));
});
