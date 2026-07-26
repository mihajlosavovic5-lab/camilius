// Camilius — mobile app shell (vanilla JS, no build step, no framework).
// Talks only to the JSON API (api.py) on the production Flask backend; renders its own
// screens instead of loading the server-rendered website.

const API_BASE = "https://app.ai-auto-reply-mail.com";
const APP_VERSION = "1.9.7"; // bumped alongside the native versionName, sent with crash reports

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
    pendingVerifyBusinessId: null,
    pendingVerifyEmail: "",
    conversations: [],
    filter: "all",
    search: "",
    activeDetail: null,
    activeTab: "inbox",
    betaFreeMode: false,
    subscriptionActive: false,
    subscriptionStatus: "none",
    proAccess: false,
    scheduleView: "day",
    scheduleDate: null,
    scheduleSlotMinutes: 30,
    scheduleSlotChoices: [15, 30, 45, 60, 90, 120],
    scheduleBookSlot: null,
};

const screens = {
    onboarding: el("screen-onboarding"),
    login: el("screen-login"),
    forgotPassword: el("screen-forgot-password"),
    register: el("screen-register"),
    verifyPending: el("screen-verify-pending"),
    connectInstagram: el("screen-connect-instagram"),
    inbox: el("screen-inbox"),
    detail: el("screen-detail"),
    schedule: el("screen-schedule"),
    profile: el("screen-profile"),
    settings: el("screen-settings"),
    help: el("screen-help"),
    terms: el("screen-terms"),
    privacy: el("screen-privacy"),
    subscription: el("screen-subscription"),
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
// Same idea for Subscription - reachable from Settings and from the Schedule tab's
// locked (Starter-plan) upsell state.
let subscriptionReturnTo = "settings";

if (capacitorPlugins) {
    capacitorPlugins.StatusBar.setBackgroundColor({ color: "#d69410" });
    capacitorPlugins.StatusBar.setStyle({ style: "LIGHT" });

    capacitorPlugins.App.addListener("backButton", ({ canGoBack }) => {
        if (screens.detail.classList.contains("active")) {
            history.back();
        } else if (screens.register.classList.contains("active")) {
            if (registerStep > 1) showRegisterStep(registerStep - 1);
            else showScreen("login");
        } else if (screens.forgotPassword.classList.contains("active")) {
            showScreen("login");
        } else if (screens.verifyPending.classList.contains("active")) {
            showScreen("login");
        } else if (screens.connectInstagram.classList.contains("active")) {
            // Deliberately swallowed - the only ways out are finishing the connection or
            // the explicit "Log out" link. Falling through to canGoBack/exitApp here would
            // let one back-press quit the app mid-setup, right after creating an account.
        } else if (screens.terms.classList.contains("active") || screens.privacy.classList.contains("active")) {
            showScreen(legalReturnTo);
        } else if (screens.help.classList.contains("active") || screens.subscription.classList.contains("active")) {
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
    // Updates apply fully automatically - no tap required - behind a full-screen overlay
    // with a progress bar (fed by the plugin's own "download" progress events) so the
    // user sees why the app is briefly unresponsive instead of it looking frozen. set()
    // reloads the app immediately once applied - nothing after that call runs.
    if (capacitorPlugins.CapacitorUpdater) {
        capacitorPlugins.CapacitorUpdater.notifyAppReady().catch(() => {});

        capacitorPlugins.CapacitorUpdater.getFailedUpdate().then((failed) => {
            if (failed) reportClientError("native rollback detected: " + JSON.stringify(failed), "", "capacitor-updater-failed-update");
        }).catch(() => {});

        let otaCheckInProgress = false;
        const otaOverlay = el("ota-update-overlay");
        const otaProgressFill = el("ota-progress-fill");
        const otaProgressPercent = el("ota-progress-percent");

        const setOtaProgress = (percent) => {
            const clamped = Math.max(0, Math.min(100, Math.round(percent) || 0));
            otaProgressFill.style.width = clamped + "%";
            otaProgressPercent.textContent = clamped + "%";
        };

        capacitorPlugins.CapacitorUpdater.addListener("download", (info) => {
            setOtaProgress(info && info.percent);
        });

        const findDownloadedBundle = async (version) => {
            const result = await capacitorPlugins.CapacitorUpdater.list();
            const bundles = (result && result.bundles) || [];
            return bundles.find((b) => b.version === version) || null;
        };

        const checkForOtaUpdate = async () => {
            if (otaCheckInProgress) return;
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

                setOtaProgress(0);
                otaOverlay.classList.remove("hidden");

                step = "download";
                let bundle;
                try {
                    bundle = await capacitorPlugins.CapacitorUpdater.download({
                        url: manifest.url,
                        version: manifest.version,
                    });
                } catch (downloadError) {
                    // Most likely "already downloaded" from a previous session that got
                    // interrupted before set() applied it - reuse the stored copy.
                    bundle = await findDownloadedBundle(manifest.version);
                    if (!bundle) throw downloadError;
                }

                setOtaProgress(100);
                step = "set";
                await capacitorPlugins.CapacitorUpdater.set(bundle);
                // set() reloads the app immediately - nothing below this line runs.
            } catch (e) {
                otaOverlay.classList.add("hidden");
                reportClientError(`failed at step=${step}: ${e.message}`, e.stack, "ota-update-check");
            } finally {
                otaCheckInProgress = false;
            }
        };

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

// Shared anchored option-list picker (Faza 4) - replaces native <select> dropdowns
// (working-hours times, price-list currency) that used to open as a large OS-centered
// dialog on Android, which read as a jarring "zoomed modal" popping up over the whole
// screen. Works exactly like the country-code picker above - one shared fixed-overlay
// instance per field "kind", positioned by JS to sit right below whichever button was
// tapped, so it reads as an inline dropdown attached to that field. Lives outside #app
// for the same reason the language/country modals do (a .screen's CSS transform would
// otherwise hijack position:fixed descendants - see the HTML comment above #lang-modal).
// One instance (backed by one #<modalId> in index.html) can serve any number of buttons/
// option-sets, since the option list itself is (re)rendered fresh on every open() call
// rather than built once up front.
function createAnchoredListPicker(modalId) {
    const modal = el(modalId);
    const sheet = modal.querySelector(".lang-modal-sheet");
    const backdrop = modal.querySelector(".lang-modal-backdrop");
    const list = modal.querySelector(".country-modal-list");
    let openTriggerBtn = null;

    function open(triggerBtn, options, currentValue, onSelect) {
        openTriggerBtn = triggerBtn;
        list.innerHTML = "";
        let selectedEl = null;
        options.forEach((opt) => {
            const optionBtn = document.createElement("button");
            optionBtn.type = "button";
            optionBtn.className = "country-option-btn";
            if (opt === currentValue) {
                optionBtn.classList.add("selected");
                selectedEl = optionBtn;
            }
            optionBtn.textContent = opt;
            optionBtn.addEventListener("click", () => {
                onSelect(opt);
                close();
            });
            list.appendChild(optionBtn);
        });

        const rect = triggerBtn.getBoundingClientRect();
        const margin = 12;
        const top = rect.bottom + 6;
        const available = window.innerHeight - top - margin;
        sheet.style.top = `${top}px`;
        sheet.style.left = `${rect.left}px`;
        sheet.style.width = `${Math.max(rect.width, 120)}px`;
        sheet.style.maxHeight = `${Math.max(160, Math.min(240, available))}px`;
        modal.classList.add("open");
        if (selectedEl) selectedEl.scrollIntoView({ block: "center" });
    }
    function close() {
        modal.classList.remove("open");
        openTriggerBtn = null;
    }
    backdrop.addEventListener("click", close);

    return { open, close, isOpenFor: (btn) => openTriggerBtn === btn };
}

const optionPicker = createAnchoredListPicker("option-modal");

// A button that looks like the old <select> but opens the shared optionPicker dropdown
// right below itself instead of the platform's own picker UI. Used for both the hours
// picker's time fields and the price list's currency field (see createPricePicker).
function createOptionButton(initialValue, options, extraClass) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "picker-btn" + (extraClass ? ` ${extraClass}` : "");
    const label = document.createElement("span");
    label.textContent = initialValue;
    const chevron = document.createElement("span");
    chevron.className = "picker-chevron";
    chevron.textContent = "⌄";
    btn.appendChild(label);
    btn.appendChild(chevron);

    let value = initialValue;
    btn.addEventListener("click", () => {
        if (optionPicker.isOpenFor(btn)) { optionPicker.close(); return; }
        optionPicker.open(btn, options, value, (chosen) => {
            value = chosen;
            label.textContent = chosen;
            // Bubbles up to the hours-picker container's own "change" listener, the same
            // way a native <select>'s change event would have.
            btn.dispatchEvent(new Event("change", { bubbles: true }));
        });
    });

    return {
        el: btn,
        get value() { return value; },
        set value(v) { value = v; label.textContent = v; },
    };
}

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
        const openTimeBtn = createOptionButton("09:00", HOURS_TIME_OPTIONS, "time-picker-btn");
        const closeTimeBtn = createOptionButton("18:00", HOURS_TIME_OPTIONS, "time-picker-btn");
        const sep = document.createElement("span");
        sep.className = "hours-time-sep";
        sep.textContent = "–";
        times.appendChild(openTimeBtn.el);
        times.appendChild(sep);
        times.appendChild(closeTimeBtn.el);

        checkbox.addEventListener("change", () => {
            times.classList.toggle("hidden", !checkbox.checked);
        });

        row.appendChild(header);
        row.appendChild(times);
        container.appendChild(row);

        rows[day] = { checkbox, openTimeBtn, closeTimeBtn, times };
    });

    return {
        getValue() {
            return HOURS_DAYS.map((day) => {
                const r = rows[day];
                if (!r.checkbox.checked) return `${HOURS_DAY_LABELS_EN[day]}: closed`;
                return `${HOURS_DAY_LABELS_EN[day]}: ${r.openTimeBtn.value}-${r.closeTimeBtn.value}`;
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
                if (HOURS_TIME_OPTIONS.includes(openTime)) r.openTimeBtn.value = openTime;
                if (HOURS_TIME_OPTIONS.includes(closeTime)) r.closeTimeBtn.value = closeTime;
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
                r.openTimeBtn.value = "09:00";
                r.closeTimeBtn.value = "18:00";
                r.times.classList.remove("hidden");
            });
        },
        // False if any open day's closing time isn't after its opening time (the
        // dropdowns only ever hold valid times, so this is the one way the picker's own
        // state can still be nonsensical - e.g. "09:00-09:00" or "18:00-09:00").
        validate() {
            return HOURS_DAYS.every((day) => {
                const r = rows[day];
                return !r.checkbox.checked || r.closeTimeBtn.value > r.openTimeBtn.value;
            });
        },
    };
}

// Major world currencies (the ones the user named explicitly first, then other common
// ones) - shown next to the price list so a business can say what its prices are in.
// Codes are used as-is in every language (ISO 4217, not translated).
const CURRENCIES = ["RSD", "EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY", "TRY", "RUB", "SEK", "NOK", "DKK", "PLN", "AED", "INR", "BRL"];

function createPricePicker(rowsContainerId, addButtonId, currencyContainerId) {
    const rowsContainer = el(rowsContainerId);
    const currencyBtn = createOptionButton("RSD", CURRENCIES, "currency-picker-btn");
    el(currencyContainerId).appendChild(currencyBtn.el);

    // Each row is two floating-label fields (Service / Price - same floating mechanism as
    // the login screen, see .floating-field in app.css) plus a remove button. The label
    // IS the translated text now (there's no separate example placeholder to show once
    // the label has risen), so it's set directly via t() at creation time, with
    // data-i18n left on for correctness if the user switches language afterward.
    function addRow(service = "", price = "") {
        const row = document.createElement("div");
        row.className = "price-row";

        const serviceField = document.createElement("div");
        serviceField.className = "floating-field floating-field-service";
        const serviceInput = document.createElement("input");
        serviceInput.type = "text";
        serviceInput.className = "price-service-input";
        serviceInput.placeholder = " ";
        serviceInput.value = service;
        const serviceLabel = document.createElement("label");
        serviceLabel.dataset.i18n = "pricePicker.servicePlaceholder";
        serviceLabel.textContent = t("pricePicker.servicePlaceholder");
        serviceField.appendChild(serviceInput);
        serviceField.appendChild(serviceLabel);

        const priceField = document.createElement("div");
        priceField.className = "floating-field floating-field-price";
        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.className = "price-amount-input";
        priceInput.placeholder = " ";
        priceInput.value = price;
        const priceLabel = document.createElement("label");
        priceLabel.dataset.i18n = "pricePicker.pricePlaceholder";
        priceLabel.textContent = t("pricePicker.pricePlaceholder");
        priceField.appendChild(priceInput);
        priceField.appendChild(priceLabel);

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "price-remove-btn";
        removeBtn.setAttribute("aria-label", "Remove");
        removeBtn.textContent = "×";
        removeBtn.addEventListener("click", () => row.remove());

        row.appendChild(serviceField);
        row.appendChild(priceField);
        row.appendChild(removeBtn);
        rowsContainer.appendChild(row);
    }

    el(addButtonId).addEventListener("click", () => addRow());

    return {
        getValue() {
            const currency = currencyBtn.value;
            return Array.from(rowsContainer.querySelectorAll(".price-row"))
                .map((row) => {
                    const service = row.querySelector(".price-service-input").value.trim();
                    const price = row.querySelector(".price-amount-input").value.trim();
                    return service && price ? `${service}: ${price} ${currency}` : null;
                })
                .filter(Boolean)
                .join("\n");
        },
        // Best-effort: if a saved price already ends with one of our known currency
        // codes (i.e. it was saved by this same picker before), strip it back out of
        // the price field being edited and use it to preselect the currency dropdown -
        // a single currency applies to the whole list. Older data with no currency code
        // (or free text that doesn't match one) is left exactly as typed, so nothing
        // that already worked silently changes.
        setValue(text) {
            rowsContainer.innerHTML = "";
            const lines = (text || "").split("\n").map((l) => l.trim()).filter(Boolean);
            if (lines.length === 0) {
                addRow();
                currencyBtn.value = "RSD";
                return;
            }
            let detectedCurrency = null;
            lines.forEach((line) => {
                const idx = line.indexOf(":");
                const service = idx === -1 ? line : line.slice(0, idx).trim();
                let price = idx === -1 ? "" : line.slice(idx + 1).trim();
                const currencyMatch = price.match(/\s([A-Za-z]{3})$/);
                if (currencyMatch && CURRENCIES.includes(currencyMatch[1].toUpperCase())) {
                    if (!detectedCurrency) detectedCurrency = currencyMatch[1].toUpperCase();
                    price = price.slice(0, currencyMatch.index).trim();
                }
                addRow(service, price);
            });
            currencyBtn.value = detectedCurrency || "RSD";
        },
        // False if any row has only one of service/price filled in - getValue() silently
        // drops such a row instead of erroring, which used to mean whatever the user typed
        // there just vanished with no explanation.
        validate() {
            return Array.from(rowsContainer.querySelectorAll(".price-row")).every((row) => {
                const service = row.querySelector(".price-service-input").value.trim();
                const price = row.querySelector(".price-amount-input").value.trim();
                return (!!service === !!price);
            });
        },
    };
}

const registerHoursPicker = createHoursPicker("register-hours-picker");
const registerPricesPicker = createPricePicker("register-prices-rows", "register-prices-add", "register-price-currency");
registerPricesPicker.setValue("");
const profileHoursPicker = createHoursPicker("settings-hours-picker");
const profilePricesPicker = createPricePicker("settings-prices-rows", "settings-prices-add", "settings-price-currency");
profilePricesPicker.setValue("");

// --- Country code picker (Create Account's phone number field) - every country with
// its calling code, searchable/scrollable in a modal (same fixed-overlay mechanism as
// the language picker), so a phone number always gets stored with a real international
// dial code attached rather than whatever the customer happened to type. ---

const COUNTRIES = [
    { name: "Afghanistan", code: "AF", dial: "+93" }, { name: "Albania", code: "AL", dial: "+355" },
    { name: "Algeria", code: "DZ", dial: "+213" }, { name: "Andorra", code: "AD", dial: "+376" },
    { name: "Angola", code: "AO", dial: "+244" }, { name: "Antigua and Barbuda", code: "AG", dial: "+1268" },
    { name: "Argentina", code: "AR", dial: "+54" }, { name: "Armenia", code: "AM", dial: "+374" },
    { name: "Australia", code: "AU", dial: "+61" }, { name: "Austria", code: "AT", dial: "+43" },
    { name: "Azerbaijan", code: "AZ", dial: "+994" }, { name: "Bahamas", code: "BS", dial: "+1242" },
    { name: "Bahrain", code: "BH", dial: "+973" }, { name: "Bangladesh", code: "BD", dial: "+880" },
    { name: "Barbados", code: "BB", dial: "+1246" }, { name: "Belarus", code: "BY", dial: "+375" },
    { name: "Belgium", code: "BE", dial: "+32" }, { name: "Belize", code: "BZ", dial: "+501" },
    { name: "Benin", code: "BJ", dial: "+229" }, { name: "Bhutan", code: "BT", dial: "+975" },
    { name: "Bolivia", code: "BO", dial: "+591" }, { name: "Bosnia and Herzegovina", code: "BA", dial: "+387" },
    { name: "Botswana", code: "BW", dial: "+267" }, { name: "Brazil", code: "BR", dial: "+55" },
    { name: "Brunei", code: "BN", dial: "+673" }, { name: "Bulgaria", code: "BG", dial: "+359" },
    { name: "Burkina Faso", code: "BF", dial: "+226" }, { name: "Burundi", code: "BI", dial: "+257" },
    { name: "Cabo Verde", code: "CV", dial: "+238" }, { name: "Cambodia", code: "KH", dial: "+855" },
    { name: "Cameroon", code: "CM", dial: "+237" }, { name: "Canada", code: "CA", dial: "+1" },
    { name: "Central African Republic", code: "CF", dial: "+236" }, { name: "Chad", code: "TD", dial: "+235" },
    { name: "Chile", code: "CL", dial: "+56" }, { name: "China", code: "CN", dial: "+86" },
    { name: "Colombia", code: "CO", dial: "+57" }, { name: "Comoros", code: "KM", dial: "+269" },
    { name: "Congo (Congo-Brazzaville)", code: "CG", dial: "+242" }, { name: "Costa Rica", code: "CR", dial: "+506" },
    { name: "Croatia", code: "HR", dial: "+385" }, { name: "Cuba", code: "CU", dial: "+53" },
    { name: "Cyprus", code: "CY", dial: "+357" }, { name: "Czechia", code: "CZ", dial: "+420" },
    { name: "Democratic Republic of the Congo", code: "CD", dial: "+243" }, { name: "Denmark", code: "DK", dial: "+45" },
    { name: "Djibouti", code: "DJ", dial: "+253" }, { name: "Dominica", code: "DM", dial: "+1767" },
    { name: "Dominican Republic", code: "DO", dial: "+1809" }, { name: "Ecuador", code: "EC", dial: "+593" },
    { name: "Egypt", code: "EG", dial: "+20" }, { name: "El Salvador", code: "SV", dial: "+503" },
    { name: "Equatorial Guinea", code: "GQ", dial: "+240" }, { name: "Eritrea", code: "ER", dial: "+291" },
    { name: "Estonia", code: "EE", dial: "+372" }, { name: "Eswatini", code: "SZ", dial: "+268" },
    { name: "Ethiopia", code: "ET", dial: "+251" }, { name: "Fiji", code: "FJ", dial: "+679" },
    { name: "Finland", code: "FI", dial: "+358" }, { name: "France", code: "FR", dial: "+33" },
    { name: "Gabon", code: "GA", dial: "+241" }, { name: "Gambia", code: "GM", dial: "+220" },
    { name: "Georgia", code: "GE", dial: "+995" }, { name: "Germany", code: "DE", dial: "+49" },
    { name: "Ghana", code: "GH", dial: "+233" }, { name: "Greece", code: "GR", dial: "+30" },
    { name: "Grenada", code: "GD", dial: "+1473" }, { name: "Guatemala", code: "GT", dial: "+502" },
    { name: "Guinea", code: "GN", dial: "+224" }, { name: "Guinea-Bissau", code: "GW", dial: "+245" },
    { name: "Guyana", code: "GY", dial: "+592" }, { name: "Haiti", code: "HT", dial: "+509" },
    { name: "Honduras", code: "HN", dial: "+504" }, { name: "Hungary", code: "HU", dial: "+36" },
    { name: "Iceland", code: "IS", dial: "+354" }, { name: "India", code: "IN", dial: "+91" },
    { name: "Indonesia", code: "ID", dial: "+62" }, { name: "Iran", code: "IR", dial: "+98" },
    { name: "Iraq", code: "IQ", dial: "+964" }, { name: "Ireland", code: "IE", dial: "+353" },
    { name: "Israel", code: "IL", dial: "+972" }, { name: "Italy", code: "IT", dial: "+39" },
    { name: "Ivory Coast", code: "CI", dial: "+225" }, { name: "Jamaica", code: "JM", dial: "+1876" },
    { name: "Japan", code: "JP", dial: "+81" }, { name: "Jordan", code: "JO", dial: "+962" },
    { name: "Kazakhstan", code: "KZ", dial: "+7" }, { name: "Kenya", code: "KE", dial: "+254" },
    { name: "Kiribati", code: "KI", dial: "+686" }, { name: "Kosovo", code: "XK", dial: "+383" },
    { name: "Kuwait", code: "KW", dial: "+965" }, { name: "Kyrgyzstan", code: "KG", dial: "+996" },
    { name: "Laos", code: "LA", dial: "+856" }, { name: "Latvia", code: "LV", dial: "+371" },
    { name: "Lebanon", code: "LB", dial: "+961" }, { name: "Lesotho", code: "LS", dial: "+266" },
    { name: "Liberia", code: "LR", dial: "+231" }, { name: "Libya", code: "LY", dial: "+218" },
    { name: "Liechtenstein", code: "LI", dial: "+423" }, { name: "Lithuania", code: "LT", dial: "+370" },
    { name: "Luxembourg", code: "LU", dial: "+352" }, { name: "Madagascar", code: "MG", dial: "+261" },
    { name: "Malawi", code: "MW", dial: "+265" }, { name: "Malaysia", code: "MY", dial: "+60" },
    { name: "Maldives", code: "MV", dial: "+960" }, { name: "Mali", code: "ML", dial: "+223" },
    { name: "Malta", code: "MT", dial: "+356" }, { name: "Marshall Islands", code: "MH", dial: "+692" },
    { name: "Mauritania", code: "MR", dial: "+222" }, { name: "Mauritius", code: "MU", dial: "+230" },
    { name: "Mexico", code: "MX", dial: "+52" }, { name: "Micronesia", code: "FM", dial: "+691" },
    { name: "Moldova", code: "MD", dial: "+373" }, { name: "Monaco", code: "MC", dial: "+377" },
    { name: "Mongolia", code: "MN", dial: "+976" }, { name: "Montenegro", code: "ME", dial: "+382" },
    { name: "Morocco", code: "MA", dial: "+212" }, { name: "Mozambique", code: "MZ", dial: "+258" },
    { name: "Myanmar", code: "MM", dial: "+95" }, { name: "Namibia", code: "NA", dial: "+264" },
    { name: "Nauru", code: "NR", dial: "+674" }, { name: "Nepal", code: "NP", dial: "+977" },
    { name: "Netherlands", code: "NL", dial: "+31" }, { name: "New Zealand", code: "NZ", dial: "+64" },
    { name: "Nicaragua", code: "NI", dial: "+505" }, { name: "Niger", code: "NE", dial: "+227" },
    { name: "Nigeria", code: "NG", dial: "+234" }, { name: "North Korea", code: "KP", dial: "+850" },
    { name: "North Macedonia", code: "MK", dial: "+389" }, { name: "Norway", code: "NO", dial: "+47" },
    { name: "Oman", code: "OM", dial: "+968" }, { name: "Pakistan", code: "PK", dial: "+92" },
    { name: "Palau", code: "PW", dial: "+680" }, { name: "Palestine", code: "PS", dial: "+970" },
    { name: "Panama", code: "PA", dial: "+507" }, { name: "Papua New Guinea", code: "PG", dial: "+675" },
    { name: "Paraguay", code: "PY", dial: "+595" }, { name: "Peru", code: "PE", dial: "+51" },
    { name: "Philippines", code: "PH", dial: "+63" }, { name: "Poland", code: "PL", dial: "+48" },
    { name: "Portugal", code: "PT", dial: "+351" }, { name: "Qatar", code: "QA", dial: "+974" },
    { name: "Romania", code: "RO", dial: "+40" }, { name: "Russia", code: "RU", dial: "+7" },
    { name: "Rwanda", code: "RW", dial: "+250" }, { name: "Saint Kitts and Nevis", code: "KN", dial: "+1869" },
    { name: "Saint Lucia", code: "LC", dial: "+1758" }, { name: "Saint Vincent and the Grenadines", code: "VC", dial: "+1784" },
    { name: "Samoa", code: "WS", dial: "+685" }, { name: "San Marino", code: "SM", dial: "+378" },
    { name: "Sao Tome and Principe", code: "ST", dial: "+239" }, { name: "Saudi Arabia", code: "SA", dial: "+966" },
    { name: "Senegal", code: "SN", dial: "+221" }, { name: "Serbia", code: "RS", dial: "+381" },
    { name: "Seychelles", code: "SC", dial: "+248" }, { name: "Sierra Leone", code: "SL", dial: "+232" },
    { name: "Singapore", code: "SG", dial: "+65" }, { name: "Slovakia", code: "SK", dial: "+421" },
    { name: "Slovenia", code: "SI", dial: "+386" }, { name: "Solomon Islands", code: "SB", dial: "+677" },
    { name: "Somalia", code: "SO", dial: "+252" }, { name: "South Africa", code: "ZA", dial: "+27" },
    { name: "South Korea", code: "KR", dial: "+82" }, { name: "South Sudan", code: "SS", dial: "+211" },
    { name: "Spain", code: "ES", dial: "+34" }, { name: "Sri Lanka", code: "LK", dial: "+94" },
    { name: "Sudan", code: "SD", dial: "+249" }, { name: "Suriname", code: "SR", dial: "+597" },
    { name: "Sweden", code: "SE", dial: "+46" }, { name: "Switzerland", code: "CH", dial: "+41" },
    { name: "Syria", code: "SY", dial: "+963" }, { name: "Taiwan", code: "TW", dial: "+886" },
    { name: "Tajikistan", code: "TJ", dial: "+992" }, { name: "Tanzania", code: "TZ", dial: "+255" },
    { name: "Thailand", code: "TH", dial: "+66" }, { name: "Timor-Leste", code: "TL", dial: "+670" },
    { name: "Togo", code: "TG", dial: "+228" }, { name: "Tonga", code: "TO", dial: "+676" },
    { name: "Trinidad and Tobago", code: "TT", dial: "+1868" }, { name: "Tunisia", code: "TN", dial: "+216" },
    { name: "Turkey", code: "TR", dial: "+90" }, { name: "Turkmenistan", code: "TM", dial: "+993" },
    { name: "Tuvalu", code: "TV", dial: "+688" }, { name: "Uganda", code: "UG", dial: "+256" },
    { name: "Ukraine", code: "UA", dial: "+380" }, { name: "United Arab Emirates", code: "AE", dial: "+971" },
    { name: "United Kingdom", code: "GB", dial: "+44" }, { name: "United States", code: "US", dial: "+1" },
    { name: "Uruguay", code: "UY", dial: "+598" }, { name: "Uzbekistan", code: "UZ", dial: "+998" },
    { name: "Vanuatu", code: "VU", dial: "+678" }, { name: "Vatican City", code: "VA", dial: "+379" },
    { name: "Venezuela", code: "VE", dial: "+58" }, { name: "Vietnam", code: "VN", dial: "+84" },
    { name: "Yemen", code: "YE", dial: "+967" }, { name: "Zambia", code: "ZM", dial: "+260" },
    { name: "Zimbabwe", code: "ZW", dial: "+263" },
].sort((a, b) => a.name.localeCompare(b.name));

function countryFlagEmoji(iso) {
    return iso.toUpperCase().replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

// Best-effort default from the device's own locale (e.g. "de-DE" -> Germany) - falls
// back to Serbia (this product's home market) when that doesn't resolve to a real country.
function defaultPhoneCountry() {
    const localeRegionMatch = /-([A-Za-z]{2})$/.exec(navigator.language || "");
    if (localeRegionMatch) {
        const guess = COUNTRIES.find((c) => c.code === localeRegionMatch[1].toUpperCase());
        if (guess) return guess;
    }
    return COUNTRIES.find((c) => c.code === "RS");
}

let selectedPhoneCountry = defaultPhoneCountry();
let selectedProfilePhoneCountry = defaultPhoneCountry();
let selectedForgotPhoneCountry = defaultPhoneCountry();

// Best-effort split of a stored "+381 6X XXX XXXX"-shaped phone number back into
// {country, rest} for re-populating the Profile screen's picker + input. Longest dial
// code first, since some codes are prefixes of others (e.g. "+1" vs "+1876").
function splitStoredPhoneNumber(stored) {
    const value = (stored || "").trim();
    if (!value) return { country: defaultPhoneCountry(), rest: "" };
    const byDialLengthDesc = [...COUNTRIES].sort((a, b) => b.dial.length - a.dial.length);
    const match = byDialLengthDesc.find((c) => value.startsWith(c.dial));
    if (!match) return { country: defaultPhoneCountry(), rest: value };
    return { country: match, rest: value.slice(match.dial.length).trim() };
}

// One shared country-modal instance reused across every phone field (Create Account's
// and Profile's) - same multi-trigger pattern as setupLanguagePicker() above, since each
// field needs its own selected-country state and its own flag/dial display.
function setupCountryPicker() {
    const modal = el("country-modal");
    const sheet = modal.querySelector(".country-modal-sheet");
    const backdrop = el("country-modal-backdrop");
    const list = el("country-modal-list");

    const bindings = [
        {
            btn: el("register-phone-country-btn"), row: el("register-phone-row"),
            flagEl: el("register-phone-country-flag"), dialEl: el("register-phone-country-dial"),
            onSelect: (c) => { selectedPhoneCountry = c; },
        },
        {
            btn: el("settings-phone-country-btn"), row: el("settings-phone-row"),
            flagEl: el("settings-phone-country-flag"), dialEl: el("settings-phone-country-dial"),
            onSelect: (c) => { selectedProfilePhoneCountry = c; },
        },
        {
            btn: el("forgot-phone-country-btn"), row: el("forgot-phone-row"),
            flagEl: el("forgot-phone-country-flag"), dialEl: el("forgot-phone-country-dial"),
            onSelect: (c) => { selectedForgotPhoneCountry = c; },
        },
    ];
    let active = null;

    // Rendered once - no search, so the list never changes after this.
    COUNTRIES.forEach((c) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "country-option-btn";
        row.innerHTML = `<span class="country-option-flag">${countryFlagEmoji(c.code)}</span>` +
            `<span class="country-option-name">${escapeHtml(c.name)}</span>` +
            `<span class="country-option-dial">${c.dial}</span>`;
        row.addEventListener("click", () => {
            if (!active) return;
            active.onSelect(c);
            active.flagEl.textContent = countryFlagEmoji(c.code);
            active.dialEl.textContent = c.dial;
            // The phone number field's own value didn't change (only which country it's
            // attached to did) - dispatch a synthetic "input" so anything listening for
            // that event on the field (e.g. the live "already taken" check, which needs
            // the full international number) re-runs with the newly-selected dial code.
            const phoneInput = active.row.querySelector('input[type="tel"]');
            if (phoneInput) phoneInput.dispatchEvent(new Event("input", { bubbles: true }));
            closeCountryModal();
        });
        list.appendChild(row);
    });

    function openCountryModal(binding) {
        active = binding;
        // Sized to match the whole phone-input-row (button + number field) and anchored
        // right below it (never above) - reads as attached to that field instead of a
        // separate popup. If there isn't full room underneath, shrink the list's
        // max-height to what's left rather than moving it above the field.
        const rect = binding.row.getBoundingClientRect();
        const margin = 12;
        const top = rect.bottom + 6;
        const available = window.innerHeight - top - margin;
        sheet.style.top = `${top}px`;
        sheet.style.left = `${rect.left}px`;
        sheet.style.width = `${rect.width}px`;
        sheet.style.maxHeight = `${Math.max(160, Math.min(320, available))}px`;

        modal.classList.add("open");
    }
    function closeCountryModal() {
        modal.classList.remove("open");
        active = null;
    }

    bindings.forEach((binding) => {
        binding.btn.addEventListener("click", () => {
            if (modal.classList.contains("open") && active === binding) closeCountryModal();
            else openCountryModal(binding);
        });
    });
    backdrop.addEventListener("click", closeCountryModal);

    const initial = [selectedPhoneCountry, selectedProfilePhoneCountry, selectedForgotPhoneCountry];
    bindings.forEach((binding, i) => {
        binding.flagEl.textContent = countryFlagEmoji(initial[i].code);
        binding.dialEl.textContent = initial[i].dial;
    });
}
setupCountryPicker();

// Updates the Profile phone-country display/state after loadSettingsIntoForm parses the
// stored number - separate from the picker's own click handling above.
function setProfilePhoneCountry(country) {
    selectedProfilePhoneCountry = country;
    el("settings-phone-country-flag").textContent = countryFlagEmoji(country.code);
    el("settings-phone-country-dial").textContent = country.dial;
}

// Builds the final international phone number to send to the server. If the user typed
// their own leading "+" (detected live below - e.g. refining the shared NANP "+1" code
// into a more specific "+1876..."), trust exactly what they typed instead of prepending
// the picker's own dial code on top of it (which would double it up).
function composePhoneNumber(inputEl, selectedCountry) {
    const raw = inputEl.value.trim();
    if (!raw) return "";
    return raw[0] === "+" ? raw : `${selectedCountry.dial} ${raw}`;
}

// Live country detection as the user types "+381..." directly into a phone number field
// (in addition to the manual picker button above) - matches the longest calling code
// that's a prefix of what's typed so far and updates that field's flag/dial display.
// Deliberately non-destructive (doesn't strip the typed "+381" back out of the field):
// some calling codes share a prefix with a more specific one (the NANP "+1" covers the
// US/Canada/several Caribbean nations, each with their own longer area-code-based
// distinction, e.g. Jamaica's "+1876") - stripping early would make it impossible to
// tell those apart as more digits arrive. composePhoneNumber() above handles not
// double-prefixing a number the user already typed with its own "+".
function attachLivePhoneCountryDetection(inputEl, flagEl, dialEl, onMatch) {
    const byDialLengthDesc = [...COUNTRIES].sort((a, b) => b.dial.length - a.dial.length);
    inputEl.addEventListener("input", () => {
        const raw = inputEl.value.trim();
        if (raw[0] !== "+") return;
        const match = byDialLengthDesc.find((c) => raw.startsWith(c.dial));
        if (!match) return;
        onMatch(match);
        flagEl.textContent = countryFlagEmoji(match.code);
        dialEl.textContent = match.dial;
    });
}
attachLivePhoneCountryDetection(el("register-phone"), el("register-phone-country-flag"), el("register-phone-country-dial"), (c) => { selectedPhoneCountry = c; });
attachLivePhoneCountryDetection(el("settings-phone"), el("settings-phone-country-flag"), el("settings-phone-country-dial"), (c) => { selectedProfilePhoneCountry = c; });
attachLivePhoneCountryDetection(el("forgot-phone"), el("forgot-phone-country-flag"), el("forgot-phone-country-dial"), (c) => { selectedForgotPhoneCountry = c; });

// --- Live "already taken" checks (Create Account step 1 + Profile) ---
//
// Debounced calls to /api/check-availability (api.py) as the user types into a
// username/contact-email/phone field. Before login (Create Account) there's no Bearer
// token, so the server checks against every account; once logged in (Profile), the
// server excludes the caller's own current value automatically via the token - see
// api.py's api_check_availability. getValue is a function rather than reading
// inputEl.value directly so the phone fields can check the FULL composed international
// number (country dial code + typed digits) instead of just the local part.
function setupAvailabilityField(inputEl, errorEl, field, errorKey, getValue, onStatusChange) {
    let status = "unknown"; // unknown | checking | available | taken
    let timer = null;

    function setStatus(next) {
        status = next;
        errorEl.classList.toggle("hidden", status !== "taken");
        if (status === "taken") errorEl.textContent = t(errorKey);
        if (onStatusChange) onStatusChange();
    }

    function check() {
        clearTimeout(timer);
        const value = getValue();
        if (!value) {
            setStatus("unknown");
            return;
        }
        setStatus("checking");
        timer = setTimeout(async () => {
            try {
                const data = await api("/api/check-availability", { method: "POST", body: { field, value } });
                if (getValue() !== value) return; // superseded by a newer edit
                setStatus(data.taken ? "taken" : "available");
            } catch (err) {
                if (getValue() !== value) return;
                // Network hiccup or offline - don't block the user forever on a check we
                // couldn't complete; the server still re-validates on actual submit.
                setStatus("unknown");
            }
        }, 450);
    }

    inputEl.addEventListener("input", check);
    return { isBlocking: () => status === "taken" || status === "checking", recheck: check, reset: () => setStatus("unknown") };
}

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
        err.data = data;
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

// --- Toast (brief bottom confirmation, e.g. after tapping "Save changes") ---

let toastTimer = null;
function showToast(message) {
    const toast = el("toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2400);
}

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
        if (err.data && err.data.pending_verification) {
            showVerifyPendingScreen(err.data.business_id, "");
        } else {
            errorBox.textContent = err.message || t("login.error");
            errorBox.classList.remove("hidden");
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = t("login.submit");
    }
});

