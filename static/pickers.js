// Camilius — website Working Hours + Price List pickers (Faza 5).
//
// Functional parity with the mobile app's own pickers (mobile/app.js). Currency (and
// service, in the price list) still use a plain native <select>/<input> - no room-to-
// grow-upward issue there. Time fields (createTimeDropdown below) are a custom dropdown
// instead, since a native <select>'s own popup flips upward for a field placed low on the
// page, same reasoning as schedule.html's own per-slot duration dropdown.
//
// Each picker renders into a mount <div> and keeps a same-named hidden <input> in sync
// on every change, serializing to/from the exact same plain-text format the backend/AI
// already expect ("Monday: 09:00-18:00" / "Service: Price CCY" per line) - so register()/
// settings() in app.py need no changes at all; they still just read request.form["working_hours"]
// and request.form["price_list"] as plain strings.

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

const HOURS_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
// English day names - this is the wire format saved to working_hours ("Monday: 09:00-
// 18:00") that the backend/AI parse, NOT display text, so it must stay fixed regardless
// of UI language (same reasoning as the {{ business.name }} exclusion elsewhere). The
// visible label text is translated separately below via HOURS_DAY_I18N_KEYS + t().
const HOURS_DAY_LABELS = {
    monday: "Monday", tuesday: "Tuesday", wednesday: "Wednesday", thursday: "Thursday",
    friday: "Friday", saturday: "Saturday", sunday: "Sunday",
};
const HOURS_DAY_I18N_KEYS = {
    monday: "pickers.days.monday", tuesday: "pickers.days.tuesday", wednesday: "pickers.days.wednesday",
    thursday: "pickers.days.thursday", friday: "pickers.days.friday", saturday: "pickers.days.saturday", sunday: "pickers.days.sunday",
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

// Closes every open time-dropdown menu - called both when a click lands outside any of
// them and right before opening one (so opening a second dropdown auto-closes the first).
function closeAllHoursTimeDropdowns() {
    document.querySelectorAll(".hours-time-dd-menu").forEach((m) => m.classList.add("hidden"));
}
if (!window.__hoursTimeDropdownHandlerAdded) {
    document.addEventListener("click", closeAllHoursTimeDropdowns);
    window.__hoursTimeDropdownHandlerAdded = true;
}

// A time field built the same way schedule.html builds its per-slot duration dropdown
// (.duration-dd/.schedule-slot-duration-select/.duration-dd-menu/.duration-dd-option) -
// reusing those classes gives it the exact same look (pill shape, shadow/glow, depth) and,
// more importantly, the same fix: a native <select>'s own popup picks its open direction
// based on viewport space and flips upward for a field placed low on the page, which is
// exactly what made the Working hours time fields fly open toward the top of the screen.
// Built as a real DOM node with a `value` property and native change events, so every call
// site can keep treating it exactly like the <select> it replaces (r.openSelect.value,
// r.openSelect.addEventListener("change", ...), etc.) without any other code changing.
function createTimeDropdown(defaultValue) {
    const dd = document.createElement("div");
    dd.className = "duration-dd hours-time-dd";

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "schedule-slot-duration-select duration-dd-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.textContent = defaultValue;

    const menu = document.createElement("div");
    menu.className = "duration-dd-menu hours-time-dd-menu hidden";
    menu.setAttribute("role", "listbox");

    let currentValue = defaultValue;
    function setValue(time, silent) {
        currentValue = time;
        trigger.textContent = time;
        menu.querySelectorAll(".duration-dd-option").forEach((o) => o.classList.toggle("active", o.dataset.value === time));
        if (!silent) dd.dispatchEvent(new Event("change"));
    }

    HOURS_TIME_OPTIONS.forEach((time) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "duration-dd-option" + (time === defaultValue ? " active" : "");
        option.dataset.value = time;
        option.textContent = time;
        option.addEventListener("click", (e) => {
            e.stopPropagation();
            menu.classList.add("hidden");
            trigger.setAttribute("aria-expanded", "false");
            setValue(time);
        });
        menu.appendChild(option);
    });

    trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const willOpen = menu.classList.contains("hidden");
        closeAllHoursTimeDropdowns();
        menu.classList.toggle("hidden", !willOpen);
        trigger.setAttribute("aria-expanded", String(willOpen));
        // The menu scrolls (48 half-hour slots don't all fit at once) - jump straight to
        // the current value instead of always opening scrolled to 00:00.
        if (willOpen) {
            const active = menu.querySelector(".duration-dd-option.active");
            if (active) active.scrollIntoView({ block: "nearest" });
        }
    });
    menu.addEventListener("click", (e) => e.stopPropagation());

    Object.defineProperty(dd, "value", {
        get() { return currentValue; },
        set(time) { setValue(time, true); },
    });

    dd.appendChild(trigger);
    dd.appendChild(menu);
    return dd;
}

