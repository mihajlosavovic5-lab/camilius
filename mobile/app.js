// Camilius — mobile app shell (vanilla JS, no build step, no framework).
// Talks only to the JSON API (api.py) on the production Flask backend; renders its own
// screens instead of loading the server-rendered website.

const API_BASE = "https://app.ai-auto-reply-mail.com";
// Bumped alongside the native versionName, sent with crash reports - deliberately NOT
// bumped for the 1.9.11 native-only release (status-bar overlay fix, see build.gradle/
// api.py's APK_VERSION). This constant ships to every device via the very same OTA
// bundle it's declared in, native-updated or not, so bumping it here would make the
// update-banner check below (info.version === APP_VERSION) go true - and stop nagging -
// for every device the instant this file's OTA update lands, including the ones that
// never actually installed the new APK. Leaving it at the prior release's value keeps
// the mismatch (and the banner) alive for anyone still on old native code; bump it to
// "1.9.11" in a later, purely-cosmetic OTA push once that's no longer needed.
const APP_VERSION = "1.9.10";

// No per-account sent-at tracking on the backend for password-reset emails (unlike email/
// phone verification below), so the forgot-password buttons' cooldown is purely client-side -
// matches EMAIL_VERIFY_RESEND_COOLDOWN_SECONDS/PHONE_VERIFY_RESEND_COOLDOWN_SECONDS in app.py.
const FORGOT_PASSWORD_RESEND_COOLDOWN_SECONDS = 60;

const el = (id) => document.getElementById(id);

// Disables `btn` and counts down "Try again in Ns" for `seconds` seconds, then restores
// its original data-i18n label - shared by every button that sends a code/link to email
// (forgot password, resend verification, phone verification). Any countdown already
// running on this button is replaced rather than stacked with the new one.
function startResendCountdown(btn, seconds) {
    if (btn._resendTimer) {
        clearInterval(btn._resendTimer);
        btn._resendTimer = null;
    }
    const defaultLabel = btn.getAttribute("data-i18n");
    let remaining = seconds;

    function render() {
        if (remaining > 0) {
            btn.disabled = true;
            btn.textContent = t("verifyPending.resendWait").replace("{n}", remaining);
        } else {
            btn.disabled = false;
            btn.textContent = t(defaultLabel);
            btn._resendTimer = null;
        }
    }

    render();
    if (remaining > 0) {
        btn._resendTimer = setInterval(() => {
            remaining -= 1;
            if (remaining <= 0) {
                remaining = 0;
                clearInterval(btn._resendTimer);
            }
            render();
        }, 1000);
    }
}

// Cancels any running countdown and restores `btn` to its normal clickable state - used
// when a screen holding a resend button is freshly (re)entered.
function resetResendButton(btn) {
    if (btn._resendTimer) {
        clearInterval(btn._resendTimer);
        btn._resendTimer = null;
    }
    btn.disabled = false;
    const defaultLabel = btn.getAttribute("data-i18n");
    if (defaultLabel) btn.textContent = t(defaultLabel);
}

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
    scheduleSlotMin: 5,
    scheduleSlotMax: 480,
    scheduleBookSlot: null,
    scheduleBookDuration: 30,
    scheduleCache: {},
    reminderCustomerMinutes: 1440,
    reminderOwnerMinutes: 60,
    // Whether this business has a saved business_pages row yet - lets the Design
    // picker's "change" listener decide synchronously whether a pick should go through
    // the standalone preferred-theme save (see saveMysitePreferredTheme) instead of the
    // slug-gated general autosave path. Set from loadMySite()'s own fetch and, for an
    // instant-correct value before that first fetch resolves, from the same boot-time
    // cache mysiteHasPage already reads to shape the rest of the My Site tab.
    mysiteHasPage: false,
};

const screens = {
    onboarding: el("screen-onboarding"),
    login: el("screen-login"),
    forgotPassword: el("screen-forgot-password"),
    register: el("screen-register"),
    verifyPending: el("screen-verify-pending"),
    mysite: el("screen-mysite"),
    websiteChat: el("screen-website-chat"),
    inbox: el("screen-inbox"),
    detail: el("screen-detail"),
    schedule: el("screen-schedule"),
    profile: el("screen-profile"),
    settings: el("screen-settings"),
    deleteWarning: el("screen-delete-warning"),
    deleteReason: el("screen-delete-reason"),
    deleteConfirm: el("screen-delete-confirm"),
    help: el("screen-help"),
    terms: el("screen-terms"),
    privacy: el("screen-privacy"),
    subscription: el("screen-subscription"),
};
const tabbar = el("tabbar");
const offlineBanner = el("offline-banner");

function showScreen(name) {
    // Leaving Login for anywhere else (Create Account, Forgot password, ...) dismisses
    // whatever error it was showing (wrong password, no account for that Google
    // identity, ...) outright - see hideLoginError()/resetLoginPhase()'s own comments -
    // rather than leaving it to resurface stale on a later visit back here.
    if (name !== "login" && screens.login.classList.contains("active")) hideLoginError();
    Object.values(screens).forEach((s) => s.classList.remove("active"));
    screens[name].classList.add("active");
    // #register-back lives outside every .screen (see the comment by its markup) so its
    // own fade+slide-in isn't tied to .screen.active the way every other back-btn's is -
    // driven directly off which screen we're actually showing instead.
    el("register-back").classList.toggle("visible", name === "register");
    // Every navigation TO the login screen (logout, back button, verify-pending "back to
    // login", etc.) starts fresh on the Google/email choice view, never mid-form - see
    // resetLoginPhase() below, defined further down but hoisted (function declaration).
    if (name === "login") resetLoginPhase();
}

function showTabbar(show) {
    tabbar.classList.toggle("hidden", !show);
    // The tab bar sits above the centered option pickers' own z-index and stays
    // visible/undimmed while one is open (see --modal-bottom-chrome's comment in
    // app.css) - .country-modal-sheet reads this to center itself in the space
    // that's actually free above the tab bar instead of the full screen, whenever
    // the tab bar is the reason that space is smaller.
    document.documentElement.style.setProperty(
        "--modal-bottom-chrome", show ? "calc(var(--tabbar-h) + var(--safe-bottom))" : "0px"
    );
}

// Register / Forgot password are a small, fixed, entirely linear
// flow (Login -> Register -> Inbox, or Login -> Forgot password ->
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

// The Google sign-in attempt currently in flight (state/errorBox/settled), or null when
// none is - see the "Continue with Google" section below for where this is actually set/
// read/cleared. Declared up here (rather than down there, next to its own logic) because
// checkForOtaUpdate below needs to check it too - it must not yank the WebView out from
// under an active attempt with a forced reload (see that check's own comment).
let activeGoogleLoginAttempt = null;

// Set below, inside `if (capacitorPlugins.CapacitorUpdater)`, to the real checkForOtaUpdate
// function - stays null on a build without that plugin. Called again once a deferred
// Google sign-in attempt settles (see checkGoogleLoginStatusOnce) so an update postponed
// for that reason doesn't just sit forgotten until the next unrelated foreground/visible
// event.
let checkForOtaUpdateFn = null;

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
        } else if (screens.terms.classList.contains("active") || screens.privacy.classList.contains("active")) {
            showScreen(legalReturnTo);
        } else if (screens.help.classList.contains("active") || screens.subscription.classList.contains("active")) {
            showScreen("settings");
        } else if (screens.websiteChat.classList.contains("active")) {
            showTabbar(true);
            showScreen("mysite");
        } else if (screens.deleteWarning.classList.contains("active")) {
            showScreen("settings");
        } else if (screens.deleteReason.classList.contains("active")) {
            showScreen("deleteWarning");
        } else if (screens.deleteConfirm.classList.contains("active")) {
            showScreen("deleteReason");
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
            // set() below reloads the WebView outright - doing that while a Google
            // sign-in attempt is in flight would tear down the very screen/state the
            // visitor is looking at (the login-error message they just got, the
            // in-progress poll) out from under them, mid-flow, for a reason that has
            // nothing to do with anything they just did. Deferred, not dropped -
            // checkGoogleLoginStatusOnce calls checkForOtaUpdateFn again once the
            // attempt settles, so the update still lands, just not mid-attempt.
            if (activeGoogleLoginAttempt) return;
            if (otaCheckInProgress) return;
            otaCheckInProgress = true;
            let step = "fetch-manifest";
            try {
                const response = await fetch(API_BASE + "/api/mobile-manifest");
                const manifest = await response.json();
                if (!manifest.available) {
                    return;
                }

                // Gate on the plugin's OWN record of the active bundle (current()), not
                // anything we store ourselves - a self-kept flag drifted from native
                // reality once before ("applied" recorded while builtin still ran).
                step = "current";
                const current = await capacitorPlugins.CapacitorUpdater.current();
                const activeVersion = (current && current.bundle) ? current.bundle.version : "unknown";
                // Gate the "never OTA-updated" case on bundle.id, not bundle.version - the
                // plugin only guarantees id === "builtin" for that case. version instead
                // falls back to the native app's own versionName (CapacitorUpdaterPlugin's
                // versionBuild = config "version" ?? PackageInfo.versionName, see
                // CapgoUpdater.getBundleInfo), e.g. "1.9.15.3", which would never equal
                // manifest.version (a real content hash) even when the APK was JUST built
                // from this exact mobile/ source - forcing a same-content re-download and
                // the "Updating app" overlay on every fresh install. document.documentElement's
                // data-bundle-version carries the hash this exact index.html was built with
                // (see build_mobile_bundle.py) - substitute it in for the builtin case so a
                // fresh install that already matches the server correctly no-ops.
                const activeBundleId = (current && current.bundle) ? current.bundle.id : null;
                const effectiveActiveVersion =
                    activeBundleId === "builtin"
                        ? document.documentElement.dataset.bundleVersion || activeVersion
                        : activeVersion;
                if (effectiveActiveVersion === manifest.version) {
                    return;
                }

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
                // Re-checked here, not just in the guard at the top of this function - a
                // Google sign-in can start well after this function began (the fetch/
                // current()/download sequence above easily takes a few seconds), and
                // set() below reloads the WebView unconditionally, wiping out that
                // attempt's in-memory state (activeGoogleLoginAttempt included) out from
                // under the visitor exactly like the top-of-function comment says must
                // never happen. The bundle is already downloaded and cached on disk at
                // this point, so bailing here doesn't lose the update - checkForUpdateFn()
                // gets called again once the attempt settles (see checkGoogleLoginStatusOnce/
                // pollGoogleLogin), and that next pass applies this same cached bundle.
                if (activeGoogleLoginAttempt) {
                    otaOverlay.classList.add("hidden");
                    return;
                }
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
        checkForOtaUpdateFn = checkForOtaUpdate;

        // Check at cold start and every time the app returns to the foreground - two
        // redundant triggers for the same "reopened" moment, since both are Capacitor/OS
        // lifecycle events that some OEM WebViews are known to skip or delay on a plain
        // app-switcher resume (no real backgrounding happened at the OS level). Deliberately
        // no repeating timer while the app stays open - that polled the server continuously
        // for every open session, which doesn't scale.
        checkForOtaUpdate();
        capacitorPlugins.App.addListener("appStateChange", ({ isActive }) => {
            if (isActive) checkForOtaUpdate();
        });
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") checkForOtaUpdate();
        });
    }

    // Native-code changes (new plugin, permissions, icon, splash, anything under
    // android/) can't ship through the OTA check above - they need a new signed APK
    // install. Two paths offer that in-app, tried in order:
    //  1. PlayUpdatePlugin (see PlayUpdatePlugin.java/MainActivity.java) - only ever
    //     has anything to offer once this install actually came from the Play Store (a
    //     sideload has no Play listing to diff against, and an old native build
    //     predating this plugin doesn't have it registered at all - checkForUpdate()
    //     below throws for both, same as any missing Capacitor plugin call always
    //     does). Play downloads and verifies the update itself in the background - no
    //     browser, no "Install unknown apps" prompt, since Play is always a trusted
    //     install source.
    //  2. The apk-version/website fallback below, for everyone checkForUpdate() threw
    //     for.
    (async () => {
        const banner = el("update-banner");
        const downloadBtn = el("update-download-btn");
        const dismissBtn = el("update-dismiss-btn");

        // Base behavior: just hide for this session. The website-fallback path below
        // adds a second listener that also persists the dismissal by version - there's
        // no equivalent "version" to key that off here, Play doesn't expose one this
        // plugin surfaces, so re-prompting next cold start is the simplest fallback.
        dismissBtn.addEventListener("click", () => banner.classList.add("hidden"));

        const offerRestart = () => {
            downloadBtn.textContent = t("update.install");
            downloadBtn.onclick = () => capacitorPlugins.PlayUpdate.completeUpdate();
            banner.classList.remove("hidden");
        };

        try {
            const status = await capacitorPlugins.PlayUpdate.checkForUpdate();
            if (status.downloaded) {
                // A FLEXIBLE download from an earlier session (or one that finished
                // while this session was backgrounded, missing the event below) is
                // already sitting there ready to install.
                offerRestart();
                return;
            }
            if (status.available && status.flexibleAllowed) {
                // Starts downloading in the background immediately - nothing for the
                // visitor to tap to kick it off. offerRestart() only runs once it's
                // actually landed, via this event.
                await capacitorPlugins.PlayUpdate.startUpdate({ type: "FLEXIBLE" });
                window.addEventListener("playUpdateDownloaded", offerRestart, { once: true });
                return;
            }
            if (status.available && status.immediateAllowed) {
                // Play takes over the screen entirely for this one - nothing left here
                // to show.
                await capacitorPlugins.PlayUpdate.startUpdate({ type: "IMMEDIATE" });
                return;
            }
            // Either nothing's available (this Play Store install is already current),
            // or an update exists but neither type is allowed yet (can happen mid-
            // rollout) - either way there's nothing actionable, and re-checking via
            // the website fallback below would only tell a Play install the exact same
            // "not installable right now", so stop here regardless.
            return;
        } catch (e) {
            // No PlayUpdatePlugin (old native build - capacitorPlugins.PlayUpdate is
            // undefined) or its checkForUpdate() rejected (Play has nothing tracked
            // for this install, e.g. still sideloaded) - both fall straight through to
            // the website fallback below, same as before this plugin existed.
        }

        try {
            const response = await fetch(API_BASE + "/api/apk-version");
            const info = await response.json();
            // Compares against the INSTALLED NATIVE app's own version (Capacitor's App
            // plugin reads this straight from the APK's versionName - build.gradle's
            // "major.minor.patch.build", first 3 segments) instead of the APP_VERSION
            // constant above - that constant ships to every device via the same OTA
            // bundle regardless of which native APK they're actually running, so it
            // can go stale (or get bumped) independently of a real native install and
            // can't actually tell a device apart from one still on the old APK.
            const nativeInfo = await capacitorPlugins.App.getInfo();
            const nativeVersion = nativeInfo.version.split(".").slice(0, 3).join(".");
            if (nativeVersion === info.version) return;

            const dismissedVersion = await storageGet("apkUpdateDismissedVersion");
            if (dismissedVersion === info.version) return;

            downloadBtn.textContent = t("update.download");
            downloadBtn.onclick = () => {
                // Deliberately the system browser, not a direct install Intent from
                // this app: that path makes OUR app the install source, which needs
                // its OWN "Install unknown apps" grant in Android Settings - a
                // separate toggle most testers have never had a reason to visit even
                // after happily sideloading before, since it's scoped per-source-app
                // rather than being one global switch. Handing the same URL to the
                // system browser instead reuses whatever source most sideloaders
                // already trust (Chrome et al.), matching the download page's
                // (templates/download.html) flow, which is already known to work.
                // Deliberately NOT capacitorPlugins.Browser.open() (Chrome Custom Tabs
                // on Android, used elsewhere for OAuth/checkout) - Custom Tabs are
                // known to leave a download's progress sheet stuck at 100% forever
                // instead of handing off to the system Download Manager for a real
                // "download complete" notification/Open action, even once the file's
                // fully written to disk. "_system" opens this in the actual full
                // browser app instead, whose downloads behave normally.
                window.open(info.url, "_system");
            };
            dismissBtn.addEventListener("click", async () => {
                await storageSet("apkUpdateDismissedVersion", info.version);
            });

            banner.classList.remove("hidden");
        } catch (e) {
            reportClientError(e.message, e.stack, "apk-update-check");
        }
    })();
}

// --- Working hours / price list pickers (Create Account and Profile both use these -
// a real "pick from options" UI instead of a freeform textarea, serializing to/from the
// same wire format the backend/AI already expect - JSON for working hours (see
// hours-picker.js), plain text for the price list - so nothing else has to change) ---

// Shared by every option-list picker (#option-modal, #country-modal, #lang-modal):
// marks whichever option row is the field's current value with .wheel-active (the
// bold/primary-color "selected" look) and scrolls it into view.
//
// This list used to be a spin-wheel: a fixed highlight band always at the list's
// vertical center, with the currently-scrolled-to row detected by proximity to that
// center and re-centered on scroll pause, and every option's opacity fading by
// distance from it. That needed blank padding reserved above the first row and below
// the last so they could reach dead center under the band too, which read as leftover
// empty space in the sheet once an end value was selected/scrolled to - see git
// history on this function and on .country-modal-list's CSS. This plain version
// doesn't track scroll position at all: the selection only changes when a row is
// actually tapped (each option's own click handler, set up by each picker's caller),
// so there's nothing to detect or re-center and nothing to reserve space for.
function markActiveOption(list, activeBtn) {
    list.querySelectorAll(".wheel-active").forEach((btn) => btn.classList.remove("wheel-active"));
    if (!activeBtn) return;
    activeBtn.classList.add("wheel-active");
    activeBtn.scrollIntoView({ block: "nearest" });
}

// Shared option-list picker (Faza 4, re-centered later) - replaces native <select>
// dropdowns (working-hours times, price-list currency, duration/lead-time fields).
// Opens centered on screen with a scale-in "zoom" and a dimmed backdrop (same
// transform/backdrop machinery as the Book/Confirm modals - see .country-modal-sheet
// and .lang-modal-backdrop) rather than anchored below whichever field was tapped -
// tapping anywhere on the dimmed backdrop closes it. Lives outside #app for the same
// reason the language/country modals do (a .screen's CSS transform would otherwise
// hijack position:fixed descendants - see the HTML comment above #lang-modal). One
// instance (backed by one #<modalId> in index.html) can serve any number of buttons/
// option-sets, since the option list itself is (re)rendered fresh on every open() call
// rather than built once up front.
function createAnchoredListPicker(modalId) {
    const modal = el(modalId);
    const backdrop = modal.querySelector(".lang-modal-backdrop");
    const list = modal.querySelector(".country-modal-list");
    const titleEl = modal.querySelector(".option-modal-title");
    const subtitleEl = modal.querySelector(".option-modal-subtitle");
    let openTriggerBtn = null;

    // title/subtitle are optional framing text shown above/below the list (only the
    // reminder lead-time picker uses them, e.g. "Send" / "before the appointment") -
    // left blank they stay hidden, so every other field using this shared modal
    // (working hours, currency, duration) looks exactly as before.
    function open(triggerBtn, options, currentValue, onSelect, { title, subtitle } = {}) {
        openTriggerBtn = triggerBtn;
        list.innerHTML = "";
        let selectedBtn = null;
        options.forEach((opt) => {
            const optionBtn = document.createElement("button");
            optionBtn.type = "button";
            optionBtn.className = "country-option-btn";
            if (opt === currentValue) selectedBtn = optionBtn;
            optionBtn.textContent = opt;
            optionBtn.addEventListener("click", () => {
                onSelect(opt);
                close();
            });
            list.appendChild(optionBtn);
        });
        if (titleEl) {
            titleEl.textContent = title || "";
            titleEl.classList.toggle("hidden", !title);
        }
        if (subtitleEl) {
            subtitleEl.textContent = subtitle || "";
            subtitleEl.classList.toggle("hidden", !subtitle);
        }

        modal.classList.add("open");
        // Marking/scrolling needs a layout pass first (list.innerHTML was just
        // rebuilt above), hence the rAF.
        requestAnimationFrame(() => markActiveOption(list, selectedBtn || list.firstElementChild));
    }
    function close() {
        modal.classList.remove("open");
        openTriggerBtn = null;
    }
    // Tapping the backdrop commits whichever option is currently marked selected -
    // the same as tapping that option directly (its own click listener, set up in
    // open() above, already does both the onSelect(opt) call and close()) - rather
    // than discarding the picker and leaving the field's old value in place.
    backdrop.addEventListener("click", () => {
        const active = list.querySelector(".wheel-active");
        if (active) active.click(); else close();
    });

    return { open, close, isOpenFor: (btn) => openTriggerBtn === btn };
}

const optionPicker = createAnchoredListPicker("option-modal");

// A button that looks like the old <select> but opens the shared optionPicker (a modal
// centered on screen, not anchored below the button) instead of the platform's own
// picker UI. Used for both the hours picker's time fields and the price list's
// currency field (see createPricePicker).
function createOptionButton(initialValue, options, extraClass) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "picker-btn" + (extraClass ? ` ${extraClass}` : "");
    const label = document.createElement("span");
    label.textContent = initialValue;
    btn.appendChild(label);

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

// Thin platform wrapper around the shared picker core (mobile/hours-picker.js, a mirror
// of static/hours-picker.js copied in by build_mobile_bundle.py - loaded before this
// file, see mobile/index.html). The returned getValue/setValue/reset/validate/
// collapseToSimple object is exactly what every existing call site already expects.
function createHoursPicker(containerId) {
    return HoursPicker.create({
        mount: el(containerId),
        buildTimeField: (defaultValue) => createOptionButton(defaultValue, HoursPicker.TIME_OPTIONS, "time-picker-btn"),
        dayI18nKey: (day) => `days.${day}`,
        // Always lands on the collapsed "Working days" summary (a 7-row list on first
        // load reads as far more overwhelming than useful) - "Set different hours for a
        // specific day" is right there for a business whose days genuinely differ.
        expandAdvancedIfNonUniform: false,
    });
}

