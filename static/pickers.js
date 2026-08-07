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

// Web-only i18n key namespace for a day's display label - the shared picker core
// (static/hours-picker.js) asks for this via its dayI18nKey config callback, since
// mobile uses a different namespace ("days.<day>") for the same purpose.
const HOURS_DAY_I18N_KEYS = {
    monday: "pickers.days.monday", tuesday: "pickers.days.tuesday", wednesday: "pickers.days.wednesday",
    thursday: "pickers.days.thursday", friday: "pickers.days.friday", saturday: "pickers.days.saturday", sunday: "pickers.days.sunday",
};

// Closes every open pill-dropdown menu (time fields and the currency field alike) -
// called both when a click lands outside any of them and right before opening one (so
// opening a second dropdown auto-closes the first).
function closeAllPillDropdowns() {
    document.querySelectorAll(".pill-dd-menu").forEach((m) => m.classList.add("hidden"));
}
if (!window.__pillDropdownHandlerAdded) {
    document.addEventListener("click", closeAllPillDropdowns);
    window.__pillDropdownHandlerAdded = true;
}

// A field built the same way schedule.html builds its per-slot duration dropdown
// (.duration-dd/.schedule-slot-duration-select/.duration-dd-menu/.duration-dd-option) -
// reusing those classes gives it the exact same look (pill shape, shadow/glow, depth) and,
// more importantly, the same fix: a native <select>'s own popup picks its open direction
// based on viewport space and flips upward for a field placed low on the page, which is
// exactly what made the Working hours time fields fly open toward the top of the screen.
// Built as a real DOM node with a `value` property and native change events, so every call
// site can keep treating it exactly like the <select> it replaces (x.value,
// x.addEventListener("change", ...), etc.) without any other code changing. extraClass
// hangs off the root for field-specific tweaks (e.g. trigger width) - see .hours-time-dd/
// .price-currency-dd in style.css.
function createPillDropdown(options, defaultValue, extraClass) {
    const dd = document.createElement("div");
    dd.className = "duration-dd pill-dd" + (extraClass ? ` ${extraClass}` : "");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "schedule-slot-duration-select duration-dd-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.textContent = defaultValue;

    const menu = document.createElement("div");
    menu.className = "duration-dd-menu pill-dd-menu hidden";
    menu.setAttribute("role", "listbox");

    let currentValue = defaultValue;
    function setValue(value, silent) {
        currentValue = value;
        trigger.textContent = value;
        menu.querySelectorAll(".duration-dd-option").forEach((o) => o.classList.toggle("active", o.dataset.value === value));
        // Must bubble: settings.html's Save button listens for "change" on the whole
        // #settings-form (one delegated listener catching every field, including this
        // dropdown), not on this element directly - a non-bubbling event never reaches
        // it, so picking a time (or a currency, which reuses this same picker) silently
        // updated the hidden field but left the Save button hidden, as if nothing changed.
        if (!silent) dd.dispatchEvent(new Event("change", { bubbles: true }));
    }

    options.forEach((value) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "duration-dd-option" + (value === defaultValue ? " active" : "");
        option.dataset.value = value;
        option.textContent = value;
        option.addEventListener("click", (e) => {
            e.stopPropagation();
            menu.classList.add("hidden");
            trigger.setAttribute("aria-expanded", "false");
            setValue(value);
        });
        menu.appendChild(option);
    });

    trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const willOpen = menu.classList.contains("hidden");
        closeAllPillDropdowns();
        menu.classList.toggle("hidden", !willOpen);
        trigger.setAttribute("aria-expanded", String(willOpen));
        // Long option lists scroll (e.g. 48 half-hour time slots) - jump straight to the
        // current value instead of always opening scrolled to the top.
        if (willOpen) {
            const active = menu.querySelector(".duration-dd-option.active");
            if (active) active.scrollIntoView({ block: "nearest" });
        }
    });
    menu.addEventListener("click", (e) => e.stopPropagation());

    Object.defineProperty(dd, "value", {
        get() { return currentValue; },
        set(value) { setValue(value, true); },
    });

    dd.appendChild(trigger);
    dd.appendChild(menu);
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
        serviceInput.className = "price-service-input";

        const priceWrap = document.createElement("div");
        priceWrap.className = "price-amount-wrap";

        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.placeholder = t("pickers.pricePlaceholder");
        priceInput.setAttribute("data-i18n-placeholder", "pickers.pricePlaceholder");
        priceInput.value = price || "";
        priceInput.className = "price-amount-input";

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

    // Best-effort: if a saved price already ends with a known currency code, strip it
    // back out of the price field being edited and preselect the currency dropdown -
    // same logic as the mobile app's price picker.
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
            let price = idx === -1 ? "" : line.slice(idx + 1).trim();
            const currencyMatch = price.match(/\s([A-Za-z]{3})$/);
            if (currencyMatch && CURRENCIES.includes(currencyMatch[1].toUpperCase())) {
                if (!detectedCurrency) detectedCurrency = currencyMatch[1].toUpperCase();
                price = price.slice(0, currencyMatch.index).trim();
            }
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
        inputA.className = "upsell-service-a";
        inputA.setAttribute("list", datalistId);

        const arrow = document.createElement("span");
        arrow.className = "upsell-arrow";
        arrow.textContent = "→";

        const inputB = document.createElement("input");
        inputB.type = "text";
        inputB.placeholder = t("pickers.addonPlaceholder");
        inputB.setAttribute("data-i18n-placeholder", "pickers.addonPlaceholder");
        inputB.value = serviceB || "";
        inputB.className = "upsell-service-b";
        inputB.setAttribute("list", datalistId);

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
