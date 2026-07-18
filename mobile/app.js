// Camilius AI — mobile app shell (vanilla JS, no build step, no framework).
// Talks only to the JSON API (api.py) on the production Flask backend; renders its own
// screens instead of loading the server-rendered website. Login is stored as a Bearer
// token (see api.py for why, instead of the site's session cookie).

const API_BASE = "https://app.ai-auto-reply-mail.com";

const state = {
    token: localStorage.getItem("token") || null,
    businessName: localStorage.getItem("businessName") || "",
    conversations: [],
    filter: "all",
    activeDetail: null, // the conversation object currently open in the detail screen
};

const el = (id) => document.getElementById(id);

const screens = {
    login: el("screen-login"),
    inbox: el("screen-inbox"),
    detail: el("screen-detail"),
    settings: el("screen-settings"),
};
const tabbar = el("tabbar");

function showScreen(name) {
    Object.values(screens).forEach((s) => s.classList.remove("active"));
    screens[name].classList.add("active");
}

function showTabbar(show) {
    tabbar.classList.toggle("hidden", !show);
}

// --- API helper ---

async function api(path, { method = "GET", body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;

    const response = await fetch(API_BASE + path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const err = new Error(data.error || "Request failed");
        err.status = response.status;
        throw err;
    }
    return data;
}

// --- Auth ---

function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("businessName");
    state.token = null;
    state.conversations = [];
    showTabbar(false);
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
        localStorage.setItem("token", data.token);
        localStorage.setItem("businessName", data.business_name);
        enterApp();
    } catch (err) {
        errorBox.textContent = err.message || "Wrong username or password.";
        errorBox.classList.remove("hidden");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Log in";
    }
});

function enterApp() {
    showTabbar(true);
    setActiveTab("inbox");
    loadDashboard();
    loadSettingsIntoForm();
}

// --- Inbox ---

function timeAgo(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " · " +
        date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

async function loadDashboard() {
    el("inbox-loading").classList.remove("hidden");
    el("inbox-empty").classList.add("hidden");
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

        renderInboxList(data.conversations);
    } catch (err) {
        if (err.status === 401) return logout();
    } finally {
        el("inbox-loading").classList.add("hidden");
    }
}

function renderInboxList(conversations) {
    const list = el("inbox-list");
    list.innerHTML = "";

    if (conversations.length === 0) {
        el("inbox-empty").classList.remove("hidden");
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

    // Only push a new history entry when actually navigating in from the inbox - re-rendering
    // this same screen (e.g. right after sending a reply) must not stack extra back-steps.
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

// Lets the Android hardware/gesture back action close the detail screen instead of
// exiting the app - works with the system back gesture in a plain WebView. Once the
// Capacitor @capacitor/app plugin is wired in (native project setup), its 'backButton'
// event can call closeDetail() directly for the same effect.
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
        openDetail(conversation); // re-render this screen in its "sent" state
        loadDashboard(); // refresh the list/counters behind it
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
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    showScreen(tab === "inbox" ? "inbox" : "settings");
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
});

// --- Boot ---

if (state.token) {
    enterApp();
} else {
    showScreen("login");
}