// --- Forgot password (its own native screen, pushed on top of Login - no browser
// involved at all, unlike the old flow which opened the website's page in a browser tab) ---

function showForgotIdentifierMode() {
    el("forgot-phone-mode").classList.add("hidden");
    el("forgot-identifier-mode").classList.remove("hidden");
    el("forgot-identifier").focus();
}
function showForgotPhoneMode() {
    el("forgot-identifier-mode").classList.add("hidden");
    el("forgot-phone-mode").classList.remove("hidden");
    el("forgot-phone").focus();
}

el("login-forgot-link").addEventListener("click", () => {
    el("forgot-form").reset();
    el("forgot-phone-form").reset();
    showForgotIdentifierMode();
    el("forgot-error").classList.add("hidden");
    el("forgot-success").classList.add("hidden");
    showScreen("forgotPassword");
});

el("forgot-back").addEventListener("click", () => showScreen("login"));
el("forgot-show-phone").addEventListener("click", () => {
    el("forgot-error").classList.add("hidden");
    showForgotPhoneMode();
});
el("forgot-show-identifier").addEventListener("click", () => {
    el("forgot-error").classList.add("hidden");
    showForgotIdentifierMode();
});

el("forgot-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const identifier = el("forgot-identifier").value.trim();
    const errorBox = el("forgot-error");
    const successBox = el("forgot-success");
    const submitBtn = el("forgot-submit");

    errorBox.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.textContent = t("forgot.submitLoading");

    try {
        await api("/api/forgot-password", { method: "POST", body: { identifier } });
        successBox.classList.remove("hidden");
        el("forgot-identifier-mode").classList.add("hidden");
    } catch (err) {
        errorBox.textContent = err.message || t("forgot.error");
        errorBox.classList.remove("hidden");
        submitBtn.disabled = false;
        submitBtn.textContent = t("forgot.submit");
    }
});

