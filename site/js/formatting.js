"use strict";

function localized(value) {
    const entries = Object.entries(value || {});
    if (!entries.length) return "";
    const language = state.language.replace("_", "-").toLocaleLowerCase();
    const exact = entries.find(([tag]) => tag.toLocaleLowerCase() === language)?.[1];
    if (exact) return exact;
    const byBase = (base) => entries.find(([tag]) => tag.toLocaleLowerCase().split("-", 1)[0] === base)?.[1] || "";
    return byBase(language.split("-", 1)[0]) || byBase("en") || entries[0][1];
}

function formatTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return value;
    return date.toLocaleString(state.language === "zh" ? "zh-CN" : "en-US", {
        year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
}

function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[character]);
}

function escapeAttribute(value) {
    return escapeHtml(value);
}
