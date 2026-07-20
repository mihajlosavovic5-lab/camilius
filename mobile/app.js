// Camilius AI — mobile app shell (vanilla JS, no build step, no framework).
// Talks only to the JSON API (api.py) on the production Flask backend; renders its own
// screens instead of loading the server-rendered website.

const API_BASE = "https://app.ai-auto-reply-mail.com";
const APP_VERSION = "1.9.6"; // bumped alongside the native versionName, sent with crash reports

const el = (id) => document.getElementById(id);

// Native plugins (StatusBar, App, Haptics, Preferences, PushNotifications, Badge,
// CapacitorUpdater) are injected by Capacitor at runtime as window.Capacitor.Plugins.* -
// guarded everywhere so this file still runs unmodified in a plain desktop/mobile
// browser (e.g. during development) where window.Capacitor doesn't exist.
const isNative = !!(window.Capacitor && window.Capacitor.isNativePlatform());
const capacitorPlugins = isNative ? window.Capacitor.Plugins : null;

const state = {
    token: null,
    businessName: "",
    conversations: [],
    filter: "all",
    search: "",
    activeDetail: null,
    activeTab: "inbox",
};

const screens = {
    onboarding: el("screen-onboarding"),
    login: el("screen-login"),
    forgotPassword: el("screen-forgot-password"),
    register: el("screen-register"),
    connectInstagram: el("screen-connect-instagram"),
    inbox: el("screen-inbox"),
    detail: el("screen-detail"),
    profile: el("screen-profile"),
    settings: el("screen-settings"),
    help: el("screen-help"),
    terms: el("screen-terms"),
    privacy: el("screen-privacy"),
};
const tabbar = el("tabbar");
const offlineBanner = el("offline-banner");

function showScreen(name) {
    Object.values(screens).forEach((s) => s.classList.remove("active"));
    screens[name].classList.add("active");
}

function showTabbar(show) {
    tabbar.classList.toggle("hidden", !show);
}

// Register / Forgot password / Connect Instagram are a small, fixed, entirely linear
// flow (Login -> Register -> Connect Instagram -> Inbox, or Login -> Forgot password ->
// Login) - their slide-in transition comes purely from the default .screen CSS (same
// mechanism Detail uses), but unlike Detail they don't need real browser-history
// push/pop plumbing to get the Android back button right: a plain showScreen() plus a
// couple of explicit cases in the backButton listener below covers every case with no
// history-state bookkeeping to keep in sync (avoids ending up with stray forward-history
// entries after a reset like logout or finishing registration).

// --- Storage (native Preferences plugin when available - backed by Android's own
// SharedPreferences storage rather than the WebView's localStorage, and not wiped
// alongside browsing data - falls back to localStorage for browser-based dev/testing) ---

async function storageGet(key) {
    if (capacitorPlugins && capacitorPlugins.Preferences) {
        const { value } = await capacitorPlugins.Preferences.get({ key });
        return value;
    }
    return localStorage.getItem(key);
}
async function storageSet(key, value) {
    if (capacitorPlugins && capacitorPlugins.Preferences) {
        await capacitorPlugins.Preferences.set({ key, value });
    } else {
        localStorage.setItem(key, value);
    }
}
async function storageRemove(key) {
    if (capacitorPlugins && capacitorPlugins.Preferences) {
        await capacitorPlugins.Preferences.remove({ key });
    } else {
        localStorage.removeItem(key);
    }
}

// --- Self-hosted crash/error reporting ---

function reportClientError(message, stack, context) {
    fetch(API_BASE + "/api/client-error", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            ...(state.token ? { Authorization: "Bearer " + state.token } : {}),
        },
        body: JSON.stringify({ message: String(message || ""), stack: stack || "", context: context || "", app_version: APP_VERSION }),
    }).catch(() => {}); // a failed error report must never itself throw
}
window.addEventListener("error", (event) => {
    reportClientError(event.message, event.error && event.error.stack, "window.onerror");
});
window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    reportClientError(reason && reason.message ? reason.message : String(reason), reason && reason.stack, "unhandledrejection");
});

// --- Native setup (status bar, back button, OTA + native update checks) ---

// Terms/Privacy are reachable from two places (Register, and Settings) - back should
// return to whichever one opened them, not always the same place.
let legalReturnTo = "settings";

