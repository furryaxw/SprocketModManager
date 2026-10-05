"use strict";

// 业务层：长期读数的**监听**都写在这一处 —— 哪个 key 变了、该重画哪块、要不要顺手做点什么。
//
// 显示层（各页面文件里的 `renderXxx`）只负责画；用户操作只负责发命令（`callApi("data_request", …)`）。
// 订阅与"变了之后干什么"放在一起，是为了同一份读数只有一条反应路径：以前每个页面各自订阅、
// 各自记一份派生结果，谁先谁后就决定了界面上显示什么。
//
// 这份名单是"我显示什么"，所以留在界面这层；key 本身归数据层（`application/data_hub.py`）。

const DATA_KEYS = ["installed", "environment", "queue", "loaders", "catalog", "servers", "diagnosis"];

/** 已安装页那一整份读数变了。 */
function watchInstalledData() {
    dataWatch(["installed"], () => {
        if (!state.ready) return;
        renderInstalled();
        renderCatalog();
        renderStatusbar();
    });
}

/**
 * 环境读数变了：左下角与状态栏重画，判定口径变了才值得重拉目录。
 */
function watchEnvironmentData() {
    dataWatch(["environment"], () => {
        if (!state.ready) return;
        const result = state.environment;
        const axes = environmentKey(result);
        renderStatusbar();
        renderEnvironment();
        // 行上的兼容性 tooltip 就是按这份环境拼的（`environmentAxesText()`）。
        renderInstalled();
        if (axes !== state.environmentAxesKey) {
            state.environmentAxesKey = axes;
            void loadCatalog(false);
        }
        void ensureModloaderNames();
    });
}

/**
 * 队列那张表变了。
 *
 * 数据层定时问一次队列（便宜）、**只在表真的变了**时推送；这边的比对只剩"这条错报过没有"，
 * 那是界面状态，不是数据。
 */
function watchQueueData() {
    dataWatch(["queue"], () => {
        if (!state.ready) return;
        const payload = dataValue("queue") || {};
        const entries = payload.entries || [];
        renderQueue();
        renderModloaders();
        updatePageHeader();
        // 「已安装」页上的按钮看队列忙不忙（`queueActive()`）：队列一变就得按新状态重画，
        // 否则上一条任务结束时画下的那把"禁用"会一直留着，直到别的读数才把它翻过来。
        renderInstalled();
        // 队列跑没跑完也是状态栏要看的活状态。
        renderStatusbar();
        // 队列里失败的那一条也算「出过事」：状态栏红着，直到下一次操作成功；同一个任务只报一次。
        const failed = entries.find(
            (entry) => entry.state === "failed" && !state.failedQueueTasks.has(entry.task_id),
        );
        if (failed) {
            state.failedQueueTasks.add(failed.task_id);
            setStatus(failed.message || tr("operationFailed"), "error");
        }
        if (payload.close_pending) setStatus(tr("closeWaiting"));
    });
}

/** 加载器目录变了。 */
function watchLoadersData() {
    dataWatch(["loaders"], () => {
        if (!state.ready) return;
        renderModloaders();
        renderEnvironment();
        updatePageHeader();
        // 加载器的在用版本同样进环境轴的文案，行上的 tooltip 跟着这份读数走。
        renderInstalled();
    });
}

/** 注册表目录变了：按新读数收尾（推送只说明"变了"，收尾口径与 ack 那条路同一处）。 */
function watchCatalogData() {
    dataWatch(["catalog"], () => {
        if (!state.ready) return;
        finishCatalogLoad({source: dataValue("catalog")?.source || ""});
    });
}

/** 开发者服务器那份读数变了：GitHub 登录状态跟着它一起来，所以这里也顺手把登录行重画。 */
function watchServersData() {
    dataWatch(["servers"], () => {
        if (!state.ready) return;
        const payload = dataValue("servers") || {};
        if (payload.github_login_expired) {
            state.settings.github_user_id = "";
            renderGithubLogin();
        } else if (typeof payload.github_user_id === "string") {
            state.settings.github_user_id = payload.github_user_id;
            renderGithubLogin();
        }
        renderDeveloperServers();
        renderCatalog();
        // 私有包也是「重装」的来源之一（`state.packages` = catalog + servers），
        // 这份读数一变，「已安装」页的行就得重画才对得上。
        renderInstalled();
    });
}

/** 诊断那份读数变了：一次扫描会推好几次，每来一次就把报告按当前这份重画。 */
function watchDiagnosisData() {
    dataWatch(["diagnosis"], () => {
        if (!state.ready) return;
        renderDiagnosis();
    });
}

/** 把界面长期显示的那些读数全部订阅上（启动时调一次）。 */
function watchData() {
    watchInstalledData();
    watchEnvironmentData();
    watchQueueData();
    watchLoadersData();
    watchCatalogData();
    watchServersData();
    watchDiagnosisData();
}
