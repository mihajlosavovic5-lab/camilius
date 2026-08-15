// Camilius — website Working Hours + Price List pickers (Faza 5).
//
// Functional parity with the mobile app's own pickers (mobile/app.js). Currency (and
// service, in the price list) still use a plain native <select>/<input> - no room-to-
// grow-upward issue there. Time fields (createTimeDropdown below) are a custom dropdown
// instead, since a native <select>'s own popup flips upward for a field placed low on the
// page, same reasoning as schedule.html's own per-slot duration dropdown.
//
// Each picker renders into a mount <div> and keeps a same-named hidden <input> in sync
// on every change, serializing to/from the exact same wire format the backend/AI already
// expect - JSON for working hours (see static/hours-picker.js), plain "Service: Price
// CCY" per line for the price list - so register()/settings() in app.py need no changes
// at all; they still just read request.form["working_hours"] and request.form["price_list"]
// as plain strings.

// settings.html inits all three pickers in the same DOMContentLoaded callback - without
// this wrapper, one picker throwing (e.g. on a business whose saved data doesn't parse
// the way the initializer expects) would abort that whole callback and silently leave
// every picker *after* it in the same block un-initialized (empty mount, no rows) even
// though nothing is wrong with them individually. safeInit() isolates each call so a
// single bad picker can't take the others down with it. (register.html's own init isn't
// wrapped the same way - its wizard reads createHoursPicker/createPricePicker's return
// value directly for step validation, so swallowing a failure there would just move the
// crash to first use instead of preventing it.)
function safeInit(fn) {
    try {
        return fn();
    } catch (e) {
        console.error("Picker init failed:", e);
        return null;
    }
}