// Major world currencies (the ones the user named explicitly first, then other common
// ones) - shown next to the price list so a business can say what its prices are in.
// Codes are used as-is in every language (ISO 4217, not translated).
const CURRENCIES = ["EUR", "USD", "RSD", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY", "TRY", "RUB", "SEK", "NOK", "DKK", "PLN", "AED", "INR", "BRL"];

function createPricePicker(rowsContainerId, addButtonId, currencyContainerId) {
    const rowsContainer = el(rowsContainerId);
    const currencyBtn = createOptionButton("RSD", CURRENCIES, "currency-picker-btn");
    el(currencyContainerId).appendChild(currencyBtn.el);

    // Makes the whole "Currency" row open the picker, not just the small pill button on
    // its right (see .price-currency-row in app.css) - bails out when the click landed on
    // the button itself so it isn't handled twice (once here via the forwarded .click(),
    // once by the button's own listener from createOptionButton), which would open then
    // immediately re-close the picker.
    const currencyRow = el(currencyContainerId).closest(".price-currency-row");
    if (currencyRow) {
        currencyRow.addEventListener("click", (e) => {
            if (e.target.closest(".currency-picker-btn")) return;
            currencyBtn.el.click();
        });
    }

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

// AI upsell pairs picker (Profile only - Pro feature, not part of registration) - a
// repeatable "Service -> suggested service" row pair, mirroring the website's
// arrow-separated picker (static/pickers.js createUpsellPicker) instead of the old
// freeform "Service -> Add-on" textarea.
function createUpsellPicker(rowsContainerId, addButtonId) {
    const rowsContainer = el(rowsContainerId);

    function addRow(serviceA = "", serviceB = "") {
        const row = document.createElement("div");
        row.className = "upsell-row";

        const fieldA = document.createElement("div");
        fieldA.className = "floating-field";
        const inputA = document.createElement("input");
        inputA.type = "text";
        inputA.className = "upsell-service-a";
        inputA.placeholder = " ";
        inputA.value = serviceA;
        const labelA = document.createElement("label");
        labelA.dataset.i18n = "settings.upsell.examplePlaceholder1";
        labelA.textContent = t("settings.upsell.examplePlaceholder1");
        fieldA.appendChild(inputA);
        fieldA.appendChild(labelA);

        const arrow = document.createElement("span");
        arrow.className = "upsell-arrow";
        arrow.textContent = "→";

        const fieldB = document.createElement("div");
        fieldB.className = "floating-field";
        const inputB = document.createElement("input");
        inputB.type = "text";
        inputB.className = "upsell-service-b";
        inputB.placeholder = " ";
        inputB.value = serviceB;
        const labelB = document.createElement("label");
        labelB.dataset.i18n = "settings.upsell.examplePlaceholder2";
        labelB.textContent = t("settings.upsell.examplePlaceholder2");
        fieldB.appendChild(inputB);
        fieldB.appendChild(labelB);

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "price-remove-btn";
        removeBtn.setAttribute("aria-label", "Remove");
        removeBtn.textContent = "×";
        removeBtn.addEventListener("click", () => row.remove());

        row.appendChild(fieldA);
        row.appendChild(arrow);
        row.appendChild(fieldB);
        row.appendChild(removeBtn);
        rowsContainer.appendChild(row);
    }

    el(addButtonId).addEventListener("click", () => addRow());

    return {
        getValue() {
            return Array.from(rowsContainer.querySelectorAll(".upsell-row"))
                .map((row) => {
                    const a = row.querySelector(".upsell-service-a").value.trim();
                    const b = row.querySelector(".upsell-service-b").value.trim();
                    return a && b ? `${a} -> ${b}` : null;
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
                const idx = line.indexOf("->");
                if (idx === -1) return;
                addRow(line.slice(0, idx).trim(), line.slice(idx + 2).trim());
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
const profileUpsellPicker = createUpsellPicker("settings-upsell-rows", "settings-upsell-add");
profileUpsellPicker.setValue("");

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
    const backdrop = el("country-modal-backdrop");
    const list = el("country-modal-list");

    const bindings = [
        {
            btn: el("register-phone-country-btn"), row: el("register-phone-row"),
            flagEl: el("register-phone-country-flag"), dialEl: el("register-phone-country-dial"),
            getCurrent: () => selectedPhoneCountry, onSelect: (c) => { selectedPhoneCountry = c; },
        },
        {
            btn: el("settings-phone-country-btn"), row: el("settings-phone-row"),
            flagEl: el("settings-phone-country-flag"), dialEl: el("settings-phone-country-dial"),
            getCurrent: () => selectedProfilePhoneCountry, onSelect: (c) => { selectedProfilePhoneCountry = c; },
        },
        {
            btn: el("forgot-phone-country-btn"), row: el("forgot-phone-row"),
            flagEl: el("forgot-phone-country-flag"), dialEl: el("forgot-phone-country-dial"),
            getCurrent: () => selectedForgotPhoneCountry, onSelect: (c) => { selectedForgotPhoneCountry = c; },
        },
    ];
    let active = null;

    // Rendered once - no search, so the list never changes after this. data-code lets
    // openCountryModal() below find the row for whichever country is currently selected
    // on the field that was tapped, to mark/scroll it into view on open.
    COUNTRIES.forEach((c) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "country-option-btn";
        row.dataset.code = c.code;
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
        modal.classList.add("open");
        requestAnimationFrame(() => {
            const target = list.querySelector(`[data-code="${binding.getCurrent().code}"]`) || list.firstElementChild;
            markActiveOption(list, target);
        });
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
    // Tapping the backdrop commits whichever country is currently marked selected,
    // same as the working-hours/currency/duration picker's backdrop.
    backdrop.addEventListener("click", () => {
        const activeRow = list.querySelector(".wheel-active");
        if (activeRow) activeRow.click(); else closeCountryModal();
    });

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
// contact-email/phone field. Before login (Create Account) there's no Bearer
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

// fetch() has no built-in timeout - without this, a stalled request (server still busy
// with someone else's slow request, a dropped mobile connection that never sends a
// TCP RST, ...) just hangs forever with no error and no way for the caller's own
// try/finally to ever re-enable a disabled button or clear a loading spinner. Generous
// enough to not false-positive on a real but slow response, short enough that the app
// recovers with a normal "network error" instead of looking frozen.
const API_TIMEOUT_MS = 30000;

async function api(path, { method = "GET", body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;

    const timeoutController = new AbortController();
    const timeoutId = setTimeout(() => timeoutController.abort(), API_TIMEOUT_MS);

    let response;
    try {
        response = await fetch(API_BASE + path, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
            signal: timeoutController.signal,
        });
    } catch (networkError) {
        const err = new Error(t("network.error"));
        err.isNetworkError = true;
        throw err;
    } finally {
        clearTimeout(timeoutId);
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

// --- Image lightbox (Faza 15) - fullscreen zoom for a tapped attachment thumbnail (see
// the compose-attachment-list chips in the voice/attach IIFEs below). Backdrop tap closes
// it; the image itself doesn't (its own click listener stops propagation), so a tap meant
// to look closer doesn't immediately dismiss the view. ---

function openImageLightbox(url) {
    const lightbox = el("image-lightbox");
    const img = el("image-lightbox-img");
    if (!lightbox || !img) return;
    img.src = url;
    lightbox.classList.add("open");
}
function closeImageLightbox() {
    const lightbox = el("image-lightbox");
    const img = el("image-lightbox-img");
    if (!lightbox || !img) return;
    lightbox.classList.remove("open");
    // Clears the src only after the fade-out transition finishes (matches app.css's own
    // .22s), not immediately - otherwise the image blinks empty mid-close instead of
    // fading away with the rest of the overlay.
    setTimeout(() => {
        if (!lightbox.classList.contains("open")) img.src = "";
    }, 220);
}
el("image-lightbox").addEventListener("click", closeImageLightbox);

// --- Save success overlay (Faza 14) - the same animated "circle draws in, then the
// checkmark traces after" celebration the website's settings.html shows for its own
// Profile save, in place of the plain .toast above for save confirmations specifically.
// classList add/remove "visible" (not "hidden") so the CSS transitions actually animate,
// same reasoning as the website's initSaveSuccessOverlay().
let saveSuccessTimer = null;
function showSaveSuccess(message) {
    const overlay = el("save-success-overlay");
    el("save-success-text").textContent = message;
    // Restart the circle/checkmark draw animations even if the overlay is already
    // showing from a previous save - removing then re-adding "visible" on the next
    // frame forces the CSS animations to replay from the start instead of a no-op.
    overlay.classList.remove("visible");
    clearTimeout(saveSuccessTimer);
    requestAnimationFrame(() => overlay.classList.add("visible"));
    saveSuccessTimer = setTimeout(() => overlay.classList.remove("visible"), 2200);
}
el("save-success-overlay").addEventListener("click", () => {
    clearTimeout(saveSuccessTimer);
    el("save-success-overlay").classList.remove("visible");
});

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

// Show/hide password toggle (see .password-toggle in app.css) - wired once for every
// password field (login, sign up, Profile security fields) instead of per-screen, so
// any new password field just needs the .floating-field-password wrapper + button.
document.querySelectorAll(".password-toggle").forEach((btn) => {
    const input = btn.closest(".floating-field").querySelector("input");
    if (!input) return;
    // Only reveal the eye icon once the user has actually typed something.
    const syncHasValue = () => {
        btn.classList.toggle("has-value", input.value.length > 0);
    };
    input.addEventListener("input", syncHasValue);
    syncHasValue();
    btn.addEventListener("click", () => {
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        btn.classList.toggle("is-visible", !showing);
        btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    });
});

// Clears a password field and its toggle button together - plain `input.value = ""`
// doesn't fire an "input" event, so the eye icon would otherwise keep showing after a
// programmatic reset (e.g. after saving the settings form).
function resetPasswordField(id) {
    const input = el(id);
    if (!input) return;
    input.value = "";
    input.type = "password";
    const btn = input.closest(".floating-field").querySelector(".password-toggle");
    if (btn) {
        btn.classList.remove("has-value", "is-visible");
        btn.setAttribute("aria-label", "Show password");
    }
}

const LOGIN_ERROR_AUTO_HIDE_MS = 5000;

// The Login screen's own error box (wrong password, no account for that Google identity,
// sign-in cancelled, ...) self-clears after LOGIN_ERROR_AUTO_HIDE_MS instead of sitting
// there indefinitely - it was still showing whatever the last attempt said even after
// switching to "Continue with email"/Create Account and back, which read as stuck rather
// than as feedback about something that already happened. Scoped to just this one error
// box (not the shared errorBox pattern used elsewhere - register/settings/schedule/etc.
// keep their existing until-next-action behavior) - only ever called with el("login-error").
function showLoginError(message) {
    const errorBox = el("login-error");
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
    clearTimeout(errorBox._autoHideTimer);
    errorBox._autoHideTimer = setTimeout(() => {
        errorBox.classList.add("hidden");
    }, LOGIN_ERROR_AUTO_HIDE_MS);
}

// Dismisses the Login screen's error box outright (cancelling its auto-hide timer too,
// so it can't re-show itself a moment later) - called wherever the visitor actively
// leaves it behind (showScreen away from login, "Continue with email", Create Account)
// rather than just waiting on LOGIN_ERROR_AUTO_HIDE_MS, and again on the way back in
// (resetLoginPhase) so a stale message from a previous attempt never resurfaces on a
// later visit to this screen either.
function hideLoginError() {
    const errorBox = el("login-error");
    clearTimeout(errorBox._autoHideTimer);
    errorBox.classList.add("hidden");
}

// startGoogleLogin/checkGoogleLoginStatusOnce below only ever run for the Login screen's
// own Google button (see its el("login-google-btn") wiring) - this routes its box through
// showLoginError's auto-hide.
function showErrorBox(errorBox, message) {
    if (errorBox.id === "login-error") {
        showLoginError(message);
        return;
    }
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
}

el("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = el("login-email").value.trim();
    const password = el("login-password").value;
    const errorBox = el("login-error");
    const submitBtn = el("login-submit");

    errorBox.classList.add("hidden");
    clearTimeout(errorBox._autoHideTimer);
    submitBtn.disabled = true;
    submitBtn.textContent = t("login.submitLoading");

    try {
        const data = await api("/api/login", { method: "POST", body: { email, password } });
        state.token = data.token;
        state.businessName = data.business_name;
        // enterApp() only ever reads state.token/state.businessName (kept in memory) -
        // persisting them to native storage is just for the next cold start, so it
        // doesn't need to gate the screen transition. Fired off in the background instead
        // of awaited so the app view appears the instant login succeeds.
        enterApp();
        storageSet("token", data.token).catch((e) => reportClientError(e.message, e.stack, "storage-set-token"));
        storageSet("businessName", data.business_name).catch((e) => reportClientError(e.message, e.stack, "storage-set-businessName"));
    } catch (err) {
        if (err.data && err.data.pending_verification) {
            showVerifyPendingScreen(err.data.business_id, "", err.data.wait_seconds);
        } else {
            showLoginError(err.message || t("login.error"));
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = t("login.submit");
    }
});

// --- Continue with Google (Login) ---
//
// The website's own /auth/google can just redirect straight to Google and rely on its
// session cookie for the round trip (see app.py) - this app has neither: it opens
// Google's consent screen in the system browser (Capacitor's Browser plugin, since Google
// blocks its OAuth screen from loading inside a plain embedded WebView), a separate
// browser context with no cookie/JS bridge back to the app at all. So instead: ask the
// server for a one-time `state` + the URL to open (POST /api/google-login/start), open
// it, then poll GET /api/google-login/status?state=... (see api.py) until app.py's
// /auth/google/callback - reached once the visitor finishes on Google's side - has filled
// in a token for that state, exactly like a normal /api/login response.

// Fallback cadence for devices where the App Link handoff below doesn't fire (older
// installed APK without that manifest entry yet, or an Android/Chrome combination that
// doesn't honor it mid-Custom-Tab) - this is the tail latency between Google actually
// finishing the redirect and the app noticing and calling Browser.close() on the Custom
// Tab. On a device where the App Link DOES fire, checkGoogleLoginStatusOnce runs the
// instant that happens instead of waiting for this timer at all - see the appUrlOpen
// listener below. api_google_login_status's own rate limit (see api.py) is sized to
// match this interval.
const GOOGLE_LOGIN_POLL_INTERVAL_MS = 400;
const GOOGLE_LOGIN_POLL_TIMEOUT_MS = 120000;

// Set by pollGoogleLogin below when a Google identity has never been seen here before
// (status 'needs_signup') and cleared once the register-form submit handler actually
// uses it (or the visitor navigates away from Create Account without finishing) - see
// openRegisterScreen() above. Resubmitted with /api/register exactly like the web sign-up
// form's own hidden google_link_token field, re-verified server-side either way.
let pendingGoogleLinkToken = null;

// activeGoogleLoginAttempt (declared up near checkForOtaUpdate, which needs to read it
// too) is set/cleared below - lets the appUrlOpen listener below reach the same attempt
// pollGoogleLogin is already waiting on, so it can settle it immediately instead of
// waiting for the next scheduled poll tick.

// Covers the gap described above the #google-signin-overlay markup in index.html.
// Shown from the moment the Google browser opens (startGoogleLogin) rather than only
// around the appUrlOpen handoff - Android can resume the app by first painting a cached
// snapshot of whatever this WebView last rendered before it was backgrounded, and that
// snapshot is captured well before any JS here gets a chance to run. If the last thing on
// screen was the bare Login choice buttons, THAT is what briefly reappears on return no
// matter how fast the real handoff logic below reacts afterward - the only fix is making
// sure the spinner is already what's rendered before backgrounding ever happens.
// safetyTimer is a last-resort auto-hide, re-armed every time this is called (including on
// every foreground resume - see the appStateChange listener near the Google button below)
// - if the confirming fetch never comes back (connectivity dropped, or the visitor simply
// cancelled without finishing) this still releases the screen after a few seconds instead
// of sitting on the spinner for the full 2-minute poll timeout.
let googleSigninOverlaySafetyTimer = null;
function showGoogleSigninOverlay() {
    clearTimeout(googleSigninOverlaySafetyTimer);
    el("google-signin-overlay").classList.remove("hidden");
    googleSigninOverlaySafetyTimer = setTimeout(hideGoogleSigninOverlay, 5000);
}
function hideGoogleSigninOverlay() {
    clearTimeout(googleSigninOverlaySafetyTimer);
    el("google-signin-overlay").classList.add("hidden");
}

// One status check - returns true once the attempt has actually resolved (done/
// needs_signup/no_account/error), false while still 'pending' (or on a network hiccup,
// which the caller just retries). Shared by pollGoogleLogin's interval loop and the App
// Link listener's immediate check below - attempt.settled makes sure whichever of the
// two "wins" the race handles the result exactly once; the loser's own fetch (if it was
// already in flight) just sees 'expired' (the row is single-use - see
// database.oauth_login_session_status) and is a silent no-op.
async function checkGoogleLoginStatusOnce(attempt, { closeBrowser = true } = {}) {
    if (attempt.settled) return true;
    let data;
    try {
        data = await api("/api/google-login/status?state=" + encodeURIComponent(attempt.state));
    } catch (err) {
        return false;
    }
    if (attempt.settled) return true; // Resolved by the other path while this fetch was in flight.
    if (data.status === "pending") return false;

    attempt.settled = true;
    hideGoogleSigninOverlay();
    storageRemove("googleLoginPendingState");
    if (activeGoogleLoginAttempt === attempt) {
        activeGoogleLoginAttempt = null;
        // A pending OTA update (see checkForOtaUpdate's own guard) was deferred while
        // this attempt was in flight - worth a prompt re-check now rather than waiting
        // for the next unrelated foreground/visibility event.
        if (checkForOtaUpdateFn) checkForOtaUpdateFn();
    }
    // closeBrowser is false only when the App Link listener below called this - Android
    // already dismissed the Custom Tab itself as part of handing the intent to this app
    // (see AndroidManifest.xml), so there's no tab left to close; calling Browser.close()
    // on it anyway would just be targeting a session the OS already tore down.
    if (closeBrowser && capacitorPlugins && capacitorPlugins.Browser) {
        capacitorPlugins.Browser.close().catch(() => {});
    }
    const errorBox = attempt.errorBox;
    if (data.status === "done") {
        state.token = data.token;
        state.businessName = data.business_name;
        // See the plain login-form handler above for why this doesn't await the storage
        // writes: the Custom Tab has just closed (or Android has just handed the app
        // link straight back to this app) and the Login screen is still what's on
        // screen underneath it, so every extra tick before enterApp() runs is a visible
        // "back to login" flash before the app view actually appears.
        enterApp();
        storageSet("token", data.token).catch((e) => reportClientError(e.message, e.stack, "storage-set-token"));
        storageSet("businessName", data.business_name).catch((e) => reportClientError(e.message, e.stack, "storage-set-businessName"));
    } else if (data.status === "needs_signup") {
        // First time this Google identity has been seen - not logged in, just handed to
        // the normal Create Account wizard with a name/email starting point (see
        // openRegisterScreen) and a banner explaining why they landed here unprompted.
        // Password and business details are still filled in by hand exactly
        // like any other sign-up.
        openRegisterScreen({
            name: data.google_name,
            contactEmail: data.google_email,
            googleLinkToken: data.google_link_token,
            notice: t("login.googleNeedsSignup"),
        });
    } else if (data.status === "no_account") {
        // The server only returns this for an intent="login" attempt, which the Login
        // screen's own Google button no longer sends (see its click listener below) - kept
        // as a fallback in case that ever changes, rather than assuming this status can no
        // longer come back.
        showErrorBox(errorBox, t("login.googleNoAccount"));
    } else {
        // 'error' (Google denied/cancelled, or something failed server-side) or 'expired'
        // (visitor closed the tab without finishing, or just sat on the consent screen
        // past GOOGLE_LOGIN_POLL_TIMEOUT_MS) - same "explain and let them retry"
        // treatment either way, nothing more specific to say.
        showErrorBox(errorBox, t("login.googleError"));
    }
    return true;
}

async function pollGoogleLogin(attempt) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < GOOGLE_LOGIN_POLL_TIMEOUT_MS) {
        await new Promise((resolve) => setTimeout(resolve, GOOGLE_LOGIN_POLL_INTERVAL_MS));
        if (await checkGoogleLoginStatusOnce(attempt)) return;
    }
    // Timed out still 'pending' - the browser tab itself already told the visitor what
    // happened (see google_auth_mobile_result.html) if they did finish; if they just
    // never came back, silently giving up (rather than showing a stale error) is right -
    // nothing failed, the attempt just never got followed through.
    attempt.settled = true;
    hideGoogleSigninOverlay();
    storageRemove("googleLoginPendingState");
    if (activeGoogleLoginAttempt === attempt) {
        activeGoogleLoginAttempt = null;
        // A pending OTA update (see checkForOtaUpdate's own guard) was deferred while
        // this attempt was in flight - worth a prompt re-check now rather than waiting
        // for the next unrelated foreground/visibility event.
        if (checkForOtaUpdateFn) checkForOtaUpdateFn();
    }
}

async function startGoogleLogin(button, errorBox, intent) {
    errorBox.classList.add("hidden");
    clearTimeout(errorBox._autoHideTimer);
    button.disabled = true;
    try {
        const data = await api("/api/google-login/start", { method: "POST", body: { intent } });
        // Persisted (and awaited - this one write needs to actually be on disk before
        // backgrounding, not just fired off) BEFORE the browser opens, so a boot that
        // happens while it's open - see the boot IIFE's own comment on why that can
        // happen - has something durable to resume from instead of just losing the
        // attempt and showing a plain Login screen.
        await storageSet("googleLoginPendingState", data.state);
        // Shown now, before the browser ever opens - see showGoogleSigninOverlay's own
        // comment for why the timing here (last thing painted before backgrounding, not
        // just "as soon as possible afterward") is what actually matters.
        showGoogleSigninOverlay();
        if (capacitorPlugins && capacitorPlugins.Browser) {
            await capacitorPlugins.Browser.open({ url: data.auth_url });
        } else {
            window.open(data.auth_url, "_blank");
        }
        activeGoogleLoginAttempt = { state: data.state, errorBox, settled: false };
        pollGoogleLogin(activeGoogleLoginAttempt);
    } catch (err) {
        hideGoogleSigninOverlay();
        storageRemove("googleLoginPendingState");
        showErrorBox(errorBox, err.message || t("login.googleError"));
    } finally {
        button.disabled = false;
    }
}

// Sent as "register" (not "login") on purpose: a brand-new Google identity should hand
// the visitor straight to Create Account with their name/email prefilled instead of
// bouncing them off with a "no account found" error (see checkGoogleLoginStatusOnce's
// 'needs_signup' handling above) - an identity that already has an account still just
// logs straight in either way (status "done"), so this only changes the first-time case.
el("login-google-btn").addEventListener("click", () => {
    startGoogleLogin(el("login-google-btn"), el("login-error"), "register");
});

// Android hands /auth/google/done straight to THIS app instead of loading it in the
// Custom Tab, on a signed APK that has the verified App Link intent-filter for it (see
// AndroidManifest.xml and app.py's auth_google_callback/auth_google_done) - meaning the
// sign-in attempt already resolved server-side and the Custom Tab never got to render
// anything at all. This settles activeGoogleLoginAttempt immediately instead of waiting
// for pollGoogleLogin's next scheduled tick, which is what makes that handoff feel
// instant rather than just "a bit faster". A device without that manifest entry yet (an
// older installed APK) never fires this at all - pollGoogleLogin's own timer is the only
// thing settling the attempt there, exactly as before this existed.
if (capacitorPlugins && capacitorPlugins.App) {
    capacitorPlugins.App.addListener("appUrlOpen", (data) => {
        if (!activeGoogleLoginAttempt) return;
        let path = "";
        try { path = new URL(data.url).pathname; } catch (e) {}
        if (path === "/auth/google/done") {
            // Belt-and-braces re-arm alongside startGoogleLogin's own call - the overlay
            // should already be up (it was shown before the browser even opened), but
            // this also refreshes the safety-hide timer right as the real handoff happens.
            showGoogleSigninOverlay();
            checkGoogleLoginStatusOnce(activeGoogleLoginAttempt, { closeBrowser: false });
        }
    });

    // Covers a return to the app that DOESN'T go through the App Link handoff above - the
    // visitor closing the Custom Tab themselves (back gesture, recents switcher) after
    // finishing, or a device/browser combination that never fires appUrlOpen at all (see
    // pollGoogleLogin's own fallback comment). Same snapshot concern as
    // showGoogleSigninOverlay's own comment: re-showing (and re-arming the safety timer)
    // on every resume, not just this one deep-link path, is what keeps the spinner - not
    // a stale Login screenshot - on screen for however long is left of that gap.
    capacitorPlugins.App.addListener("appStateChange", ({ isActive }) => {
        if (isActive && activeGoogleLoginAttempt && !activeGoogleLoginAttempt.settled) {
            showGoogleSigninOverlay();
        }
    });
}

// --- Two-phase login (Google/email choice -> revealed email+password form) ---

function resetLoginPhase() {
    // See .login-wrap-form-phase in app.css - this one class alone drives which panel
    // (choice/form) is on/off screen and its own back-btn fade, with the grid-stacked
    // layout keeping the surrounding block at a constant height either way.
    el("login-wrap").classList.remove("login-wrap-form-phase");
    // Every fresh arrival at Login starts with a clean slate, not whatever error a
    // previous attempt left behind (see hideLoginError()'s own comment).
    hideLoginError();
}

el("login-email-btn").addEventListener("click", () => {
    hideLoginError();
    el("login-wrap").classList.add("login-wrap-form-phase");
});
el("login-email-back-btn").addEventListener("click", resetLoginPhase);

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
    resetResendButton(el("forgot-submit"));
    resetResendButton(el("forgot-phone-submit"));
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
        startResendCountdown(submitBtn, FORGOT_PASSWORD_RESEND_COOLDOWN_SECONDS);
    } catch (err) {
        errorBox.textContent = err.message || t("forgot.error");
        errorBox.classList.remove("hidden");
        submitBtn.disabled = false;
        submitBtn.textContent = t("forgot.submit");
    }
});

// "Find by mobile number" - same success/error UI as the email path above,
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
        startResendCountdown(submitBtn, FORGOT_PASSWORD_RESEND_COOLDOWN_SECONDS);
    } catch (err) {
        errorBox.textContent = err.message || t("forgot.phoneError");
        errorBox.classList.remove("hidden");
        submitBtn.disabled = false;
        submitBtn.textContent = t("forgot.phoneSubmit");
    }
});

// --- Create account (its own native screen - full registration form, pushed on top of
// Login. Terms of Service/Privacy Policy are native screens too - see showLegalScreen
// below. On success, replaces itself with the "check your email" verify-pending screen
// (not pushed - the account already exists at that point, so the back button shouldn't
// return here). ---

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
// a price row on step 2) - without this, the viewport's fixed height (set once in
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

// Live "already taken" checks for step 1's unique fields - see
// setupAvailabilityField() above. Unlike steps 2/3 (still just a dimmed-but-tappable
// arrow that explains what's missing on tap), step 1's arrow is genuinely disabled once
// any of these comes back taken, or while a check is still in flight - see
// updateRegisterNextButtonValidity() below.
const registerEmailAvailability = setupAvailabilityField(
    el("register-contact-email"), el("register-contact-email-error"), "contact_email", "register.emailTaken",
    () => el("register-contact-email").value.trim(), updateRegisterNextButtonValidity,
);
const registerPhoneAvailability = setupAvailabilityField(
    el("register-phone"), el("register-phone-error"), "phone_number", "register.phoneTaken",
    () => composePhoneNumber(el("register-phone"), selectedPhoneCountry), updateRegisterNextButtonValidity,
);

// "Your name"/"Your surname" are two separate inputs purely for a nicer sign-up
// form - api.py's api_register() still expects a single "name" field (the same
// one shown everywhere else: Settings, emails, the public page, admin), so this
// is the one place that combines them instead of splitting "name" into two
// columns end to end.
function registerFullName() {
    return (el("register-firstname").value.trim() + " " + el("register-surname").value.trim()).trim();
}

// Returns a specific error message if step 1 isn't ready to advance, or null if it is.
function registerStep1ValidationError() {
    const name = registerFullName();
    const password = el("register-password").value;
    const confirmPassword = el("register-confirm-password").value;
    const emailField = el("register-contact-email");
    if (!name) return t("register.errorFillRequired");
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
// same field-error treatment as the email/phone "already taken" checks above,
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
// Price list rows are optional, but a half-filled one (service typed, price left blank
// or vice versa) used to just silently vanish on submit with no explanation - checked
// here too since the price list now lives on this same step, alongside working hours.
function registerStep2ValidationError() {
    if (!registerHoursPicker.validate()) return t("hoursPicker.errorOrder");
    if (!registerPricesPicker.validate()) return t("pricePicker.errorIncomplete");
    return null;
}

function registerStep3ValidationError() {
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
// of contact email/phone number taken) genuinely gate the arrow via .disabled -
// the inline field errors above already explain "why" for the taken case, and empty
// required fields are visually obvious, so there's no need to keep it tappable-but-dim
// there. Steps 2/3 (hours/prices/terms) keep the original behavior: dim only, still
// tappable, since tapping is what surfaces a specific reason via register-error - there's
// nothing here that needs a live per-field message the way "already taken" does.
function updateRegisterNextButtonValidity() {
    const btn = el("register-next-btn");
    if (registerStep === 1) {
        const blocked = !!registerStep1ValidationError()
            || registerEmailAvailability.isBlocking()
            || registerPhoneAvailability.isBlocking();
        btn.classList.toggle("register-next-btn-dim", blocked);
        btn.disabled = blocked;
    } else {
        btn.classList.toggle("register-next-btn-dim", !!currentRegisterStepError());
        btn.disabled = false;
    }
}
["register-firstname", "register-surname", "register-password", "register-confirm-password", "register-contact-email"].forEach((id) => {
    el(id).addEventListener("input", updateRegisterNextButtonValidity);
});
el("register-password").addEventListener("input", updatePasswordLengthError);
el("register-password").addEventListener("input", updateConfirmPasswordError);
el("register-confirm-password").addEventListener("input", updateConfirmPasswordError);
el("register-hours-picker").addEventListener("change", updateRegisterNextButtonValidity);
// Price list moved onto this same step (alongside working hours) - live-dims the arrow
// the same way typing into a step 1 field does, instead of only catching an incomplete
// row once the user actually taps the arrow. Delegated on the container since rows are
// created/removed dynamically (see createPricePicker) rather than being static inputs.
el("register-prices-rows").addEventListener("input", updateRegisterNextButtonValidity);
el("register-prices-rows").addEventListener("click", updateRegisterNextButtonValidity);
el("register-prices-add").addEventListener("click", updateRegisterNextButtonValidity);

// register-error doubles as an informational banner (the "you don't have an account yet"
// notice openRegisterScreen shows below, styled amber via .alert-warning) as well as its
// original role showing an actual validation/submit error (red, .alert-error) - this
// keeps whichever one is currently relevant from bleeding into the other.
function setRegisterErrorBoxStyle(isNotice) {
    el("register-error").classList.toggle("alert-warning", !!isNotice);
    el("register-error").classList.toggle("alert-error", !isNotice);
}

el("register-next-btn").addEventListener("click", () => {
    const error = currentRegisterStepError();
    if (error) {
        setRegisterErrorBoxStyle(false);
        el("register-error").textContent = error;
        el("register-error").classList.remove("hidden");
        return;
    }
    el("register-error").classList.add("hidden");
    if (registerStep < REGISTER_STEP_COUNT) showRegisterStep(registerStep + 1);
});

// Shared by the plain "Create account" link and the "Continue with Google" needs_signup
// path below (see pollGoogleLogin) - the latter just adds a name/email starting point
// (and a pending google_link_token to resubmit with the form) on top of the same reset,
// plus an optional `notice` banner explaining why the visitor landed here unprompted.
function openRegisterScreen({ name, contactEmail, googleLinkToken, notice } = {}) {
    // Deliberately NOT register-form.reset() - the working-hours/price-list pickers
    // contain dynamically-created <select>/checkbox elements with no selected/checked
    // HTML attribute set, so a native form reset would revert them to the browser's own
    // default (first option / unchecked) instead of our intended defaults. Clear the
    // plain fields by hand and use each picker's own .reset()/.setValue("") instead.
    ["register-firstname", "register-surname", "register-password", "register-confirm-password", "register-contact-email", "register-phone"].forEach((id) => {
        el(id).value = "";
    });
    el("register-terms").checked = false;
    el("register-push-toggle").checked = true;
    el("register-new-booking-toggle").checked = true;
    registerHoursPicker.reset();
    registerPricesPicker.setValue("");
    registerEmailAvailability.reset();
    registerPhoneAvailability.reset();
    el("register-password-error").classList.add("hidden");
    el("register-confirm-password-error").classList.add("hidden");
    if (notice) {
        setRegisterErrorBoxStyle(true);
        el("register-error").textContent = notice;
        el("register-error").classList.remove("hidden");
    } else {
        setRegisterErrorBoxStyle(false);
        el("register-error").classList.add("hidden");
    }
    pendingGoogleLinkToken = googleLinkToken || null;
    if (name) {
        const spaceIndex = name.indexOf(" ");
        el("register-firstname").value = spaceIndex === -1 ? name : name.slice(0, spaceIndex);
        el("register-surname").value = spaceIndex === -1 ? "" : name.slice(spaceIndex + 1);
    }
    if (contactEmail) {
        el("register-contact-email").value = contactEmail;
        registerEmailAvailability.recheck();
    }
    showRegisterStep(1);
    showScreen("register");
}

el("login-signup-link").addEventListener("click", () => openRegisterScreen());

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
            setRegisterErrorBoxStyle(false);
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
        setRegisterErrorBoxStyle(false);
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
                name: registerFullName(),
                password: el("register-password").value,
                contact_email: el("register-contact-email").value.trim(),
                phone_number: composePhoneNumber(el("register-phone"), selectedPhoneCountry),
                working_hours: registerHoursPicker.getValue(),
                price_list: registerPricesPicker.getValue(),
                accept_terms: el("register-terms").checked,
                push_enabled: el("register-push-toggle").checked,
                new_booking_notifications_enabled: el("register-new-booking-toggle").checked,
                // Re-verified server-side against whatever contact_email was actually
                // submitted (see api_register) - only takes effect if this is still the
                // same email Google prefilled the form with (see openRegisterScreen).
                google_link_token: pendingGoogleLinkToken || undefined,
            },
        });
        pendingGoogleLinkToken = null;
        if (data.token) {
            // Google already proved this email - api_register skipped the confirmation
            // link and handed back a usable Bearer token straight away, same shape as
            // /api/login's own response.
            state.token = data.token;
            state.businessName = data.business_name;
            enterApp();
            storageSet("token", data.token).catch((e) => reportClientError(e.message, e.stack, "storage-set-token"));
            storageSet("businessName", data.business_name).catch((e) => reportClientError(e.message, e.stack, "storage-set-businessName"));
        } else {
            showVerifyPendingScreen(data.business_id, data.contact_email, data.wait_seconds);
        }
    } catch (err) {
        setRegisterErrorBoxStyle(false);
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

function showVerifyPendingScreen(businessId, email, waitSeconds) {
    state.pendingVerifyBusinessId = businessId;
    state.pendingVerifyEmail = email;
    // Falls back to the translated generic "your email" placeholder (its own data-i18n
    // default, restored here since a previous call may have overwritten it with a real
    // address) when the caller doesn't know the address - e.g. a login attempt only ever
    // gets a business_id back, never the contact_email itself.
    el("verify-pending-email").textContent = email || t("verifyPending.yourEmail");
    el("verify-pending-code").value = "";
    el("verify-pending-error").classList.add("hidden");
    el("verify-pending-resent").classList.add("hidden");
    // A verification email was just sent (by register/login) to get here at all, so the
    // resend button starts disabled with the real server-side cooldown already counting
    // down, instead of looking freely clickable until the first tap.
    startResendCountdown(el("verify-pending-resend"), waitSeconds || 0);
    showScreen("verifyPending");
}

// Submitting the typed code hands back a Bearer token straight away on success (see
// api_verify_email_code) - unlike the old emailed-link flow, this never needs a browser
// tab or a trip back to the website: the code entered here is the entire continuation,
// so a verified signup on the app always keeps going in the app.
el("verify-pending-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorBox = el("verify-pending-error");
    const submitBtn = el("verify-pending-submit");
    const code = el("verify-pending-code").value.trim();

    errorBox.classList.add("hidden");
    submitBtn.disabled = true;
    submitBtn.textContent = t("verifyPending.verifying");

    try {
        const data = await api("/api/verify-email-code", {
            method: "POST",
            body: { business_id: state.pendingVerifyBusinessId, code },
        });
        state.pendingVerifyBusinessId = null;
        state.token = data.token;
        state.businessName = data.business_name;
        enterApp();
        storageSet("token", data.token).catch((e) => reportClientError(e.message, e.stack, "storage-set-token"));
        storageSet("businessName", data.business_name).catch((e) => reportClientError(e.message, e.stack, "storage-set-businessName"));
    } catch (err) {
        errorBox.textContent = err.message || t("verifyPending.wrongCode");
        errorBox.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = t("verifyPending.verify");
    }
});