// "Find by mobile number" - same success/error UI as the email-or-username path above,
// just looked up by phone_number instead (see api.py's /api/forgot-password/phone). The
// reset link still only ever goes out by email - there's no SMS channel.
el("forgot-phone-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const phoneNumber = composePhoneNumber(el("forgot-phone"), selectedForgotPhoneCountry);
    const errorBox = el("forgot-error");
    const successBox = el("forgot-success");
    const submitBtn = el("forgot-phone-submit");

    errorBox.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.textContent = t("forgot.submitLoading");

    try {
        await api("/api/forgot-password/phone", { method: "POST", body: { phone_number: phoneNumber } });
        successBox.classList.remove("hidden");
        el("forgot-phone-mode").classList.add("hidden");
    } catch (err) {
        errorBox.textContent = err.message || t("forgot.phoneError");
        errorBox.classList.remove("hidden");
        submitBtn.disabled = false;
        submitBtn.textContent = t("forgot.phoneSubmit");
    }
});

// --- Create account (its own native screen - full registration form, pushed on top of
// Login. Terms of Service/Privacy Policy are native screens too - see showLegalScreen
// below. On success, replaces itself with the Connect Instagram screen (not pushed - the
// account already exists at that point, so the back button shouldn't return here). ---

// --- Create Account is a 3-step wizard (Account -> Working hours -> Prices/FAQ/Terms)
// instead of one long scrolling form: step-dots + a bottom-right arrow that only lights
// up once the current step's required fields are actually filled in. All three steps
// stay inside the same <form> the whole time (nothing is created/destroyed switching
// steps) - hidden steps are simply skipped by the browser's own validation, and the
// final submit still gathers every field exactly like before. ---

