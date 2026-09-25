"use strict";

// 兼容性判定的取值：与索引里每个 release 的 `verdict` 字段一一对应。
const VERDICT_COMPATIBLE = "compatible";
const VERDICT_UNKNOWN = "unknown";
const VERDICT_INCOMPATIBLE = "incompatible";
// 不判这个包自己的环境（翻译包）：不标色、不隐藏，它依赖的包照常判。
const VERDICT_NOT_APPLICABLE = "not_applicable";

// 游戏那一维的轴 id（与索引里 release 的 `dependencies[].id` 一致）；其余每一轴就是它自己那个能力 id。
const SPROCKET_AXIS_ID = "hamish.sprocket";

// 这一层只做「拿判定结果做决定」：判定本身是后端按当前环境算好的，前端不重新解析版本区间。

/**
 * 兼容性那一行的轴名：逐轴结果自带 id，名字照 id 查 —— 游戏轴是固定的，
 * 其余每轴都是它自己那个能力（见 `capabilityLabel`），认不出来就写 id。
 */
function axisLabel(axisId) {
    if (axisId === SPROCKET_AXIS_ID) return "Sprocket";
    return capabilityLabel(axisId);
}

/** 包的全部可安装版本（新到旧）；索引里没带就是空数组。 */
function packageReleases(pkg) {
    return Array.isArray(pkg?.releases) ? pkg.releases : [];
}

/**
 * 包这一层的判定：界面拿它决定标什么、藏不藏。
 *
 * 有版本兼容就是兼容；一版兼容都没有、但至少判过一版不兼容才算不兼容；其余（一版都没判过）
 * 是未知 —— 未知的包照常显示，还不知道能不能用不等于不能用。
 */
function packageVerdict(pkg) {
    const releases = packageReleases(pkg);
    if (releases.some((release) => release.verdict === VERDICT_COMPATIBLE)) return VERDICT_COMPATIBLE;
    if (releases.some((release) => release.verdict === VERDICT_INCOMPATIBLE)) return VERDICT_INCOMPATIBLE;
    return pkg?.release?.verdict || releases[0]?.verdict || VERDICT_UNKNOWN;
}

/** 「点安装会装的那个版本」的判定：列表里那个版本号用它上色。 */
function targetVerdict(pkg) {
    // 包整体一个兼容版本都没有时，装哪版都一样跑不起来 —— 跟着包判，别让那一版自己的「未知」把话说软。
    if (packageVerdict(pkg) === VERDICT_INCOMPATIBLE) return VERDICT_INCOMPATIBLE;
    const target = preferredVersion(pkg);
    if (!target) return VERDICT_UNKNOWN;
    const release = packageReleases(pkg).find((entry) => entry.version === target);
    return release?.verdict || packageVerdict(pkg);
}

/** 从目录里消失的包：判过、且一个兼容版本都没有。一版都没判过的照常显示。 */
function packageHidden(pkg) {
    if (!packageReleases(pkg).length) return false;
    return packageVerdict(pkg) === VERDICT_INCOMPATIBLE;
}

/** 默认要装的版本：兼容的里面最高的；没有兼容的就最新的。 */
function preferredVersion(pkg) {
    const releases = packageReleases(pkg);
    const compatible = releases.find((release) => release.verdict === VERDICT_COMPATIBLE);
    if (compatible) return compatible.version;
    return releases.length ? releases[0].version : String(pkg?.release?.version || "");
}

function verdictClass(verdict) {
    if (verdict === VERDICT_INCOMPATIBLE) return "incompatible";
    if (verdict === VERDICT_UNKNOWN) return "unknown";
    if (verdict === VERDICT_NOT_APPLICABLE) return "";
    // 兼容也要有类名：chip 会落在带颜色的容器里（例如详情页标题行的强调色），
    // 没有自己的类就会被容器染色。
    return "compatible";
}

/** 判定对应的短标签；空串表示这个包不参与环境判定，界面上不挂 chip。 */
function verdictLabel(verdict) {
    if (verdict === VERDICT_INCOMPATIBLE) return tr("incompatibleState");
    if (verdict === VERDICT_UNKNOWN) return tr("unknownState");
    if (verdict === VERDICT_NOT_APPLICABLE) return "";
    return tr("compatibleState");
}

/** 列表行要不要挂判定 chip：只有「未知 / 不兼容」值得标出来（兼容是默认状态）。 */
function verdictNeedsChip(verdict) {
    return verdict === VERDICT_UNKNOWN || verdict === VERDICT_INCOMPATIBLE;
}

/** 按（包, 版本）反查判定：安装计划里的每一行、已安装行都要用它上色。 */
function releaseVerdict(packageId, version) {
    const pkg = (state.packages || []).find((item) => item.id === packageId);
    if (!pkg) return "";
    const release = packageReleases(pkg).find((entry) => entry.version === String(version || ""));
    return release?.verdict || "";
}

/** 某个包某个版本的兼容性来源信息（`compatibility` 字段）。 */
function releaseCompatibility(pkg, version) {
    const release = packageReleases(pkg).find((entry) => entry.version === String(version || ""));
    return release?.compatibility || null;
}

/**
 * 版本号的显示：沿用更早版本声明的版本后面缀一个 `*`。
 *
 * 星号只表示「这个版本自己没写声明，用的是更早那份」，不带别的含义 —— 用了哪份、判成什么，
 * 写在详情页最底下的兼容性细节里。
 */
function versionWithSource(pkg, version) {
    const text = String(version || "");
    if (!text || text === "-") return text;
    return releaseCompatibility(pkg, text)?.source === "inherited" ? `${text}*` : text;
}

/**
 * 「点安装实际会装哪个」：兼容的里面最高的，一个都不兼容才退到最新。
 *
 * 列表和详情页显示的版本号都是它 —— 直接写「最新版」会让人以为点下去装的就是那一版。
 * 它跟最新版不一致时前面缀一个 `↓`（详情页另外把最新版划掉）。
 */
function installTargetVersion(pkg) {
    const target = preferredVersion(pkg);
    const newest = String(pkg?.release?.version || "");
    const text = versionWithSource(pkg, target);
    return target && newest && target !== newest ? `↓ ${text}` : text;
}