el("verify-pending-resend").addEventListener("click", async () => {
    const btn = el("verify-pending-resend");
    if (btn.disabled) return;
    btn.disabled = true;
    try {
        const data = await api("/api/resend-verification", { method: "POST", body: { business_id: state.pendingVerifyBusinessId } });
        el("verify-pending-resent").classList.remove("hidden");
        startResendCountdown(btn, data.wait_seconds || 0);
    } catch (err) {
        // Best-effort - the endpoint always responds sent:true when reachable at all, so
        // this only fires on a real network error, already surfaced by the offline banner.
        btn.disabled = false;
    }
});

el("verify-pending-back").addEventListener("click", () => {
    state.pendingVerifyBusinessId = null;
    showScreen("login");
});

function enterApp() {
    showTabbar(true);
    // Faza 15: My Site (an overview of the AI-built website) is the true landing tab now,
    // not the conversation inbox - same pivot the website made with its own home()/
    // conversations_home() split (see app.py). loadDashboard() still runs eagerly so
    // Conversations is already populated (same "was loaded before you tapped it" feel as
    // Schedule below) the moment it's tapped.
    setActiveTab("mysite");
    loadMySite();
    loadDashboard();
    loadSettingsIntoForm();
    loadBusinessMedia();
    loadBusinessDocuments();
    startAutoRefresh();
    // Fire-and-forget: warms state.scheduleCache in the background so the Schedule tab
    // is usually already populated (same "was loaded before you tapped it" feel as
    // Inbox/Profile) by the time it's actually opened, instead of only ever fetching on
    // tap - loadSchedule() is safe to call here even though Schedule isn't the visible
    // screen yet, since it only ever toggles that screen's own (currently offscreen)
    // elements.
    loadSchedule();
}

// --- Push notifications ---
let pushListenersRegistered = false;

