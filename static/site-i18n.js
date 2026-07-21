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

        "register.tagline": "Register your business in a few minutes — AI starts drafting replies to customer Instagram messages right away.",
        "register.title": "Register your business",
        "register.subtitle": "You can change all of these fields later in settings. You'll connect your Instagram account in the next step.",
        "register.accountSection": "Account",
        "register.businessName": "Business name",
        "register.businessNamePlaceholder": "e.g. Frizerski salon Stil",
        "register.username": "Username",
        "register.usernameHint": "(for dashboard login)",
        "register.password": "Password",
        "register.contactEmail": "Contact email",
        "register.contactEmailHint": "(for account recovery only - not read by the AI)",
        "register.phone": "Phone number",
        "register.phoneHint": "(optional - lets you log in with it too)",
        "register.businessDetailsSection": "Business details — the AI uses this to write replies",
        "register.hours": "Working hours",
        "register.hoursPlaceholder": "Monday: 09:00 - 20:00\nSaturday: 09:00 - 16:00\nSunday: closed",
        "register.prices": "Price list",
        "register.pricesPlaceholder": "Men's haircut: 800 din\nWomen's haircut: 1500 din",
        "register.faq": "Tell us more about your business",
        "register.faqPlaceholder": "Q: Do I need to book an appointment?\nA: Yes, we recommend booking in advance.\n\nAnything else worth knowing about your business.",
        "register.faqHint": "The more you share, the more questions our AI can answer for your customers.",
        "register.acceptPrefix": "I accept the",
        "register.acceptAnd": "and",
        "register.submit": "Register",
        "register.alreadyHaveAccount": "Already have an account?",
        "register.usernameTaken": "That username is already taken.",
        "register.emailTaken": "That email is already registered.",
        "register.phoneTaken": "That phone number is already registered.",

        "forgot.title": "Forgot password",
        "forgot.subtitle": "Enter the email or username you registered with — we'll send a password reset link to the email on file.",
        "forgot.identifierLabel": "Email or username",
        "forgot.submit": "Send reset link",
        "forgot.findByPhone": "Find by mobile number",
        "forgot.phoneSubtitle": "Enter the phone number on your account — we'll send a password reset link to the email on file.",
        "forgot.phoneLabel": "Phone number",
        "forgot.phoneSubmit": "Find account",
        "forgot.backToIdentifier": "← Use email or username instead",
        "forgot.backToLogin": "← Back to login",

        "reset.invalidTitle": "This link is no longer valid",
        "reset.invalidBody": "This password reset link has expired or has already been used (valid for 1 hour after it's sent).",
        "reset.requestNewLink": "Request a new link",
        "reset.title": "Set a new password",
        "reset.newPasswordLabel": "New password",
        "reset.submit": "Save new password",
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
