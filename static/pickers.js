// Camilius — website Working Hours + Price List pickers (Faza 5).
//
// Functional parity with the mobile app's own pickers (mobile/app.js), but web-
// appropriate: plain native <select> elements for the time/currency fields, since the
// large-OS-modal problem that made mobile/app.js build a custom anchored dropdown is
// specific to Android's native <select> rendering - a desktop/mobile browser's own
// <select> has no such issue.
//
// Each picker renders into a mount <div> and keeps a same-named hidden <input> in sync
// on every change, serializing to/from the exact same plain-text format the backend/AI
// already expect ("Monday: 09:00-18:00" / "Service: Price CCY" per line) - so register()/
// settings() in app.py need no changes at all; they still just read request.form["working_hours"]
// and request.form["price_list"] as plain strings.

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

function createHoursPicker(mountId, hiddenFieldId) {
    const mount = document.getElementById(mountId);
    const hiddenField = document.getElementById(hiddenFieldId);
    if (!mount || !hiddenField) return null;
    const rows = {};

    function sync() {
        hiddenField.value = HOURS_DAYS.map((day) => {
            const r = rows[day];
            if (!r.checkbox.checked) return `${HOURS_DAY_LABELS[day]}: closed`;
            return `${HOURS_DAY_LABELS[day]}: ${r.openSelect.value}-${r.closeSelect.value}`;
        }).join("\n");
    }

    HOURS_DAYS.forEach((day) => {
        const row = document.createElement("div");
        row.className = "hours-day-row";

        const header = document.createElement("div");
        header.className = "hours-day-header";
        const label = document.createElement("span");
        label.className = "hours-day-name";
        label.setAttribute("data-i18n", HOURS_DAY_I18N_KEYS[day]);
        label.textContent = t(HOURS_DAY_I18N_KEYS[day]);

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
        const closeSelect = document.createElement("select");
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
            sync();
        });
        openSelect.addEventListener("change", sync);
        closeSelect.addEventListener("change", sync);

        row.appendChild(header);
        row.appendChild(times);
        mount.appendChild(row);

        rows[day] = { checkbox, openSelect, closeSelect, times };
    });

    // Best-effort parse of our own generated format back into picker state - used when
    // loading a business's existing hours into the Profile form. Leaves a day's defaults
    // alone if its line isn't found or doesn't parse, same as the mobile app's picker.
    function parse(text) {
        const lines = (text || "").split("\n");
        HOURS_DAYS.forEach((day) => {
            const dayLabel = HOURS_DAY_LABELS[day].toLowerCase();
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
    }

    // True iff every open day's closing time is after its opening time - same rule the
    // mobile app's own hours picker enforces (mobile/app.js's registerHoursPicker.validate()).
    function validate() {
        return HOURS_DAYS.every((day) => {
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