const REGISTER_STEP_COUNT = 3;
let registerStep = 1;

// Keeps .register-steps-viewport's height matched to whichever step is currently
// active even when that step's OWN content changes size afterward (e.g. adding/removing
// a price row on step 3) - without this, the viewport's fixed height (set once in
// showRegisterStep below) plus its overflow:hidden would silently clip any content that
// grows past it.
let registerStepResizeObserver = null;

function showRegisterStep(n) {
    registerStep = n;
    // Slide the steps track by exact viewport-widths (pixels, not %, so this doesn't
    // depend on how the browser reports the track's own overflowing box width) - the
    // same transform+transition technique .screen itself uses for screen-to-screen nav.
    const viewport = el("register-steps-viewport");
    el("register-steps-track").style.transform = `translateX(-${viewport.clientWidth * (n - 1)}px)`;
    // A flex row of all 3 steps side by side sizes itself to the TALLEST one by default,
    // leaving dead scrollable space below shorter steps - matching the viewport's height
    // to just the step actually being shown removes that (and animates alongside the
    // horizontal slide via the CSS transition on .register-steps-viewport).
    const activeStep = el(`register-step-${n}`);
    viewport.style.height = `${activeStep.scrollHeight}px`;
    document.querySelectorAll(".register-step-dot").forEach((dot, i) => {
        dot.classList.toggle("active", i === n - 1);
    });
    el("register-next-btn").classList.toggle("hidden", n === REGISTER_STEP_COUNT);
    el("register-submit").classList.toggle("hidden", n !== REGISTER_STEP_COUNT);
    updateRegisterNextButtonValidity();

    if (registerStepResizeObserver) registerStepResizeObserver.disconnect();
    registerStepResizeObserver = new ResizeObserver(() => {
        viewport.style.height = `${activeStep.scrollHeight}px`;
    });
    registerStepResizeObserver.observe(activeStep);
}

// Live "already taken" checks for step 1's three unique fields - see
// setupAvailabilityField() above. Unlike steps 2/3 (still just a dimmed-but-tappable
// arrow that explains what's missing on tap), step 1's arrow is genuinely disabled once
// any of these comes back taken, or while a check is still in flight - see
// updateRegisterNextButtonValidity() below.
const registerUsernameAvailability = setupAvailabilityField(
    el("register-username"), el("register-username-error"), "username", "register.usernameTaken",
    () => el("register-username").value.trim(), updateRegisterNextButtonValidity,
);
const registerEmailAvailability = setupAvailabilityField(
    el("register-contact-email"), el("register-contact-email-error"), "contact_email", "register.emailTaken",
    () => el("register-contact-email").value.trim(), updateRegisterNextButtonValidity,
);
const registerPhoneAvailability = setupAvailabilityField(
    el("register-phone"), el("register-phone-error"), "phone_number", "register.phoneTaken",
    () => composePhoneNumber(el("register-phone"), selectedPhoneCountry), updateRegisterNextButtonValidity,
);

// Returns a specific error message if step 1 isn't ready to advance, or null if it is.
function registerStep1ValidationError() {
    const name = el("register-name").value.trim();
    const username = el("register-username").value.trim();
    const password = el("register-password").value;
    const confirmPassword = el("register-confirm-password").value;
    const emailField = el("register-contact-email");
    if (!name || !username) return t("register.errorFillRequired");
    if (password.length < 8) return t("register.errorPasswordLength");
    if (password !== confirmPassword) return t("register.errorPasswordMismatch");
    if (!emailField.value.trim() || !emailField.checkValidity()) return t("register.errorFillRequired");
    return null;
}