// Phone country-select button (flag + dial code, e.g. "🇷🇸 +381") - same full ITU
// calling-code list as the mobile app's own COUNTRIES (mobile/app.js), used by every phone
// field's country picker below (register.html/settings.html) instead of each page keeping
// its own separate passive "detected country" hint table.
const PHONE_COUNTRIES = [
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

// Best-effort default from the browser's own locale (e.g. "de-DE" -> Germany) - falls back
// to Serbia (this product's home market) when that doesn't resolve to a real country. Same
// reasoning as the mobile app's own defaultPhoneCountry.
function defaultPhoneCountry() {
    const localeRegionMatch = /-([A-Za-z]{2})$/.exec(navigator.language || "");
    if (localeRegionMatch) {
        const guess = PHONE_COUNTRIES.find((c) => c.code === localeRegionMatch[1].toUpperCase());
        if (guess) return guess;
    }
    return PHONE_COUNTRIES.find((c) => c.code === "RS");
}

// Best-effort split of a stored "+381 6X XXX XXXX"-shaped phone number back into
// {country, rest} for re-populating a country-select button + its plain local-number
// input on page load (a saved value already carries its dial code, composePhoneNumber's
// own output). Longest dial code first, since some codes are prefixes of others (e.g.
// "+1" vs "+1876").
function splitStoredPhoneNumber(stored) {
    const value = (stored || "").trim();
    if (!value) return { country: defaultPhoneCountry(), rest: "" };
    const byDialLengthDesc = [...PHONE_COUNTRIES].sort((a, b) => b.dial.length - a.dial.length);
    const match = byDialLengthDesc.find((c) => value.startsWith(c.dial));
    if (!match) return { country: defaultPhoneCountry(), rest: value };
    return { country: match, rest: value.slice(match.dial.length).trim() };
}

// Builds the final international phone number to send to the server. If the user typed
// their own leading "+" (detected live below - e.g. refining the shared NANP "+1" code
// into a more specific "+1876..."), trust exactly what they typed instead of prepending
// the picker's own dial code on top of it (which would double it up). Same shape as the
// mobile app's own composePhoneNumber.
function composePhoneNumber(inputEl, selectedCountry) {
    const raw = inputEl.value.trim();
    if (!raw) return "";
    return raw[0] === "+" ? raw : `${selectedCountry.dial} ${raw}`;
}

// Wires one phone-input-row's country-select button to the shared centered option modal -
// tapping it opens the full country list (flag/name/dial per row), same "tap opens a
// centered sheet instead of a native <select>'s own popup" choice as every other picker on
// this page. onChange(country) fires whenever the selected country changes, whether from
// picking one in the list or from typing a recognized "+<dial>..." directly into the field
// (see the live-detection listener below) - callers use it to keep a hidden combined
// phone_number field in sync (see register.html/settings.html). Returns {getCountry,
// setCountry} so a caller can also set the initial country itself (e.g. after splitting a
// saved value via splitStoredPhoneNumber above).
function createPhoneCountryPicker(btnId, inputId, onChange) {
    const btn = document.getElementById(btnId);
    const input = document.getElementById(inputId);
    if (!btn || !input) return null;

    let current = defaultPhoneCountry();

    function render() {
        btn.textContent = `${countryFlagEmoji(current.code)} ${current.dial}`;
    }

    // Explicit height match rather than trusting the row's own CSS grid stretch (see
    // .phone-input-row in style.css) - that's the "should just work" answer, but measured
    // in a real browser it wasn't reliably stretching the <button> to the (taller,
    // floating-label-padded) field's height, leaving the button visibly shorter than the
    // app's identical layout. Measuring and setting an explicit pixel height instead
    // guarantees the match regardless of any browser/engine quirk with a <button> as a
    // stretched grid/flex item. Re-synced via ResizeObserver (not just once on load) since
    // the field's own height can still shift after that - the web font finishing its
    // (font-display: optional, so this is rare but possible) load, a page zoom, viewport/
    // orientation change, or this same shared input being reused by the field's own
    // tap-to-edit-field modal on narrow viewports.
    const floatingField = input.closest(".floating-field") || input;
    function syncHeight() {
        const h = floatingField.offsetHeight;
        if (h) btn.style.height = `${h}px`;
    }
    if (window.ResizeObserver) {
        new ResizeObserver(syncHeight).observe(floatingField);
    } else {
        window.addEventListener("resize", syncHeight);
    }
    syncHeight();

    function setCountry(country, silent) {
        current = country;
        render();
        if (!silent && onChange) onChange(current);
    }

    btn.addEventListener("click", () => {
        const options = PHONE_COUNTRIES.map((c) => ({
            value: c.code,
            html: `<span class="country-option-flag">${countryFlagEmoji(c.code)}</span>` +
                `<span class="country-option-name">${c.name}</span>` +
                `<span class="country-option-dial">${c.dial}</span>`,
        }));
        openOptionModal(options, current.code, (code) => {
            setCountry(PHONE_COUNTRIES.find((c) => c.code === code));
        });
    });

    // Live detection as the user types "+381..." directly into the field (in addition to
    // the button above) - matches the longest calling code that's a prefix of what's typed
    // so far. Deliberately non-destructive (doesn't strip the typed "+..." back out): some
    // calling codes share a prefix with a more specific one (the NANP "+1" covers the US/
    // Canada/several Caribbean nations, each with their own longer distinction, e.g.
    // Jamaica's "+1876") - stripping early would make it impossible to tell those apart as
    // more digits arrive. composePhoneNumber() above handles not double-prefixing a number
    // already typed with its own "+".
    const byDialLengthDesc = [...PHONE_COUNTRIES].sort((a, b) => b.dial.length - a.dial.length);
    input.addEventListener("input", () => {
        const raw = input.value.trim();
        if (raw[0] !== "+") return;
        const match = byDialLengthDesc.find((c) => raw.startsWith(c.dial));
        if (match && match.code !== current.code) setCountry(match);
    });

    render();
    return { getCountry: () => current, setCountry };
}

// Web-only i18n key namespace for a day's display label - the shared picker core
// (static/hours-picker.js) asks for this via its dayI18nKey config callback, since
// mobile uses a different namespace ("days.<day>") for the same purpose.
const HOURS_DAY_I18N_KEYS = {
    monday: "pickers.days.monday", tuesday: "pickers.days.tuesday", wednesday: "pickers.days.wednesday",
    thursday: "pickers.days.thursday", friday: "pickers.days.friday", saturday: "pickers.days.saturday", sunday: "pickers.days.sunday",
};

// Shared centered option-list picker (openOptionModal below) - one singleton instance
// (built lazily by ensureOptionModal, appended to <body> the first time anything opens
// it) reused by every "pick one value from a short list" field on the site: the working-
// hours time fields and price-list currency field below, plus schedule.html's per-slot
// duration picker and app_settings.html's reminder lead-time picker (each wires its own
// existing .duration-dd markup to call openOptionModal directly - see those templates).
// Matches the mobile app's own shared #option-modal (mobile/app.css/app.js's
// createAnchoredListPicker): a small sheet that zooms in centered on screen with a
// dimmed backdrop, opened already scrolled to the current value, instead of a dropdown
// menu anchored below whichever field opened it.
let _optionModalEl = null;
function ensureOptionModal() {
    if (_optionModalEl) return _optionModalEl;
    const modal = document.createElement("div");
    modal.className = "option-modal";
    modal.innerHTML =
        '<div class="option-modal-backdrop"></div>' +
        '<div class="option-modal-sheet">' +
        '<p class="option-modal-title hidden"></p>' +
        '<p class="option-modal-subtitle hidden"></p>' +
        '<div class="option-modal-list" role="listbox"></div>' +
        "</div>";
    document.body.appendChild(modal);
    modal.querySelector(".option-modal-backdrop").addEventListener("click", closeOptionModal);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && modal.classList.contains("open")) closeOptionModal();
    });
    _optionModalEl = modal;
    return modal;
}
function closeOptionModal() {
    if (_optionModalEl) _optionModalEl.classList.remove("open");
}
// options: array of either plain strings, {value, label} objects (a plain string is its
// own label), or {value, html} objects for a richer row than a single centered text label
// can show (e.g. the phone country picker's flag/name/dial columns above - see
// createPhoneCountryPicker) - html is trusted markup, never built from user input anywhere
// this is called from. activeValue is compared against each option's value to mark/scroll
// to the current selection. onSelect(value) fires once, right before the modal closes.
// title/subtitle are optional framing text shown above/below the list - only set by a
// field whose trigger doesn't already carry its own label outside the modal (see
// mobile/app.js's openReminderOwnerLeadPicker for the one field that uses this).
function openOptionModal(options, activeValue, onSelect, title, subtitle) {
    const modal = ensureOptionModal();
    const list = modal.querySelector(".option-modal-list");
    const titleEl = modal.querySelector(".option-modal-title");
    const subtitleEl = modal.querySelector(".option-modal-subtitle");
    titleEl.textContent = title || "";
    titleEl.classList.toggle("hidden", !title);
    subtitleEl.textContent = subtitle || "";
    subtitleEl.classList.toggle("hidden", !subtitle);

    list.innerHTML = "";
    let activeBtn = null;
    options.forEach((opt) => {
        const value = typeof opt === "object" ? opt.value : opt;
        const label = typeof opt === "object" ? opt.label : opt;
        const html = typeof opt === "object" ? opt.html : null;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "option-modal-option" + (html ? " option-modal-option-rich" : "");
        if (html) btn.innerHTML = html; else btn.textContent = label;
        if (value === activeValue) { btn.classList.add("active"); activeBtn = btn; }
        btn.addEventListener("click", () => {
            closeOptionModal();
            onSelect(value);
        });
        list.appendChild(btn);
    });

    modal.classList.add("open");
    // Marking/scrolling needs a layout pass first (list.innerHTML was just rebuilt
    // above), hence the rAF - same reasoning as mobile's own markActiveOption() call.
    requestAnimationFrame(() => { (activeBtn || list.firstElementChild)?.scrollIntoView({ block: "nearest" }); });
}