if (capacitorPlugins) {
    capacitorPlugins.StatusBar.setBackgroundColor({ color: "#d69410" });
    capacitorPlugins.StatusBar.setStyle({ style: "LIGHT" });

    capacitorPlugins.App.addListener("backButton", ({ canGoBack }) => {
        if (screens.detail.classList.contains("active")) {
            history.back();
        } else if (screens.register.classList.contains("active") || screens.forgotPassword.classList.contains("active")) {
            showScreen("login");
        } else if (screens.connectInstagram.classList.contains("active")) {
            // Deliberately swallowed - the only ways out are finishing the connection or
            // the explicit "Log out" link. Falling through to canGoBack/exitApp here would
            // let one back-press quit the app mid-setup, right after creating an account.
        } else if (screens.terms.classList.contains("active") || screens.privacy.classList.contains("active")) {
            showScreen(legalReturnTo);
        } else if (screens.help.classList.contains("active")) {
            showScreen("settings");
        } else if (canGoBack) {
            history.back();
        } else {
            capacitorPlugins.App.exitApp();
        }
    });

    // Self-hosted OTA updates: the app ships with the mobile/ shell baked in at build
    // time, but small changes to it can be pushed live afterward without a new signed
    // APK - see api.py's /api/mobile-manifest and build_mobile_bundle.py.
    //
    // Updates surface as a visible bottom banner the user taps to install, instead of
    // applying silently in the background - the user's explicit preference after the
    // silent flow repeatedly appeared unreliable (no way to tell "no update" apart from
    // "update silently failed"). Tapping Install calls set(), which applies the new
    // bundle and reloads immediately - expected and fine, since the user initiated it.
    if (capacitorPlugins.CapacitorUpdater) {
        capacitorPlugins.CapacitorUpdater.notifyAppReady().catch(() => {});

        capacitorPlugins.CapacitorUpdater.getFailedUpdate().then((failed) => {
            if (failed) reportClientError("native rollback detected: " + JSON.stringify(failed), "", "capacitor-updater-failed-update");
        }).catch(() => {});

        let otaCheckInProgress = false;
        let pendingOtaBundle = null;
        const otaBanner = el("ota-banner");

        const findDownloadedBundle = async (version) => {
            const result = await capacitorPlugins.CapacitorUpdater.list();
            const bundles = (result && result.bundles) || [];
            return bundles.find((b) => b.version === version) || null;
        };

        const checkForOtaUpdate = async () => {
            if (otaCheckInProgress) return;
            if (pendingOtaBundle) {
                // Already downloaded earlier this session - just re-show the banner
                // instead of re-fetching. There's no dismiss button (updates are
                // mandatory), but re-entering the app re-runs this check regardless.
                otaBanner.classList.remove("hidden");
                return;
            }
            otaCheckInProgress = true;
            let step = "fetch-manifest";
            try {
                const response = await fetch(API_BASE + "/api/mobile-manifest");
                const manifest = await response.json();
                if (!manifest.available) return;

                // Gate on the plugin's OWN record of the active bundle (current()), not
                // anything we store ourselves - a self-kept flag drifted from native
                // reality once before ("applied" recorded while builtin still ran).
                step = "current";
                const current = await capacitorPlugins.CapacitorUpdater.current();
                const activeVersion = (current && current.bundle) ? current.bundle.version : "unknown";
                if (activeVersion === manifest.version) return;

                step = "download";
                let bundle;
                try {
                    bundle = await capacitorPlugins.CapacitorUpdater.download({
                        url: manifest.url,
                        version: manifest.version,
                    });
                } catch (downloadError) {
                    // Most likely "already downloaded" from a previous session where the
                    // user never tapped Install - reuse the stored copy instead of failing.
                    bundle = await findDownloadedBundle(manifest.version);
                    if (!bundle) throw downloadError;
                }

                pendingOtaBundle = bundle;
                otaBanner.classList.remove("hidden");
            } catch (e) {
                reportClientError(`failed at step=${step}: ${e.message}`, e.stack, "ota-update-check");
            } finally {
                otaCheckInProgress = false;
            }
        };

        el("ota-install-btn").addEventListener("click", async () => {
            if (!pendingOtaBundle) return;
            try {
                await capacitorPlugins.CapacitorUpdater.set(pendingOtaBundle);
                // set() reloads the app immediately - nothing below this line runs.
            } catch (e) {
                reportClientError("ota install failed: " + e.message, e.stack, "ota-apply");
                otaBanner.classList.add("hidden");
                pendingOtaBundle = null;
            }
        });

        // Check at cold start AND every time the app returns to the foreground - "every
        // time I enter the app", not just full restarts.
        checkForOtaUpdate();
        capacitorPlugins.App.addListener("appStateChange", ({ isActive }) => {
            if (isActive) checkForOtaUpdate();
        });
    }

    // Native-code changes (new plugin, permissions, icon, splash, anything under
    // android/) can't ship through the OTA check above - they need a new signed APK
    // install. This offers that in-app instead of requiring the user be sent a link
    // manually. Android still shows its own "Install unknown app?" confirmation before
    // installing - that step is an OS security measure and can't be skipped from here.
    (async () => {
        try {
            const response = await fetch(API_BASE + "/api/apk-version");
            const info = await response.json();
            if (info.version === APP_VERSION) return;

            const dismissedVersion = await storageGet("apkUpdateDismissedVersion");
            if (dismissedVersion === info.version) return;

            const banner = el("update-banner");
            banner.classList.remove("hidden");
            el("update-download-btn").addEventListener("click", async () => {
                if (capacitorPlugins.Browser) {
                    await capacitorPlugins.Browser.open({ url: info.url });
                } else {
                    window.open(info.url, "_blank");
                }
            });
            el("update-dismiss-btn").addEventListener("click", async () => {
                await storageSet("apkUpdateDismissedVersion", info.version);
                banner.classList.add("hidden");
            });
        } catch (e) {
            reportClientError(e.message, e.stack, "apk-update-check");
        }
    })();
}

// --- Working hours / price list pickers (Create Account and Profile both use these -
// a real "pick from options" UI instead of a freeform textarea, serializing to/from the
// same plain-text format the backend/AI already expect, so nothing else has to change) ---