function setupPushListeners() {
    if (pushListenersRegistered || !capacitorPlugins || !capacitorPlugins.PushNotifications) return;
    pushListenersRegistered = true;

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

// Requests OS notification permission and registers for a device token - safe to call
// more than once (e.g. every time the Settings "Notifications" toggle is switched on),
// unlike the old one-shot registerForPushNotifications() this replaces for that path.
// Android only re-shows its own permission dialog if the user hasn't permanently denied
// it - nothing this code can do about that OS-level restriction, but it always at least
// tries, instead of silently doing nothing until a reinstall.
function requestPushPermissionAndRegister() {
    if (!capacitorPlugins || !capacitorPlugins.PushNotifications) return;
    setupPushListeners();
    capacitorPlugins.PushNotifications.requestPermissions().then((result) => {
        if (result.receive === "granted") capacitorPlugins.PushNotifications.register();
    });
}

// Only ever called once we've confirmed (via /api/dashboard's push_enabled flag) that a
// Firebase project is actually configured server-side - avoids calling into the
// Firebase-backed native registration code at all while it isn't, so there's nothing to
// go wrong before that's set up. The initial silent request right after login; see
// requestPushPermissionAndRegister() above for the explicit re-ask from Settings.
let pushRegistrationAttempted = false;

function registerForPushNotifications() {
    if (pushRegistrationAttempted) return;
    pushRegistrationAttempted = true;
    requestPushPermissionAndRegister();
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

        el("count-all").textContent = data.total_count;
        el("count-pending").textContent = data.pending_count;
        el("count-sent").textContent = data.sent_count;
        el("count-archived").textContent = data.archived_count;
        el("count-trash").textContent = data.trash_count;

        if (data.stats) {
            el("inbox-stat-sent").textContent = data.stats.total_sent;
            el("inbox-stat-sent-week").textContent = data.stats.sent_this_week;
        }

        el("banner-inactive").classList.toggle("hidden", data.beta_free_mode || data.subscription_active);

        el("donation-strip").classList.toggle("hidden", !data.donation_link);
        if (data.donation_link) el("donation-strip-link").href = data.donation_link;
        // Cached so the next launch can show/hide this correctly right away instead of
        // waiting on this same fetch every time - see the boot sequence below, which reads
        // this back before the inbox is even shown. donation_link barely ever changes (it's
        // a business-wide setting, not per-conversation), so a stale cached copy for the
        // first render or two of a rare change is a fine trade for never seeing this strip
        // pop in and shove the list down after the screen already looks fully loaded.
        storageSet("donationLink", data.donation_link || "").catch(() => {});

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

// Bumped on every renderInboxList() call so a slower, older call (still waiting on
// resolveAvatarUrl's preloads below) can tell it's been superseded by a newer one - e.g.
// a poll refresh landing while the user is still typing in the search box - and bail
// instead of overwriting the list with stale rows a moment later.
let inboxRenderToken = 0;

async function renderInboxList(conversations) {
    const token = ++inboxRenderToken;
    const list = el("inbox-list");

    if (conversations.length === 0) {
        list.innerHTML = "";
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
        } else if (state.filter === "pending") {
            el("inbox-empty-title").textContent = t("inbox.emptyPendingTitle");
            el("inbox-empty-detail").textContent = t("inbox.emptyPendingDetail");
        } else {
            el("inbox-empty-title").textContent = t("inbox.emptyTitle");
            el("inbox-empty-detail").textContent = t("inbox.emptyDetail");
        }
        return;
    }
    el("inbox-empty").classList.add("hidden");

    // Every avatar is resolved (real photo loaded, or given up in favor of the letter
    // avatar) BEFORE any row is built, instead of the old approach of building the row
    // right away with an <img> whose src was still in flight - that showed the sender
    // name/message text immediately with an empty avatar slot next to it, then had each
    // photo pop in on its own as its own fetch happened to finish, one row at a time.
    // Resolving everything up front (in parallel) means every row's avatar and text
    // reach the screen together, in one pass, no matter how the network responses land.
    const avatarUrls = await Promise.all(conversations.map(resolveAvatarUrl));
    if (token !== inboxRenderToken) return; // superseded by a newer render meanwhile

    list.innerHTML = "";
    const fragment = document.createDocumentFragment();
    conversations.forEach((c, i) => {
        const row = document.createElement("div");
        row.className = "inbox-row" + (c.status === "sent" || c.expired ? " inbox-row-sent" : "");

        const tapArea = document.createElement("button");
        tapArea.type = "button";
        tapArea.className = "inbox-row-tap";
        tapArea.appendChild(buildAvatarElement(c, avatarUrls[i]));
        tapArea.insertAdjacentHTML("beforeend", `
            <span class="inbox-row-main">
                <span class="inbox-row-top">
                    <span class="inbox-sender-group">
                        <span class="inbox-sender">${escapeHtml(c.sender)}</span>
                    </span>
                    <span class="inbox-time">${timeAgo(c.created_at)}</span>
                </span>
                <span class="inbox-preview">${escapeHtml(c.question)}</span>
            </span>
            <span class="inbox-dot ${c.expired ? "expired" : (c.status === "sent" ? "sent" : "")}"></span>
        `);
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
        fragment.appendChild(row);
    });
    list.appendChild(fragment);

    // One measurement pass after every row is in the DOM (not interleaved with the
    // appends above) so this is a single reflow instead of layout-thrashing once per
    // row. Only a name that's actually clipped (its text is wider than the space the
    // flex row gave it) gets the fade-to-transparent mask; a short name like "Pera"
    // that fits next to the date stays fully readable, with just the row's normal
    // 8px gap between it and the timestamp.
    list.querySelectorAll(".inbox-sender").forEach((senderEl) => {
        senderEl.classList.toggle("truncated", senderEl.scrollWidth > senderEl.clientWidth);
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

// Loads (not just requests - actually waits for decode success/failure) a URL in a
// detached Image(), so a later real <img src=url> for the same URL paints immediately
// from the browser's own cache instead of re-fetching. Bounded by a timeout so one
// slow or hanging request can't hold up the whole inbox list's render (see
// resolveAvatarUrl/renderInboxList below) - past that, it's treated the same as a load
// failure and falls back to the letter avatar.
function preloadImage(url, timeoutMs = 3000) {
    return new Promise((resolve) => {
        if (!url) { resolve(false); return; }
        let settled = false;
        const finish = (ok) => { if (!settled) { settled = true; resolve(ok); } };
        const img = new Image();
        img.onload = () => finish(true);
        img.onerror = () => finish(false);
        img.src = url;
        setTimeout(() => finish(false), timeoutMs);
    });
}

// Resolves to a usable profile picture URL, or null (meaning "use the letter avatar" -
// see buildAvatarElement below). c.profile_pic_url can be missing entirely (a page-chat
// visitor has no profile picture), in which case there's nothing to try loading.
//
// Cached per conversation+URL so repeat calls (a poll refresh, typing in the search box
// - both re-render the whole list) don't re-fetch/re-preload an avatar already resolved.
const avatarUrlCache = new Map();
async function resolveAvatarUrl(c) {
    const cacheKey = `${c.id}:${c.profile_pic_url || ""}`;
    if (avatarUrlCache.has(cacheKey)) return avatarUrlCache.get(cacheKey);

    const resolved = (c.profile_pic_url && await preloadImage(c.profile_pic_url)) ? c.profile_pic_url : null;
    avatarUrlCache.set(cacheKey, resolved);
    return resolved;
}

// Builds the actual avatar element from an already-resolved URL (or null) - never does
// its own loading/fetching, so building it can never delay the row it belongs to.
function buildAvatarElement(c, resolvedUrl) {
    if (!resolvedUrl) return buildLetterAvatar(c.sender);
    const img = document.createElement("img");
    img.className = "inbox-avatar inbox-avatar-img loaded";
    img.alt = "";
    img.src = resolvedUrl;
    return img;
}

function buildLetterAvatar(sender) {
    const span = document.createElement("span");
    span.className = "inbox-avatar";
    span.textContent = (sender[0] || "?").toUpperCase();
    return span;
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

// Pull-to-refresh - on top of the automatic push-triggered + 15s poll refresh
// (startAutoRefresh above), since it's still the gesture users instinctively reach for
// on any inbox screen. Only tracked while .inbox-list-wrap (the actual scrolling
// element - title/stats/search/filters/list all scroll together inside it, see
// app.css) is scrolled to the very top - once it's scrolled down, a downward drag is
// just normal scrolling.
(function setupPullToRefresh() {
    const list = document.querySelector(".inbox-list-wrap");
    const indicator = el("inbox-pull-indicator");
    const spinner = indicator.querySelector(".inbox-pull-spinner");
    const PULL_THRESHOLD = 60;
    const MAX_PULL = 90;

    let startY = null;
    let pulling = false;
    let refreshing = false;

    list.addEventListener("touchstart", (event) => {
        if (refreshing || list.scrollTop > 0) {
            startY = null;
            return;
        }
        startY = event.touches[0].clientY;
        pulling = false;
    }, { passive: true });

    list.addEventListener("touchmove", (event) => {
        if (startY === null || refreshing) return;
        const dy = event.touches[0].clientY - startY;
        if (dy <= 0) return;
        // Bails out of the gesture (falls back to normal scroll) if the list has
        // scrolled away from the top mid-drag, e.g. new content shifted it.
        if (list.scrollTop > 0) {
            startY = null;
            pulling = false;
            indicator.classList.remove("dragging");
            indicator.style.height = "0px";
            spinner.style.transform = "";
            return;
        }
        pulling = true;
        event.preventDefault();
        const pull = Math.min(dy * 0.5, MAX_PULL);
        indicator.classList.add("dragging");
        indicator.style.height = pull + "px";
        spinner.style.transform = `rotate(${pull * 3}deg)`;
    }, { passive: false });

    async function endPull() {
        if (!pulling) {
            startY = null;
            return;
        }
        pulling = false;
        startY = null;
        indicator.classList.remove("dragging");

        const pulledEnough = parseFloat(indicator.style.height || "0") >= PULL_THRESHOLD;
        if (!pulledEnough) {
            indicator.style.height = "0px";
            spinner.style.transform = "";
            return;
        }

        refreshing = true;
        indicator.classList.add("refreshing");
        indicator.style.height = PULL_THRESHOLD + "px";
        spinner.style.transform = "";
        try {
            await loadDashboard({ silent: true });
        } finally {
            indicator.classList.remove("refreshing");
            indicator.style.height = "0px";
            refreshing = false;
        }
    }

    list.addEventListener("touchend", endPull);
    list.addEventListener("touchcancel", endPull);
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
    const isExpired = !!conversation.expired;
    if (isExpired) {
        badge.textContent = t("detail.badgeExpired");
        badge.className = "badge badge-expired";
    } else {
        badge.textContent = isPending ? t("detail.badgePending") : t("detail.badgeSent");
        badge.className = "badge " + (isPending ? "badge-pending" : "badge-sent");
    }

    // Archived/Trash conversations are read-only here too (matches the web dashboard's
    // same restriction) - sending a reply to something already archived, deleted, or
    // expired would be a confusing way to act on it without restoring/unarchiving first.
    const canReply = isPending && !isExpired && state.filter !== "archived" && state.filter !== "trash";
    el("detail-pending-block").classList.toggle("hidden", !canReply);
    el("detail-sent-block").classList.toggle("hidden", canReply);

    // Proactive heads-up for a still-pending reply whose 24h window has already quietly
    // passed but the background sweep (reminders.expire_stale_pending_conversations_if_
    // needed) hasn't caught up yet - it runs every few minutes, not instantly - so this
    // warns before the tap instead of only after a failed send.
    const HOURS_24_MS = 24 * 60 * 60 * 1000;
    const createdAtMs = conversation.created_at ? new Date(conversation.created_at).getTime() : NaN;
    const windowPassed = !isNaN(createdAtMs) && Date.now() - createdAtMs > HOURS_24_MS;
    const warningEl = el("detail-expiry-warning");
    if (isExpired) {
        warningEl.textContent = t("detail.expiredNote");
        warningEl.classList.remove("hidden");
        // Expired: show the reply first, the expiry note after it (matches the web
        // dashboard's archived-conversation order). Otherwise the warning stays above
        // the still-editable reply form (see the non-expired branch below).
        el("detail-sent-block").insertAdjacentElement("afterend", warningEl);
    } else if (canReply && windowPassed) {
        warningEl.textContent = t("detail.expiryWarning");
        warningEl.classList.remove("hidden");
        el("detail-pending-block").insertAdjacentElement("beforebegin", warningEl);
    } else {
        warningEl.classList.add("hidden");
        el("detail-pending-block").insertAdjacentElement("beforebegin", warningEl);
    }

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

// Autosave for the three "Save changes" flows below (Profile, App Settings, My Site) -
// a valid, dirty form saves itself a short debounce after the user stops editing instead
// of waiting for a tap. SHOW_MANUAL_SAVE_BUTTONS is the single switch for all of it: false
// keeps every button/handler/validity-check below exactly as it already was, just hidden
// and autosave-triggered instead of tapped; flip it back to true to restore the old
// tap-to-save UI (visible floating buttons, full-screen save-success overlay, no
// autosave) with no other code changes.
const SHOW_MANUAL_SAVE_BUTTONS = false;

function makeAutosaveTrigger(buttonId, debounceMs) {
    let timer = null;
    // If a previous autosave is still in flight (button.disabled) when the debounce
    // fires, retry on the same interval instead of dropping this edit - the button
    // clears its own disabled state (see each click handler's `finally`) once that
    // request settles.
    function attempt() {
        const button = el(buttonId);
        if (button.disabled) { timer = setTimeout(attempt, debounceMs); return; }
        timer = null;
        button.click();
    }
    return function trigger(shouldSave) {
        if (timer) { clearTimeout(timer); timer = null; }
        if (SHOW_MANUAL_SAVE_BUTTONS || !shouldSave) return;
        timer = setTimeout(attempt, debounceMs);
    };
}

// A save-success message (full-screen celebration or toast) makes sense after a
// deliberate tap; silent background autosave shows nothing at all - see
// SHOW_MANUAL_SAVE_BUTTONS above to bring tap-to-save (and this confirmation) back.
function notifySaved(message) {
    if (SHOW_MANUAL_SAVE_BUTTONS) showSaveSuccess(message);
}

// The Save button stays hidden until the form actually differs from what was loaded (a
// serialized snapshot comparison, not a generic "something fired an event" flag - so
// re-picking the same phone country or an untouched field never shows it), then goes
// from disabled/gray to enabled/yellow only once every field satisfies its own
// validation - the same checks the click handler below re-runs before actually saving.
let profileFormSnapshot = null;

function serializeProfileForm() {
    return JSON.stringify({
        name: el("settings-name").value,
        contactEmail: el("settings-contact-email").value,
        phone: composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry),
        hours: profileHoursPicker.getValue(),
        prices: profilePricesPicker.getValue(),
        upsell: profileUpsellPicker.getValue(),
        currentPassword: el("settings-current-password").value,
        newPassword: el("settings-new-password").value,
    });
}

// Same live "already taken" mechanism as Create Account step 1 (see
// setupAvailabilityField above) - here the caller IS logged in, so the Bearer token lets
// the server exclude this business's own current value automatically (editing a field
// without changing it never flags itself as taken).
function isProfileFormValid() {
    const contactEmailField = el("settings-contact-email");
    const contactEmail = contactEmailField.value.trim();
    const newPassword = el("settings-new-password").value;
    const currentPassword = el("settings-current-password").value;

    if (!contactEmail || !contactEmailField.checkValidity()) return false;
    if (profileEmailAvailability.isBlocking() || profilePhoneAvailability.isBlocking()) return false;
    if (newPassword && !currentPassword) return false;
    if (newPassword && newPassword.length < 8) return false;
    if (!profileHoursPicker.validate()) return false;
    if (!profilePricesPicker.validate()) return false;
    return true;
}

const triggerProfileAutosave = makeAutosaveTrigger("settings-save", 1400);

function updateSettingsSaveValidity() {
    const button = el("settings-save");
    const dirty = profileFormSnapshot !== null && serializeProfileForm() !== profileFormSnapshot;
    const valid = isProfileFormValid();
    button.classList.toggle("hidden", !SHOW_MANUAL_SAVE_BUTTONS || !dirty);
    button.disabled = !valid;
    el("screen-profile").classList.toggle("has-unsaved", SHOW_MANUAL_SAVE_BUTTONS && dirty);
    triggerProfileAutosave(dirty && valid);
}

// Delegated instead of one listener per field - also catches the hours/prices pickers'
// custom controls (checkboxes, the option-button dropdowns, add/remove service rows),
// which either bubble a "change"/"click" or (for the option buttons) dispatch a synthetic
// bubbling "change" themselves (see createOptionButton above).
["input", "change", "click"].forEach((evt) => {
    el("screen-profile").addEventListener(evt, updateSettingsSaveValidity);
});

const profileEmailAvailability = setupAvailabilityField(
    el("settings-contact-email"), el("settings-contact-email-error"), "contact_email", "register.emailTaken",
    () => el("settings-contact-email").value.trim(), updateSettingsSaveValidity,
);
const profilePhoneAvailability = setupAvailabilityField(
    el("settings-phone"), el("settings-phone-error"), "phone_number", "register.phoneTaken",
    () => composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry), updateSettingsSaveValidity,
);

function updatePhoneVerifyStatus(phoneNumber, phoneVerified, smsAvailable, phoneLoginEnabled) {
    el("settings-phone-verify-sent").classList.add("hidden");
    el("settings-phone-verify-code-row").classList.add("hidden");
    el("settings-phone-verify-code").value = "";
    el("settings-phone-verify-code-error").classList.add("hidden");

    // While PHONE_LOGIN_ENABLED is False server-side (database.py) the phone number is
    // just optional contact info the AI can use when replying to customers, not a login
    // identifier - no verified badge, no "not verified" nag, regardless of the account's
    // actual (stale, from before this was disabled) phone_verified value.
    if (!phoneLoginEnabled) {
        el("settings-phone-verified-badge").classList.add("hidden");
        el("settings-phone-unverified-banner").classList.add("hidden");
        return;
    }

    el("settings-phone-verified-badge").classList.toggle("hidden", !(phoneNumber && phoneVerified));
    el("settings-phone-unverified-banner").classList.toggle("hidden", !(phoneNumber && !phoneVerified));

    // Button copy matches which channel /api/verify-phone/send will actually use (see
    // send_sms.sms_configured() server-side) - known upfront from /api/settings, instead
    // of only finding out after the button's already been tapped once.
    const verifyBtn = el("settings-phone-verify-btn");
    verifyBtn.setAttribute("data-i18n", smsAvailable ? "settings.phoneVerifyBtnSms" : "settings.phoneVerifyBtn");
    verifyBtn.textContent = t(verifyBtn.getAttribute("data-i18n"));
}

async function loadSettingsIntoForm() {
    try {
        const data = await api("/api/settings");
        el("settings-name").value = data.name;
        el("settings-contact-email").value = data.contact_email;
        const { country, rest } = splitStoredPhoneNumber(data.phone_number);
        setProfilePhoneCountry(country);
        el("settings-phone").value = rest;
        updatePhoneVerifyStatus(data.phone_number, data.phone_verified, data.sms_verification_available, data.phone_login_enabled);
        profileHoursPicker.setValue(data.working_hours);
        profilePricesPicker.setValue(data.price_list);
        profileUpsellPicker.setValue(data.upsell_pairs || "");
        resetPasswordField("settings-current-password");
        resetPasswordField("settings-new-password");
        applyProAccessLocks();
        profileFormSnapshot = serializeProfileForm();
        updateSettingsSaveValidity();
    } catch (err) {
        if (err.status === 401) logout();
    }
}

// --- Website photos & video (Profile screen, Faza 14) - logo/gallery/hero video for
// the business's own public page (see api.py's /api/media, business_media.py). Each
// item uploads immediately on selection rather than being staged for the Save button
// above - a business_media row is harmless the moment it exists, unlike the profile
// fields Save actually writes, so there's no reason to hold uploads back behind a
// second explicit save step. Bypasses the JSON-only api() helper (see its own docstring
// above) since this needs a multipart/form-data body, not JSON. ---

const mediaGrids = Array.prototype.slice.call(document.querySelectorAll("#screen-profile .media-grid"));
const mediaErrorBox = el("media-error");

function mediaAcceptFor(role) {
    return role === "hero_video" ? "video/mp4,video/quicktime" : "image/jpeg,image/png,image/webp";
}

function mediaShowError(message) {
    mediaErrorBox.textContent = message;
    mediaErrorBox.classList.remove("hidden");
}

function mediaRefreshAddButton(grid) {
    const max = parseInt(grid.dataset.max, 10);
    const count = grid.querySelectorAll(".media-thumb").length;
    const addBtn = grid.querySelector(".media-add");
    if (addBtn) addBtn.classList.toggle("hidden", count >= max);
}

function mediaMakeThumb(grid, item) {
    const thumb = document.createElement("div");
    thumb.className = "media-thumb";
    thumb.dataset.id = item.id;

    const media = item.type === "video"
        ? Object.assign(document.createElement("video"), { src: item.url, muted: true, playsInline: true })
        : Object.assign(document.createElement("img"), { src: item.url, alt: "" });
    thumb.appendChild(media);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "media-remove";
    removeBtn.setAttribute("aria-label", "Remove");
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => mediaRemoveItem(grid, item.id, thumb));
    thumb.appendChild(removeBtn);

    grid.insertBefore(thumb, grid.querySelector(".media-add"));
    mediaRefreshAddButton(grid);
}

function mediaUploadFile(grid, role, file) {
    const max = parseInt(grid.dataset.max, 10);
    if (grid.querySelectorAll(".media-thumb").length >= max) {
        mediaShowError(t("settings.media.limitError").replace("{max}", max));
        return;
    }
    mediaErrorBox.classList.add("hidden");

    const placeholder = document.createElement("div");
    placeholder.className = "media-thumb uploading";
    grid.insertBefore(placeholder, grid.querySelector(".media-add"));

    const formData = new FormData();
    formData.append("role", role);
    formData.append("file", file);

    fetch(API_BASE + "/api/media", {
        method: "POST",
        headers: state.token ? { Authorization: "Bearer " + state.token } : {},
        body: formData,
    }).then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
            placeholder.remove();
            if (!ok) {
                mediaShowError(data.error || t("settings.media.error"));
                return;
            }
            // logo/hero_video replace whatever was there - drop any existing thumb in
            // this grid before adding the new one, mirroring what the server just did.
            if (max === 1) {
                grid.querySelectorAll(".media-thumb").forEach((node) => node.remove());
            }
            mediaMakeThumb(grid, data);
        }).catch(() => {
            placeholder.remove();
            mediaShowError(t("settings.media.error"));
        });
}

function mediaRemoveItem(grid, id, thumb) {
    thumb.classList.add("uploading");
    fetch(API_BASE + "/api/media/" + id, {
        method: "DELETE",
        headers: state.token ? { Authorization: "Bearer " + state.token } : {},
    }).then((response) => {
        if (!response.ok) throw new Error();
        thumb.remove();
        mediaRefreshAddButton(grid);
    }).catch(() => {
        thumb.classList.remove("uploading");
        mediaShowError(t("settings.media.removeError"));
    });
}

function mediaInitGrid(grid) {
    const role = grid.dataset.role;
    const label = document.createElement("label");
    label.className = "media-add";
    label.textContent = "+";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = mediaAcceptFor(role);
    input.multiple = grid.dataset.max !== "1";
    input.addEventListener("change", () => {
        const files = Array.prototype.slice.call(input.files || []);
        input.value = "";
        files.forEach((file) => mediaUploadFile(grid, role, file));
    });
    label.appendChild(input);
    grid.appendChild(label);
}

async function loadBusinessMedia() {
    mediaGrids.forEach((grid) => {
        grid.querySelectorAll(".media-thumb").forEach((node) => node.remove());
    });
    try {
        const data = await api("/api/media");
        (data.media || []).forEach((item) => {
            const grid = mediaGrids.find((g) => g.dataset.role === item.role);
            if (grid) mediaMakeThumb(grid, item);
        });
    } catch (err) {
        if (err.status === 401) logout();
    }
}

mediaGrids.forEach(mediaInitGrid);

// --- Business documents (Profile screen) - PDF/Word source material uploaded alongside
// the typed description ("Tell us more about your business" field above), see api.py's
// /api/documents and business_documents.py. Same "uploads immediately, no separate Save
// step, bypasses the JSON-only api() helper for the multipart body" reasoning as the
// media section above - just a flat list of filenames instead of a thumbnail grid,
// since a PDF/Word file has no useful inline preview. ---

const documentList = el("document-list");
const documentErrorBox = el("document-error");
const documentFileInput = el("document-file-input");

function documentShowError(message) {
    documentErrorBox.textContent = message;
    documentErrorBox.classList.remove("hidden");
}

function documentMakeRow(item) {
    const row = document.createElement("div");
    row.className = "document-row";
    row.dataset.id = item.id;

    const name = document.createElement("span");
    name.className = "document-row-name";
    name.textContent = item.filename;
    row.appendChild(name);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "document-row-remove";
    removeBtn.setAttribute("aria-label", "Remove");
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => documentRemoveItem(item.id, row));
    row.appendChild(removeBtn);

    documentList.appendChild(row);
}

function documentUploadFile(file) {
    documentErrorBox.classList.add("hidden");

    const placeholder = document.createElement("div");
    placeholder.className = "document-row uploading";
    placeholder.textContent = file.name;
    documentList.appendChild(placeholder);

    const formData = new FormData();
    formData.append("file", file);

    fetch(API_BASE + "/api/documents", {
        method: "POST",
        headers: state.token ? { Authorization: "Bearer " + state.token } : {},
        body: formData,
    }).then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
            placeholder.remove();
            if (!ok) {
                documentShowError(data.error || t("settings.documents.error"));
                return;
            }
            documentMakeRow(data);
        }).catch(() => {
            placeholder.remove();
            documentShowError(t("settings.documents.error"));
        });
}

function documentRemoveItem(id, row) {
    row.classList.add("uploading");
    fetch(API_BASE + "/api/documents/" + id, {
        method: "DELETE",
        headers: state.token ? { Authorization: "Bearer " + state.token } : {},
    }).then((response) => {
        if (!response.ok) throw new Error();
        row.remove();
    }).catch(() => {
        row.classList.remove("uploading");
        documentShowError(t("settings.documents.removeError"));
    });
}

documentFileInput.addEventListener("change", () => {
    const files = Array.prototype.slice.call(documentFileInput.files || []);
    documentFileInput.value = "";
    files.forEach(documentUploadFile);
});

async function loadBusinessDocuments() {
    documentList.innerHTML = "";
    try {
        const data = await api("/api/documents");
        (data.documents || []).forEach(documentMakeRow);
    } catch (err) {
        if (err.status === 401) logout();
    }
}

async function saveProfileForm() {
    const button = el("settings-save");
    const errorBox = el("settings-error");
    errorBox.classList.add("hidden");

    const contactEmailField = el("settings-contact-email");
    const contactEmail = contactEmailField.value.trim();
    const newPassword = el("settings-new-password").value;
    const currentPassword = el("settings-current-password").value;

    if (!contactEmail || !contactEmailField.checkValidity()) {
        errorBox.textContent = t("register.errorFillRequired");
        errorBox.classList.remove("hidden");
        return;
    }
    if (profileEmailAvailability.isBlocking() || profilePhoneAvailability.isBlocking()) {
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
                contact_email: contactEmail,
                phone_number: composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry),
                working_hours: profileHoursPicker.getValue(),
                price_list: profilePricesPicker.getValue(),
                upsell_pairs: state.proAccess ? profileUpsellPicker.getValue() : undefined,
                current_password: currentPassword || undefined,
                new_password: newPassword || undefined,
            },
        });
        notifySaved(t("settings.saved"));
        el("inbox-business-name").textContent = el("settings-name").value;
        resetPasswordField("settings-current-password");
        resetPasswordField("settings-new-password");
        updatePhoneVerifyStatus(data.phone_number, data.phone_verified, data.sms_verification_available, data.phone_login_enabled);
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
}
el("settings-save").addEventListener("click", saveProfileForm);

el("settings-phone-verify-btn").addEventListener("click", async () => {
    const btn = el("settings-phone-verify-btn");
    if (btn.disabled) return;
    btn.disabled = true;
    try {
        const data = await api("/api/verify-phone/send", { method: "POST" });
        // channel="sms" (send_sms.sms_configured() is True server-side) shows the code
        // field instead of the "check your email" message - see
        // settings-phone-verify-code-submit below for redeeming it.
        el("settings-phone-verify-sent").classList.toggle("hidden", data.channel === "sms");
        el("settings-phone-verify-code-row").classList.toggle("hidden", data.channel !== "sms");
        startResendCountdown(btn, data.wait_seconds || 0);
    } catch (err) {
        // Best-effort, same as the account-email resend button - a real failure here is
        // already surfaced by the offline banner.
        btn.disabled = false;
    }
});

el("settings-phone-verify-code-submit").addEventListener("click", async () => {
    const btn = el("settings-phone-verify-code-submit");
    const errorBox = el("settings-phone-verify-code-error");
    if (btn.disabled) return;
    const code = el("settings-phone-verify-code").value.trim();
    errorBox.classList.add("hidden");
    btn.disabled = true;
    try {
        await api("/api/verify-phone/confirm", { method: "POST", body: { code } });
        updatePhoneVerifyStatus(composePhoneNumber(el("settings-phone"), selectedProfilePhoneCountry), true, true);
    } catch (err) {
        errorBox.textContent = err.message || t("settings.error");
        errorBox.classList.remove("hidden");
    } finally {
        btn.disabled = false;
    }
});

// --- Delete account wizard: warning -> reason -> confirm, three separate screens
// instead of the old single confirm() dialog (see the 3 screen-delete-* sections in
// index.html). Reason + optional free-text picked on the middle screen are held here in
// plain JS state and only ever sent to the server as part of the final DELETE call on
// the confirm screen - nothing is persisted (or even validated server-side) before then.