// A field built the same way schedule.html builds its per-slot duration dropdown
// (.duration-dd/.schedule-slot-duration-select/.duration-dd-trigger) - reusing those
// classes gives it the exact same trigger look (pill shape, shadow/glow, depth). Opens
// the shared centered option modal above on tap, instead of a native <select>'s own
// popup, whose open direction picks based on viewport space and flips upward for a field
// placed low on the page - exactly what made the Working hours time fields fly open
// toward the top of the screen before this replaced them. Built as a real DOM node with
// a `value` property and native change events, so every call site can keep treating it
// exactly like the <select> it replaces (x.value, x.addEventListener("change", ...),
// etc.) without any other code changing. extraClass hangs off the root for field-specific
// tweaks (e.g. trigger width) - see .hours-time-dd/.price-currency-dd in style.css.
function createPillDropdown(options, defaultValue, extraClass) {
    const dd = document.createElement("div");
    dd.className = "duration-dd pill-dd" + (extraClass ? ` ${extraClass}` : "");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "schedule-slot-duration-select duration-dd-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.textContent = defaultValue;

    let currentValue = defaultValue;
    function setValue(value, silent) {
        currentValue = value;
        trigger.textContent = value;
        // Must bubble: settings.html's Save button listens for "change" on the whole
        // #settings-form (one delegated listener catching every field, including this
        // dropdown), not on this element directly - a non-bubbling event never reaches
        // it, so picking a time (or a currency, which reuses this same picker) silently
        // updated the hidden field but left the Save button hidden, as if nothing changed.
        if (!silent) dd.dispatchEvent(new Event("change", { bubbles: true }));
    }

    trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        openOptionModal(options, currentValue, (value) => setValue(value));
    });

    Object.defineProperty(dd, "value", {
        get() { return currentValue; },
        set(value) { setValue(value, true); },
    });

    dd.appendChild(trigger);
    return dd;
}

