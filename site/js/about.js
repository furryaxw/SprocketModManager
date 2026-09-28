"use strict";

// About 页与目录页共用翻译表，但不加载 Registry 运行时，所以这里只做文案替换。

let aboutLanguage = readStoredLanguage();

document.addEventListener("DOMContentLoaded", () => {
    bindLanguageSwitch();
    applyAboutLanguage();
    refreshAboutIcons();
});

function readStoredLanguage() {
    try {
        const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
        if (stored === "en" || stored === "zh") return stored;
    } catch (_) {
    }
    return navigator.language.toLocaleLowerCase().startsWith("zh") ? "zh" : "en";
}

function bindLanguageSwitch() {
    document.querySelectorAll("[data-language]").forEach((button) => {
        button.addEventListener("click", () => {
            const language = button.dataset.language;
            if (language !== "en" && language !== "zh") return;
            aboutLanguage = language;
            try {
                localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
            } catch (_) {
            }
            applyAboutLanguage();
        });
    });
}

function translated(key) {
    const table = I18N[aboutLanguage] || I18N.en;
    return table[key] ?? I18N.en[key] ?? key;
}

function applyAboutLanguage() {
    document.documentElement.lang = aboutLanguage === "zh" ? "zh-CN" : "en";
    document.title = translated("aboutPageTitle");
    document.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = translated(node.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-title]").forEach((node) => {
        node.title = translated(node.dataset.i18nTitle);
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
        node.setAttribute("aria-label", translated(node.dataset.i18nAria));
    });
    // 两份 README 的隐私政策锚点不同，链接跟着界面语言走。
    document.querySelectorAll("[data-href-en][data-href-zh]").forEach((node) => {
        node.href = aboutLanguage === "zh" ? node.dataset.hrefZh : node.dataset.hrefEn;
    });
    document.querySelectorAll("[data-language]").forEach((button) => {
        button.classList.toggle("active", button.dataset.language === aboutLanguage);
        button.setAttribute("aria-pressed", String(button.dataset.language === aboutLanguage));
    });
}

function refreshAboutIcons() {
    if (window.lucide) window.lucide.createIcons({attrs: {"stroke-width": 1.8}});
}