// Mon-Fri get individual controls; Sat/Sun are always written out as "closed" - most
// businesses using this picker don't work weekends, so showing 7 rows up front made the
// section longer than it needed to be for the common case. The picker starts collapsed
// into a single "Working days" summary (one open/close pair applied to all five weekdays)
// and only reveals the five per-day rows once the user clicks through to customize one.
const WEEKEND_DAYS = ["saturday", "sunday"];

function createHoursPicker(mountId, hiddenFieldId) {
    const mount = document.getElementById(mountId);
    const hiddenField = document.getElementById(hiddenFieldId);
    if (!mount || !hiddenField) return null;
    const rows = {};
    const WEEKDAYS = HOURS_DAYS.filter((day) => !WEEKEND_DAYS.includes(day));
    let mode = "simple";

    function sync() {
        hiddenField.value = HOURS_DAYS.map((day) => {
            if (WEEKEND_DAYS.includes(day)) return `${HOURS_DAY_LABELS[day]}: closed`;
            if (mode === "simple") {
                return `${HOURS_DAY_LABELS[day]}: ${simpleOpenSelect.value}-${simpleCloseSelect.value}`;
            }
            const r = rows[day];
            if (!r.checkbox.checked) return `${HOURS_DAY_LABELS[day]}: closed`;
            return `${HOURS_DAY_LABELS[day]}: ${r.openSelect.value}-${r.closeSelect.value}`;
        }).join("\n");
    }

    // Shows either the open/close selects or a "Closed" label in a row's middle slot,
    // without changing the row's height either way - avoids the layout "jumping" that
    // hiding/showing the whole times block used to cause.
    function updateRowVisibility(day) {
        const r = rows[day];
        const open = r.checkbox.checked;
        r.times.classList.toggle("hidden", !open);
        r.closedLabel.classList.toggle("hidden", open);
    }

    // Collapsed default view: one open/close pair covering all five weekdays, plus a
    // note that weekends are closed and a link to switch to per-day editing below.
    const simpleView = document.createElement("div");
    simpleView.className = "hours-simple-view";

    const simpleRow = document.createElement("div");
    simpleRow.className = "hours-simple-row";
    const simpleLabelBtn = document.createElement("button");
    simpleLabelBtn.type = "button";
    simpleLabelBtn.className = "hours-simple-label-btn";
    simpleLabelBtn.setAttribute("data-i18n", "hoursPicker.workingDays");
    simpleLabelBtn.textContent = t("hoursPicker.workingDays");
    const simpleTimes = document.createElement("div");
    simpleTimes.className = "hours-day-times";
    const simpleOpenSelect = createTimeDropdown("09:00");
    const simpleCloseSelect = createTimeDropdown("18:00");
    const simpleSep = document.createElement("span");
    simpleSep.className = "hours-time-sep";
    simpleSep.textContent = "–";
    simpleTimes.appendChild(simpleOpenSelect);
    simpleTimes.appendChild(simpleSep);
    simpleTimes.appendChild(simpleCloseSelect);
    simpleRow.appendChild(simpleLabelBtn);
    simpleRow.appendChild(simpleTimes);
    simpleView.appendChild(simpleRow);

    const simpleNote = document.createElement("p");
    simpleNote.className = "hours-simple-note";
    simpleNote.setAttribute("data-i18n", "hoursPicker.weekendsClosedNote");
    simpleNote.textContent = t("hoursPicker.weekendsClosedNote");
    simpleView.appendChild(simpleNote);

    const customizeLink = document.createElement("button");
    customizeLink.type = "button";
    customizeLink.className = "hours-customize-link";
    customizeLink.setAttribute("data-i18n", "hoursPicker.customizeLink");
    customizeLink.textContent = t("hoursPicker.customizeLink");
    simpleView.appendChild(customizeLink);
    mount.appendChild(simpleView);

    simpleOpenSelect.addEventListener("change", sync);
    simpleCloseSelect.addEventListener("change", sync);

    const advancedView = document.createElement("div");
    advancedView.className = "hours-advanced-view hidden";
    mount.appendChild(advancedView);

    // Reveals the five weekday rows, seeded from whatever hours were set in the
    // collapsed view above, so switching to customize a single day doesn't reset the rest.
    function showAdvanced(seedFromSimple) {
        mode = "advanced";
        if (seedFromSimple) {
            WEEKDAYS.forEach((day) => {
                const r = rows[day];
                r.checkbox.checked = true;
                r.openSelect.value = simpleOpenSelect.value;
                r.closeSelect.value = simpleCloseSelect.value;
                updateRowVisibility(day);
            });
        }
        simpleView.classList.add("hidden");
        advancedView.classList.remove("hidden");
        sync();
    }
    simpleLabelBtn.addEventListener("click", () => showAdvanced(true));
    customizeLink.addEventListener("click", () => showAdvanced(true));

    // Quick-fill row: lets a user set one open/close pair and apply it to every weekday
    // at once, instead of repeating the same two dropdown picks five times.
    const quickfill = document.createElement("div");
    quickfill.className = "hours-quickfill";
    const quickfillLabel = document.createElement("span");
    quickfillLabel.className = "hours-quickfill-label";
    quickfillLabel.setAttribute("data-i18n", "hoursPicker.sameEveryDay");
    quickfillLabel.textContent = t("hoursPicker.sameEveryDay");
    const quickOpenSelect = createTimeDropdown("09:00");
    const quickCloseSelect = createTimeDropdown("18:00");
    const quickSep = document.createElement("span");
    quickSep.className = "hours-time-sep";
    quickSep.textContent = "–";
    const quickfillBtn = document.createElement("button");
    quickfillBtn.type = "button";
    quickfillBtn.className = "hours-quickfill-btn";
    quickfillBtn.setAttribute("data-i18n", "hoursPicker.applyToAll");
    quickfillBtn.textContent = t("hoursPicker.applyToAll");
    quickfillBtn.addEventListener("click", () => {
        WEEKDAYS.forEach((day) => {
            const r = rows[day];
            r.checkbox.checked = true;
            r.openSelect.value = quickOpenSelect.value;
            r.closeSelect.value = quickCloseSelect.value;
            updateRowVisibility(day);
        });
        sync();
    });
    quickfill.appendChild(quickfillLabel);
    quickfill.appendChild(quickOpenSelect);
    quickfill.appendChild(quickSep);
    quickfill.appendChild(quickCloseSelect);
    quickfill.appendChild(quickfillBtn);
    advancedView.appendChild(quickfill);

    const dayList = document.createElement("div");
    dayList.className = "hours-day-list";
    advancedView.appendChild(dayList);

    WEEKDAYS.forEach((day) => {
        const row = document.createElement("div");
        row.className = "hours-day-row";

        const label = document.createElement("span");
        label.className = "hours-day-name";
        label.setAttribute("data-i18n", HOURS_DAY_I18N_KEYS[day]);
        label.textContent = t(HOURS_DAY_I18N_KEYS[day]);

        const times = document.createElement("div");
        times.className = "hours-day-times";
        const openSelect = createTimeDropdown("09:00");
        const closeSelect = createTimeDropdown("18:00");
        const sep = document.createElement("span");
        sep.className = "hours-time-sep";
        sep.textContent = "–";
        times.appendChild(openSelect);
        times.appendChild(sep);
        times.appendChild(closeSelect);

        const closedLabel = document.createElement("span");
        closedLabel.className = "hours-day-closed-label hidden";
        closedLabel.setAttribute("data-i18n", "hoursPicker.closed");
        closedLabel.textContent = t("hoursPicker.closed");

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

        checkbox.addEventListener("change", () => {
            updateRowVisibility(day);
            sync();
        });
        openSelect.addEventListener("change", sync);
        closeSelect.addEventListener("change", sync);

        row.appendChild(label);
        row.appendChild(times);
        row.appendChild(closedLabel);
        row.appendChild(toggleLabel);
        dayList.appendChild(row);

        rows[day] = { checkbox, openSelect, closeSelect, times, closedLabel };
    });

    // Best-effort parse of our own generated format back into picker state - used when
    // loading a business's existing hours into the Profile form. Leaves a day's defaults
    // alone if its line isn't found or doesn't parse, same as the mobile app's picker.
    // Weekend lines are ignored - they're always forced closed by sync() regardless of
    // what a business had saved there before this picker stopped exposing them.
    function parse(text) {
        const lines = (text || "").split("\n");
        WEEKDAYS.forEach((day) => {
            const dayLabel = HOURS_DAY_LABELS[day].toLowerCase();
            const line = lines.find((l) => l.trim().toLowerCase().startsWith(dayLabel));
            if (!line) return;
            const r = rows[day];
            if (/closed/i.test(line)) {
                r.checkbox.checked = false;
                updateRowVisibility(day);
                return;
            }
            const match = line.match(/(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/);
            if (!match) return;
            const openTime = `${match[1].padStart(2, "0")}:${match[2]}`;
            const closeTime = `${match[3].padStart(2, "0")}:${match[4]}`;
            if (HOURS_TIME_OPTIONS.includes(openTime)) r.openSelect.value = openTime;
            if (HOURS_TIME_OPTIONS.includes(closeTime)) r.closeSelect.value = closeTime;
            r.checkbox.checked = true;
            updateRowVisibility(day);
        });

        // If every weekday came out open with identical hours, the collapsed "Working
        // days" summary already represents this schedule exactly, so keep it collapsed.
        // A closed weekday or differing hours needs the per-day view to show correctly.
        const allSame = WEEKDAYS.every((day) => rows[day].checkbox.checked) &&
            WEEKDAYS.every((day) => rows[day].openSelect.value === rows.monday.openSelect.value &&
                rows[day].closeSelect.value === rows.monday.closeSelect.value);
        if (allSame) {
            simpleOpenSelect.value = rows.monday.openSelect.value;
            simpleCloseSelect.value = rows.monday.closeSelect.value;
            mode = "simple";
        } else {
            showAdvanced(false);
        }
    }

    // True iff every open day's closing time is after its opening time - same rule the
    // mobile app's own hours picker enforces (mobile/app.js's registerHoursPicker.validate()).
    function validate() {
        if (mode === "simple") return simpleCloseSelect.value > simpleOpenSelect.value;
        return WEEKDAYS.every((day) => {
            const r = rows[day];
            return !r.checkbox.checked || r.closeSelect.value > r.openSelect.value;
        });
    }

    if (hiddenField.value.trim()) parse(hiddenField.value);
    sync();
    return { sync, validate };
}

// Major world currencies - same list as the mobile app's price-list currency picker
// (mobile/app.js). Codes are used as-is, not translated.
const CURRENCIES = ["RSD", "EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY", "TRY", "RUB", "SEK", "NOK", "DKK", "PLN", "AED", "INR", "BRL"];

function createPricePicker(mountId, addButtonId, currencySelectId, hiddenFieldId) {
    const mount = document.getElementById(mountId);
    const currencySelect = document.getElementById(currencySelectId);
    const hiddenField = document.getElementById(hiddenFieldId);
    if (!mount || !currencySelect || !hiddenField) return null;

    CURRENCIES.forEach((code) => {
        const option = document.createElement("option");
        option.value = code;
        option.textContent = code;
        currencySelect.appendChild(option);
    });
    currencySelect.value = "RSD";
    currencySelect.addEventListener("change", sync);

    function sync() {
        const currency = currencySelect.value;
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

        const priceInput = document.createElement("input");
        priceInput.type = "text";
        priceInput.placeholder = t("pickers.pricePlaceholder");
        priceInput.setAttribute("data-i18n-placeholder", "pickers.pricePlaceholder");
        priceInput.value = price || "";
        priceInput.className = "price-amount-input";

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "price-remove-btn";
        removeBtn.textContent = "×";
        removeBtn.setAttribute("aria-label", "Remove");
        removeBtn.addEventListener("click", () => { row.remove(); sync(); });

        serviceInput.addEventListener("input", sync);
        priceInput.addEventListener("input", sync);

        row.appendChild(serviceInput);
        row.appendChild(priceInput);
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