function createTimeDropdown(defaultValue) {
    return createPillDropdown(HoursPicker.TIME_OPTIONS, defaultValue, "hours-time-dd");
}

// Thin platform wrapper around the shared picker core (static/hours-picker.js, loaded
// before this file - see templates/base.html) - keeps a hidden <input> in sync with the
// core's live state, since register()/settings() in app.py just read
// request.form["working_hours"] as a plain string and need no changes here.
function createHoursPicker(mountId, hiddenFieldId) {
    const mount = document.getElementById(mountId);
    const hiddenField = document.getElementById(hiddenFieldId);
    if (!mount || !hiddenField) return null;

    const picker = HoursPicker.create({
        mount,
        buildTimeField: (defaultValue) => {
            const dd = createTimeDropdown(defaultValue);
            return { el: dd, get value() { return dd.value; }, set value(v) { dd.value = v; } };
        },
        dayI18nKey: (day) => HOURS_DAY_I18N_KEYS[day],
        onLanguageChange: (cb) => document.addEventListener("site-language-changed", cb),
        // A business with genuinely different per-day hours lands straight on the
        // editable per-day list, rather than a "Working days" summary that would only
        // show Monday's hours.
        expandAdvancedIfNonUniform: true,
    });

    // Every one of the core's own controls dispatches a bubbling "change" event, so one
    // listener here on the mount reproduces a per-control sync exactly.
    mount.addEventListener("change", () => { hiddenField.value = picker.getValue(); });
    if (hiddenField.value.trim()) picker.setValue(hiddenField.value);
    hiddenField.value = picker.getValue();

    return {
        sync: () => { hiddenField.value = picker.getValue(); },
        validate: picker.validate,
        collapseToSimple: () => { picker.collapseToSimple(); hiddenField.value = picker.getValue(); },
    };
}