const HOURS_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const HOURS_DAY_LABELS_EN = {
    monday: "Monday", tuesday: "Tuesday", wednesday: "Wednesday", thursday: "Thursday",
    friday: "Friday", saturday: "Saturday", sunday: "Sunday",
};

function buildHoursTimeOptions() {
    const options = [];
    for (let h = 0; h < 24; h++) {
        for (let m = 0; m < 60; m += 30) {
            options.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
        }
    }
    return options;
}
const HOURS_TIME_OPTIONS = buildHoursTimeOptions();

function createHoursPicker(containerId) {
    const container = el(containerId);
    const rows = {};

    HOURS_DAYS.forEach((day) => {
        const row = document.createElement("div");
        row.className = "hours-day-row";

        const header = document.createElement("div");
        header.className = "hours-day-header";

        const label = document.createElement("span");
        label.className = "hours-day-name";
        label.dataset.i18n = `days.${day}`;
        label.textContent = HOURS_DAY_LABELS_EN[day];

        const toggleLabel = document.createElement("label");
        toggleLabel.className = "hours-day-toggle";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = true;
        const track = document.createElement("span");
        track.className = "hours-day-toggle-track";
        const thumb = document.createElement("span");
        thumb.className = "hours-day-toggle-thumb";
        track.appendChild(thumb);
        toggleLabel.appendChild(checkbox);
        toggleLabel.appendChild(track);

        header.appendChild(label);
        header.appendChild(toggleLabel);

        const times = document.createElement("div");
        times.className = "hours-day-times";
        const openSelect = document.createElement("select");
        openSelect.className = "hours-time-select";
        const closeSelect = document.createElement("select");
        closeSelect.className = "hours-time-select";
        HOURS_TIME_OPTIONS.forEach((time) => {
            const openOption = document.createElement("option");
            openOption.value = time;
            openOption.textContent = time;
            openSelect.appendChild(openOption);
            const closeOption = document.createElement("option");
            closeOption.value = time;
            closeOption.textContent = time;
            closeSelect.appendChild(closeOption);
        });
        openSelect.value = "09:00";
        closeSelect.value = "18:00";
        const sep = document.createElement("span");
        sep.className = "hours-time-sep";
        sep.textContent = "–";
        times.appendChild(openSelect);
        times.appendChild(sep);
        times.appendChild(closeSelect);

        checkbox.addEventListener("change", () => {
            times.classList.toggle("hidden", !checkbox.checked);
        });

        row.appendChild(header);
        row.appendChild(times);
        container.appendChild(row);

        rows[day] = { checkbox, openSelect, closeSelect, times };
    });

    return {
        getValue() {
            return HOURS_DAYS.map((day) => {
                const r = rows[day];
                if (!r.checkbox.checked) return `${HOURS_DAY_LABELS_EN[day]}: closed`;
                return `${HOURS_DAY_LABELS_EN[day]}: ${r.openSelect.value}-${r.closeSelect.value}`;
            }).join("\n");
        },
        // Best-effort parse of our own generated format (or something close enough to
        // it) back into picker state - used when loading a business's existing hours
        // into the Profile screen. Leaves a day's defaults alone if its line isn't found
        // or doesn't parse, rather than guessing.
        setValue(text) {
            const lines = (text || "").split("\n");
            HOURS_DAYS.forEach((day) => {
                const dayLabel = HOURS_DAY_LABELS_EN[day].toLowerCase();
                const line = lines.find((l) => l.trim().toLowerCase().startsWith(dayLabel));
                if (!line) return;
                const r = rows[day];
                if (/closed/i.test(line)) {
                    r.checkbox.checked = false;
                    r.times.classList.add("hidden");
                    return;
                }
                const match = line.match(/(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/);
                if (!match) return;
                const openTime = `${match[1].padStart(2, "0")}:${match[2]}`;
                const closeTime = `${match[3].padStart(2, "0")}:${match[4]}`;
                if (HOURS_TIME_OPTIONS.includes(openTime)) r.openSelect.value = openTime;
                if (HOURS_TIME_OPTIONS.includes(closeTime)) r.closeSelect.value = closeTime;
                r.checkbox.checked = true;
                r.times.classList.remove("hidden");
            });
        },
        // Restores every day to the default open 09:00-18:00 - NOT the same as
        // setValue("") (a no-op, since an empty string matches no day's line). Used when
        // opening a fresh Create Account form, since a plain form.reset() would instead
        // revert these dynamically-created <select>/<input> elements to the browser's
        // own default (first option / unchecked), not our intended defaults, because
        // they were never given selected/checked HTML attributes at creation time.
        reset() {
            HOURS_DAYS.forEach((day) => {
                const r = rows[day];
                r.checkbox.checked = true;
                r.openSelect.value = "09:00";
                r.closeSelect.value = "18:00";
                r.times.classList.remove("hidden");
            });
        },
    };
}

function createPricePicker(rowsContainerId, addButtonId) {
    const rowsContainer = el(rowsContainerId);

    function addRow(service = "", price = "") {
        const row = document.createElement("div");
        row.className = "price-row";

        const serviceInput = document.createElement("input");
        serviceInput.type = "text";
        serviceInput.className = "price-service-input";
        serviceInput.dataset.i18nPlaceholder = "pricePicker.servicePlaceholder";
        serviceInput.placeholder = t("pricePicker.servicePlaceholder");
        serviceInput.value = service;

        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.className = "price-amount-input";
        priceInput.dataset.i18nPlaceholder = "pricePicker.pricePlaceholder";
        priceInput.placeholder = t("pricePicker.pricePlaceholder");
        priceInput.value = price;

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "price-remove-btn";
        removeBtn.setAttribute("aria-label", "Remove");
        removeBtn.textContent = "×";
        removeBtn.addEventListener("click", () => row.remove());

        row.appendChild(serviceInput);
        row.appendChild(priceInput);
        row.appendChild(removeBtn);
        rowsContainer.appendChild(row);
    }

    el(addButtonId).addEventListener("click", () => addRow());

    return {
        getValue() {
            return Array.from(rowsContainer.querySelectorAll(".price-row"))
                .map((row) => {
                    const service = row.querySelector(".price-service-input").value.trim();
                    const price = row.querySelector(".price-amount-input").value.trim();
                    return service && price ? `${service}: ${price}` : null;
                })
                .filter(Boolean)
                .join("\n");
        },
        setValue(text) {
            rowsContainer.innerHTML = "";
            const lines = (text || "").split("\n").map((l) => l.trim()).filter(Boolean);
            if (lines.length === 0) {
                addRow();
                return;
            }
            lines.forEach((line) => {
                const idx = line.indexOf(":");
                if (idx === -1) {
                    addRow(line, "");
                } else {
                    addRow(line.slice(0, idx).trim(), line.slice(idx + 1).trim());
                }
            });
        },
    };
}

const registerHoursPicker = createHoursPicker("register-hours-picker");
const registerPricesPicker = createPricePicker("register-prices-rows", "register-prices-add");
registerPricesPicker.setValue("");
const profileHoursPicker = createHoursPicker("settings-hours-picker");
const profilePricesPicker = createPricePicker("settings-prices-rows", "settings-prices-add");
profilePricesPicker.setValue("");

// --- API helper ---

async function api(path, { method = "GET", body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;

    let response;
    try {
        response = await fetch(API_BASE + path, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
        });
    } catch (networkError) {
        const err = new Error(t("network.error"));
        err.isNetworkError = true;
        throw err;
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const err = new Error(data.error || "Request failed");
        err.status = response.status;
        throw err;
    }
    return data;
}

// --- Offline detection ---

function updateOfflineBanner() {
    offlineBanner.classList.toggle("hidden", navigator.onLine);
}
window.addEventListener("online", updateOfflineBanner);
window.addEventListener("offline", updateOfflineBanner);

// --- Auth ---

async function logout() {
    await storageRemove("token");
    await storageRemove("businessName");
    state.token = null;
    state.conversations = [];
    showTabbar(false);
    if (capacitorPlugins && capacitorPlugins.Badge) capacitorPlugins.Badge.clear().catch(() => {});
    showScreen("login");
}

el("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = el("login-username").value.trim();
    const password = el("login-password").value;
    const errorBox = el("login-error");
    const submitBtn = el("login-submit");

    errorBox.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.textContent = t("login.submitLoading");

    try {
        const data = await api("/api/login", { method: "POST", body: { username, password } });
        state.token = data.token;
        state.businessName = data.business_name;
        await storageSet("token", data.token);
        await storageSet("businessName", data.business_name);
        enterApp();
    } catch (err) {
        errorBox.textContent = err.message || t("login.error");
        errorBox.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = t("login.submit");
    }
});