let deleteWizardReasonCategory = null;
let deleteWizardReasonDetails = "";

el("settings-delete-account").addEventListener("click", () => {
    deleteWizardReasonCategory = null;
    deleteWizardReasonDetails = "";
    document.querySelectorAll('input[name="delete-reason"]').forEach((input) => { input.checked = false; });
    el("delete-reason-details").value = "";
    el("delete-reason-error").classList.add("hidden");
    el("delete-confirm-password").value = "";
    el("delete-confirm-checkbox").checked = false;
    el("delete-confirm-error").classList.add("hidden");
    el("delete-confirm-submit").disabled = true;
    showScreen("deleteWarning");
});

el("delete-warning-back").addEventListener("click", () => showScreen("settings"));
el("delete-warning-continue").addEventListener("click", () => showScreen("deleteReason"));

el("delete-reason-back").addEventListener("click", () => showScreen("deleteWarning"));
el("delete-reason-continue").addEventListener("click", () => {
    const checked = document.querySelector('input[name="delete-reason"]:checked');
    if (!checked) {
        el("delete-reason-error").textContent = t("deleteWizard.step2.errorReason");
        el("delete-reason-error").classList.remove("hidden");
        return;
    }
    el("delete-reason-error").classList.add("hidden");
    deleteWizardReasonCategory = checked.value;
    deleteWizardReasonDetails = el("delete-reason-details").value.trim().slice(0, 1000);
    showScreen("deleteConfirm");
});

function updateDeleteConfirmSubmitState() {
    el("delete-confirm-submit").disabled = !(
        el("delete-confirm-password").value.trim().length > 0 && el("delete-confirm-checkbox").checked
    );
}
el("delete-confirm-password").addEventListener("input", updateDeleteConfirmSubmitState);
el("delete-confirm-checkbox").addEventListener("change", updateDeleteConfirmSubmitState);

el("delete-confirm-back").addEventListener("click", () => showScreen("deleteReason"));

el("delete-confirm-submit").addEventListener("click", async () => {
    const button = el("delete-confirm-submit");
    const errorBox = el("delete-confirm-error");
    errorBox.classList.add("hidden");
    button.disabled = true;
    try {
        await api("/api/delete-account", {
            method: "POST",
            body: {
                password: el("delete-confirm-password").value,
                reason_category: deleteWizardReasonCategory,
                reason_details: deleteWizardReasonDetails,
            },
        });
        await logout();
    } catch (err) {
        errorBox.textContent = err.message || t("appSettings.deleteError");
        errorBox.classList.remove("hidden");
        updateDeleteConfirmSubmitState();
    }
});

// Confirm modal (same .lang-modal pattern used elsewhere) instead of logging out
// immediately on tap - a real tap-target for something this hard to undo mid-session
// shouldn't fire on the first touch.
function closeLogoutConfirmModal() {
    el("logout-confirm-modal").classList.remove("open");
}

el("settings-logout").addEventListener("click", () => {
    el("logout-confirm-modal").classList.add("open");
});
el("logout-confirm-backdrop").addEventListener("click", closeLogoutConfirmModal);
el("logout-confirm-cancel").addEventListener("click", closeLogoutConfirmModal);
el("logout-confirm-confirm").addEventListener("click", () => {
    closeLogoutConfirmModal();
    logout();
});

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

    // While billing is hidden (BETA_FREE_MODE), the Settings "Billing" row, the Help FAQ
    // entry about the Pro plan, and the Pricing/Paddle mentions in the Terms/Privacy
    // screens all reference a plan concept nobody can act on right now - hide them too,
    // same idea as the website's app_settings.html/terms.html/privacy.html gating their
    // equivalents.
    el("settings-billing-label").classList.toggle("hidden", state.betaFreeMode);
    el("settings-subscription-row").classList.toggle("hidden", state.betaFreeMode);
    el("help-faq-pro-plan").classList.toggle("hidden", state.betaFreeMode);
    el("terms-pricing-section").classList.toggle("hidden", state.betaFreeMode);
    el("privacy-paddle-item").classList.toggle("hidden", state.betaFreeMode);

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
// not duplicated natively) in the in-app browser - a short-lived signed link, since the
// mobile app's Bearer token can't be attached to a request the Browser plugin opens.
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

// Same "hand off to a web page" pattern as openChoosePlan above, for the $15/yr custom-
// domain checkout (templates/domain_checkout.html) that api_save_page's
// domain_checkout_required response triggers - the domain is billed to the business
// directly, as its own subscription separate from Starter/Pro, so there's no in-app
// payment step to build here.
async function openDomainCheckout(domain) {
    try {
        const data = await api("/api/domain-checkout-link?domain=" + encodeURIComponent(domain));
        if (capacitorPlugins && capacitorPlugins.Browser) {
            await capacitorPlugins.Browser.open({ url: data.url });
        } else {
            window.open(data.url, "_blank");
        }
    } catch (err) {
        reportClientError(err.message, err.stack, "domain-checkout-link");
    }
}

// "View page" on the My Site tab - same bridge as "Choose a plan" above, opening the
// full public_page.html render (see app.py's site_preview()/site_preview_mobile()) in
// the in-app browser so the owner can see the complete site Camilius built from
// everything on file, even before it's published.
async function openSitePreview() {
    try {
        const data = await api("/api/site-preview-link");
        if (capacitorPlugins && capacitorPlugins.Browser) {
            await capacitorPlugins.Browser.open({ url: data.url });
        } else {
            window.open(data.url, "_blank");
        }
    } catch (err) {
        reportClientError(err.message, err.stack, "site-preview-link");
    }
}
el("mysite-view-page-btn").addEventListener("click", openSitePreview);

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
    if (tab === "mysite") loadMySite();
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

// Same LOCALE_TAGS map the Inbox tab's timeAgo() uses (see above) - keeps date
// formatting on the Schedule tab consistent with the rest of the app.
function scheduleDateLocale() {
    return LOCALE_TAGS[currentLang] || "en-US";
}

// Unlike Inbox/Settings - already in memory the moment their tab is tapped - Schedule
// used to hit the network fresh on every single tab switch, going blank until the
// response came back. It's now cached per view+date: a repeat visit renders the last
// response instantly (like Inbox does) while a fresh copy loads silently underneath.
// `force` skips the cached render, used after a mutation (booking/cancelling/changing
// slot duration) where showing stale data would be misleading.
async function loadSchedule(date, { force = false } = {}) {
    const view = state.scheduleView;
    const targetDate = date || state.scheduleDate;
    const cacheKey = `${view}:${targetDate || "today"}`;
    const cached = !force && state.scheduleCache[cacheKey];

    if (cached) {
        el("schedule-loading").classList.add("hidden");
        el("schedule-error").classList.add("hidden");
        el("schedule-locked").classList.toggle("hidden", !cached.locked);
        el("schedule-body").classList.toggle("hidden", !!cached.locked);
        if (!cached.locked) {
            state.scheduleDate = cached.selected_date;
            state.scheduleSlotMinutes = cached.slot_minutes;
            renderSchedule(cached);
        }
    } else {
        el("schedule-locked").classList.add("hidden");
        el("schedule-body").classList.add("hidden");
        el("schedule-error").classList.add("hidden");
        el("schedule-loading").classList.remove("hidden");
    }

    const params = new URLSearchParams({ view });
    if (targetDate) params.set("date", targetDate);

    try {
        const data = await api(`/api/schedule?${params.toString()}`);
        state.scheduleCache[cacheKey] = data;
        el("schedule-loading").classList.add("hidden");
        if (data.locked) {
            el("schedule-locked").classList.remove("hidden");
            el("schedule-body").classList.add("hidden");
            return;
        }
        state.scheduleDate = data.selected_date;
        state.scheduleSlotMinutes = data.slot_minutes;
        el("schedule-locked").classList.add("hidden");
        el("schedule-body").classList.remove("hidden");
        renderSchedule(data);
    } catch (err) {
        if (err.status === 401) { logout(); return; }
        el("schedule-loading").classList.add("hidden");
        if (!cached) {
            el("schedule-error").classList.remove("hidden");
            el("schedule-error-message").textContent = err.message || t("schedule.errorTitle");
        }
        reportClientError(err.message, err.stack, "schedule-load");
    }
}

function renderSchedule(data) {
    el("schedule-duration-label").textContent = `${data.slot_minutes} min`;
    state.scheduleSlotChoices = data.slot_duration_choices;
    state.scheduleSlotMin = data.slot_duration_min;
    state.scheduleSlotMax = data.slot_duration_max;

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

function buildScheduleRow(row) {
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
        // The actual start-end range, not just the grid slot it starts in - a custom-
        // duration booking can run longer than one row (see row.covered_by below).
        if (row.appointment.start_time && row.appointment.end_time) {
            const range = document.createElement("div");
            range.className = "schedule-slot-booked-range";
            range.textContent = `${row.appointment.start_time}–${row.appointment.end_time}`;
            info.appendChild(range);
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
    } else if (row.covered_by) {
        // Swallowed by a longer appointment that started earlier - blocked, not a
        // free "Book" button, even though nothing starts at this exact time.
        const covered = document.createElement("div");
        covered.className = "schedule-slot schedule-slot-covered";
        covered.textContent = `${t("schedule.partOfAppointment")} ${row.covered_by.customer_name}`;
        contentEl.appendChild(covered);
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
    return rowEl;
}

// Past-slots toggle (Faza 13) - collapsed by default so a day that's already partly
// elapsed doesn't bury the still-relevant (current/upcoming) slots under a wall of
// "Past" placeholders. pastRows and futureRows are only both non-empty when viewing
// today - a fully past day or a fully future day has nothing to split, so this only
// ever changes what "today" looks like. Mirrors schedule.html's <details> toggle.
function buildSchedulePastToggle(pastRows) {
    const details = document.createElement("details");
    details.className = "schedule-past-toggle";

    const summary = document.createElement("summary");
    summary.className = "schedule-past-summary";
    summary.innerHTML = `<span></span>`;
    summary.children[0].textContent = t("schedule.past");
    details.appendChild(summary);

    // No "schedule-timeline" class here (unlike schedule.html's equivalent) - #schedule-
    // timeline is already the padded ancestor these rows render into on this platform,
    // so a second copy of that padding would just double the gutter.
    const pastTimeline = document.createElement("div");
    pastTimeline.className = "schedule-timeline-past";
    pastRows.forEach((row) => pastTimeline.appendChild(buildScheduleRow(row)));
    details.appendChild(pastTimeline);

    return details;
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

    const pastRows = data.slot_rows.filter((row) => row.is_past);
    const futureRows = data.slot_rows.filter((row) => !row.is_past);

    if (pastRows.length && futureRows.length) {
        timeline.appendChild(buildSchedulePastToggle(pastRows));
        futureRows.forEach((row) => timeline.appendChild(buildScheduleRow(row)));
    } else {
        data.slot_rows.forEach((row) => timeline.appendChild(buildScheduleRow(row)));
    }
}

function goToScheduleDay(date) {
    state.scheduleView = "day";
    el("schedule-view-day-btn").classList.add("active");
    el("schedule-view-week-btn").classList.remove("active");
    loadSchedule(date);
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
            // Cap each day at 5 visible entries with a "+N more" link to the day view,
            // instead of letting a busy day grow the card unevenly against its neighbors.
            day.appointments.slice(0, 5).forEach((appt) => {
                const apptEl = document.createElement("div");
                apptEl.className = "schedule-week-appt" + (appt.source === "ai" ? " schedule-week-appt-ai" : "");
                const timeEl = document.createElement("span");
                timeEl.className = "schedule-week-appt-time";
                timeEl.textContent = appt.time;
                const nameEl = document.createElement("span");
                nameEl.className = "schedule-week-appt-name";
                nameEl.textContent = appt.customer_name;
                apptEl.appendChild(timeEl);
                apptEl.appendChild(nameEl);
                dayEl.appendChild(apptEl);
            });
            if (day.appointments.length > 5) {
                const more = document.createElement("button");
                more.type = "button";
                more.className = "schedule-week-more";
                more.textContent = `+${day.appointments.length - 5} ${t("schedule.more")}`;
                more.addEventListener("click", (event) => {
                    event.stopPropagation();
                    goToScheduleDay(day.date);
                });
                dayEl.appendChild(more);
            }
        }

        dayEl.addEventListener("click", () => goToScheduleDay(day.date));
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
el("schedule-retry-btn").addEventListener("click", () => loadSchedule(null, { force: true }));

el("schedule-upgrade-btn").addEventListener("click", () => {
    subscriptionReturnTo = "schedule";
    showScreen("subscription");
});

// Shared "Custom" duration prompt (business-default picker in the header, the
// per-booking picker in the Book sheet below, and the reminder lead-time pickers in
// Settings) - opened when "Custom" is chosen from any of those option lists. Resolves to
// the chosen number of minutes, or null if cancelled, so every call site can just
// `await` it instead of juggling its own modal state. knownChoices defaults to the
// schedule duration pills (the modal's original, and still most common, caller) - pass
// a different list for a picker with its own separate preset values (e.g. the reminder
// lead-time pickers' 15/30/60/120/1440), so "already one of the presets" is judged
// against the right set rather than always the schedule one.
function promptForCustomDuration(currentMinutes, min, max, knownChoices = state.scheduleSlotChoices) {
    return new Promise((resolve) => {
        const modal = el("duration-modal");
        const input = el("duration-modal-input");
        const errorBox = el("duration-modal-error");
        const confirmBtn = el("duration-modal-confirm");
        const cancelBtn = el("duration-modal-cancel");
        const backdrop = el("duration-modal-backdrop");

        input.min = min;
        input.max = max;
        input.value = Number.isInteger(currentMinutes) && (currentMinutes < min || currentMinutes > max || knownChoices.includes(currentMinutes)) ? "" : currentMinutes;
        errorBox.classList.add("hidden");
        pinModalCenter(modal);
        modal.classList.add("open");
        setTimeout(() => input.focus(), 250);

        function settle(value) {
            modal.classList.remove("open");
            confirmBtn.removeEventListener("click", onConfirm);
            cancelBtn.removeEventListener("click", onCancel);
            backdrop.removeEventListener("click", onCancel);
            input.removeEventListener("keydown", onKeydown);
            resolve(value);
        }
        function onConfirm() {
            const minutes = parseInt(input.value, 10);
            if (!minutes || minutes < min || minutes > max) {
                errorBox.textContent = `${min}–${max}`;
                errorBox.classList.remove("hidden");
                return;
            }
            settle(minutes);
        }
        function onCancel() { settle(null); }
        function onKeydown(e) {
            if (e.key === "Enter") { e.preventDefault(); onConfirm(); }
        }
        confirmBtn.addEventListener("click", onConfirm);
        cancelBtn.addEventListener("click", onCancel);
        backdrop.addEventListener("click", onCancel);
        input.addEventListener("keydown", onKeydown);
    });
}

el("schedule-duration-btn").addEventListener("click", () => {
    const btn = el("schedule-duration-btn");
    if (optionPicker.isOpenFor(btn)) { optionPicker.close(); return; }
    const customLabel = t("schedule.custom");
    const options = state.scheduleSlotChoices.map((m) => `${m} min`).concat(customLabel);
    const currentLabel = state.scheduleSlotChoices.includes(state.scheduleSlotMinutes) ? `${state.scheduleSlotMinutes} min` : customLabel;
    optionPicker.open(btn, options, currentLabel, async (chosen) => {
        let minutes;
        if (chosen === customLabel) {
            minutes = await promptForCustomDuration(state.scheduleSlotMinutes, state.scheduleSlotMin, state.scheduleSlotMax);
            if (minutes == null) return;
        } else {
            minutes = parseInt(chosen, 10);
        }
        if (minutes === state.scheduleSlotMinutes) return;
        try {
            await api("/api/schedule/slot-duration", { method: "POST", body: { slot_duration: minutes } });
            loadSchedule(null, { force: true });
        } catch (err) {
            reportClientError(err.message, err.stack, "schedule-slot-duration");
        }
    });
});

// Caps a centered dialog's sheet to the height it measures right now, in real pixels
// rather than the CSS max-height:80(l)vh it falls back to. That CSS alone shrinks the
// sheet the instant the keyboard opens on some WebViews (even with lvh), which - since
// .book-modal-sheet's centering transform:translate(-50%,-50%) is relative to the
// sheet's OWN height - moves its rendered top edge down as a side effect, even though
// nothing about the dialog's content changed. Measuring once here, before any keyboard
// exists (these dialogs all open unfocused), removes that ambiguity.
//
// Left/top are deliberately NOT frozen the same way anymore: .lang-modal (the sheet's
// containing block) is position:fixed + inset:0, so CSS's own left:50%/top:50% already
// tracks the CURRENT viewport on every reflow, keyboard or not - freezing them in real
// pixels instead (as this used to) meant that once the keyboard shrank the viewport,
// the stale, too-low pixel value no longer matched actual center, and the whole sheet
// visibly drifted down / behind the keyboard the moment it opened.
function pinModalCenter(modal) {
    const sheet = modal.querySelector(".book-modal-sheet, .confirm-modal-sheet");
    if (!sheet) return;
    sheet.style.maxHeight = `${window.innerHeight * 0.8}px`;
}

// --- Book-a-slot bottom sheet ---

function openBookModal(time) {
    state.scheduleBookSlot = time;
    state.scheduleBookDuration = state.scheduleSlotMinutes;
    el("book-modal-time").textContent = `${new Date(`${state.scheduleDate}T00:00:00`).toLocaleDateString(scheduleDateLocale(), { weekday: "long", month: "short", day: "numeric" })} · ${time}`;
    el("book-customer-name").value = "";
    el("book-service").value = "";
    el("book-duration-label").textContent = `${state.scheduleBookDuration} min`;
    el("book-modal-error").classList.add("hidden");
    pinModalCenter(el("book-modal"));
    el("book-modal").classList.add("open");
}

function closeBookModal() {
    el("book-modal").classList.remove("open");
    state.scheduleBookSlot = null;
}

el("book-duration-btn").addEventListener("click", () => {
    const btn = el("book-duration-btn");
    if (optionPicker.isOpenFor(btn)) { optionPicker.close(); return; }
    const customLabel = t("schedule.custom");
    const options = state.scheduleSlotChoices.map((m) => `${m} min`).concat(customLabel);
    const currentLabel = state.scheduleSlotChoices.includes(state.scheduleBookDuration) ? `${state.scheduleBookDuration} min` : customLabel;
    optionPicker.open(btn, options, currentLabel, async (chosen) => {
        let minutes;
        if (chosen === customLabel) {
            minutes = await promptForCustomDuration(state.scheduleBookDuration, state.scheduleSlotMin, state.scheduleSlotMax);
            if (minutes == null) return;
        } else {
            minutes = parseInt(chosen, 10);
        }
        state.scheduleBookDuration = minutes;
        el("book-duration-label").textContent = `${minutes} min`;
    });
});

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
                duration: state.scheduleBookDuration,
            },
        });
        closeBookModal();
        if (capacitorPlugins && capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        loadSchedule(null, { force: true });
    } catch (err) {
        errorBox.textContent = err.status === 409 ? t("schedule.bookError") : (err.message || t("schedule.bookError"));
        errorBox.classList.remove("hidden");
    }
});

async function cancelAppointment(appointmentId) {
    try {
        await api(`/api/schedule/appointments/${appointmentId}/cancel`, { method: "POST" });
        if (capacitorPlugins && capacitorPlugins.Haptics) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        loadSchedule(null, { force: true });
    } catch (err) {
        reportClientError(err.message, err.stack, "schedule-cancel");
    }
}

// Reminder lead-time presets (Faza 13) - mirrors the website's REMINDER_LEAD_CHOICES
// (see app.py) - minutes before the appointment, shown as a compact hour/minute label.
// A business can also pick "Custom" from the option list below and type any other value
// via the shared duration-modal (same widget the Schedule tab's own duration pickers
// use) - REMINDER_LEAD_MAX_MINUTES caps that input, matching the server-side cap.
const REMINDER_LEAD_CHOICES = [15, 30, 60, 120, 1440];
const REMINDER_LEAD_MIN_MINUTES = 1;
const REMINDER_LEAD_MAX_MINUTES = 7 * 24 * 60;

function reminderLeadLabel(minutes) {
    if (minutes === 60) return "1h";
    if (minutes === 120) return "2h";
    if (minutes === 1440) return "24h";
    return `${minutes} min`;
}

// Loads the current push/reminder preferences into the toggles - purely local UI state
// until the user taps "Save changes" below (Faza 4: these used to POST instantly on
// every toggle flip; now nothing is sent to the server until Save is tapped, same as the
// Profile screen's own Save button). See api.py's /api/notification-preferences.
async function loadNotificationPreferences() {
    try {
        const data = await api("/api/notification-preferences");
        el("settings-new-booking-toggle").checked = !!data.new_booking_notifications_enabled;
        el("settings-reminders-owner-toggle").checked = !!data.owner_reminders_enabled;
        state.reminderOwnerMinutes = data.owner_reminder_minutes || 60;
        applyProAccessLocks();
        appSettingsSnapshot = serializeAppSettingsForm();
        updateAppSettingsSaveState();
    } catch (err) {
        if (err.status === 401) logout();
    }
}

// Same "hidden until dirty" treatment as the Profile screen's Save button - the toggles
// have no "invalid" state of their own (a checkbox flip is always a valid value), so once
// dirty this one goes straight to enabled rather than passing through a gray step.
let appSettingsSnapshot = null;

function serializeAppSettingsForm() {
    return JSON.stringify({
        newBooking: el("settings-new-booking-toggle").checked,
        remindersOwner: el("settings-reminders-owner-toggle").checked,
        ownerMinutes: state.reminderOwnerMinutes,
    });
}

const triggerAppSettingsAutosave = makeAutosaveTrigger("settings-app-save", 400);

function updateAppSettingsSaveState() {
    const dirty = appSettingsSnapshot !== null && serializeAppSettingsForm() !== appSettingsSnapshot;
    el("settings-app-save").classList.toggle("hidden", !SHOW_MANUAL_SAVE_BUTTONS || !dirty);
    el("screen-settings").classList.toggle("has-unsaved", SHOW_MANUAL_SAVE_BUTTONS && dirty);
    triggerAppSettingsAutosave(dirty);
}

["settings-new-booking-toggle", "settings-reminders-owner-toggle"].forEach((id) => {
    el(id).addEventListener("change", updateAppSettingsSaveState);
});

// Re-asks for OS notification permission the moment either of the two owner-facing push
// toggles is switched on - covers the case where permission was declined (or never
// asked) at first launch and the user only decides they want it later, instead of
// leaving them stuck with a toggle that looks on but was never actually granted.
["settings-new-booking-toggle", "settings-reminders-owner-toggle"].forEach((id) => {
    el(id).addEventListener("change", (event) => {
        if (event.target.checked) requestPushPermissionAndRegister();
    });
});

