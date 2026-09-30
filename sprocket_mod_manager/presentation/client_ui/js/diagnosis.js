"use strict";

// 「错误修复」页：手动跑一次诊断，把报告按「必须解决 / 非必要」两栏画出来。
//
// 只画，不做任何修复动作 —— 每条问题给的是证据、原因和一串步骤，用户照着去「已安装」页自己处理。
// 报告来自数据层那份现状：一次扫描边走边推，每推一次就把整份重画（报告很小，重画比按条打补丁
// 更好懂）。规则包里的文案是 `{语言: 文案}`，占位符先取 `labels`（本地化值）再退回 `params`。

/** 数据层推来的那份现状：一份报告，扫描进行中也会来。 */
function diagnosisState() {
    return state.diagnosis || null;
}

/** 规则包文案里的 `{占位符}`：`labels` 里的本地化值优先，其次 `params` 的纯字符串。 */
function fillDiagnosisText(text, finding) {
    if (typeof text !== "string" || !text) return "";
    const params = (finding && finding.params) || {};
    const labels = (finding && finding.labels) || {};
    return text.replace(/\{([A-Za-z0-9_]+)\}/g, (whole, name) => {
        if (labels[name]) {
            const value = localized(labels[name], "");
            if (value) return value;
        }
        return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : whole;
    });
}

function diagnosisText(finding, key) {
    return fillDiagnosisText(localized(finding[key], ""), finding);
}

function diagnosisSteps(finding) {
    const steps = localized(finding.tutorial, null);
    if (!Array.isArray(steps)) return [];
    return steps.map((step) => fillDiagnosisText(step, finding));
}

function diagnosisReason(reason) {
    return tr(`diagnosisReason_${reason}`);
}

/** 一条事实：标签 + 值；值后面可以挂一段原样引用的日志。 */
function diagnosisFact(label, value, quote = "") {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    const text = document.createElement("span");
    text.textContent = value;
    description.append(text);
    if (quote) {
        const raw = document.createElement("code");
        raw.className = "diagnosis-quote";
        raw.textContent = quote;
        description.append(raw);
    }
    row.append(term, description);
    return row;
}

function diagnosisCard(finding) {
    const card = document.createElement("article");
    card.className = "diagnosis-card";
    card.dataset.bucket = finding.bucket;

    const header = document.createElement("header");
    const level = document.createElement("span");
    level.className = "diagnosis-level";
    // 等级是排序用的序号，不是文案。
    level.textContent = `L${finding.level}`;
    const title = document.createElement("h3");
    title.textContent = diagnosisText(finding, "title");
    header.append(level, title);
    card.append(header);

    const facts = document.createElement("dl");
    facts.className = "diagnosis-facts";
    const line = finding.log_line;
    if (line) {
        facts.append(
            diagnosisFact(
                tr("diagnosisEvidence"),
                `${line.source}:${line.number}`,
                line.text,
            ),
        );
    } else if (localized(finding.evidence, "")) {
        facts.append(diagnosisFact(tr("diagnosisEvidence"), diagnosisText(finding, "evidence")));
    }
    if (diagnosisText(finding, "explain")) {
        facts.append(diagnosisFact(tr("diagnosisCause"), diagnosisText(finding, "explain")));
    }
    card.append(facts);

    const steps = diagnosisSteps(finding);
    if (steps.length) {
        const fix = document.createElement("div");
        fix.className = "diagnosis-fix";
        const label = document.createElement("p");
        label.className = "diagnosis-label";
        label.textContent = tr("diagnosisFix");
        const list = document.createElement("ol");
        for (const step of steps) {
            const item = document.createElement("li");
            item.textContent = step;
            list.append(item);
        }
        fix.append(label, list);
        card.append(fix);
    }

    const target = finding.go_to;
    if (target && target.page) {
        const go = document.createElement("button");
        go.type = "button";
        go.className = "secondary-button";
        go.textContent = tr("diagnosisGo");
        go.addEventListener("click", () => void goToDiagnosisTarget(target));
        card.append(go);
    }
    return card;
}