// --- Forgot password (its own native screen, pushed on top of Login - no browser
// involved at all, unlike the old flow which opened the website's page in a browser tab) ---

el("login-forgot-link").addEventListener("click", () => {
    el("forgot-form").reset();
    el("forgot-form").classList.remove("hidden");
    el("forgot-error").classList.add("hidden");
    el("forgot-success").classList.add("hidden");
    showScreen("forgotPassword");
});

el("forgot-back").addEventListener("click", () => showScreen("login"));

el("forgot-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = el("forgot-email").value.trim();
    const errorBox = el("forgot-error");
    const successBox = el("forgot-success");
    const submitBtn = el("forgot-submit");

    errorBox.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.textContent = t("forgot.submitLoading");

    try {
        await api("/api/forgot-password", { method: "POST", body: { email } });
        successBox.classList.remove("hidden");
        el("forgot-form").classList.add("hidden");
    } catch (err) {
        errorBox.textContent = err.message || t("forgot.error");
        errorBox.classList.remove("hidden");
        submitBtn.disabled = false;
        submitBtn.textContent = t("forgot.submit");
    }
});

// --- Create account (its own native screen - full registration form, pushed on top of
// Login. Terms of Service/Privacy Policy are native screens too - see showLegalScreen
// below. On success, replaces itself with the Connect Instagram screen (not pushed - the
// account already exists at that point, so the back button shouldn't return here). ---