// Reminder lead-time picker - same optionPicker (centered on screen, see
// createOptionButton's comment above) + "Custom" -> duration-modal pattern as the
// Schedule tab's own duration buttons above, just with the reminder presets instead
// of slot-duration ones. There's no persistent button/label for this anymore (the
// "Send Xh before" row was removed - see settings-reminders-owner-row below): flipping
// "Remind me" on is now the only way to reach it, so it's asked once right there
// instead of leaving a second control sitting under the toggle. The toggle row itself
// is passed as the picker's anchor purely so optionPicker.isOpenFor has some element to
// track; it plays no part in the (centered, not anchored) modal's positioning.
function openReminderOwnerLeadPicker() {
    const anchor = el("settings-reminders-owner-row");
    if (optionPicker.isOpenFor(anchor)) { optionPicker.close(); return; }
    const customLabel = t("schedule.custom");
    const options = REMINDER_LEAD_CHOICES.map(reminderLeadLabel).concat(customLabel);
    const currentLabel = REMINDER_LEAD_CHOICES.includes(state.reminderOwnerMinutes)
        ? reminderLeadLabel(state.reminderOwnerMinutes) : customLabel;
    optionPicker.open(anchor, options, currentLabel, async (chosen) => {
        let minutes;
        if (chosen === customLabel) {
            minutes = await promptForCustomDuration(state.reminderOwnerMinutes, REMINDER_LEAD_MIN_MINUTES, REMINDER_LEAD_MAX_MINUTES, REMINDER_LEAD_CHOICES);
            if (minutes == null) return;
        } else {
            minutes = REMINDER_LEAD_CHOICES[options.indexOf(chosen)];
        }
        state.reminderOwnerMinutes = minutes;
        updateAppSettingsSaveState();
    }, { title: t("appSettings.remindersLeadLabel"), subtitle: t("appSettings.remindersBeforeSuffix") });
}

// Flipping "Remind me" on immediately surfaces the lead-time picker (centered on
// screen, per createOptionButton's comment above) so choosing "how long before" reads
// as one continuous action rather than a second thing to remember to go tap - it
// closes itself on a pick, same as any other optionPicker use. Skipped when the row is
// pro-locked (see applyProAccessLocks): the toggle itself gets forced back off there,
// and popping a picker for a setting that can't actually be saved would be confusing.
el("settings-reminders-owner-toggle").addEventListener("change", (event) => {
    if (event.target.checked && state.proAccess) openReminderOwnerLeadPicker();
});

// Faza 11/13: locks the No-show reminders toggle/lead-time picker and AI upsell field
// for a Starter-plan (or lapsed) business - mirrors the website's .pro-locked treatment.
// state.proAccess comes from /api/dashboard's response (see loadInbox), so this is
// called both there and after loadNotificationPreferences/loadSettingsIntoForm in case
// Settings/Profile is opened before the Inbox tab has ever loaded once.
function applyProAccessLocks() {
    const ownerToggle = el("settings-reminders-owner-toggle");
    const ownerRow = el("settings-reminders-owner-row");
    if (ownerToggle && ownerRow) {
        ownerToggle.disabled = !state.proAccess;
        ownerRow.classList.toggle("pro-locked", !state.proAccess);
        if (!state.proAccess) ownerToggle.checked = false;
    }
    const upsellPicker = el("settings-upsell-picker");
    const upsellHint = el("settings-upsell-hint");
    const upsellLockedHint = el("settings-upsell-locked-hint");
    if (upsellPicker) {
        upsellPicker.classList.toggle("pro-locked", !state.proAccess);
        upsellPicker.querySelectorAll("input, button").forEach((field) => { field.disabled = !state.proAccess; });
        if (upsellHint) upsellHint.classList.toggle("hidden", !state.proAccess);
        if (upsellLockedHint) upsellLockedHint.classList.toggle("hidden", state.proAccess);
    }
    // A plan downgrade arriving mid-edit (loadDashboard's poll, not just the initial
    // load) can force the reminders toggles off from under the user - re-check dirtiness
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
                new_booking_notifications_enabled: el("settings-new-booking-toggle").checked,
                owner_reminders_enabled: state.proAccess ? el("settings-reminders-owner-toggle").checked : undefined,
                owner_reminder_minutes: state.proAccess ? state.reminderOwnerMinutes : undefined,
            },
        });
        notifySaved(t("settings.saved"));
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
// Beyond the dot indicator, this drives a visual touch that makes the intro feel
// premium rather than a bare emoji-and-caption swipe: a live scroll-progress custom
// property (--p, -1..1) written to every slide on each scroll frame, which app.css
// uses for the copy/art's own fade-and-settle as slides pass - deliberately continuous
// and undebounced, so it's always in sync with the real scroll position and has no
// separate "trigger" that could ever fire late against content that's already visible.
// (The background glow behind the screen is a single fixed color for the whole
// carousel - see .screen-onboarding::before in app.css - not something driven here.)
// The active slide also gets an "is-active" class - once scrolling settles, not on
// every frame - which gates only the small looping decorations (typing dots, send
// icon, pulsing badge) defined in app.css, kept off on inactive slides so they don't
// run (or replay stale) off-screen. It deliberately does NOT gate anything that could
// hide already-visible core content - see the comment in app.css above the (removed)
// old entrance animation for why that combination used to look like the slide
// reloading a couple seconds after arriving.
function setupOnboarding() {
    const slides = el("onboarding-slides");
    const slideEls = Array.from(slides.querySelectorAll(".onboarding-slide"));
    const dots = document.querySelectorAll("#onboarding-dots .onboarding-dot");
    let activeIndex = -1;
    let ticking = false;
    let settleTimer = null;

    // Live parallax + dots - runs on every scroll frame while dragging, so the swipe
    // (and the dot indicator tracking it) follows the finger with no lag. The
    // background glow is a single fixed color for the whole carousel now (see
    // .screen-onboarding::before in app.css) rather than something this needs to
    // update per slide. The dots keep a short .2s transition since that's small/fast
    // enough to read as a nice touch rather than a lag - and, not being a keyframe
    // animation with a "snap to an earlier state" risk, there's no reason to hold it
    // back for a settle debounce like is-active below; doing that only made it
    // visibly trail behind the slide that's already on screen (lagging by up to the
    // full 120ms debounce plus however long the settle detection itself took to fire).
    function updateParallax() {
        ticking = false;
        const width = slides.clientWidth || 1;
        const raw = slides.scrollLeft / width;
        slideEls.forEach((slideEl, i) => {
            slideEl.style.setProperty("--p", String(raw - i));
        });
        const liveIndex = Math.min(Math.max(Math.round(raw), 0), slideEls.length - 1);
        dots.forEach((dot, i) => dot.classList.toggle("active", i === liveIndex));
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
            if (state.token) {
                enterApp();
            } else {
                showScreen("login");
            }
        });
    });
}
setupOnboarding();
setupLanguagePicker();

// --- Toggle-only settings rows (label text + on/off switch, e.g. "Publish this page",
// "New bookings", "Remind me") - tapping anywhere on the row, not just the small switch
// itself, now flips it, with a light pulse on the switch so it's clear which control
// just changed regardless of where the tap landed. Scoped to plain .settings-row
// elements (label + a single .hours-day-toggle switch, nothing else in between) -
// the working-hours day rows (hours-picker.js) reuse .hours-day-toggle too but pair it
// with time-picker fields in the same row, where a whole-row tap must NOT also flip
// the day on/off, so those are deliberately left out of this delegation. ---
document.querySelectorAll(".settings-row").forEach((row) => {
    const toggle = row.querySelector(".hours-day-toggle");
    const checkbox = toggle ? toggle.querySelector('input[type="checkbox"]') : null;
    if (!checkbox) return;
    row.addEventListener("click", (event) => {
        if (checkbox.disabled) return;
        toggle.classList.remove("toggle-row-pulse");
        void toggle.offsetWidth; // restart the animation even on back-to-back taps
        toggle.classList.add("toggle-row-pulse");
        if (toggle.contains(event.target)) return; // the native label click already flipped it
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change", { bubbles: true }));
    });
});

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
// The native APK version above never changes between OTA pushes (see build_mobile_bundle.py) -
// appending the actual active bundle hash too gives a way to confirm from the device
// itself whether a given OTA update actually landed, without needing server-side logs.
if (capacitorPlugins && capacitorPlugins.CapacitorUpdater) {
    capacitorPlugins.CapacitorUpdater.current()
        .then((current) => {
            const bundleVersion = (current && current.bundle && current.bundle.version) || "builtin";
            el("settings-app-version").textContent += ` · ${bundleVersion.slice(0, 12)}`;
        })
        .catch(() => {});
}

// --- My Site tab (Faza 15) - website overview + the business-info tools (description,
// voice) that only ever lived once, during the web onboarding wizard, with no mobile
// equivalent at all. See api.py's /api/page, /api/generate-page, /api/voice-transcribe -
// Bearer-authed mirrors of the website's own session-authed routes, same underlying
// business_pages row/page_generator.py either surface calls into. ---

// Same 5 classic presets as themes.py's THEMES dict (label only - full color data isn't
// needed client-side for a plain <select>).
const CLASSIC_THEMES = [
    { id: "minimal", label: "Minimal", primary: "#141414", surface: "#ffffff" },
    { id: "warm", label: "Warm & elegant", primary: "#d69410", surface: "#ffffff" },
    { id: "bold", label: "Bold & contrast", primary: "#ff4d33", surface: "#171717" },
    { id: "nature", label: "Nature & organic", primary: "#3f7d20", surface: "#ffffff" },
    { id: "luxury", label: "Luxury", primary: "#c9a227", surface: "#161616" },
];

let mysiteSelectedCustomTheme = null; // a previously-saved custom theme object, if one is currently selected

// Same "hidden until dirty" floating Save button as Profile/Settings (see
// updateSettingsSaveValidity above) - only the website's own fields count, not the
// separate "Draft this with AI" description/voice controls below them, which don't
// persist anything themselves.
let mysiteFormSnapshot = null;

function serializeMysiteForm() {
    return JSON.stringify({
        slug: el("mysite-slug").value,
        tagline: el("mysite-tagline-input").value,
        about: el("mysite-about-input").value,
        published: el("mysite-published").checked,
        theme: el("mysite-theme-select").value,
        customTheme: mysiteSelectedCustomTheme ? JSON.stringify(mysiteSelectedCustomTheme) : "",
        pendingCustomDomain: mysiteChosenDomain || "",
    });
}

const triggerMysiteAutosave = makeAutosaveTrigger("mysite-save-btn", 1400);

// Mirrors pages.py's is_valid_slug/_SLUG_RE (minus the reserved-word list, which only
// matters for a deliberate save attempt, not this "don't autosave yet" gate) - without
// this, picking a Design or typing a tagline before ever touching the Web address field
// (an empty slug is the state every new business starts this form in) marked the form
// dirty on its own and fired the silent autosave below, which the server then rejects -
// surfacing a "choose a web address" error the user never asked to save.
const MYSITE_SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$/;
function isMysiteFormValid() {
    return MYSITE_SLUG_RE.test(el("mysite-slug").value.trim().toLowerCase());
}

function updateMysiteSaveValidity() {
    const button = el("mysite-save-btn");
    const dirty = mysiteFormSnapshot !== null && serializeMysiteForm() !== mysiteFormSnapshot;
    const valid = isMysiteFormValid();
    button.classList.toggle("hidden", !SHOW_MANUAL_SAVE_BUTTONS || !dirty);
    button.disabled = !valid;
    el("screen-mysite").classList.toggle("has-unsaved", SHOW_MANUAL_SAVE_BUTTONS && dirty);
    // isMysiteFormValid() only ever fails on a missing/invalid slug (see its comment
    // above) - without this, picking a Design (or editing tagline/about) with no web
    // address set yet silently never autosaves: the picker updates the row's own label
    // right away so it looks like the change took, but nothing persists, and the very
    // next loadMySite() (switching tabs and back, reopening the app...) quietly puts the
    // old saved value back with no explanation of why. Surface the reason right here
    // instead of leaving that "chose a design, it reverted a few seconds later" mystery.
    el("mysite-slug-required-hint").classList.toggle("hidden", !(dirty && !valid));
    triggerMysiteAutosave(dirty && valid);
}

// Delegated for the same reason as Profile's equivalent listener - also catches the
// theme-select dropdown, which dispatches its own bubbling "change".
["input", "change", "click"].forEach((evt) => {
    el("screen-mysite").addEventListener(evt, updateMysiteSaveValidity);
});

function mysitePopulateThemeSelect(currentThemeId) {
    const select = el("mysite-theme-select");
    select.innerHTML = "";
    CLASSIC_THEMES.forEach((theme) => {
        const option = document.createElement("option");
        option.value = theme.id;
        option.textContent = theme.label;
        select.appendChild(option);
    });
    if (currentThemeId === "custom") {
        const option = document.createElement("option");
        option.value = "custom";
        option.textContent = t("mysite.website.aiDesign") || "AI-suggested design";
        select.appendChild(option);
        select.value = "custom";
    } else {
        select.value = CLASSIC_THEMES.some((t2) => t2.id === currentThemeId) ? currentThemeId : "warm";
    }
    // The real <select> above is kept around (hidden) purely as the existing value/
    // change-event API the rest of this file already reads (serializeMysiteForm, the
    // save button, the "change" listener below) - mysite-theme-select-row is what's
    // actually visible/tappable, so its own label needs to mirror whichever option just
    // became selected.
    el("mysite-theme-select-value").textContent = select.options[select.selectedIndex].textContent;
}

async function loadMySite() {
    el("mysite-error").classList.add("hidden");
    try {
        const data = await api("/api/page");
        const page = data.page;
        state.mysiteHasPage = !!page;

        el("mysite-view-page-btn").classList.toggle("hidden", !page);
        // Cached (along with hasAppointments below) so next launch can show this section
        // in its right shape right away instead of waiting on this same fetch every time -
        // same reasoning as pageTheme/donationLink above it in the boot sequence. Without
        // this, the appointments card pops in a moment later, shoving the whole rest of
        // the screen down after it already looks fully loaded.
        storageSet("mysiteHasPage", page ? "1" : "").catch(() => {});
        if (page) {
            el("mysite-slug").value = page.slug || "";
            mysiteSyncDomainCheckBtnVisibility();
            mysiteChosenDomain = page.pending_custom_domain || null;
            el("mysite-tagline-input").value = page.tagline || "";
            el("mysite-about-input").value = page.about || "";
            el("mysite-published").checked = !!page.published;
            // Cached the same way as mysiteHasPage/pageTheme above - so next launch's boot
            // sequence can fill these fields in immediately instead of leaving Web
            // address/Headline/About empty until this same fetch resolves again.
            storageSet("mysiteSlug", page.slug || "").catch(() => {});
            storageSet("mysiteTagline", page.tagline || "").catch(() => {});
            storageSet("mysiteAbout", page.about || "").catch(() => {});
            storageSet("mysiteChosenDomain", page.pending_custom_domain || "").catch(() => {});
            mysitePopulateThemeSelect(page.theme);
            storageSet("pageTheme", page.theme || "warm").catch(() => {});
            mysiteSelectedCustomTheme = page.theme === "custom" && page.custom_theme_json ? JSON.parse(page.custom_theme_json) : null;
        } else {
            // No page yet - clear any stale cache from a page that existed on a previous
            // launch (e.g. deleted since), so a future boot doesn't flash old Web
            // address/Headline/About text before this same fetch corrects it again.
            storageSet("mysiteSlug", "").catch(() => {});
            storageSet("mysiteTagline", "").catch(() => {});
            storageSet("mysiteAbout", "").catch(() => {});
            storageSet("mysiteChosenDomain", "").catch(() => {});
            // Seed the Design row from whatever was picked before a web address existed
            // (see api_save_page's blank-slug branch/set_preferred_theme), instead of
            // always defaulting to "warm", so that preference actually shows up as
            // pre-selected across app restarts/devices instead of only ever living in
            // this session's in-memory <select> value.
            const preferredTheme = data.preferred_theme || "warm";
            mysitePopulateThemeSelect(preferredTheme);
            storageSet("pageTheme", preferredTheme).catch(() => {});
            mysiteSelectedCustomTheme = preferredTheme === "custom" && data.preferred_custom_theme_json
                ? JSON.parse(data.preferred_custom_theme_json) : null;
        }
        mysiteFormSnapshot = serializeMysiteForm();
        updateMysiteSaveValidity();
        mysiteUpdateAllFieldFades();

        const appointments = data.upcoming_appointments || [];
        const appointmentsCard = el("mysite-appointments-card");
        appointmentsCard.classList.toggle("hidden", appointments.length === 0);
        storageSet("mysiteHasAppointments", appointments.length > 0 ? "1" : "").catch(() => {});
        const list = el("mysite-appointments-list");
        list.innerHTML = "";
        appointments.forEach((appt) => {
            const row = document.createElement("div");
            row.className = "mysite-appointment-row";
            const label = document.createElement("span");
            label.textContent = appt.customer_name + (appt.service ? " · " + appt.service : "");
            const when = document.createElement("span");
            when.className = "field-hint";
            when.style.margin = "0";
            when.textContent = new Date(appt.start_time).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
            row.appendChild(label);
            row.appendChild(when);
            list.appendChild(row);
        });
    } catch (err) {
        if (err.status === 401) { logout(); return; }
        // A network error is already surfaced by the offline banner - don't duplicate it here.
        if (err.isNetworkError) return;
        el("mysite-error").textContent = err.message || t("network.error");
        el("mysite-error").classList.remove("hidden");
    }
}

// Persists a Design pick made before this business has a saved page (no web address
// set yet) - see api_save_page's blank-slug branch. Deliberately its own standalone
// POST, NOT routed through updateMysiteSaveValidity/triggerMysiteAutosave: that general
// path is slug-gated (isMysiteFormValid) precisely so tagline/about edits never
// autosave with no address set (the server would silently drop them, and the next
// loadMySite() would revert those inputs back to empty) - reusing that path here would
// only be safe for theme specifically, but the two are driven by the same delegated
// listener and can't be cleanly split, so this bypasses it entirely instead of loosening
// the gate for everything.
async function saveMysitePreferredTheme(themeValue) {
    const body = { slug: "", theme: themeValue };
    if (themeValue === "custom" && mysiteSelectedCustomTheme) {
        body.custom_theme_json = JSON.stringify(mysiteSelectedCustomTheme);
    }
    // Patched into the snapshot synchronously, before the network call - updateMysite
    // SaveValidity's dirty check (serializeMysiteForm() vs mysiteFormSnapshot) runs
    // synchronously right after this "change" event finishes dispatching, so patching
    // only after an await would let mysite-slug-required-hint flash on for one frame
    // (the theme now differs from the stale snapshot until the save round-trips).
    if (mysiteFormSnapshot !== null) {
        const snapshot = JSON.parse(mysiteFormSnapshot);
        snapshot.theme = themeValue;
        snapshot.customTheme = body.custom_theme_json || "";
        mysiteFormSnapshot = JSON.stringify(snapshot);
    }
    try {
        await api("/api/page", { method: "POST", body });
        storageSet("pageTheme", themeValue).catch(() => {});
    } catch (err) {
        el("mysite-error").textContent = err.message || t("network.error");
        el("mysite-error").classList.remove("hidden");
    } finally {
        updateMysiteSaveValidity();
    }
}

el("mysite-theme-select").addEventListener("change", (event) => {
    if (event.target.value !== "custom") {
        mysiteSelectedCustomTheme = null;
    }
    if (!state.mysiteHasPage) {
        saveMysitePreferredTheme(event.target.value);
    }
});

// Design picker (My Site tab) - the "Design" row opens #design-modal, a full-width
// sheet sliding up from the screen's bottom edge (same look as the inbox row-actions
// sheet), instead of the small anchored dropdown createOptionButton()/optionPicker use
// elsewhere - there can be more entries here (5 classics + the AI-suggested design, once
// one's been picked) than reads well as a compact popover. Picking an option just drives
// the existing hidden <select> (value + a dispatched "change") so every other place that
// already reads/listens to it - serializeMysiteForm, the save button, the swatch-updating
// listener above - keeps working unchanged.
function openDesignPicker() {
    const select = el("mysite-theme-select");
    const list = el("design-modal-list");
    list.innerHTML = "";
    Array.from(select.options).forEach((option) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "row-action-btn" + (option.value === select.value ? " selected" : "");
        btn.textContent = option.textContent;
        btn.addEventListener("click", () => {
            select.value = option.value;
            el("mysite-theme-select-value").textContent = option.textContent;
            select.dispatchEvent(new Event("change", { bubbles: true }));
            closeDesignPicker();
        });
        list.appendChild(btn);
    });
    el("design-modal").classList.add("open");
}
function closeDesignPicker() {
    el("design-modal").classList.remove("open");
}
el("mysite-theme-select-row").addEventListener("click", openDesignPicker);
el("design-modal-backdrop").addEventListener("click", closeDesignPicker);
el("design-modal-cancel").addEventListener("click", closeDesignPicker);

el("mysite-save-btn").addEventListener("click", async () => {
    const button = el("mysite-save-btn");
    const errorBox = el("mysite-error");
    errorBox.classList.add("hidden");
    const themeValue = el("mysite-theme-select").value;
    const body = {
        slug: el("mysite-slug").value.trim(),
        tagline: el("mysite-tagline-input").value.trim(),
        about: el("mysite-about-input").value.trim(),
        published: el("mysite-published").checked,
        theme: themeValue,
        pending_custom_domain: mysiteChosenDomain || null,
    };
    if (themeValue === "custom" && mysiteSelectedCustomTheme) {
        body.custom_theme_json = JSON.stringify(mysiteSelectedCustomTheme);
    }
    button.disabled = true;
    button.textContent = t("settings.saveLoading");
    // Same fix as saveProfileForm's own scrollTopBeforeSave above: loadMySite() below
    // clears/rebuilds mysite-appointments-list and the theme <select> (innerHTML = ""
    // then re-appended), which can momentarily shrink .inbox-list-wrap's content height
    // enough for the WebView to clamp its scroll position back up - reads as autosave
    // yanking the screen while you're mid-edit, since this fires on every debounced
    // keystroke/toggle, not just a deliberate tap. Restoring the pre-save scrollTop
    // afterward keeps you exactly where you were.
    const scrollContainer = el("screen-mysite").querySelector(".inbox-list-wrap");
    const scrollTopBeforeSave = scrollContainer ? scrollContainer.scrollTop : 0;
    try {
        const result = await api("/api/page", { method: "POST", body });
        notifySaved(t("settings.saved"));
        await loadMySite();
        if (scrollContainer) scrollContainer.scrollTop = scrollTopBeforeSave;
        // The domain is charged directly to the business (its own $15/yr subscription,
        // separate from Starter/Pro - see templates/domain_checkout.html), so a picked
        // domain isn't actually bought on Publish - the server just flags that a
        // checkout is still needed, same "hand off to a web page" pattern as
        // openChoosePlan() below.
        if (result && result.domain_checkout_required) await openDomainCheckout(result.domain);
    } catch (err) {
        errorBox.textContent = err.message || t("network.error");
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = t("settings.save");
        updateMysiteSaveValidity();
    }
});