// Major world currencies - same list as the mobile app's price-list currency picker
// (mobile/app.js). Codes are used as-is, not translated.
const CURRENCIES = ["EUR", "USD", "RSD", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY", "TRY", "RUB", "SEK", "NOK", "DKK", "PLN", "AED", "INR", "BRL"];

// Common ways a price's currency ends up written as something other than its real
// 3-letter code - an owner typing free text ("180 dinara"), an AI-generated draft that
// didn't normalize it, or older saved data from before prices were expected to carry a
// code at all. Mapped so a saved price list still gets its currency recognized and
// stripped into the picker above instead of the word/symbol being left stuck in the
// price field forever. Checked case-insensitively - see resolveCurrencyWord below.
const CURRENCY_ALIASES = {
    din: "RSD", dinar: "RSD", dinara: "RSD", dinari: "RSD", dinaru: "RSD", rsd: "RSD",
    eur: "EUR", evro: "EUR", evra: "EUR", evri: "EUR", eura: "EUR", "€": "EUR",
    usd: "USD", dolar: "USD", dolara: "USD", dolari: "USD", "$": "USD",
    gbp: "GBP", funti: "GBP", funta: "GBP", "£": "GBP",
};

// Resolves a trailing price word/symbol (as matched by the regex below) to one of
// CURRENCIES, or null if it isn't recognized as any currency at all - a real code
// (any case) resolves directly, everything else goes through CURRENCY_ALIASES.
function resolveCurrencyWord(word) {
    const upper = word.toUpperCase();
    if (CURRENCIES.includes(upper)) return upper;
    return CURRENCY_ALIASES[word.toLowerCase()] || null;
}

function createPricePicker(mountId, addButtonId, currencySelectId, hiddenFieldId) {
    const mount = document.getElementById(mountId);
    const currencySelectPlaceholder = document.getElementById(currencySelectId);
    const hiddenField = document.getElementById(hiddenFieldId);
    if (!mount || !currencySelectPlaceholder || !hiddenField) return null;

    // Swaps out the template's plain <select id="...-price-currency"> for the same pill
    // dropdown the Working hours time fields use - same "opens downward" fix, same look.
    // Kept in the exact same spot in the DOM so surrounding layout is untouched.
    const currencySelect = createPillDropdown(CURRENCIES, "RSD", "price-currency-dd");
    currencySelectPlaceholder.replaceWith(currencySelect);
    currencySelect.addEventListener("change", sync);

    function sync() {
        const currency = currencySelect.value;
        mount.querySelectorAll(".price-amount-currency").forEach((el) => { el.textContent = currency; });
        hiddenField.value = Array.from(mount.querySelectorAll(".price-row-picker"))
            .map((row) => {
                const service = row.querySelector(".price-service-input").value.trim();
                const price = row.querySelector(".price-amount-input").value.trim();
                return service && price ? `${service}: ${price} ${currency}` : null;
            })
            .filter(Boolean)
            .join("\n");
    }

    function addRow(service, price) {
        const row = document.createElement("div");
        row.className = "price-row-picker";

        const serviceInput = document.createElement("input");
        serviceInput.type = "text";
        serviceInput.placeholder = t("pickers.servicePlaceholder");
        serviceInput.setAttribute("data-i18n-placeholder", "pickers.servicePlaceholder");
        serviceInput.value = service || "";
        serviceInput.className = "price-service-input tap-to-edit-field";
        serviceInput.dataset.fieldEditTitleI18n = "pickers.servicePlaceholder";
        // Set directly (not left to a later syncTapToEditFields(document) sweep) since a
        // row created here - on page load, or later from "+ Add service" - has no other
        // guaranteed re-sync point that would ever catch it otherwise (see
        // static/field-edit-modal.js's own comment on why fields need their readonly
        // state set at creation time, not just once for the page as a whole).
        serviceInput.readOnly = TAP_TO_EDIT_MQ.matches;

        const priceWrap = document.createElement("div");
        priceWrap.className = "price-amount-wrap";

        const priceInput = document.createElement("input");
        // Numeric only - the currency is its own dropdown (see priceCurrency below), so
        // this field is only ever meant to hold the bare amount (see sync()'s own
        // `${price} ${currency}` join above), never letters. type="number" rejects a
        // letter keystroke at the browser level rather than just formatting one back out
        // after the fact; inputmode="decimal" picks the right on-screen keyboard on
        // mobile. step="0.01" allows cents/decimal amounts, min="0" rules out a negative
        // price.
        priceInput.type = "number";
        priceInput.inputMode = "decimal";
        priceInput.min = "0";
        priceInput.step = "0.01";
        priceInput.placeholder = t("pickers.pricePlaceholder");
        priceInput.setAttribute("data-i18n-placeholder", "pickers.pricePlaceholder");
        priceInput.value = price || "";
        priceInput.className = "price-amount-input tap-to-edit-field";
        priceInput.dataset.fieldEditTitleI18n = "pickers.pricePlaceholder";
        priceInput.dataset.fieldEditNumeric = "";
        priceInput.readOnly = TAP_TO_EDIT_MQ.matches;

        // Shows the currently selected currency inside the field itself (kept in sync by
        // sync() above, whenever the currency dropdown changes) - the code already gets
        // appended to the stored price on save, this just previews it inline instead of
        // making the owner check the currency row separately.
        const priceCurrency = document.createElement("span");
        priceCurrency.className = "price-amount-currency";
        priceCurrency.textContent = currencySelect.value;

        priceWrap.appendChild(priceInput);
        priceWrap.appendChild(priceCurrency);

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "price-remove-btn";
        removeBtn.textContent = "×";
        removeBtn.setAttribute("aria-label", "Remove");
        removeBtn.addEventListener("click", () => { row.remove(); sync(); });

        serviceInput.addEventListener("input", sync);
        priceInput.addEventListener("input", sync);

        row.appendChild(serviceInput);
        row.appendChild(priceWrap);
        row.appendChild(removeBtn);
        mount.appendChild(row);
    }

    // Best-effort: pulls the currency out of the saved price text - a real code, or one
    // of CURRENCY_ALIASES' common words/symbols, found ANYWHERE in it rather than only
    // the very last word, since older/free-typed data can carry both a word and the
    // real code together (e.g. "200 dinara RSD" - stripping only the trailing "RSD"
    // used to leave "dinara" stuck in the price field forever) - and leaves only the
    // bare numeric amount for the field itself, which is numeric-only now (see
    // priceInput.type = "number" below) and so can't display anything else anyway.
    function parse(text) {
        mount.innerHTML = "";
        const lines = (text || "").split("\n").map((l) => l.trim()).filter(Boolean);
        if (lines.length === 0) {
            addRow("", "");
            return;
        }
        let detectedCurrency = null;
        lines.forEach((line) => {
            const idx = line.indexOf(":");
            const service = idx === -1 ? line : line.slice(0, idx).trim();
            const rawPrice = idx === -1 ? "" : line.slice(idx + 1).trim();
            const words = rawPrice.match(/[A-Za-z€$£]+/g) || [];
            for (let i = words.length - 1; i >= 0; i--) {
                const resolved = resolveCurrencyWord(words[i]);
                if (resolved) { if (!detectedCurrency) detectedCurrency = resolved; break; }
            }
            const numberMatch = rawPrice.match(/-?\d+(?:[.,]\d+)?/);
            const price = numberMatch ? numberMatch[0].replace(",", ".") : "";
            addRow(service, price);
        });
        currencySelect.value = detectedCurrency || "RSD";
    }

    document.getElementById(addButtonId).addEventListener("click", () => { addRow("", ""); sync(); });

    // True unless some row has exactly one of service/price filled in - a fully empty row
    // is fine (silently dropped by sync() above), but a half-filled one used to just
    // vanish on submit with no explanation. Same rule as the mobile app's price picker.
    function validate() {
        return Array.from(mount.querySelectorAll(".price-row-picker")).every((row) => {
            const service = row.querySelector(".price-service-input").value.trim();
            const price = row.querySelector(".price-amount-input").value.trim();
            return !!service === !!price;
        });
    }

    if (hiddenField.value.trim()) parse(hiddenField.value);
    else addRow("", "");
    sync();
    return { sync, validate };
}

// --- AI upsell pairs picker (Faza 11 polish - replaces the plain textarea from Faza 9)
// "Service A -> Service B" per line (see booking.parse_upsell_pairs). Each side is a
// text input with a <datalist> suggestion list built from the business's own price list
// services - a plain <select> couldn't offer a service not yet added to the price list,
// so this stays free-text with autocomplete instead of a rigid dropdown.
function createUpsellPicker(mountId, addButtonId, hiddenFieldId, priceListHiddenFieldId) {
    const mount = document.getElementById(mountId);
    const hiddenField = document.getElementById(hiddenFieldId);
    const priceListField = document.getElementById(priceListHiddenFieldId);
    if (!mount || !hiddenField) return null;

    const datalistId = mountId + "-services-list";
    const datalist = document.createElement("datalist");
    datalist.id = datalistId;
    mount.appendChild(datalist);

    function refreshServiceOptions() {
        const text = priceListField ? priceListField.value : "";
        const services = (text || "").split("\n")
            .map((line) => line.split(":")[0].trim())
            .filter(Boolean);
        datalist.innerHTML = "";
        services.forEach((service) => {
            const option = document.createElement("option");
            option.value = service;
            datalist.appendChild(option);
        });
    }

    function sync() {
        hiddenField.value = Array.from(mount.querySelectorAll(".upsell-row-picker"))
            .map((row) => {
                const a = row.querySelector(".upsell-service-a").value.trim();
                const b = row.querySelector(".upsell-service-b").value.trim();
                return a && b ? `${a} -> ${b}` : null;
            })
            .filter(Boolean)
            .join("\n");
    }

    function addRow(serviceA, serviceB) {
        const row = document.createElement("div");
        row.className = "upsell-row-picker";

        const inputA = document.createElement("input");
        inputA.type = "text";
        inputA.placeholder = t("pickers.servicePlaceholder");
        inputA.setAttribute("data-i18n-placeholder", "pickers.servicePlaceholder");
        inputA.value = serviceA || "";
        inputA.className = "upsell-service-a tap-to-edit-field";
        inputA.setAttribute("list", datalistId);
        inputA.dataset.fieldEditTitleI18n = "pickers.servicePlaceholder";
        // Carries the same <datalist> into the shared field-edit modal (field-edit-modal.js
        // reads this back off the source element when it opens) so the autocomplete-against-
        // the-price-list behavior this field has isn't lost once it's edited through there
        // instead of in place.
        inputA.dataset.fieldEditList = datalistId;
        inputA.readOnly = TAP_TO_EDIT_MQ.matches;

        const arrow = document.createElement("span");
        arrow.className = "upsell-arrow";
        arrow.textContent = "→";

        const inputB = document.createElement("input");
        inputB.type = "text";
        inputB.placeholder = t("pickers.addonPlaceholder");
        inputB.setAttribute("data-i18n-placeholder", "pickers.addonPlaceholder");
        inputB.value = serviceB || "";
        inputB.className = "upsell-service-b tap-to-edit-field";
        inputB.setAttribute("list", datalistId);
        inputB.dataset.fieldEditTitleI18n = "pickers.addonPlaceholder";
        inputB.dataset.fieldEditList = datalistId;
        inputB.readOnly = TAP_TO_EDIT_MQ.matches;

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "price-remove-btn";
        removeBtn.textContent = "×";
        removeBtn.setAttribute("aria-label", "Remove");
        removeBtn.addEventListener("click", () => { row.remove(); sync(); });

        inputA.addEventListener("input", sync);
        inputB.addEventListener("input", sync);

        row.appendChild(inputA);
        row.appendChild(arrow);
        row.appendChild(inputB);
        row.appendChild(removeBtn);
        mount.appendChild(row);
    }

    // Only clears rendered rows (not the <datalist> also living in this mount) - unlike
    // the price picker's parse(), which can safely wipe its whole mount since it has no
    // sibling element like this to preserve.
    function parse(text) {
        Array.from(mount.querySelectorAll(".upsell-row-picker")).forEach((row) => row.remove());
        const lines = (text || "").split("\n").map((l) => l.trim()).filter(Boolean);
        if (lines.length === 0) {
            addRow("", "");
            return;
        }
        lines.forEach((line) => {
            const idx = line.indexOf("->");
            if (idx === -1) return;
            addRow(line.slice(0, idx).trim(), line.slice(idx + 2).trim());
        });
    }

    document.getElementById(addButtonId).addEventListener("click", () => {
        refreshServiceOptions();
        addRow("", "");
        sync();
    });

    refreshServiceOptions();
    if (hiddenField.value.trim()) parse(hiddenField.value);
    else addRow("", "");
    sync();
    return { sync };
}