el("login-signup-link").addEventListener("click", () => {
    // Deliberately NOT register-form.reset() - the working-hours/price-list pickers
    // contain dynamically-created <select>/checkbox elements with no selected/checked
    // HTML attribute set, so a native form reset would revert them to the browser's own
    // default (first option / unchecked) instead of our intended defaults. Clear the
    // plain fields by hand and use each picker's own .reset()/.setValue("") instead.
    ["register-name", "register-username", "register-password", "register-contact-email", "register-phone", "register-faq"].forEach((id) => {
        el(id).value = "";
    });
    el("register-terms").checked = false;
    registerHoursPicker.reset();
    registerPricesPicker.setValue("");
    el("register-error").classList.add("hidden");
    showScreen("register");
});

el("register-back").addEventListener("click", () => showScreen("login"));

// Terms/Privacy are native screens now (not a browser tab) - reachable from here and
// from Settings, so back needs to know which one to return to (see legalReturnTo above).
function showLegalScreen(name, returnTo) {
    legalReturnTo = returnTo;
    showScreen(name);
}
el("register-view-terms").addEventListener("click", () => showLegalScreen("terms", "register"));
el("register-view-privacy").addEventListener("click", () => showLegalScreen("privacy", "register"));
el("terms-back").addEventListener("click", () => showScreen(legalReturnTo));
el("privacy-back").addEventListener("click", () => showScreen(legalReturnTo));

el("register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const errorBox = el("register-error");
    const submitBtn = el("register-submit");

    errorBox.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.textContent = t("register.submitLoading");

    try {
        const data = await api("/api/register", {
            method: "POST",
            body: {
                name: el("register-name").value.trim(),
                username: el("register-username").value.trim(),
                password: el("register-password").value,
                contact_email: el("register-contact-email").value.trim(),
                phone_number: el("register-phone").value.trim(),
                working_hours: registerHoursPicker.getValue(),
                price_list: registerPricesPicker.getValue(),
                faq: el("register-faq").value,
                accept_terms: el("register-terms").checked,
            },
        });
        state.token = data.token;
        state.businessName = data.business_name;
        await storageSet("token", data.token);
        await storageSet("businessName", data.business_name);
        el("connect-error").classList.add("hidden");
        el("connect-waiting").classList.add("hidden");
        showScreen("connectInstagram");
    } catch (err) {
        errorBox.textContent = err.message || t("register.error");
        errorBox.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = t("register.submit");
    }
});

// --- Connect Instagram - the one unavoidable step that has to leave native UI, since
// Meta's own OAuth consent screen must run in a real browser context (no way around
// that for any app, native or not). Opened as an in-app browser tab so it still feels
// like part of the app rather than switching to a separate browser app. Completion is
// detected via the Browser plugin's own "browserFinished" event (fires when the user
// closes the tab, whether by finishing the OAuth flow or bailing out) rather than
// requiring the user to tell us - we just check /api/settings for the now-connected
// Instagram username right after. ---

let awaitingInstagramConnect = false;

el("connect-instagram-btn").addEventListener("click", async () => {
    el("connect-error").classList.add("hidden");
    try {
        const data = await api("/api/instagram-connect-url");
        if (capacitorPlugins && capacitorPlugins.Browser) {
            awaitingInstagramConnect = true;
            await capacitorPlugins.Browser.open({ url: data.url });
        } else {
            window.open(data.url, "_blank");
        }
    } catch (err) {
        reportClientError(err.message, err.stack, "instagram-connect-url");
        el("connect-error").textContent = err.message || t("connect.notYetBody");
        el("connect-error").classList.remove("hidden");
    }
});