// The real, independently-registrable domain (mihajlo-prodaja.com) the owner picked via
// a "Choose" button below - null until they choose one. Kept separate from mysite-slug
// (that field is strictly the platform's own /p/<slug> path, see MYSITE_SLUG_RE, and
// can't hold a dotted domain name), and separate from api.py's naming: this is the
// *pending* pick, purchased automatically once the owner publishes (not yet owned).
let mysiteChosenDomain = null;

// No visible confirmation beyond the Check button's own checkmark (see
// mysiteFinishDomainCheck) - the owner's real "is this really the domain I want, and am I
// sure I want to pay for it" confirmation is the dedicated checkout page
// (templates/domain_checkout.html, opened from Publish) that names the domain and price
// before anything is charged, so an inline chip here would just be a second, redundant
// readout of the same pick.
function mysiteChooseDomain(domain) {
    mysiteChosenDomain = domain;
}

// Swaps the loading dots for a small green checkmark - the "yes, that's yours" signal in
// place of a badge the owner has to read - then, once it's had a moment to register,
// reverts the button back to "Check" (always - re-checking an already-chosen domain
// still reads as "Check", not "Change"). Shared by both call sites in mysiteCheckDomains
// below: the single-match auto-pick and the multi-match "Choose" click, so the
// confirmation reads identically either way.
function mysiteFinishDomainCheck(btn) {
    btn.disabled = true;
    // Drops the button's own pill border/background while the checkmark shows - see
    // .field-inline-btn-success in app.css - so the checkmark reads as a plain icon next
    // to the field instead of a shape boxed inside another shape.
    btn.classList.add("field-inline-btn-success");
    btn.innerHTML = '<svg class="field-inline-btn-check" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.3" pathLength="1"/><path d="M4.7 8.4l2.1 2.1 4.4-4.9" pathLength="1"/></svg>';
    setTimeout(() => {
        btn.classList.remove("field-inline-btn-success");
        btn.textContent = t("mysite.website.checkDomainsShort") || "Check";
        btn.disabled = false;
    }, 1500);
}

// "Check real domain availability" - queries domain_lookup.py's RDAP-based check (see
// /api/check-domains) for baseName across .com/.net/.rs, in that fixed order (see
// check_domains' own DEFAULT_TLDS) - domains[0] is always the ".com" the owner actually
// searched for. Entirely separate from the mysite-slug field's own meaning (this
// platform's own /p/<slug> page address) - this only tells the owner whether a real,
// independently-registrable domain of the same name is free. That exact ".com" is what
// decides the whole outcome: free, and it's auto-picked with nothing else to look at;
// taken (or unconfirmable), and it's shown right there tagged "Unavailable" - not silently
// dropped - with whatever other TLDs actually came back free listed as real alternatives
// underneath. A plain function declaration, not just a click listener, so it can be
// called from more than one place once a draft suggests (or keeps) a web address, same
// "AI does the legwork" spirit as the drafted tagline/about themselves.
async function mysiteCheckDomains(baseName) {
    if (!baseName) return;
    const btn = el("mysite-domain-check-btn");
    const resultsList = el("mysite-domain-check-results");
    const errorBox = el("mysite-domain-check-error");
    if (!btn || !resultsList || !errorBox) return;
    errorBox.classList.add("hidden");
    resultsList.innerHTML = "";
    btn.disabled = true;
    btn.classList.remove("field-inline-btn-success");
    const idleLabel = t(btn.getAttribute("data-i18n")) || btn.textContent;
    btn.innerHTML = '<span class="field-inline-btn-dots"><span></span><span></span><span></span></span>';
    try {
        const data = await api("/api/check-domains", { method: "POST", body: { name: baseName } });
        const domains = data.domains || [];
        const primary = domains[0];
        if (!primary) {
            btn.disabled = false;
            btn.textContent = idleLabel;
            return;
        }

        if (primary.status === "available") {
            mysiteChooseDomain(primary.domain);
            mysiteFinishDomainCheck(btn);
            return;
        }

        // The exact address searched for isn't free (taken, or couldn't be confirmed) -
        // a disabled tag, not a Choose button, so it can't be picked but still tells the
        // owner plainly that's the outcome for the name they actually typed.
        const primaryRow = document.createElement("div");
        primaryRow.className = "mysite-domain-row";
        const primaryName = document.createElement("span");
        primaryName.className = "mysite-domain-name";
        primaryName.textContent = primary.domain;
        primaryRow.appendChild(primaryName);
        const unavailableTag = document.createElement("button");
        unavailableTag.type = "button";
        unavailableTag.className = "mysite-domain-unavailable-tag";
        unavailableTag.textContent = t("mysite.website.domainUnavailable") || "Unavailable";
        unavailableTag.disabled = true;
        primaryRow.appendChild(unavailableTag);
        resultsList.appendChild(primaryRow);

        // Whatever other TLDs domain_lookup.py checked and came back free - real
        // alternatives, not the taken one dressed up differently.
        domains.slice(1).filter((entry) => entry.status === "available").forEach((entry) => {
            const row = document.createElement("div");
            row.className = "mysite-domain-row";

            const name = document.createElement("span");
            name.className = "mysite-domain-name";
            name.textContent = entry.domain;
            row.appendChild(name);

            const chooseBtn = document.createElement("button");
            chooseBtn.type = "button";
            chooseBtn.className = "mysite-domain-choose-btn";
            chooseBtn.textContent = t("mysite.website.domainChoose") || "Choose";
            chooseBtn.addEventListener("click", () => {
                resultsList.innerHTML = "";
                mysiteChooseDomain(entry.domain);
                mysiteFinishDomainCheck(btn);
            });
            row.appendChild(chooseBtn);

            resultsList.appendChild(row);
        });
        btn.disabled = false;
        btn.textContent = idleLabel;
    } catch (err) {
        btn.disabled = false;
        btn.textContent = idleLabel;
        errorBox.textContent = (err && err.message) || t("mysite.website.checkDomainsError") || t("network.error");
        errorBox.classList.remove("hidden");
    }
}

// The Check button's actual current job (mysiteCheckDomains above - real, purchasable
// internet domains via Dynadot/RDAP - is unused for now, see mobile/index.html's own
// comment on the now-hidden purchase UI): whether `slug` is free as THIS platform's own
// /p/<slug> address. A plain database lookup on our own side (see /api/check-slug ->
// pages.slug_is_available), nothing external, nothing to ever pay for - reuses the same
// checkmark/pick-list UI language as mysiteCheckDomains purely for consistency, not
// because the two features are related. Picking a suggestion here writes straight into
// mysite-slug itself (there's no separate "chosen" value to track, unlike a purchasable
// domain) - the owner still has to hit Save/Publish same as if they'd typed it themselves.
async function mysiteCheckSlug(slug) {
    if (!slug) return;
    const btn = el("mysite-domain-check-btn");
    const resultsList = el("mysite-domain-check-results");
    const errorBox = el("mysite-domain-check-error");
    if (!btn || !resultsList || !errorBox) return;
    errorBox.classList.add("hidden");
    resultsList.innerHTML = "";
    btn.disabled = true;
    btn.classList.remove("field-inline-btn-success");
    const idleLabel = t(btn.getAttribute("data-i18n")) || btn.textContent;
    btn.innerHTML = '<span class="field-inline-btn-dots"><span></span><span></span><span></span></span>';
    try {
        const data = await api("/api/check-slug", { method: "POST", body: { slug } });
        if (data.available) {
            mysiteFinishDomainCheck(btn);
            return;
        }
        (data.suggestions || []).forEach((candidate) => {
            const row = document.createElement("div");
            row.className = "mysite-domain-row";

            const name = document.createElement("span");
            name.className = "mysite-domain-name";
            name.textContent = candidate;
            row.appendChild(name);

            const chooseBtn = document.createElement("button");
            chooseBtn.type = "button";
            chooseBtn.className = "mysite-domain-choose-btn";
            chooseBtn.textContent = t("mysite.website.domainChoose") || "Choose";
            chooseBtn.addEventListener("click", () => {
                resultsList.innerHTML = "";
                el("mysite-slug").value = candidate;
                mysiteUpdateFieldFade(el("mysite-slug"));
                mysiteFinishDomainCheck(btn);
            });
            row.appendChild(chooseBtn);

            resultsList.appendChild(row);
        });
        btn.disabled = false;
        btn.textContent = idleLabel;
    } catch (err) {
        btn.disabled = false;
        btn.textContent = idleLabel;
        errorBox.textContent = (err && err.message) || t("mysite.website.checkDomainsError") || t("network.error");
        errorBox.classList.remove("hidden");
    }
}
el("mysite-domain-check-btn").addEventListener("click", () => mysiteCheckSlug(el("mysite-slug").value.trim()));

// Hidden until the field actually has something to check - same reasoning as the
// password-toggle eye icon, so it isn't sitting there inert over an empty field. Not
// just an "input" listener: mysite-slug's value is also set programmatically (loadMySite
// loading a saved page), which never fires "input" - callers of that must call this
// themselves too.
function mysiteSyncDomainCheckBtnVisibility() {
    el("mysite-domain-check-btn").classList.toggle("has-value", el("mysite-slug").value.trim().length > 0);
}
el("mysite-slug").addEventListener("input", mysiteSyncDomainCheckBtnVisibility);
mysiteSyncDomainCheckBtnVisibility();

// Fade-to-transparent for a clipped Web address/Headline/About value - same
// "scrollWidth > clientWidth after layout" measurement the Inbox list uses for its own
// sender/preview truncation (see the .truncated class in app.css and its handling in
// renderInboxList above), just applied to these three fields instead of inbox rows.
function mysiteUpdateFieldFade(field) {
    if (!field) return;
    field.classList.toggle("truncated", field.scrollWidth > field.clientWidth);
}
function mysiteUpdateAllFieldFades() {
    mysiteUpdateFieldFade(el("mysite-slug"));
    mysiteUpdateFieldFade(el("mysite-tagline-input"));
    mysiteUpdateFieldFade(el("mysite-about-input"));
}
// Rotating the device (or the card otherwise resizing) changes how much of a long value
// fits, so a value that was clipped before might not be anymore, and vice versa.
window.addEventListener("resize", mysiteUpdateAllFieldFades);

// Web address/Headline/About editor (My Site tab) - the three fields above are readonly
// (index.html) purely so a tap can open this centered modal instead of focusing them in
// place; #mysite-field-modal (index.html) holds one shared input + textarea pair, shown
// one at a time depending on which field was tapped. Opened without ever calling
// .focus() on either control, so the modal appears with no keyboard - it only shows up
// once the user taps inside the modal's own input/textarea, same as tapping into any
// ordinary field would.
const MYSITE_FIELD_MODAL_FIELDS = {
    "mysite-slug": { control: "input", titleKey: "mysite.website.slug" },
    "mysite-tagline-input": { control: "input", titleKey: "mysite.website.taglineLabel" },
    "mysite-about-input": { control: "textarea", titleKey: "mysite.website.aboutLabel" },
};
let mysiteFieldModalTargetId = null;

function mysiteFieldModalActiveControl() {
    const config = MYSITE_FIELD_MODAL_FIELDS[mysiteFieldModalTargetId];
    return config && config.control === "textarea" ? el("mysite-field-modal-textarea") : el("mysite-field-modal-input");
}

// Grows the textarea to exactly fit its current value (capped by app.css's own
// max-height, past which it scrolls instead) - without this, a short About value sits
// pinned at the top of a tall, mostly-empty box; sized to its content instead, it's
// short enough for .mysite-field-modal-body's own centering (app.css) to actually read
// as "the text is in the middle of the field" rather than doing nothing because the box
// already filled all the available space.
function mysiteFieldModalAutoGrowTextarea() {
    const textarea = el("mysite-field-modal-textarea");
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
}

function openMysiteFieldModal(fieldId) {
    const config = MYSITE_FIELD_MODAL_FIELDS[fieldId];
    const source = el(fieldId);
    if (!config || !source) return;
    mysiteFieldModalTargetId = fieldId;
    el("mysite-field-modal-title").textContent = t(config.titleKey);
    const input = el("mysite-field-modal-input");
    const textarea = el("mysite-field-modal-textarea");
    const showTextarea = config.control === "textarea";
    input.classList.toggle("hidden", showTextarea);
    textarea.classList.toggle("hidden", !showTextarea);
    const active = showTextarea ? textarea : input;
    // Mirrors the real field's own maxlength (200 for the tagline, 2000 for about, none
    // for the slug) so the modal can't be used to type past what the real field - and the
    // server behind it - would actually accept.
    if (source.maxLength >= 0) active.maxLength = source.maxLength; else active.removeAttribute("maxlength");
    active.value = source.value;
    if (showTextarea) mysiteFieldModalAutoGrowTextarea();
    source.blur();
    pinModalCenter(el("mysite-field-modal"));
    el("mysite-field-modal").classList.add("open");
}

function closeMysiteFieldModal() {
    el("mysite-field-modal").classList.remove("open");
    el("mysite-field-modal-input").blur();
    el("mysite-field-modal-textarea").blur();
    mysiteFieldModalTargetId = null;
}

// Mirrors every keystroke straight back onto the real (hidden) field, dispatching the
// same "input" event it would fire itself if it were focused directly - the existing
// autosave/validation (updateMysiteSaveValidity's delegated listener on #screen-mysite,
// mysiteSyncDomainCheckBtnVisibility on #mysite-slug) already listen for exactly that,
// so this modal needed no changes of its own to either.
function mysiteFieldModalSync() {
    if (!mysiteFieldModalTargetId) return;
    const source = el(mysiteFieldModalTargetId);
    const active = mysiteFieldModalActiveControl();
    source.value = active.value;
    source.dispatchEvent(new Event("input", { bubbles: true }));
    mysiteUpdateFieldFade(source);
    if (active === el("mysite-field-modal-textarea")) mysiteFieldModalAutoGrowTextarea();
}
el("mysite-field-modal-input").addEventListener("input", mysiteFieldModalSync);
el("mysite-field-modal-textarea").addEventListener("input", mysiteFieldModalSync);
el("mysite-field-modal-backdrop").addEventListener("click", closeMysiteFieldModal);

Object.keys(MYSITE_FIELD_MODAL_FIELDS).forEach((fieldId) => {
    el(fieldId).addEventListener("click", () => openMysiteFieldModal(fieldId));
});

