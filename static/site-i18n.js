// Camilius — website translation layer (vanilla JS, no framework, no build step),
// mirrors mobile/i18n.js's approach exactly (same LANGUAGES list, same t()/
// applyTranslations() mechanism, same data-i18n/data-i18n-placeholder HTML attributes).
// Client-side only: server-rendered dynamic content (flash messages like "Wrong
// username or password.", {{ business.name }}, etc.) is NOT translated by this layer,
// same accepted limitation the mobile app already has.

const LANGUAGES = [
    { code: "en", name: "English" },
    { code: "es", name: "Español" },
    { code: "pt", name: "Português" },
    { code: "fr", name: "Français" },
    { code: "de", name: "Deutsch" },
    { code: "it", name: "Italiano" },
    { code: "ru", name: "Русский" },
    { code: "sr", name: "Srpski" },
    { code: "zh", name: "中文" },
    { code: "tr", name: "Türkçe" },
];

const TRANSLATIONS = {
    en: {
        "common.conversations": "Conversations",
        "common.subscription": "Subscription",
        "common.profile": "Profile",
        "common.settings": "Settings",
        "common.logOut": "Log out",
        "common.logIn": "Log in",
        "common.termsOfService": "Terms of Service",
        "common.privacyPolicy": "Privacy Policy",

        "login.tagline": "AI reads your customers' Instagram messages and drafts replies — you just review and send.",
        "login.title": "Log in",
        "login.subtitle": "Welcome back.",
        "login.usernameLabel": "Username, email, or phone number",
        "login.passwordLabel": "Password",
        "login.noAccount": "Don't have an account?",
        "login.registerLink": "Register your business",
        "login.forgotLink": "Forgot your password?",
        "login.replayIntro": "Replay intro",
    },
};

function t(key) {
    const lang = TRANSLATIONS[currentLang] || TRANSLATIONS.en;
    return lang[key] || TRANSLATIONS.en[key] || key;
}

let currentLang = "en";

function applyTranslations() {
    document.documentElement.lang = currentLang;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = t(node.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
        node.placeholder = t(node.getAttribute("data-i18n-placeholder"));
    });
    const lang = LANGUAGES.find((l) => l.code === currentLang);
    const label = document.getElementById("site-lang-select-label");
    if (label) label.textContent = lang ? lang.name : currentLang;
}

function setLanguage(code) {
    if (!TRANSLATIONS[code]) return;
    currentLang = code;
    try { localStorage.setItem("siteLanguage", code); } catch (e) {}
    applyTranslations();
}

function initLanguage() {
    let saved = null;
    try { saved = localStorage.getItem("siteLanguage"); } catch (e) {}
    if (saved && TRANSLATIONS[saved]) {
        currentLang = saved;
    } else {
        // Best-effort default from the browser's own language, same fallback logic as
        // the mobile app - falls back to English (not Serbian, unlike mobile's phone-
        // number-country guess) since the website has no equivalent "home market" signal.
        const guess = (navigator.language || "").slice(0, 2).toLowerCase();
        if (TRANSLATIONS[guess]) currentLang = guess;
    }
    applyTranslations();
}

function setupLanguagePicker() {
    const btn = document.getElementById("site-lang-select-btn");
    const modal = document.getElementById("site-lang-modal");
    const backdrop = document.getElementById("site-lang-modal-backdrop");
    const list = document.getElementById("site-lang-modal-list");
    if (!btn || !modal || !list) return;

    const openPicker = () => {
        modal.classList.add("open");
        btn.classList.add("active");
    };
    const closePicker = () => {
        modal.classList.remove("open");
        btn.classList.remove("active");
    };

    LANGUAGES.forEach((lang) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "site-lang-option-btn";
        row.textContent = lang.name;
        row.addEventListener("click", () => {
            setLanguage(lang.code);
            closePicker();
        });
        list.appendChild(row);
    });

    btn.addEventListener("click", () => {
        if (modal.classList.contains("open")) closePicker();
        else openPicker();
    });
    backdrop.addEventListener("click", closePicker);
}

document.addEventListener("DOMContentLoaded", () => {
    setupLanguagePicker();
    initLanguage();
});

// The main nav (_nav.html) is hx-boosted - clicking Conversations/Profile/Settings/
// Subscription swaps htmx's default target (document.body's innerHTML) via AJAX instead
// of a full page load, so DOMContentLoaded (which only fires once) never re-fires for
// it. That swap replaces the picker button/modal too (they're rendered as part of every
// page's <body> via base.html, same as the content), leaving fresh DOM nodes with no
// click listeners and default English text - re-running both setup steps against
// whatever's currently in the DOM fixes both.
document.body.addEventListener("htmx:afterSwap", () => {
    setupLanguagePicker();
    applyTranslations();
});