if (capacitorPlugins && capacitorPlugins.Browser) {
    capacitorPlugins.Browser.addListener("browserFinished", async () => {
        if (!awaitingInstagramConnect) return;
        awaitingInstagramConnect = false;
        if (!screens.connectInstagram.classList.contains("active")) return;

        el("connect-waiting").classList.remove("hidden");
        try {
            const data = await api("/api/settings");
            if (data.instagram_username) {
                if (capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
                enterApp();
            } else {
                el("connect-error").textContent = t("connect.notYetBody");
                el("connect-error").classList.remove("hidden");
            }
        } catch (err) {
            if (err.status === 401) { logout(); return; }
            el("connect-error").textContent = err.message || t("connect.notYetBody");
            el("connect-error").classList.remove("hidden");
        } finally {
            el("connect-waiting").classList.add("hidden");
        }
    });
}

el("connect-logout").addEventListener("click", logout);

function enterApp() {
    showTabbar(true);
    setActiveTab("inbox");
    loadDashboard();
    loadSettingsIntoForm();
    startAutoRefresh();
}

// --- Push notifications ---
// Only ever called once we've confirmed (via /api/dashboard's push_enabled flag) that a
// Firebase project is actually configured server-side - avoids calling into the
// Firebase-backed native registration code at all while it isn't, so there's nothing to
// go wrong before that's set up.
let pushRegistrationAttempted = false;

function registerForPushNotifications() {
    if (pushRegistrationAttempted) return;
    pushRegistrationAttempted = true;
    if (!capacitorPlugins || !capacitorPlugins.PushNotifications) return;

    capacitorPlugins.PushNotifications.requestPermissions().then((result) => {
        if (result.receive !== "granted") return;
        capacitorPlugins.PushNotifications.register();
    });

    capacitorPlugins.PushNotifications.addListener("registration", (token) => {
        api("/api/push-token", { method: "POST", body: { token: token.value } }).catch((e) => {
            reportClientError(e.message, e.stack, "push-token-register");
        });
    });

    capacitorPlugins.PushNotifications.addListener("registrationError", (error) => {
        reportClientError("Push registration error", JSON.stringify(error), "push-registration-error");
    });

    // Tapping a notification: refresh the inbox and, if we can find the conversation
    // it was about, jump straight to its detail screen instead of just the inbox list.
    capacitorPlugins.PushNotifications.addListener("pushNotificationActionPerformed", async (action) => {
        const conversationId = action.notification.data && action.notification.data.conversation_id;
        setActiveTab("inbox");
        await loadDashboard();
        if (conversationId) {
            const match = state.conversations.find((c) => String(c.id) === String(conversationId));
            if (match) openDetail(match);
        }
    });
}

// --- Auto-refresh (only while the app is in the foreground, on the inbox tab) ---

let autoRefreshTimer = null;

function startAutoRefresh() {
    if (autoRefreshTimer) return;
    autoRefreshTimer = setInterval(() => {
        if (document.visibilityState === "visible" && state.activeTab === "inbox" && state.token) {
            loadDashboard({ silent: true });
        }
    }, 45000);
}

// --- Inbox ---

// Locale tags for toLocale*String - keyed to our own language picker rather than
// navigator.language, so dates/times follow the language the user actually chose in
// the app instead of the device's (which can disagree, e.g. a phone set to English
// with the app switched to Serbian).
const LOCALE_TAGS = {
    en: "en-US", sr: "sr-RS", es: "es-ES", pt: "pt-PT", fr: "fr-FR",
    de: "de-DE", it: "it-IT", ru: "ru-RU", zh: "zh-CN", tr: "tr-TR",
};

function timeAgo(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    const locale = LOCALE_TAGS[currentLang] || "en-US";
    return date.toLocaleDateString(locale, { month: "short", day: "numeric" }) + " · " +
        date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

function renderSkeletons() {
    const list = el("inbox-list");
    let html = "";
    for (let i = 0; i < 5; i++) {
        html += `
            <div class="skeleton-row">
                <span class="skeleton skeleton-avatar"></span>
                <span class="skeleton-lines">
                    <span class="skeleton skeleton-line" style="width: 40%;"></span>
                    <span class="skeleton skeleton-line" style="width: 80%;"></span>
                </span>
            </div>`;
    }
    list.innerHTML = html;
}

async function loadDashboard({ silent = false } = {}) {
    if (!silent) {
        renderSkeletons();
        el("inbox-empty").classList.add("hidden");
        el("inbox-error").classList.add("hidden");
    }
    try {
        const data = await api("/api/dashboard?filter=" + state.filter);
        state.conversations = data.conversations;

        el("inbox-business-name").textContent = data.business_name;
        el("quota-today").textContent = data.today_processed;
        el("quota-limit").textContent = data.daily_limit;
        el("quota-fill").style.width = Math.min(100, Math.round((data.today_processed * 100) / data.daily_limit)) + "%";

        el("count-all").textContent = data.total_count;
        el("count-pending").textContent = data.pending_count;
        el("count-sent").textContent = data.sent_count;

        el("banner-inactive").classList.toggle("hidden", data.beta_free_mode || data.subscription_active);

        renderInboxList(filteredConversations());

        if (capacitorPlugins && capacitorPlugins.Badge) {
            if (data.pending_count > 0) capacitorPlugins.Badge.set({ count: data.pending_count }).catch(() => {});
            else capacitorPlugins.Badge.clear().catch(() => {});
        }

        if (data.push_enabled) registerForPushNotifications();

        el("inbox-error").classList.add("hidden");
    } catch (err) {
        if (err.status === 401) return logout();
        if (!silent) {
            el("inbox-list").innerHTML = "";
            el("inbox-error-message").textContent = err.isNetworkError ? err.message : t("inbox.errorGeneric");
            el("inbox-error").classList.remove("hidden");
        }
    }
}

function filteredConversations() {
    if (!state.search.trim()) return state.conversations;
    const q = state.search.trim().toLowerCase();
    return state.conversations.filter((c) =>
        c.sender.toLowerCase().includes(q) || c.question.toLowerCase().includes(q)
    );
}

function renderInboxList(conversations) {
    const list = el("inbox-list");
    list.innerHTML = "";

    if (conversations.length === 0) {
        el("inbox-empty").classList.remove("hidden");
        el("inbox-empty-title").textContent = state.search.trim() ? t("inbox.emptySearchTitle") : t("inbox.emptyTitle");
        el("inbox-empty-detail").textContent = state.search.trim() ? t("inbox.emptySearchDetail") : t("inbox.emptyDetail");
        return;
    }
    el("inbox-empty").classList.add("hidden");

    conversations.forEach((c) => {
        const row = document.createElement("button");
        row.className = "inbox-row";
        row.innerHTML = `
            <span class="inbox-avatar">${(c.sender[0] || "?").toUpperCase()}</span>
            <span class="inbox-row-main">
                <span class="inbox-row-top">
                    <span class="inbox-sender">${escapeHtml(c.sender)}</span>
                    <span class="inbox-time">${timeAgo(c.created_at)}</span>
                </span>
                <span class="inbox-preview">${escapeHtml(c.question)}</span>
            </span>
            <span class="inbox-dot ${c.status === "sent" ? "sent" : ""}"></span>
        `;
        row.addEventListener("click", () => openDetail(c));
        list.appendChild(row);
    });
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
}

document.querySelectorAll(".filter-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".filter-pill").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.filter = btn.dataset.filter;
        loadDashboard();
    });
});