// "Draft this with AI" chat (Faza 16) - see screen-website-chat in index.html and
// website_assistant.py's module docstring for the persona/scope this is restricted to.
// Tapping anywhere on the My Site tab's compose card (id="mysite-draft-card", above)
// opens this screen instead of that card doing anything inline now - the capture-phase
// listener below runs before that (now purely visual/static) card can do anything else
// with the tap. The compose bar here (attach + mic + send) is the exact functionality
// that card used to have inline before Faza 16 - moved here wholesale, not duplicated,
// since mysite-draft-card no longer has any of those controls left to be reachable
// through (see index.html) - this is the only place it lives/works now.
(function () {
    const draftCard = el("mysite-draft-card");
    const backBtn = el("website-chat-back");
    const messagesEl = el("website-chat-messages");
    const input = el("website-chat-input");
    const sendBtn = el("website-chat-send-btn");
    if (!draftCard || !backBtn || !messagesEl || !input || !sendBtn) return;

    const attachBtn = el("website-chat-attach-btn");
    const recordBtn = el("website-chat-voice-btn");

    let history = [];
    let greeted = false;
    let greetingEl = null;

    // --- Messages ---

    // Same textContent-only <p>/<ul> rendering the web dashboard's own website_chat.html
    // uses (and, before that, the support-chat widget) - one fact/line, "- " lists become
    // real bullets, kept as its own copy since mobile and the web dashboard don't share a
    // script file.
    function renderBotContent(container, text) {
        const lines = String(text).split(/\n+/).map((line) => line.trim()).filter(Boolean);
        let i = 0;
        while (i < lines.length) {
            if (/^[-•]\s+/.test(lines[i])) {
                const ul = document.createElement("ul");
                ul.className = "website-chat-msg-list";
                while (i < lines.length && /^[-•]\s+/.test(lines[i])) {
                    const li = document.createElement("li");
                    li.textContent = lines[i].replace(/^[-•]\s+/, "");
                    ul.appendChild(li);
                    i++;
                }
                container.appendChild(ul);
            } else {
                const p = document.createElement("p");
                p.textContent = lines[i];
                container.appendChild(p);
                i++;
            }
        }
    }

    function addMessage(role, text) {
        const wrap = document.createElement("div");
        wrap.className = "website-chat-msg " + (role === "user" ? "website-chat-msg-user" : "website-chat-msg-bot");
        if (role === "user") wrap.textContent = text;
        else renderBotContent(wrap, text);
        messagesEl.appendChild(wrap);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return wrap;
    }

    // Rendered the first time the card is actually tapped, not at app boot - by then the
    // app's own i18n has long finished loading (unlike the web dashboard's version of
    // this same greeting, which has to wait out a deferred script - see that page's own
    // comment), so t() is always safe to call here.
    function ensureGreeting() {
        if (greeted) return;
        greeted = true;
        greetingEl = addMessage("bot", t("websiteChat.greeting") || "Hi! Tell me about your business and let's figure out your website together.");
    }

    // Re-renders the greeting bubble when Settings changes the language, same rule
    // website_chat.html's own "site-language-changed" listener follows: once the
    // conversation has real messages in it, later switches leave them (and this
    // bubble) in whatever language they were sent in.
    document.addEventListener("site-language-changed", () => {
        if (!greetingEl || history.length > 0) return;
        greetingEl.textContent = "";
        renderBotContent(greetingEl, t("websiteChat.greeting"));
    });

    // --- Compose bar: auto-grow + send-button fill state, same behavior the old
    // mysite-description field had (see mysiteAutoGrowDescription/mysiteUpdateComposeButtons,
    // before Faza 16 - removed alongside that field itself, this is their replacement). ---

    function autoGrowInput() {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    }
    function updateComposeButtons() {
        const hasText = input.value.trim().length > 0;
        sendBtn.classList.toggle("has-text", hasText);
        if (recordBtn && !recordBtn.classList.contains("recording")) {
            recordBtn.classList.toggle("mic-typing-hidden", hasText);
        }
    }
    input.addEventListener("input", autoGrowInput);
    input.addEventListener("input", updateComposeButtons);

    // --- Attachments (photos/video/documents) - staged client-side, uploaded on Send.
    // Same /api/media//api/documents round trip as before Faza 16 (see removeAttachment/
    // persistStagedEntry below), just targeting this screen's own elements. ---

    let flushStagedAttachments = () => Promise.resolve();
    let cancelRecordingIfActive = () => false;

    (function setupAttachments() {
        const cameraInput = el("website-chat-attach-input-camera");
        const photosInput = el("website-chat-attach-input-photos");
        const filesInput = el("website-chat-attach-input-files");
        const list = el("website-chat-attachment-list");
        if (!attachBtn || !cameraInput || !photosInput || !filesInput || !list) return;

        // Camera / Photos / Files popover - see #attach-options-modal in index.html, same
        // fixed-overlay pattern as openRowActions(), but positioned as a small card right
        // above the "+" (same anchoring technique as i18n.js's openPicker()) instead of a
        // full-width sheet. Picking an option closes it and clicks that option's own
        // hidden file input.
        const sheet = el("attach-options-modal");
        const sheetCard = el("attach-options-sheet");
        function openAttachSheet() {
            if (!sheet || !sheetCard) return;
            const rect = attachBtn.getBoundingClientRect();
            const gap = 10;
            sheetCard.style.left = `${rect.left}px`;
            sheetCard.style.bottom = `${window.innerHeight - rect.top + gap}px`;
            sheet.classList.add("open");
        }
        function closeAttachSheet() {
            if (sheet) sheet.classList.remove("open");
        }
        if (sheet) {
            el("attach-options-backdrop").addEventListener("click", closeAttachSheet);
            el("attach-option-camera").addEventListener("click", () => { closeAttachSheet(); cameraInput.click(); });
            el("attach-option-photos").addEventListener("click", () => { closeAttachSheet(); photosInput.click(); });
            el("attach-option-files").addEventListener("click", () => { closeAttachSheet(); filesInput.click(); });
        }

        function kindFor(file) {
            const type = (file.type || "").toLowerCase();
            if (type === "application/pdf") return "document";
            if (type.indexOf("wordprocessingml") !== -1 || /\.docx?$/i.test(file.name || "")) return "document";
            if (type.indexOf("video/") === 0) return "video";
            if (type.indexOf("image/") === 0) return "image";
            return null;
        }
        function iconFor(kind) {
            return kind === "video" ? "▶" : kind === "image" ? "🖼" : "📄";
        }
        function labelFor(kind) {
            return kind === "video" ? "Video" : kind === "image" ? "Photo" : "File";
        }

        function fillUploadedChip(chip, kind, item) {
            if (kind === "image") {
                const img = document.createElement("img");
                img.src = item.url;
                img.alt = "";
                img.style.cursor = "zoom-in";
                img.addEventListener("click", (event) => {
                    event.stopPropagation();
                    openImageLightbox(item.url);
                });
                chip.appendChild(img);
            } else {
                const icon = document.createElement("span");
                icon.className = "chip-icon";
                icon.textContent = iconFor(kind);
                chip.appendChild(icon);
            }

            const name = document.createElement("span");
            name.className = "chip-name";
            name.textContent = item.filename || labelFor(kind);
            chip.appendChild(name);

            const removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "chip-remove";
            removeBtn.setAttribute("aria-label", "Remove");
            removeBtn.textContent = "×";
            removeBtn.addEventListener("click", () => removeAttachment(kind, item.id, chip));
            chip.appendChild(removeBtn);
        }

        function removeAttachment(kind, id, chip) {
            chip.classList.add("uploading");
            const path = kind === "document" ? "/api/documents/" + id : "/api/media/" + id;
            fetch(API_BASE + path, {
                method: "DELETE",
                headers: state.token ? { Authorization: "Bearer " + state.token } : {},
            }).then((response) => {
                if (!response.ok) throw new Error();
                chip.remove();
            }).catch(() => {
                chip.classList.remove("uploading");
                showToast(t("settings.media.removeError") || "Couldn't remove that - please try again.");
            });
        }

        // Every kind attached to the compose bar but not yet sent - each entry's chip
        // already sits in the DOM (with a local, not-yet-uploaded preview/icon), so
        // removing a staged entry before Send is just a client-side splice, no request
        // needed.
        const staged = [];

        function stageFile(kind, file) {
            const chip = document.createElement("span");
            chip.className = "compose-attachment-chip";

            let previewUrl = null;
            if (kind === "image") {
                previewUrl = URL.createObjectURL(file);
                const img = document.createElement("img");
                img.src = previewUrl;
                img.alt = "";
                chip.appendChild(img);
            } else {
                const icon = document.createElement("span");
                icon.className = "chip-icon";
                icon.textContent = iconFor(kind);
                chip.appendChild(icon);
            }

            const name = document.createElement("span");
            name.className = "chip-name";
            name.textContent = labelFor(kind);
            chip.appendChild(name);

            const entry = { kind, file, chip, previewUrl };

            const removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "chip-remove";
            removeBtn.setAttribute("aria-label", "Remove");
            removeBtn.textContent = "×";
            removeBtn.addEventListener("click", () => {
                const idx = staged.indexOf(entry);
                if (idx !== -1) staged.splice(idx, 1);
                if (entry.previewUrl) URL.revokeObjectURL(entry.previewUrl);
                chip.remove();
            });
            chip.appendChild(removeBtn);

            list.appendChild(chip);
            staged.push(entry);
        }

        // Actually uploads one staged entry, in place - swaps its local preview for the
        // same in-flight spinner look uploadFile uses, then fills in the real thumbnail/
        // remove button (now wired to a real DELETE via removeAttachment) once it lands.
        // Never rejects - a failed upload reports its own toast and drops the chip, same
        // as uploadFile's failure path, so callers can Promise.all a whole batch of these
        // without one failure losing the rest.
        function persistStagedEntry(entry) {
            const { kind, file, chip, previewUrl } = entry;
            chip.classList.add("uploading");
            chip.innerHTML = "";

            const icon = document.createElement("span");
            icon.className = "chip-icon";
            const spinner = document.createElement("span");
            spinner.className = "chip-spinner";
            icon.appendChild(spinner);
            chip.appendChild(icon);

            const name = document.createElement("span");
            name.className = "chip-name";
            name.textContent = labelFor(kind);
            chip.appendChild(name);

            const isDocument = kind === "document";
            const formData = new FormData();
            formData.append("file", file);
            if (!isDocument) formData.append("role", kind === "video" ? "hero_video" : "gallery");
            const path = isDocument ? "/api/documents" : "/api/media";
            const errorKey = isDocument ? "settings.documents.error" : "settings.media.error";

            return fetch(API_BASE + path, {
                method: "POST",
                headers: state.token ? { Authorization: "Bearer " + state.token } : {},
                body: formData,
            }).then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
                .then(({ ok, data }) => {
                    if (previewUrl) URL.revokeObjectURL(previewUrl);
                    if (!ok) {
                        chip.remove();
                        showToast(data.error || t(errorKey) || "Couldn't upload that - please try again.");
                        return;
                    }
                    chip.classList.remove("uploading");
                    chip.innerHTML = "";
                    fillUploadedChip(chip, kind, data);
                }).catch(() => {
                    if (previewUrl) URL.revokeObjectURL(previewUrl);
                    chip.remove();
                    showToast(t(errorKey) || "Couldn't upload that - please try again.");
                });
        }

        // Called once Send is actually tapped, before the message itself goes out - drains
        // `staged` up front so a second Send while these are still uploading, or one that
        // runs after they've already landed, never re-uploads the same file.
        flushStagedAttachments = function () {
            const toFlush = staged.splice(0, staged.length);
            return Promise.all(toFlush.map(persistStagedEntry));
        };

        function handlePicked(pickedInput) {
            const files = Array.prototype.slice.call(pickedInput.files || []);
            pickedInput.value = "";
            files.forEach((file) => {
                const kind = kindFor(file);
                if (!kind) {
                    showToast("That file type isn't supported here.");
                    return;
                }
                stageFile(kind, file);
            });
        }
        cameraInput.addEventListener("change", () => handlePicked(cameraInput));
        photosInput.addEventListener("change", () => handlePicked(photosInput));
        filesInput.addEventListener("change", () => handlePicked(filesInput));

        // A tap here would otherwise blur the compose textarea first (its default
        // mousedown behavior), dismissing the keyboard and shrinking the WebView's
        // viewport back down before the click that opens the sheet even fires - the
        // whole bar visibly drops, THEN the sheet appears a beat later. Blocking that
        // default keeps focus (and the keyboard, and the bar's current position) exactly
        // where it was, so the sheet opens immediately in place instead.
        attachBtn.addEventListener("mousedown", (event) => event.preventDefault());
        // While the mic is recording, this same button is a cancel (X) instead - the send
        // button already covers "stop and use" (see stopRecordingIfActive below), so this
        // is deliberately the other exit: stop and throw away everything recorded so far.
        attachBtn.addEventListener("click", () => {
            if (cancelRecordingIfActive()) return;
            openAttachSheet();
        });
    })();

    // --- Voice input - same MediaRecorder/Whisper round trip the onboarding wizard and
    // Settings page use. ---

    let stopRecordingIfActive = () => false;

    (function setupVoice() {
        const btnLabel = el("website-chat-voice-btn-label");
        const status = el("website-chat-voice-status");
        const waveformCanvas = el("website-chat-voice-waveform");
        if (!recordBtn) return;

        const supported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
        if (!supported) return;
        recordBtn.hidden = false;

        function setStatus(text, isError) {
            status.textContent = text || "";
            status.classList.toggle("voice-status-error", !!isError);
        }

        const MAX_RECORD_MS = 90 * 1000;
        let mediaRecorder = null;
        let mediaStream = null;
        let chunks = [];
        let maxLengthTimer = null;
        let busy = false;
        // Set when send is tapped while recording is in progress - stopRecording() is
        // async (waits for the "stop" event, then upload + transcription), so this just
        // marks the intent; uploadRecording's success handler is what actually calls
        // send() once the transcribed text is in hand.
        let pendingAutoSend = false;
        // Set by cancelRecording() just before stop() - the "stop" event handler checks
        // this first and, if set, discards everything instead of transcribing, no matter
        // how much was captured. This is the attach button's "throw it away" exit from
        // recording, distinct from the mic/stop button's own "stop and keep it" click.
        let cancelled = false;

        // Live waveform: a separate AnalyserNode tapping the same mic stream, purely
        // visual - it never touches the recorded audio or the MediaRecorder above. Drawn
        // with getByteTimeDomainData so the line sits flat at rest and swings with the
        // incoming sound, so the compose bar visibly reads as "listening" while recording.
        let audioCtx = null;
        let analyser = null;
        let waveformSource = null;
        let waveformRAF = null;

        function startWaveform(stream) {
            if (!waveformCanvas || !(window.AudioContext || window.webkitAudioContext)) return;
            const Ctx = window.AudioContext || window.webkitAudioContext;
            audioCtx = new Ctx();
            analyser = audioCtx.createAnalyser();
            analyser.fftSize = 1024;
            waveformSource = audioCtx.createMediaStreamSource(stream);
            waveformSource.connect(analyser);

            // The canvas is sized by CSS (fills the field's left side, see
            // .chat-compose-waveform), not fixed HTML attributes - match its backing
            // store to the rendered box, scaled for devicePixelRatio, right before the
            // first frame so the line draws crisp, not blurry.
            waveformCanvas.classList.add("active");
            const dpr = window.devicePixelRatio || 1;
            const w = waveformCanvas.clientWidth;
            const h = waveformCanvas.clientHeight;
            waveformCanvas.width = w * dpr;
            waveformCanvas.height = h * dpr;
            const data = new Uint8Array(analyser.fftSize);
            const ctx2d = waveformCanvas.getContext("2d");
            ctx2d.scale(dpr, dpr);
            const lineColor = getComputedStyle(document.documentElement).getPropertyValue("--text-muted").trim() || "#7a6c52";

            // Bar-style meter (ChatGPT voice-input look) instead of a continuous
            // oscilloscope trace: each frame's peak level becomes one bar, pushed onto a
            // rolling history that scrolls left as new ones arrive on the right - a fixed
            // number of bars sized to fit the canvas, redrawn as short rounded-cap
            // strokes rather than filled rects.
            const barWidth = 3;
            const barStep = 6;
            // One extra bar beyond what's visible so there's always one ready to slide in
            // from the right edge as scrollOffset wraps, instead of a gap flashing into view.
            const barCount = Math.max(1, Math.ceil(w / barStep)) + 2;
            const history = new Array(barCount).fill(0.04);
            // Sampling a new bar every 70ms but sliding it into place gradually, frame by
            // frame (scrollOffset), rather than snapping the whole row left in one 6px
            // jump every 70ms - the jump read as a stutter/glitch even though the sample
            // rate itself was fine.
            const PUSH_INTERVAL_MS = 70;
            const scrollSpeed = barStep / PUSH_INTERVAL_MS; // px per ms
            let scrollOffset = 0;
            let lastFrameTime = null;

            function draw(now) {
                waveformRAF = requestAnimationFrame(draw);
                if (lastFrameTime === null) lastFrameTime = now;
                scrollOffset += scrollSpeed * (now - lastFrameTime);
                lastFrameTime = now;
                while (scrollOffset >= barStep) {
                    scrollOffset -= barStep;
                    analyser.getByteTimeDomainData(data);
                    let maxDev = 0;
                    for (let i = 0; i < data.length; i++) {
                        const dev = Math.abs(data[i] - 128);
                        if (dev > maxDev) maxDev = dev;
                    }
                    history.push(Math.min(1, maxDev / 110));
                    history.shift();
                }

                ctx2d.clearRect(0, 0, w, h);
                ctx2d.lineWidth = barWidth;
                ctx2d.lineCap = "round";
                ctx2d.strokeStyle = lineColor;
                ctx2d.beginPath();
                for (let j = 0; j < history.length; j++) {
                    const barH = Math.max(barWidth, history[j] * h);
                    const x = j * barStep - scrollOffset + barWidth / 2;
                    const yTop = (h - barH) / 2;
                    ctx2d.moveTo(x, yTop);
                    ctx2d.lineTo(x, yTop + barH);
                }
                ctx2d.stroke();
            }
            waveformRAF = requestAnimationFrame(draw);
        }

        function stopWaveform() {
            if (waveformRAF) cancelAnimationFrame(waveformRAF);
            waveformRAF = null;
            if (waveformSource) { waveformSource.disconnect(); waveformSource = null; }
            if (audioCtx) { audioCtx.close(); audioCtx = null; }
            analyser = null;
            if (waveformCanvas) {
                waveformCanvas.classList.remove("active");
                const ctx2d = waveformCanvas.getContext("2d");
                ctx2d.setTransform(1, 0, 0, 1, 0, 0);
                ctx2d.clearRect(0, 0, waveformCanvas.width, waveformCanvas.height);
            }
        }

        function stopStream() {
            if (mediaStream) {
                mediaStream.getTracks().forEach((track) => track.stop());
                mediaStream = null;
            }
        }
        function setRecordingUI(isRecording) {
            recordBtn.classList.toggle("recording", isRecording);
            if (attachBtn) attachBtn.classList.toggle("recording", isRecording);
            btnLabel.textContent = isRecording
                ? (t("onboarding.describe.recordStop") || "Stop recording")
                : (t("onboarding.describe.recordStart") || "Record instead of typing");
            // The send button fills solid gold the instant recording starts, same as if
            // text were already typed - tapping it mid-recording is a valid "stop and
            // send now" shortcut (see below), not just a no-op on an empty field. Once
            // recording ends, hand back to updateComposeButtons() to reflect whatever's
            // actually in the field (transcribed text, or empty if the recording
            // failed/was too short).
            if (isRecording) sendBtn.classList.add("has-text");
            else updateComposeButtons();
        }
        function startRecording() {
            busy = true;
            recordBtn.disabled = true;
            setStatus(null);
            navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
                mediaStream = stream;
                chunks = [];
                const options = MediaRecorder.isTypeSupported("audio/webm") ? { mimeType: "audio/webm" } : undefined;
                mediaRecorder = new MediaRecorder(stream, options);
                mediaRecorder.addEventListener("dataavailable", (event) => {
                    if (event.data && event.data.size > 0) chunks.push(event.data);
                });
                mediaRecorder.addEventListener("stop", () => {
                    stopStream();
                    stopWaveform();
                    clearTimeout(maxLengthTimer);
                    setRecordingUI(false);
                    if (cancelled || chunks.length === 0) {
                        busy = false;
                        recordBtn.disabled = false;
                        pendingAutoSend = false;
                        cancelled = false;
                        return;
                    }
                    uploadRecording(new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" }));
                });
                mediaRecorder.start();
                startWaveform(stream);
                busy = false;
                recordBtn.disabled = false;
                setRecordingUI(true);
                setStatus(t("onboarding.describe.recording") || "Listening... tap to stop");
                maxLengthTimer = setTimeout(() => {
                    if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
                }, MAX_RECORD_MS);
            }).catch((err) => {
                busy = false;
                recordBtn.disabled = false;
                if (err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError")) {
                    setStatus(t("onboarding.describe.micPermission") || "Please allow microphone access to record your description.", true);
                } else {
                    setStatus(t("onboarding.describe.micError") || "Couldn't access the microphone - check your permissions and try again.", true);
                }
            });
        }
        function stopRecording() {
            if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
        }
        function cancelRecording() {
            if (!(mediaRecorder && mediaRecorder.state === "recording")) return;
            cancelled = true;
            mediaRecorder.stop();
        }
        function uploadRecording(blob) {
            busy = true;
            recordBtn.disabled = true;
            setStatus(t("onboarding.describe.transcribing") || "Transcribing...");
            const extension = (blob.type || "").indexOf("mp4") !== -1 ? "m4a" : "webm";
            const formData = new FormData();
            formData.append("audio", blob, "website-chat-message." + extension);
            fetch(API_BASE + "/api/voice-transcribe", {
                method: "POST",
                headers: state.token ? { Authorization: "Bearer " + state.token } : {},
                body: formData,
            }).then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
                .then(({ ok, data }) => {
                    busy = false;
                    recordBtn.disabled = false;
                    if (!ok) {
                        pendingAutoSend = false;
                        setStatus(data.error || (t("onboarding.describe.transcribeError") || "Couldn't understand that recording - please try again."), true);
                        return;
                    }
                    input.value = data.text || "";
                    autoGrowInput();
                    updateComposeButtons();
                    input.focus();
                    setStatus(null);
                    if (pendingAutoSend) {
                        pendingAutoSend = false;
                        send();
                    }
                }).catch(() => {
                    busy = false;
                    recordBtn.disabled = false;
                    pendingAutoSend = false;
                    setStatus(t("onboarding.describe.transcribeError") || "Couldn't understand that recording - please try again.", true);
                });
        }

        recordBtn.addEventListener("click", () => {
            if (busy) return;
            if (mediaRecorder && mediaRecorder.state === "recording") stopRecording();
            else startRecording();
        });
        // Tapping send while recording is a shortcut: stop, transcribe, and submit
        // immediately instead of requiring stop-then-review-then-send as three separate
        // taps (see the send button's own listener below).
        stopRecordingIfActive = () => {
            if (!(mediaRecorder && mediaRecorder.state === "recording")) return false;
            pendingAutoSend = true;
            stopRecording();
            return true;
        };
        cancelRecordingIfActive = () => {
            if (!(mediaRecorder && mediaRecorder.state === "recording")) return false;
            cancelRecording();
            return true;
        };
        window.addEventListener("beforeunload", () => {
            stopStream();
            stopWaveform();
        });
    })();

    // --- Send ---

    async function send() {
        const text = input.value.trim();
        if (!text) return;
        input.value = "";
        autoGrowInput();
        updateComposeButtons();
        input.disabled = true;
        sendBtn.classList.add("send-loading");
        addMessage("user", text);
        history.push({ role: "user", content: text });
        const typingEl = addMessage("bot", "...");
        typingEl.classList.add("website-chat-msg-typing");
        try {
            // Photos/video/documents attached to the compose bar were only staged
            // locally - this is the point they actually upload, same as a normal chat
            // only sending attachments once you hit send.
            await flushStagedAttachments();
            const data = await api("/api/website-chat", { method: "POST", body: { message: text, history: history.slice(0, -1) } });
            typingEl.remove();
            const reply = data.reply || t("websiteChat.error");
            addMessage("bot", reply);
            history.push({ role: "assistant", content: reply });
        } catch (err) {
            typingEl.remove();
            addMessage("bot", (err && err.message) || t("websiteChat.error"));
        } finally {
            input.disabled = false;
            sendBtn.classList.remove("send-loading");
            input.focus();
        }
    }

    // --- Wiring ---

    draftCard.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        // Hides the tabbar for this screen (unlike Detail/Help/Subscription, which stay
        // under it) - the chat is meant to read as its own independent window, not
        // another tab of the Home page, restored the moment the owner leaves it below.
        showTabbar(false);
        showScreen("websiteChat");
        ensureGreeting();
    }, true);

    backBtn.addEventListener("click", () => {
        showTabbar(true);
        showScreen("mysite");
    });
    sendBtn.addEventListener("click", () => {
        if (stopRecordingIfActive()) return;
        send();
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
        }
    });
})();

// --- Boot ---

// On a cold start (never on a warm resume - Android's already dispatched these once by
// then), the WebView's very first frames can render before Android finishes delivering
// the real WindowInsets, so mobile/app.css's --safe-top/--safe-bottom (env(safe-area-
// inset-*)) briefly read as their 0px fallback. Every screen's top padding is built on
// --safe-top (see .topbar above), so revealing a screen before the real inset lands and
// then having it arrive a frame later shoves the whole screen down by the status bar's
// height right in front of the user. Every .screen starts visibility:hidden until
// showScreen()/enterApp() below adds .active, so waiting a few frames here - before that
// first reveal - costs nothing visible; it just gives Android's insets dispatch, which
// normally lands within a frame or two, room to land first. Capped by the timeout so a
// stalled rAF (backgrounded tab, some OEM WebView oddity) can never hang the boot.
function waitForLayoutSettle() {
    return new Promise((resolve) => {
        let settled = false;
        const done = () => {
            if (settled) return;
            settled = true;
            resolve();
        };
        let frames = 0;
        const tick = () => {
            frames += 1;
            if (frames >= 3) { done(); return; }
            requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
        setTimeout(done, 250);
    });
}

updateOfflineBanner();

(async () => {
    const [token, businessName, onboardingSeen, theme, donationLink, pageTheme, mysiteHasPage, mysiteHasAppointments, mysiteSlug, mysiteTagline, mysiteAbout, mysiteChosenDomainCached, googleLoginPendingState] = await Promise.all([
        storageGet("token"),
        storageGet("businessName"),
        storageGet("onboardingSeen"),
        storageGet("theme"),
        storageGet("donationLink"),
        storageGet("pageTheme"),
        storageGet("mysiteHasPage"),
        storageGet("mysiteHasAppointments"),
        storageGet("mysiteSlug"),
        storageGet("mysiteTagline"),
        storageGet("mysiteAbout"),
        storageGet("mysiteChosenDomain"),
        storageGet("googleLoginPendingState"),
    ]);
    applyTheme(theme || "system");
    await initLanguage();

    state.token = token || null;
    state.businessName = businessName || "";
    // Applied from last launch's cache before the inbox is ever shown, same reasoning as
    // loadDashboard() caching it in the first place - avoids the strip popping in and
    // shoving the conversation list down partway through the very first render. Corrected
    // by that same fetch moments later if the business's donation link actually changed.
    el("donation-strip").classList.toggle("hidden", !donationLink);
    if (donationLink) el("donation-strip-link").href = donationLink;
    // Same reasoning as the donation strip above - populates the My Site tab's "Design"
    // row from last launch's cache instead of leaving it blank until loadMySite()'s own
    // /api/page fetch resolves. Corrected by that same fetch moments later if the
    // business's design actually changed.
    mysitePopulateThemeSelect(pageTheme || "warm");
    // Same reasoning again - the "Coming up" appointments card starts hidden in the raw
    // HTML and otherwise only appears once loadMySite()'s fetch resolves, which shoves
    // the whole rest of the My Site tab (its own web address/tagline/about fields, the
    // Design row, everything below) down a beat after the tab already looks fully loaded
    // - since My Site is the landing tab, that happens on every single launch. Applying
    // last launch's cached shape immediately avoids that jump; loadMySite() corrects it
    // moments later if anything actually changed.
    state.mysiteHasPage = mysiteHasPage === "1";
    el("mysite-appointments-card").classList.toggle("hidden", !mysiteHasAppointments);
    el("mysite-view-page-btn").classList.toggle("hidden", !state.mysiteHasPage);
    // Same reasoning again - fills in the Web address/Headline/About fields themselves
    // from last launch's cache instead of leaving them empty until loadMySite()'s fetch
    // resolves a couple seconds later. Corrected by that same fetch moments later if the
    // business's page content actually changed.
    if (state.mysiteHasPage) {
        el("mysite-slug").value = mysiteSlug || "";
        el("mysite-tagline-input").value = mysiteTagline || "";
        el("mysite-about-input").value = mysiteAbout || "";
        mysiteSyncDomainCheckBtnVisibility();
        mysiteUpdateAllFieldFades();
        mysiteChosenDomain = mysiteChosenDomainCached || null;
    }

    await waitForLayoutSettle();
    if (state.token) {
        enterApp();
    } else if (googleLoginPendingState) {
        // A Google sign-in was in flight (startGoogleLogin persisted this right before
        // opening the browser) when this boot happened - meaning whatever last ran in
        // this WebView got wiped out from under that attempt: an OTA update reloading
        // (see checkForOtaUpdate's own guard, which only protects against that once the
        // attempt is back in memory - it can't protect a boot that's re-deriving that
        // memory from scratch) or Android reclaiming the backgrounded process outright
        // for memory while the Google/Chrome tab was open on top of it, a real
        // possibility on constrained devices with `singleTask` - the OS is free to kill a
        // background process at any time and just recreate the activity fresh on return.
        // Either way this is not a fresh visitor arriving at Login - resume polling with
        // the same state instead of showing the plain choice screen and losing the
        // attempt (and the sign-in the visitor already completed on Google's side, whose
        // result is sitting server-side waiting to be picked up - see
        // database.oauth_login_session_status). showScreen("login") first so there's a
        // real screen under the overlay for checkGoogleLoginStatusOnce's own outcomes
        // (openRegisterScreen, showErrorBox) to land on once it settles.
        showScreen("login");
        showGoogleSigninOverlay();
        activeGoogleLoginAttempt = { state: googleLoginPendingState, errorBox: el("login-error"), settled: false };
        pollGoogleLogin(activeGoogleLoginAttempt);
    } else if (isNative && !onboardingSeen) {
        showScreen("onboarding");
    } else {
        showScreen("login");
    }
})().catch((e) => reportClientError(e.message, e.stack, "boot"));