// Live red inline warning under "Password" the moment it's shorter than 8 characters,
// cleared the instant it reaches 8 - same field-error treatment as the confirm-password
// mismatch warning below.
function updatePasswordLengthError() {
    const password = el("register-password").value;
    const errorEl = el("register-password-error");
    if (password && password.length < 8) {
        errorEl.textContent = t("register.errorPasswordLength");
        errorEl.classList.remove("hidden");
    } else {
        errorEl.classList.add("hidden");
    }
}

// Live red inline warning under "Confirm password" the moment the two fields disagree -
// same field-error treatment as the username/email/phone "already taken" checks above,
// but purely local (no availability request needed to compare two strings). Empty confirm
// field stays quiet - only complain once the user has actually typed something that
// doesn't match, not while they're still on the way to typing it.
function updateConfirmPasswordError() {
    const password = el("register-password").value;
    const confirmPassword = el("register-confirm-password").value;
    const errorEl = el("register-confirm-password-error");
    if (confirmPassword && password !== confirmPassword) {
        errorEl.textContent = t("register.errorPasswordMismatch");
        errorEl.classList.remove("hidden");
    } else {
        errorEl.classList.add("hidden");
    }
}

// Working hours only ever holds valid times (it's all dropdowns/toggles), so the one way
// it can still be wrong is a closing time not after its opening time on an open day.
function registerStep2ValidationError() {
    if (!registerHoursPicker.validate()) return t("hoursPicker.errorOrder");
    return null;
}

// Price list rows are optional, but a half-filled one (service typed, price left blank
// or vice versa) used to just silently vanish on submit with no explanation. Terms
// acceptance is checked here too since it's on the same step.
function registerStep3ValidationError() {
    if (!registerPricesPicker.validate()) return t("pricePicker.errorIncomplete");
    if (!el("register-terms").checked) return t("register.errorTermsRequired");
    return null;
}

function currentRegisterStepError() {
    if (registerStep === 1) return registerStep1ValidationError();
    if (registerStep === 2) return registerStep2ValidationError();
    if (registerStep === 3) return registerStep3ValidationError();
    return null;
}

// Step 1 (Account) has its two named conditions (all required fields filled, and none
// of username/contact email/phone number taken) genuinely gate the arrow via .disabled -
// the inline field errors above already explain "why" for the taken case, and empty
// required fields are visually obvious, so there's no need to keep it tappable-but-dim
// there. Steps 2/3 (hours/prices/terms) keep the original behavior: dim only, still
// tappable, since tapping is what surfaces a specific reason via register-error - there's
// nothing here that needs a live per-field message the way "already taken" does.
function updateRegisterNextButtonValidity() {
    const btn = el("register-next-btn");
    if (registerStep === 1) {
        const blocked = !!registerStep1ValidationError()
            || registerUsernameAvailability.isBlocking()
            || registerEmailAvailability.isBlocking()
            || registerPhoneAvailability.isBlocking();
        btn.classList.toggle("register-next-btn-dim", blocked);
        btn.disabled = blocked;
    } else {
        btn.classList.toggle("register-next-btn-dim", !!currentRegisterStepError());
        btn.disabled = false;
    }
}
["register-name", "register-username", "register-password", "register-confirm-password", "register-contact-email"].forEach((id) => {
    el(id).addEventListener("input", updateRegisterNextButtonValidity);
});
el("register-password").addEventListener("input", updatePasswordLengthError);
el("register-password").addEventListener("input", updateConfirmPasswordError);
el("register-confirm-password").addEventListener("input", updateConfirmPasswordError);
el("register-hours-picker").addEventListener("change", updateRegisterNextButtonValidity);

el("register-next-btn").addEventListener("click", () => {
    const error = currentRegisterStepError();
    if (error) {
        el("register-error").textContent = error;
        el("register-error").classList.remove("hidden");
        return;
    }
    el("register-error").classList.add("hidden");
    if (registerStep < REGISTER_STEP_COUNT) showRegisterStep(registerStep + 1);
});

el("login-signup-link").addEventListener("click", () => {
    // Deliberately NOT register-form.reset() - the working-hours/price-list pickers
    // contain dynamically-created <select>/checkbox elements with no selected/checked
    // HTML attribute set, so a native form reset would revert them to the browser's own
    // default (first option / unchecked) instead of our intended defaults. Clear the
    // plain fields by hand and use each picker's own .reset()/.setValue("") instead.
    ["register-name", "register-username", "register-password", "register-confirm-password", "register-contact-email", "register-phone", "register-faq"].forEach((id) => {
        el(id).value = "";
    });
    el("register-terms").checked = false;
    registerHoursPicker.reset();
    registerPricesPicker.setValue("");
    registerUsernameAvailability.reset();
    registerEmailAvailability.reset();
    registerPhoneAvailability.reset();
    el("register-password-error").classList.add("hidden");
    el("register-confirm-password-error").classList.add("hidden");
    el("register-error").classList.add("hidden");
    showRegisterStep(1);
    showScreen("register");
});

// Back either steps back within the wizard, or (from step 1) exits to Login.
el("register-back").addEventListener("click", () => {
    if (registerStep > 1) {
        showRegisterStep(registerStep - 1);
    } else {
        showScreen("login");
    }
});

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

    // Enter key in a text field implicitly submits the form regardless of which step is
    // visible - treat that the same as tapping the arrow (advance a step, or explain
    // what's missing) unless we're actually on the last one.
    if (registerStep < REGISTER_STEP_COUNT) {
        const error = currentRegisterStepError();
        if (error) {
            el("register-error").textContent = error;
            el("register-error").classList.remove("hidden");
            return;
        }
        el("register-error").classList.add("hidden");
        showRegisterStep(registerStep + 1);
        return;
    }

    const errorBox = el("register-error");
    const submitBtn = el("register-submit");

    const finalError = registerStep3ValidationError();
    if (finalError) {
        errorBox.textContent = finalError;
        errorBox.classList.remove("hidden");
        return;
    }

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
                phone_number: composePhoneNumber(el("register-phone"), selectedPhoneCountry),
                working_hours: registerHoursPicker.getValue(),
                price_list: registerPricesPicker.getValue(),
                faq: el("register-faq").value,
                accept_terms: el("register-terms").checked,
            },
        });
        showVerifyPendingScreen(data.business_id, data.contact_email);
    } catch (err) {
        errorBox.textContent = err.message || t("register.error");
        errorBox.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = t("register.submit");
    }
});

// --- Verify pending - shown after registration, or after a login attempt against an
// account that hasn't confirmed its email yet (see the login-form and register-form
// handlers above). Doesn't hold a token - the account can't do anything else until the
// emailed link is opened, so this is a dead end with only "resend" and "back to login". ---

function showVerifyPendingScreen(businessId, email) {
    state.pendingVerifyBusinessId = businessId;
    state.pendingVerifyEmail = email;
    // Falls back to the translated generic "your email" placeholder (its own data-i18n
    // default, restored here since a previous call may have overwritten it with a real
    // address) when the caller doesn't know the address - e.g. a login attempt only ever
    // gets a business_id back, never the contact_email itself.
    el("verify-pending-email").textContent = email || t("verifyPending.yourEmail");
    el("verify-pending-resent").classList.add("hidden");
    showScreen("verifyPending");
}

el("verify-pending-resend").addEventListener("click", async () => {
    const btn = el("verify-pending-resend");
    btn.disabled = true;
    try {
        await api("/api/resend-verification", { method: "POST", body: { business_id: state.pendingVerifyBusinessId } });
        el("verify-pending-resent").classList.remove("hidden");
    } catch (err) {
        // Best-effort - the endpoint always responds sent:true when reachable at all, so
        // this only fires on a real network error, already surfaced by the offline banner.
    } finally {
        btn.disabled = false;
    }
});