el("inbox-search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderInboxList(filteredConversations());
});

el("inbox-retry").addEventListener("click", () => loadDashboard());

// --- Pull-to-refresh ---

(function setupPullToRefresh() {
    const list = el("inbox-list");
    const indicator = el("pull-indicator");
    const THRESHOLD = 70;
    let startY = null;
    let pulling = false;

    list.addEventListener("touchstart", (e) => {
        if (list.scrollTop <= 0) {
            startY = e.touches[0].clientY;
            pulling = true;
        }
    }, { passive: true });

    list.addEventListener("touchmove", (e) => {
        if (!pulling || startY === null) return;
        const delta = e.touches[0].clientY - startY;
        if (delta > 0 && list.scrollTop <= 0) {
            const capped = Math.min(delta * 0.5, 90);
            indicator.style.height = capped + "px";
            indicator.style.opacity = Math.min(capped / THRESHOLD, 1);
            indicator.classList.toggle("pull-ready", capped >= THRESHOLD * 0.6);
        }
    }, { passive: true });

    list.addEventListener("touchend", () => {
        if (!pulling) return;
        const height = parseFloat(indicator.style.height) || 0;
        indicator.style.height = "0px";
        indicator.style.opacity = "0";
        indicator.classList.remove("pull-ready");
        if (height >= THRESHOLD * 0.6) {
            indicator.classList.add("pull-spinning");
            loadDashboard().finally(() => indicator.classList.remove("pull-spinning"));
        }
        startY = null;
        pulling = false;
    });
})();

// --- Conversation detail (its own screen, not inline in the list) ---

function openDetail(conversation) {
    state.activeDetail = conversation;

    el("detail-sender").textContent = conversation.sender;
    el("detail-time").textContent = timeAgo(conversation.created_at);
    el("detail-question").textContent = conversation.question;
    el("detail-error").classList.add("hidden");

    const badge = el("detail-badge");
    const isPending = conversation.status === "pending";
    badge.textContent = isPending ? t("detail.badgePending") : t("detail.badgeSent");
    badge.className = "badge " + (isPending ? "badge-pending" : "badge-sent");

    el("detail-pending-block").classList.toggle("hidden", !isPending);
    el("detail-sent-block").classList.toggle("hidden", isPending);

    if (isPending) {
        el("detail-reply").value = conversation.suggested_reply;
    } else {
        el("detail-final-reply").textContent = conversation.final_reply;
    }

    if (!screens.detail.classList.contains("active")) {
        history.pushState({ view: "detail" }, "");
    }
    showScreen("detail");
}

function closeDetail() {
    state.activeDetail = null;
    showScreen("inbox");
}

el("detail-back").addEventListener("click", () => {
    history.back();
});

window.addEventListener("popstate", () => {
    if (!screens.detail.classList.contains("active")) return;
    closeDetail();
});

el("detail-send").addEventListener("click", async () => {
    const conversation = state.activeDetail;
    if (!conversation) return;

    const button = el("detail-send");
    const errorBox = el("detail-error");
    const reply = el("detail-reply").value;

    errorBox.classList.add("hidden");
    button.disabled = true;
    button.textContent = t("detail.sendLoading");

    try {
        await api(`/api/conversations/${conversation.id}/send`, { method: "POST", body: { reply } });
        conversation.status = "sent";
        conversation.final_reply = reply;
        if (capacitorPlugins) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        openDetail(conversation);
        loadDashboard();
    } catch (err) {
        errorBox.textContent = err.message || t("detail.error");
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = t("detail.send");
    }
});

// --- Settings ---

async function loadSettingsIntoForm() {
    try {
        const data = await api("/api/settings");
        el("settings-name").value = data.name;
        el("settings-instagram-account").value = data.instagram_username ? "@" + data.instagram_username : "";
        profileHoursPicker.setValue(data.working_hours);
        profilePricesPicker.setValue(data.price_list);
        el("settings-faq").value = data.faq;
    } catch (err) {
        if (err.status === 401) logout();
    }
}

el("settings-save").addEventListener("click", async () => {
    const button = el("settings-save");
    const messageBox = el("settings-message");
    const errorBox = el("settings-error");
    messageBox.classList.add("hidden");
    errorBox.classList.add("hidden");
    button.disabled = true;
    button.textContent = t("settings.saveLoading");

    try {
        await api("/api/settings", {
            method: "POST",
            body: {
                name: el("settings-name").value,
                working_hours: profileHoursPicker.getValue(),
                price_list: profilePricesPicker.getValue(),
                faq: el("settings-faq").value,
            },
        });
        messageBox.textContent = t("settings.saved");
        messageBox.classList.remove("hidden");
        el("inbox-business-name").textContent = el("settings-name").value;
    } catch (err) {
        errorBox.textContent = err.message || t("settings.error");
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = t("settings.save");
    }
});

