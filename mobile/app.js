// Camilius AI — mobile app shell (vanilla JS, no build step, no framework).
// Talks only to the JSON API (api.py) on the production Flask backend; renders its own
// screens instead of loading the server-rendered website.

const API_BASE = "https://app.ai-auto-reply-mail.com";
const APP_VERSION = "1.9.2"; // bumped alongside the native versionName, sent with crash reports

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
    inbox: el("screen-inbox"),
    detail: el("screen-detail"),
    settings: el("screen-settings"),
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

// --- Native setup (status bar, back button, OTA update check) ---

if (capacitorPlugins) {
    capacitorPlugins.StatusBar.setBackgroundColor({ color: "#d69410" });
    capacitorPlugins.StatusBar.setStyle({ style: "LIGHT" });

    capacitorPlugins.App.addListener("backButton", ({ canGoBack }) => {
        if (screens.detail.classList.contains("active")) {
            history.back();
        } else if (canGoBack) {
            history.back();
        } else {
            capacitorPlugins.App.exitApp();
        }
    });

    // Self-hosted OTA updates: the app ships with the mobile/ shell baked in at build
    // time, but small changes to it can be pushed live afterward without a new signed
    // APK - see api.py's /api/mobile-manifest and build_mobile_bundle.py.
    if (capacitorPlugins.CapacitorUpdater) {
        capacitorPlugins.CapacitorUpdater.notifyAppReady().catch(() => {});
        (async () => {
            try {
                const response = await fetch(API_BASE + "/api/mobile-manifest");
                const manifest = await response.json();
                if (!manifest.available) return;

                const currentVersion = await storageGet("mobileBundleVersion");
                if (currentVersion === manifest.version) return;

                const bundle = await capacitorPlugins.CapacitorUpdater.download({
                    url: manifest.url,
                    version: manifest.version,
                });
                await capacitorPlugins.CapacitorUpdater.set(bundle);
                await storageSet("mobileBundleVersion", manifest.version);
            } catch (e) {
                reportClientError(e.message, e.stack, "ota-update-check");
            }
        })();
    }
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
        const err = new Error("No connection. Check your internet and try again.");
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
    submitBtn.textContent = "Logging in…";

    try {
        const data = await api("/api/login", { method: "POST", body: { username, password } });
        state.token = data.token;
        state.businessName = data.business_name;
        await storageSet("token", data.token);
        await storageSet("businessName", data.business_name);
        enterApp();
    } catch (err) {
        errorBox.textContent = err.message || "Wrong username or password.";
        errorBox.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Log in";
    }
});

// Registration is a long form (business details, Gmail app password verification) that
// already exists as a full page on the website - rather than duplicate it natively, it
// opens in an in-app browser tab (Chrome Custom Tab), which feels integrated rather than
// bouncing out to a separate browser app.
el("login-signup-link").addEventListener("click", async () => {
    const url = API_BASE + "/register";
    if (capacitorPlugins && capacitorPlugins.Browser) {
        await capacitorPlugins.Browser.open({ url });
    } else {
        window.open(url, "_blank");
    }
});

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

function timeAgo(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " · " +
        date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
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
            el("inbox-error-message").textContent = err.isNetworkError ? err.message : "Couldn't load your inbox. Try again.";
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
        el("inbox-empty-title").textContent = state.search.trim() ? "No matching conversations" : "You're all set — Camilius is watching your inbox";
        el("inbox-empty-detail").textContent = state.search.trim() ? "Try a different search term." : "New customer questions will show up here within 60 seconds.";
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
    badge.textContent = isPending ? "Awaiting approval" : "Sent";
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
    button.textContent = "Sending…";

    try {
        await api(`/api/conversations/${conversation.id}/send`, { method: "POST", body: { reply } });
        conversation.status = "sent";
        conversation.final_reply = reply;
        if (capacitorPlugins) capacitorPlugins.Haptics.impact({ style: "LIGHT" });
        openDetail(conversation);
        loadDashboard();
    } catch (err) {
        errorBox.textContent = err.message || "Couldn't send the reply. Try again.";
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = "Send reply";
    }
});

// --- Settings ---

async function loadSettingsIntoForm() {
    try {
        const data = await api("/api/settings");
        el("settings-name").value = data.name;
        el("settings-email").value = data.email;
        el("settings-hours").value = data.working_hours;
        el("settings-prices").value = data.price_list;
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
    button.textContent = "Saving…";

    try {
        await api("/api/settings", {
            method: "POST",
            body: {
                name: el("settings-name").value,
                working_hours: el("settings-hours").value,
                price_list: el("settings-prices").value,
                faq: el("settings-faq").value,
                email_app_password: el("settings-app-password").value,
            },
        });
        el("settings-app-password").value = "";
        messageBox.textContent = "Your details have been saved.";
        messageBox.classList.remove("hidden");
        el("inbox-business-name").textContent = el("settings-name").value;
    } catch (err) {
        errorBox.textContent = err.message || "Couldn't save changes.";
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = "Save changes";
    }
});

el("settings-logout").addEventListener("click", logout);

// --- Tab bar ---

function setActiveTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    showScreen(tab === "inbox" ? "inbox" : "settings");
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

// --- Boot ---

updateOfflineBanner();

(async () => {
    const [token, businessName, onboardingSeen] = await Promise.all([
        storageGet("token"),
        storageGet("businessName"),
        storageGet("onboardingSeen"),
    ]);

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