/**
 * 应用内跳转：切到目标页，能落到具体条目就落上去。
 *
 * 目录页本来就有 `focusPackage()`（它会放开挡住那一行的筛选）；安装页的行带 `data-package`，
 * 这里滚动过去并闪一下。跳转本身不写任何东西。
 */
async function goToDiagnosisTarget(target) {
    const page = target.page;
    if (target.package && (page === "catalog" || page === "translations")) {
        if (await focusPackage(target.package)) return;
    }
    if (page === "installed") setInstalledFilter("all");
    await showPage(page);
    if (target.package && page === "installed") {
        const row = document.querySelector(`#installed-list [data-package="${CSS.escape(target.package)}"]`);
        if (row) {
            row.classList.add("diagnosis-target");
            row.scrollIntoView({block: "nearest"});
            row.addEventListener("animationend", () => row.classList.remove("diagnosis-target"), {once: true});
        }
    }
}

/**
 * 一栏结论：`bucket` 是域里的桶名（CSS 按它着色），`headingKey` 才是文案键。
 *
 * 两者不能混用一个参数：桶名进了 `dataset.bucket`，拿文案键去填就再也匹配不上样式。
 */
function diagnosisSection(bucket, findings, headingKey, emptyKey) {
    const section = document.createElement("section");
    section.className = "diagnosis-section";
    section.dataset.bucket = bucket;
    const heading = document.createElement("h2");
    heading.className = "diagnosis-heading";
    heading.textContent = `${tr(headingKey)} (${findings.length})`;
    section.append(heading);
    if (!findings.length) {
        const empty = document.createElement("p");
        empty.className = "diagnosis-empty";
        empty.textContent = tr(emptyKey);
        section.append(empty);
        return section;
    }
    for (const finding of findings) section.append(diagnosisCard(finding));
    return section;
}

function renderDiagnosis() {
    const container = $("#diagnosis-report");
    const meta = $("#diagnosis-meta");
    if (!container) return;
    container.replaceChildren();

    const report = diagnosisState();
    if (!report) {
        meta.textContent = "";
        const empty = document.createElement("p");
        empty.className = "diagnosis-empty";
        empty.textContent = tr("diagnosisNeverRan");
        container.append(empty);
        return;
    }

    meta.textContent = report.pack_source === "missing"
        ? tr("diagnosisPackMissing")
        : tr("diagnosisPackLine", {
            version: report.pack_version,
            time: new Date(report.generated_at).toLocaleString(),
        });

    container.append(
        diagnosisSection("required", report.required || [], "diagnosisRequired", "diagnosisNoRequired")
    );
    container.append(
        diagnosisSection("optional", report.optional || [], "diagnosisOptional", "diagnosisNoOptional")
    );

    const unjudged = report.unjudged || [];
    if (unjudged.length) {
        const reasons = [...new Set(unjudged.map((item) => diagnosisReason(item.reason)))].join(" · ");
        const line = document.createElement("p");
        line.className = "diagnosis-unjudged";
        line.textContent = tr("diagnosisUnjudged", {count: unjudged.length, reasons});
        container.append(line);
    }

    if (report.running) {
        const running = document.createElement("p");
        running.className = "diagnosis-running";
        running.textContent = tr("diagnosisRunning");
        container.append(running);
        return;
    }

    if (!(report.required || []).length && !(report.optional || []).length) {
        const hint = document.createElement("p");
        hint.className = "diagnosis-hint";
        hint.textContent = tr("diagnosisUploadHint");
        container.append(hint);
    }
}

/**
 * 页面上那颗按钮：跑一次。
 *
 * 结论是数据层边走边推过来的，所以这里只管交代"正在跑"和报错；报告由推送那条路重画。
 */
async function runDiagnosis() {
    const button = $("#run-diagnosis");
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = tr("diagnosisRunning");
    try {
        const result = await callApi("run_diagnosis");
        if (!result.ok) {
            resultError(result);
            return;
        }
        renderDiagnosis();
    } finally {
        button.disabled = false;
        button.textContent = previous;
    }
}