el("verify-pending-back").addEventListener("click", () => {
    state.pendingVerifyBusinessId = null;
    showScreen("login");
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

// Connect Instagram is a mandatory gate with nowhere else to "go back" to (it replaces
// the register screen rather than being pushed on top of it, and a logged-in-but-not-
// yet-connected business lands here straight from login too) - the back arrow's only
// sensible action is the same one the old "Log out" link performed, just presented as
// standard back-navigation chrome instead of an explicit logout affordance.
el("connect-instagram-back").addEventListener("click", logout);

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

    // This is what makes the inbox feel real-time without any manual "Refresh" action
    // (Faza 3): Capacitor fires "pushNotificationReceived" instead of showing a system
    // notification banner when a push arrives while the app is already open - a new
    // customer message shows up in the list within a second or two of arriving, with no
    // pull-to-refresh or tap required. The 15s poll below is just the safety net for
    // when this doesn't fire (push permission declined, a dropped push, etc.).
    capacitorPlugins.PushNotifications.addListener("pushNotificationReceived", () => {
        if (state.activeTab === "inbox") loadDashboard({ silent: true });
    });
}

// --- Auto-refresh (only while the app is in the foreground, on the inbox tab) - a
// fallback safety net underneath the instant push-triggered refresh above, for whenever
// that doesn't fire (push permission declined, Firebase not configured, a dropped
// push...). Short enough that even without a single push ever arriving, the inbox still
// reads as "basically real-time" rather than something you have to remember to refresh. ---

let autoRefreshTimer = null;

function startAutoRefresh() {
    if (autoRefreshTimer) return;
    autoRefreshTimer = setInterval(() => {
        if (document.visibilityState === "visible" && state.activeTab === "inbox" && state.token) {
            loadDashboard({ silent: true });
        }
    }, 15000);
}

// --- Inbox ---

// Locale tags for toLocale*String - keyed to our own language picker rather than
// navigator.language, so dates/times follow the language the user actually chose in
// the app instead of the device's (which can disagree, e.g. a phone set to English
// with the app switched to Serbian).
// sr-Latn-RS (not the bare "sr-RS" you'd expect) - Serbia's default script in ICU is
// Cyrillic, but every Serbian string in i18n.js is written in Latin script, so the
// plain region tag would render dates in a different script than the rest of the UI.
const LOCALE_TAGS = {
    en: "en-US", sr: "sr-Latn-RS", es: "es-ES", pt: "pt-PT", fr: "fr-FR",
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
        el("count-archived").textContent = data.archived_count;
        el("count-trash").textContent = data.trash_count;
        el("trash-note").classList.toggle("hidden", state.filter !== "trash");

        el("banner-inactive").classList.toggle("hidden", data.beta_free_mode || data.subscription_active);

        state.betaFreeMode = !!data.beta_free_mode;
        state.subscriptionActive = !!data.subscription_active;
        state.subscriptionStatus = data.subscription_status || "none";
        state.proAccess = !!data.pro_access;
        renderSubscriptionScreen();
        applyProAccessLocks();

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
        if (state.search.trim()) {
            el("inbox-empty-title").textContent = t("inbox.emptySearchTitle");
            el("inbox-empty-detail").textContent = t("inbox.emptySearchDetail");
        } else if (state.filter === "archived") {
            el("inbox-empty-title").textContent = t("inbox.emptyArchivedTitle");
            el("inbox-empty-detail").textContent = t("inbox.emptyArchivedDetail");
        } else if (state.filter === "trash") {
            el("inbox-empty-title").textContent = t("inbox.emptyTrashTitle");
            el("inbox-empty-detail").textContent = t("inbox.emptyTrashDetail");
        } else {
            el("inbox-empty-title").textContent = t("inbox.emptyTitle");
            el("inbox-empty-detail").textContent = t("inbox.emptyDetail");
        }
        return;
    }
    el("inbox-empty").classList.add("hidden");

    conversations.forEach((c) => {
        const row = document.createElement("div");
        row.className = "inbox-row";

        const tapArea = document.createElement("button");
        tapArea.type = "button";
        tapArea.className = "inbox-row-tap";
        tapArea.innerHTML = `
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
        tapArea.addEventListener("click", () => openDetail(c));

        const moreBtn = document.createElement("button");
        moreBtn.type = "button";
        moreBtn.className = "inbox-row-more";
        moreBtn.setAttribute("aria-label", "More options");
        moreBtn.textContent = "⋯";
        moreBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            openRowActions(c);
        });

        row.appendChild(tapArea);
        row.appendChild(moreBtn);
        list.appendChild(row);
    });
}

// --- Archive / Delete / Restore / Unarchive (Faza 3) ---
//
// A "more options" (⋯) button per row opens a bottom-sheet with just the actions that
// make sense for the tab it's shown from - reuses the same fixed-overlay pattern as the
// language/country pickers (see #row-actions-modal in index.html).
let rowActionsTarget = null;

function openRowActions(conversation) {
    rowActionsTarget = conversation;
    const inTrash = state.filter === "trash";
    const inArchive = state.filter === "archived";
    el("row-action-archive").classList.toggle("hidden", inTrash || inArchive);
    el("row-action-unarchive").classList.toggle("hidden", !inArchive);
    el("row-action-restore").classList.toggle("hidden", !inTrash);
    // Trash already means "deleted" - nothing further to delete to from there (no manual
    // permanent-delete action; purge_expired_trash() on the server handles that after
    // 30 days). Archive and the normal tabs can both delete straight to Trash.
    el("row-action-delete").classList.toggle("hidden", inTrash);
    el("row-actions-modal").classList.add("open");
}

function closeRowActions() {
    el("row-actions-modal").classList.remove("open");
    rowActionsTarget = null;
}

el("row-actions-backdrop").addEventListener("click", closeRowActions);
el("row-action-cancel").addEventListener("click", closeRowActions);

async function runRowAction(path) {
    if (!rowActionsTarget) return;
    const conversationId = rowActionsTarget.id;
    closeRowActions();
    try {
        await api(`/api/conversations/${conversationId}/${path}`, { method: "POST" });
        if (capacitorPlugins && capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        loadDashboard();
    } catch (err) {
        reportClientError(err.message, err.stack, `inbox-${path}`);
    }
}

el("row-action-archive").addEventListener("click", () => runRowAction("archive"));
el("row-action-unarchive").addEventListener("click", () => runRowAction("unarchive"));
el("row-action-delete").addEventListener("click", () => runRowAction("delete"));
el("row-action-restore").addEventListener("click", () => runRowAction("restore"));

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

// Pull-to-refresh removed (Faza 3) - the inbox now updates on its own (instant
// push-triggered refresh + a 15s poll fallback, see startAutoRefresh above), so there's
// no longer a manual "Refresh" gesture to offer.

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

    // Archived/Trash conversations are read-only here too (matches the web dashboard's
    // same restriction) - sending a reply to something already archived or deleted would
    // be a confusing way to act on it without restoring/unarchiving it first.
    const canReply = isPending && state.filter !== "archived" && state.filter !== "trash";
    el("detail-pending-block").classList.toggle("hidden", !canReply);
    el("detail-sent-block").classList.toggle("hidden", canReply);

    if (canReply) {
        el("detail-reply").value = conversation.suggested_reply;
    } else {
        el("detail-final-reply").textContent = isPending ? conversation.suggested_reply : conversation.final_reply;
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

// The Save button stays hidden until the form actually differs from what was loaded (a
// serialized snapshot comparison, not a generic "something fired an event" flag - so
// re-picking the same phone country or an untouched field never shows it), then goes
// from disabled/gray to enabled/yellow only once every field satisfies its own
// validation - the same checks the click handler below re-runs before actually saving.
let profileFormSnapshot = null;

function serializeProfileForm() {
    return JSON.stringify({
        name: el("settings-name").value,
        username: el("settings-username").value,
        contactEmail: el("settings-contact-email").value,
        phone: composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry),
        hours: profileHoursPicker.getValue(),
        prices: profilePricesPicker.getValue(),
        faq: el("settings-faq").value,
        upsell: el("settings-upsell-pairs").value,
        currentPassword: el("settings-current-password").value,
        newPassword: el("settings-new-password").value,
    });
}

// Same live "already taken" mechanism as Create Account step 1 (see
// setupAvailabilityField above) - here the caller IS logged in, so the Bearer token lets
// the server exclude this business's own current value automatically (editing a field
// without changing it never flags itself as taken).
function isProfileFormValid() {
    const username = el("settings-username").value.trim();
    const contactEmailField = el("settings-contact-email");
    const contactEmail = contactEmailField.value.trim();
    const newPassword = el("settings-new-password").value;
    const currentPassword = el("settings-current-password").value;

    if (!username || !contactEmail || !contactEmailField.checkValidity()) return false;
    if (profileUsernameAvailability.isBlocking() || profileEmailAvailability.isBlocking() || profilePhoneAvailability.isBlocking()) return false;
    if (newPassword && !currentPassword) return false;
    if (newPassword && newPassword.length < 8) return false;
    if (!profileHoursPicker.validate()) return false;
    if (!profilePricesPicker.validate()) return false;
    return true;
}

function updateSettingsSaveValidity() {
    const button = el("settings-save");
    const dirty = profileFormSnapshot !== null && serializeProfileForm() !== profileFormSnapshot;
    button.classList.toggle("hidden", !dirty);
    button.disabled = !isProfileFormValid();
}

// Delegated instead of one listener per field - also catches the hours/prices pickers'
// custom controls (checkboxes, the option-button dropdowns, add/remove service rows),
// which either bubble a "change"/"click" or (for the option buttons) dispatch a synthetic
// bubbling "change" themselves (see createOptionButton above).
["input", "change", "click"].forEach((evt) => {
    el("screen-profile").addEventListener(evt, updateSettingsSaveValidity);
});

const profileUsernameAvailability = setupAvailabilityField(
    el("settings-username"), el("settings-username-error"), "username", "register.usernameTaken",
    () => el("settings-username").value.trim(), updateSettingsSaveValidity,
);
const profileEmailAvailability = setupAvailabilityField(
    el("settings-contact-email"), el("settings-contact-email-error"), "contact_email", "register.emailTaken",
    () => el("settings-contact-email").value.trim(), updateSettingsSaveValidity,
);
const profilePhoneAvailability = setupAvailabilityField(
    el("settings-phone"), el("settings-phone-error"), "phone_number", "register.phoneTaken",
    () => composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry), updateSettingsSaveValidity,
);

function updatePhoneVerifyStatus(phoneNumber, phoneVerified) {
    el("settings-phone-verify-sent").classList.add("hidden");
    el("settings-phone-empty-hint").classList.toggle("hidden", !!phoneNumber);
    el("settings-phone-verified-badge").classList.toggle("hidden", !(phoneNumber && phoneVerified));
    el("settings-phone-unverified-banner").classList.toggle("hidden", !(phoneNumber && !phoneVerified));
}

async function loadSettingsIntoForm() {
    try {
        const data = await api("/api/settings");
        el("settings-name").value = data.name;
        el("settings-username").value = data.username;
        el("settings-contact-email").value = data.contact_email;
        const { country, rest } = splitStoredPhoneNumber(data.phone_number);
        setProfilePhoneCountry(country);
        el("settings-phone").value = rest;
        updatePhoneVerifyStatus(data.phone_number, data.phone_verified);
        el("settings-instagram-account").value = data.instagram_username ? "@" + data.instagram_username : "";
        profileHoursPicker.setValue(data.working_hours);
        profilePricesPicker.setValue(data.price_list);
        el("settings-faq").value = data.faq;
        el("settings-upsell-pairs").value = data.upsell_pairs || "";
        el("settings-current-password").value = "";
        el("settings-new-password").value = "";
        applyProAccessLocks();
        profileFormSnapshot = serializeProfileForm();
        updateSettingsSaveValidity();
    } catch (err) {
        if (err.status === 401) logout();
    }
}

el("settings-save").addEventListener("click", async () => {
    const button = el("settings-save");
    const errorBox = el("settings-error");
    errorBox.classList.add("hidden");

    const username = el("settings-username").value.trim();
    const contactEmailField = el("settings-contact-email");
    const contactEmail = contactEmailField.value.trim();
    const newPassword = el("settings-new-password").value;
    const currentPassword = el("settings-current-password").value;

    if (!username || !contactEmail || !contactEmailField.checkValidity()) {
        errorBox.textContent = t("register.errorFillRequired");
        errorBox.classList.remove("hidden");
        return;
    }
    if (profileUsernameAvailability.isBlocking() || profileEmailAvailability.isBlocking() || profilePhoneAvailability.isBlocking()) {
        errorBox.textContent = t("settings.errorFieldTaken");
        errorBox.classList.remove("hidden");
        return;
    }
    if (newPassword && !currentPassword) {
        errorBox.textContent = t("settings.errorCurrentPasswordRequired");
        errorBox.classList.remove("hidden");
        return;
    }
    if (newPassword && newPassword.length < 8) {
        errorBox.textContent = t("register.errorPasswordLength");
        errorBox.classList.remove("hidden");
        return;
    }
    if (!profileHoursPicker.validate()) {
        errorBox.textContent = t("hoursPicker.errorOrder");
        errorBox.classList.remove("hidden");
        return;
    }
    if (!profilePricesPicker.validate()) {
        errorBox.textContent = t("pricePicker.errorIncomplete");
        errorBox.classList.remove("hidden");
        return;
    }

    button.disabled = true;
    button.textContent = t("settings.saveLoading");

    try {
        const data = await api("/api/settings", {
            method: "POST",
            body: {
                name: el("settings-name").value,
                username,
                contact_email: contactEmail,
                phone_number: composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry),
                working_hours: profileHoursPicker.getValue(),
                price_list: profilePricesPicker.getValue(),
                faq: el("settings-faq").value,
                upsell_pairs: state.proAccess ? el("settings-upsell-pairs").value : undefined,
                current_password: currentPassword || undefined,
                new_password: newPassword || undefined,
            },
        });
        showToast(t("settings.saved"));
        el("inbox-business-name").textContent = el("settings-name").value;
        el("settings-current-password").value = "";
        el("settings-new-password").value = "";
        updatePhoneVerifyStatus(data.phone_number, data.phone_verified);
        profileFormSnapshot = serializeProfileForm();
    } catch (err) {
        errorBox.textContent = err.message || t("settings.error");
        errorBox.classList.remove("hidden");
    } finally {
        // NOT an unconditional button.disabled = false - if the user edited a field to a
        // value that resolved as "taken" while this request was in flight, that check
        // runs independently (see updateSettingsSaveValidity) and may have already set
        // disabled = true; unconditionally clearing it here would silently re-enable
        // Save over a known-taken value. Deferring to the same validity check keeps
        // there being exactly one source of truth for this button's disabled state.
        updateSettingsSaveValidity();
        button.textContent = t("settings.save");
    }
});

el("settings-phone-verify-btn").addEventListener("click", async () => {
    const btn = el("settings-phone-verify-btn");
    btn.disabled = true;
    try {
        await api("/api/verify-phone/send", { method: "POST" });
        el("settings-phone-verify-sent").classList.remove("hidden");
    } catch (err) {
        // Best-effort, same as the account-email resend button - a real failure here is
        // already surfaced by the offline banner.
    } finally {
        btn.disabled = false;
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
el("settings-subscription-row").addEventListener("click", () => { subscriptionReturnTo = "settings"; showScreen("subscription"); });
el("subscription-back").addEventListener("click", () => showScreen(subscriptionReturnTo));

// --- Subscription screen content (Faza 5) - which of the beta/trialing/active/needs-
// plan blocks is shown, driven entirely by the same fields /api/dashboard already
// returns (see loadDashboard above) - no separate fetch needed just to open this screen. ---
function renderSubscriptionScreen() {
    const needsPlan = !state.betaFreeMode && !state.subscriptionActive;
    const trialing = !state.betaFreeMode && state.subscriptionActive && state.subscriptionStatus === "trialing";
    const active = !state.betaFreeMode && state.subscriptionActive && state.subscriptionStatus !== "trialing";
    el("subscription-beta").classList.toggle("hidden", !state.betaFreeMode);
    el("subscription-trialing").classList.toggle("hidden", !trialing);
    el("subscription-active").classList.toggle("hidden", !active);
    el("subscription-needs-plan").classList.toggle("hidden", !needsPlan);

    if (needsPlan) {
        // subscription_status "none" means this account never started a trial at all
        // (real Stripe billing is live and it hasn't picked a plan/entered a card yet -
        // see api.py's api_register()) - "Your free trial has ended" would be a lie, so
        // swap in "start your trial" copy instead. Same distinction app.py's
        // subscription_required makes for the website's equivalent redirect.
        const neverStarted = state.subscriptionStatus === "none";
        const bodyEl = el("subscription-needs-plan-body");
        const key = neverStarted ? "subscription.needsPlanBodyNew" : "subscription.needsPlanBody";
        bodyEl.setAttribute("data-i18n", key);
        bodyEl.textContent = t(key);
    }
}

// "Choose a plan" opens the website's own Starter/Pro pricing page (built for the web,
// not duplicated natively) in the in-app browser - same short-lived-signed-link bridge
// already used for Export my data and Connect Instagram, since the mobile app's Bearer
// token can't be attached to a request the Browser plugin opens.
async function openChoosePlan() {
    try {
        const data = await api("/api/subscription-link");
        if (capacitorPlugins && capacitorPlugins.Browser) {
            await capacitorPlugins.Browser.open({ url: data.url });
        } else {
            window.open(data.url, "_blank");
        }
    } catch (err) {
        reportClientError(err.message, err.stack, "subscription-link");
    }
}
el("subscription-choose-plan-btn").addEventListener("click", openChoosePlan);
el("subscription-choose-plan-btn-2").addEventListener("click", openChoosePlan);

const SUPPORT_EMAIL = "support@ai-auto-reply-mail.com";

el("help-email-btn").addEventListener("click", () => {
    // Plain mailto navigation - works when the device has a mail app configured (most
    // do). "Copy email address" right below is the guaranteed fallback for when it isn't.
    window.open(`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent("Camilius support")}`, "_system");
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
    if (tab === "settings") loadNotificationPreferences();
    if (tab === "schedule") loadSchedule();
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
});

// --- Schedule (Faza 12) - native day/week calendar tab, JSON equivalent of the
// website's /schedule page (see api.py's api_schedule and friends). Renders its own
// timeline/week grid from the same booking.py logic the website uses, instead of
// bridging out to the website in an in-app browser like the old Settings > Schedule
// row used to (see mobile/index.html git history for that bridge, still available as
// /api/schedule-link if it's ever needed again).
function scheduleNoHoursMessage() {
    return `${t("schedule.noWorkingHours")} ${t("tab.profile")} ${t("schedule.pageSuffix")}`;
}

// Same LOCALE_TAGS map the Inbox tab's timeAgo() uses (see above) - keeps date
// formatting on the Schedule tab consistent with the rest of the app.
function scheduleDateLocale() {
    return LOCALE_TAGS[currentLang] || "en-US";
}

async function loadSchedule(date) {
    el("schedule-locked").classList.add("hidden");
    el("schedule-body").classList.add("hidden");
    el("schedule-error").classList.add("hidden");

    const params = new URLSearchParams({ view: state.scheduleView });
    if (date || state.scheduleDate) params.set("date", date || state.scheduleDate);

    try {
        const data = await api(`/api/schedule?${params.toString()}`);
        if (data.locked) {
            el("schedule-locked").classList.remove("hidden");
            return;
        }
        state.scheduleDate = data.selected_date;
        state.scheduleSlotMinutes = data.slot_minutes;
        el("schedule-body").classList.remove("hidden");
        renderSchedule(data);
    } catch (err) {
        if (err.status === 401) { logout(); return; }
        el("schedule-error").classList.remove("hidden");
        el("schedule-error-message").textContent = err.message || t("schedule.errorTitle");
        reportClientError(err.message, err.stack, "schedule-load");
    }
}

function renderSchedule(data) {
    el("schedule-no-hours").classList.toggle("hidden", data.has_any_open_day);
    el("schedule-no-hours").textContent = scheduleNoHoursMessage();

    el("schedule-duration-label").textContent = `${data.slot_minutes} min`;
    state.scheduleSlotChoices = data.slot_duration_choices;

    el("schedule-prev-btn").dataset.date = data.prev_date;
    el("schedule-next-btn").dataset.date = data.next_date;

    const selectedDate = new Date(`${data.selected_date}T00:00:00`);
    if (data.view === "week") {
        const days = data.days.map((d) => new Date(`${d.date}T00:00:00`));
        const fmt = (d) => d.toLocaleDateString(scheduleDateLocale(), { month: "short", day: "numeric" });
        el("schedule-date-label").textContent = `${t("schedule.weekOf")} ${fmt(days[0])} - ${fmt(days[6])}`;
    } else {
        el("schedule-date-label").textContent = selectedDate.toLocaleDateString(scheduleDateLocale(), { weekday: "long", month: "short", day: "numeric", year: "numeric" });
    }

    el("schedule-day-view").classList.toggle("hidden", data.view !== "day");
    el("schedule-week-view").classList.toggle("hidden", data.view !== "week");
    if (data.view === "week") {
        renderScheduleWeek(data);
    } else {
        renderScheduleDay(data);
    }
}

function renderScheduleDay(data) {
    const timeline = el("schedule-timeline");
    const closedBox = el("schedule-closed-day");
    timeline.innerHTML = "";

    if (data.slot_rows.length === 0) {
        closedBox.classList.remove("hidden");
        return;
    }
    closedBox.classList.add("hidden");

    data.slot_rows.forEach((row) => {
        const rowEl = document.createElement("div");
        rowEl.className = "schedule-timeline-row" + (row.time.endsWith(":00") ? " schedule-timeline-row-hour" : "");

        const timeEl = document.createElement("div");
        timeEl.className = "schedule-timeline-time";
        timeEl.textContent = row.time;
        rowEl.appendChild(timeEl);

        const contentEl = document.createElement("div");
        contentEl.className = "schedule-timeline-content";

        if (row.appointment) {
            const card = document.createElement("div");
            card.className = "schedule-slot schedule-slot-booked";

            const info = document.createElement("div");
            info.className = "schedule-slot-booked-info";
            const badge = document.createElement("span");
            badge.className = "badge schedule-slot-booked-badge " + (row.appointment.source === "ai" ? "badge-sent" : "badge-pending");
            badge.textContent = row.appointment.source === "ai" ? t("schedule.aiBooked") : t("schedule.manual");
            const name = document.createElement("div");
            name.className = "schedule-slot-booked-name";
            name.textContent = row.appointment.customer_name;
            info.appendChild(name);
            if (row.appointment.service) {
                const service = document.createElement("div");
                service.className = "schedule-slot-booked-service";
                service.textContent = row.appointment.service;
                info.appendChild(service);
            }
            info.appendChild(badge);
            card.appendChild(info);

            const cancelBtn = document.createElement("button");
            cancelBtn.type = "button";
            cancelBtn.className = "schedule-slot-cancel";
            cancelBtn.textContent = t("schedule.cancel");
            cancelBtn.addEventListener("click", () => cancelAppointment(row.appointment.id));
            card.appendChild(cancelBtn);

            contentEl.appendChild(card);
        } else if (row.is_past) {
            const past = document.createElement("div");
            past.className = "schedule-slot schedule-slot-past";
            past.textContent = t("schedule.past");
            contentEl.appendChild(past);
        } else {
            const freeBtn = document.createElement("button");
            freeBtn.type = "button";
            freeBtn.className = "schedule-slot schedule-slot-free";
            freeBtn.textContent = t("schedule.book");
            freeBtn.addEventListener("click", () => openBookModal(row.time));
            contentEl.appendChild(freeBtn);
        }

        rowEl.appendChild(contentEl);
        timeline.appendChild(rowEl);
    });
}

function renderScheduleWeek(data) {
    const list = el("schedule-week-list");
    list.innerHTML = "";

    data.days.forEach((day) => {
        const dayEl = document.createElement("div");
        dayEl.className = "schedule-week-day" + (day.is_today ? " today" : "");

        const date = new Date(`${day.date}T00:00:00`);
        const header = document.createElement("div");
        header.className = "schedule-week-day-header";
        const weekday = document.createElement("span");
        weekday.textContent = date.toLocaleDateString(scheduleDateLocale(), { weekday: "short" });
        const num = document.createElement("span");
        num.className = "schedule-week-day-num";
        num.textContent = date.getDate();
        header.appendChild(weekday);
        header.appendChild(num);
        dayEl.appendChild(header);

        if (day.closed) {
            const closed = document.createElement("div");
            closed.className = "schedule-week-closed";
            closed.textContent = t("schedule.closed");
            dayEl.appendChild(closed);
        } else if (day.appointments.length === 0) {
            const empty = document.createElement("div");
            empty.className = "schedule-week-closed";
            empty.textContent = t("schedule.noAppointments");
            dayEl.appendChild(empty);
        } else {
            day.appointments.forEach((appt) => {
                const apptEl = document.createElement("div");
                apptEl.className = "schedule-week-appt" + (appt.source === "ai" ? " schedule-week-appt-ai" : "");
                apptEl.textContent = `${appt.time} · ${appt.customer_name}`;
                dayEl.appendChild(apptEl);
            });
        }

        dayEl.addEventListener("click", () => {
            state.scheduleView = "day";
            el("schedule-view-day-btn").classList.add("active");
            el("schedule-view-week-btn").classList.remove("active");
            loadSchedule(day.date);
        });
        list.appendChild(dayEl);
    });
}

el("schedule-view-day-btn").addEventListener("click", () => {
    if (state.scheduleView === "day") return;
    state.scheduleView = "day";
    el("schedule-view-day-btn").classList.add("active");
    el("schedule-view-week-btn").classList.remove("active");
    loadSchedule();
});
el("schedule-view-week-btn").addEventListener("click", () => {
    if (state.scheduleView === "week") return;
    state.scheduleView = "week";
    el("schedule-view-week-btn").classList.add("active");
    el("schedule-view-day-btn").classList.remove("active");
    loadSchedule();
});
el("schedule-prev-btn").addEventListener("click", (event) => loadSchedule(event.currentTarget.dataset.date));
el("schedule-next-btn").addEventListener("click", (event) => loadSchedule(event.currentTarget.dataset.date));
el("schedule-today-btn").addEventListener("click", () => loadSchedule(new Date().toISOString().slice(0, 10)));
el("schedule-retry-btn").addEventListener("click", () => loadSchedule());

el("schedule-upgrade-btn").addEventListener("click", () => {
    subscriptionReturnTo = "schedule";
    showScreen("subscription");
});

el("schedule-duration-btn").addEventListener("click", () => {
    const btn = el("schedule-duration-btn");
    if (optionPicker.isOpenFor(btn)) { optionPicker.close(); return; }
    const options = state.scheduleSlotChoices.map((m) => `${m} min`);
    optionPicker.open(btn, options, `${state.scheduleSlotMinutes} min`, async (chosen) => {
        const minutes = parseInt(chosen, 10);
        if (minutes === state.scheduleSlotMinutes) return;
        try {
            await api("/api/schedule/slot-duration", { method: "POST", body: { slot_duration: minutes } });
            loadSchedule();
        } catch (err) {
            reportClientError(err.message, err.stack, "schedule-slot-duration");
        }
    });
});

// --- Book-a-slot bottom sheet ---

function openBookModal(time) {
    state.scheduleBookSlot = time;
    el("book-modal-time").textContent = `${new Date(`${state.scheduleDate}T00:00:00`).toLocaleDateString(scheduleDateLocale(), { weekday: "long", month: "short", day: "numeric" })} · ${time}`;
    el("book-customer-name").value = "";
    el("book-service").value = "";
    el("book-modal-error").classList.add("hidden");
    el("book-modal").classList.add("open");
    setTimeout(() => el("book-customer-name").focus(), 250);
}

function closeBookModal() {
    el("book-modal").classList.remove("open");
    state.scheduleBookSlot = null;
}

el("book-modal-backdrop").addEventListener("click", closeBookModal);
el("book-modal-cancel").addEventListener("click", closeBookModal);

el("book-modal-submit").addEventListener("click", async () => {
    const errorBox = el("book-modal-error");
    errorBox.classList.add("hidden");
    const customerName = el("book-customer-name").value.trim();
    if (!customerName) {
        errorBox.textContent = t("schedule.customerNamePlaceholder");
        errorBox.classList.remove("hidden");
        return;
    }

    try {
        await api("/api/schedule/appointments", {
            method: "POST",
            body: {
                date: state.scheduleDate,
                time: state.scheduleBookSlot,
                customer_name: customerName,
                service: el("book-service").value.trim(),
            },
        });
        closeBookModal();
        if (capacitorPlugins && capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        loadSchedule();
    } catch (err) {
        errorBox.textContent = err.status === 409 ? t("schedule.bookError") : (err.message || t("schedule.bookError"));
        errorBox.classList.remove("hidden");
    }
});

async function cancelAppointment(appointmentId) {
    try {
        await api(`/api/schedule/appointments/${appointmentId}/cancel`, { method: "POST" });
        if (capacitorPlugins && capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        loadSchedule();
    } catch (err) {
        reportClientError(err.message, err.stack, "schedule-cancel");
    }
}

// Loads the current push/auto-send preferences into the toggles - purely local UI
// state until the user taps "Save changes" below (Faza 4: these used to POST instantly
// on every toggle flip; now nothing is sent to the server until Save is tapped, same as
// the Profile screen's own Save button).
async function loadNotificationPreferences() {
    try {
        const data = await api("/api/notification-preferences");
        el("settings-push-toggle").checked = !!data.push_enabled;
        el("settings-autosend-toggle").checked = !!data.auto_send;
        el("settings-reminders-toggle").checked = !!data.reminders_enabled;
        applyProAccessLocks();
        appSettingsSnapshot = serializeAppSettingsForm();
        updateAppSettingsSaveState();
    } catch (err) {
        if (err.status === 401) logout();
    }
}

// Same "hidden until dirty" treatment as the Profile screen's Save button - these three
// toggles have no "invalid" state of their own (a checkbox flip is always a valid value),
// so once dirty this one goes straight to enabled rather than passing through a gray
// step.
let appSettingsSnapshot = null;

function serializeAppSettingsForm() {
    return JSON.stringify({
        push: el("settings-push-toggle").checked,
        autoSend: el("settings-autosend-toggle").checked,
        reminders: el("settings-reminders-toggle").checked,
    });
}

function updateAppSettingsSaveState() {
    const dirty = appSettingsSnapshot !== null && serializeAppSettingsForm() !== appSettingsSnapshot;
    el("settings-app-save").classList.toggle("hidden", !dirty);
}

["settings-push-toggle", "settings-autosend-toggle", "settings-reminders-toggle"].forEach((id) => {
    el(id).addEventListener("change", updateAppSettingsSaveState);
});

// Faza 11: locks the No-show reminders toggle and AI upsell field for a Starter-plan
// (or lapsed) business - mirrors the website's .pro-locked treatment. state.proAccess
// comes from /api/dashboard's response (see loadInbox), so this is called both there
// and after loadNotificationPreferences/loadSettingsIntoForm in case Settings/Profile
// is opened before the Inbox tab has ever loaded once.
function applyProAccessLocks() {
    const remindersToggle = el("settings-reminders-toggle");
    const remindersRow = el("settings-reminders-row");
    if (remindersToggle && remindersRow) {
        remindersToggle.disabled = !state.proAccess;
        remindersRow.classList.toggle("pro-locked", !state.proAccess);
        if (!state.proAccess) remindersToggle.checked = false;
    }
    const upsellField = el("settings-upsell-pairs");
    const upsellHint = el("settings-upsell-hint");
    const upsellLockedHint = el("settings-upsell-locked-hint");
    if (upsellField) {
        upsellField.disabled = !state.proAccess;
        if (upsellHint) upsellHint.classList.toggle("hidden", !state.proAccess);
        if (upsellLockedHint) upsellLockedHint.classList.toggle("hidden", state.proAccess);
    }
    // A plan downgrade arriving mid-edit (loadDashboard's poll, not just the initial
    // load) can force the reminders toggle off from under the user - re-check dirtiness
    // so the Save button reacts to that too, not just to the user's own taps.
    updateAppSettingsSaveState();
}

el("settings-app-save").addEventListener("click", async () => {
    const button = el("settings-app-save");
    const errorBox = el("settings-app-error");
    errorBox.classList.add("hidden");
    button.disabled = true;

    try {
        await api("/api/notification-preferences", {
            method: "POST",
            body: {
                push_enabled: el("settings-push-toggle").checked,
                auto_send: el("settings-autosend-toggle").checked,
                reminders_enabled: state.proAccess ? el("settings-reminders-toggle").checked : undefined,
            },
        });
        showToast(t("settings.saved"));
        appSettingsSnapshot = serializeAppSettingsForm();
        updateAppSettingsSaveState();
    } catch (err) {
        errorBox.textContent = err.message || t("settings.error");
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
    }
});

// --- Onboarding (shown once, only inside the installed app) ---
//
// Beyond the dot indicator, this drives two visual touches that make the intro feel
// premium rather than a bare emoji-and-caption swipe:
//  - a per-slide accent glow behind the whole screen (--onboarding-accent), read from
//    each slide's data-accent and swapped as the active slide changes;
//  - a live scroll-progress custom property (--p, -1..1) written to every slide on
//    each scroll frame, which app.css uses for the copy/art's own fade-and-settle as
//    slides pass - deliberately continuous and undebounced, so it's always in sync
//    with the real scroll position and has no separate "trigger" that could ever fire
//    late against content that's already visible.
// The active slide also gets an "is-active" class - once scrolling settles, not on
// every frame - which gates only the small looping decorations (typing dots, send
// icon, pulsing badge) defined in app.css, kept off on inactive slides so they don't
// run (or replay stale) off-screen. It deliberately does NOT gate anything that could
// hide already-visible core content - see the comment in app.css above the (removed)
// old entrance animation for why that combination used to look like the slide
// reloading a couple seconds after arriving.
const ONBOARDING_ACCENTS = {
    1: "var(--primary)",
    2: "var(--warning)",
    3: "var(--primary-dark)",
    4: "var(--success)",
};

function setupOnboarding() {
    const screen = el("screen-onboarding");
    const slides = el("onboarding-slides");
    const slideEls = Array.from(slides.querySelectorAll(".onboarding-slide"));
    const dots = document.querySelectorAll("#onboarding-dots .onboarding-dot");
    let activeIndex = -1;
    let ticking = false;
    let settleTimer = null;

    // Live parallax + dots + accent glow - runs on every scroll frame while dragging,
    // so the swipe (and the dot indicator and background glow color tracking it)
    // follows the finger with no lag. Dots and the glow are both a plain CSS
    // transition (background-color / width), not a keyframe animation with a "snap to
    // an earlier state" risk, so - unlike is-active below - there's no reason to hold
    // them back for a settle debounce; doing that only made them visibly trail behind
    // the slide that's already on screen (the glow lagging by up to the full 120ms
    // debounce plus however long the settle detection itself took to fire).
    function updateParallax() {
        ticking = false;
        const width = slides.clientWidth || 1;
        const raw = slides.scrollLeft / width;
        slideEls.forEach((slideEl, i) => {
            slideEl.style.setProperty("--p", String(raw - i));
        });
        const liveIndex = Math.min(Math.max(Math.round(raw), 0), slideEls.length - 1);
        dots.forEach((dot, i) => dot.classList.toggle("active", i === liveIndex));
        const accentVar = ONBOARDING_ACCENTS[slideEls[liveIndex] && slideEls[liveIndex].dataset.accent];
        if (accentVar) screen.style.setProperty("--onboarding-accent", accentVar);
    }

    // "Active slide" bookkeeping - just the "is-active" class that starts the
    // entrance/loop decorations now - fires only once scrolling has actually settled
    // (see the debounce below), not on every scroll frame while mid-drag. Doing this
    // continuously used to flip "is-active" onto the next slide the moment it crossed
    // the halfway point, while the finger (and the live parallax transform above) was
    // still moving it - the entrance animation's own transform then fought the
    // parallax transform on the same element (.onboarding-art has both), which looked
    // like the slide rendering a second time mid-swipe. The (now one-shot) decorative
    // loops still need that debounce - unlike dots/glow above, replaying them on every
    // frame of a drag would look wrong.
    function commitActiveSlide() {
        const width = slides.clientWidth || 1;
        const index = Math.round(slides.scrollLeft / width);
        if (index === activeIndex) return;
        activeIndex = index;
        slideEls.forEach((slideEl, i) => slideEl.classList.toggle("is-active", i === index));
    }

    slides.addEventListener("scroll", () => {
        if (!ticking) {
            ticking = true;
            requestAnimationFrame(updateParallax);
        }
        clearTimeout(settleTimer);
        settleTimer = setTimeout(commitActiveSlide, 120);
    });
    // Run once on setup (scrollLeft is 0 at this point) so the first slide gets its
    // parallax, "is-active" class, entrance animation, and accent glow without
    // waiting for a scroll event.
    updateParallax();
    commitActiveSlide();

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

el("settings-app-version").textContent = `Camilius · v${APP_VERSION}`;

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