// Export my data (GDPR-style self-serve export) - mirrors the APK-update-download
// pattern: fetch a short-lived signed link with the Bearer token, then hand the actual
// download off to the in-app browser (which can't carry that header itself, hence the
// link-based bridge - see api.py's /api/export-data-link).
el("settings-export").addEventListener("click", async () => {
    const button = el("settings-export");
    const errorBox = el("settings-app-error");
    errorBox.classList.add("hidden");
    button.disabled = true;
    button.textContent = t("settings.exportLoading");

    try {
        const data = await api("/api/export-data-link");
        if (capacitorPlugins && capacitorPlugins.Browser) {
            await capacitorPlugins.Browser.open({ url: data.url });
        } else {
            window.open(data.url, "_blank");
        }
    } catch (err) {
        if (err.status === 401) { logout(); return; }
        errorBox.textContent = err.message || t("settings.exportError");
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = t("settings.export");
    }
});

el("settings-delete-account").addEventListener("click", async () => {
    if (!window.confirm(t("appSettings.deleteConfirm"))) return;
    const errorBox = el("settings-app-error");
    errorBox.classList.add("hidden");
    try {
        await api("/api/delete-account", { method: "POST" });
        await logout();
    } catch (err) {
        errorBox.textContent = err.message || t("appSettings.deleteError");
        errorBox.classList.remove("hidden");
    }
});

el("settings-logout").addEventListener("click", logout);

// --- Settings: Terms/Privacy/Help rows, and the Help screen itself ---

el("settings-terms-row").addEventListener("click", () => showLegalScreen("terms", "settings"));
el("settings-privacy-row").addEventListener("click", () => showLegalScreen("privacy", "settings"));
el("settings-help-row").addEventListener("click", () => showScreen("help"));
el("help-back").addEventListener("click", () => showScreen("settings"));

const SUPPORT_EMAIL = "mihajlosavovic75757@gmail.com";

el("help-email-btn").addEventListener("click", () => {
    // Plain mailto navigation - works when the device has a mail app configured (most
    // do). "Copy email address" right below is the guaranteed fallback for when it isn't.
    window.open(`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent("Camilius AI support")}`, "_system");
});

el("help-copy-btn").addEventListener("click", async () => {
    try {
        await navigator.clipboard.writeText(SUPPORT_EMAIL);
    } catch (e) {
        // Clipboard API can be unavailable in some WebView configs - a manual
        // select-all-and-copy fallback via a temporary input.
        const temp = document.createElement("input");
        temp.value = SUPPORT_EMAIL;
        document.body.appendChild(temp);
        temp.select();
        document.execCommand("copy");
        document.body.removeChild(temp);
    }
    const confirmation = el("help-copy-confirmation");
    confirmation.classList.remove("hidden");
    setTimeout(() => confirmation.classList.add("hidden"), 2500);
    if (capacitorPlugins && capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
});

// --- Tab bar ---

function setActiveTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    showScreen(tab);
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
});

// --- Onboarding (shown once, only inside the installed app) ---

function setupOnboarding() {
    const slides = el("onboarding-slides");
    const dots = document.querySelectorAll("#onboarding-dots .onboarding-dot");
    slides.addEventListener("scroll", () => {
        const index = Math.round(slides.scrollLeft / slides.clientWidth);
        dots.forEach((dot, i) => dot.classList.toggle("active", i === index));
    });
    document.querySelectorAll(".onboarding-continue, .onboarding-skip").forEach((btn) => {
        btn.addEventListener("click", async () => {
            await storageSet("onboardingSeen", "1");
            showScreen("login");
        });
    });
}
setupOnboarding();
setupLanguagePicker();

// --- Theme (System default via prefers-color-scheme, or an explicit Light/Dark
// override chosen in Settings and remembered from then on) ---

function applyTheme(theme) {
    if (theme === "light" || theme === "dark") {
        document.documentElement.setAttribute("data-theme", theme);
    } else {
        document.documentElement.removeAttribute("data-theme");
    }
    document.querySelectorAll(".theme-option").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.themeChoice === theme);
    });
}

async function setTheme(theme) {
    await storageSet("theme", theme);
    applyTheme(theme);
}

document.querySelectorAll(".theme-option").forEach((btn) => {
    btn.addEventListener("click", () => setTheme(btn.dataset.themeChoice));
});

el("settings-app-version").textContent = `Camilius AI · v${APP_VERSION}`;

// --- Boot ---

updateOfflineBanner();

(async () => {
    const [token, businessName, onboardingSeen, theme] = await Promise.all([
        storageGet("token"),
        storageGet("businessName"),
        storageGet("onboardingSeen"),
        storageGet("theme"),
    ]);
    await initLanguage();
    applyTheme(theme || "system");

    state.token = token || null;
    state.businessName = businessName || "";

    if (state.token) {
        enterApp();
    } else if (isNative && !onboardingSeen) {
        showScreen("onboarding");
    } else {
        showScreen("login");
    }
})().catch((e) => reportClientError(e.message, e.stack, "boot"));
