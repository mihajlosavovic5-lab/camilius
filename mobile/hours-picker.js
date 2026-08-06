// Camilius — shared Working Hours picker core, used by both the website
// (static/pickers.js) and the mobile app (mobile/app.js). This file is the single
// canonical source - mobile/hours-picker.js is a mechanically generated mirror written
// by build_mobile_bundle.py on every run, so edit this file only and re-run
// `python build_mobile_bundle.py` afterward (a native APK build via `npx cap sync` reads
// mobile/ directly and won't pick up an edit made here alone until that script has run).
//
// Everything platform-specific is passed in via `config` - see createHoursPicker(mountId,
// hiddenFieldId) in static/pickers.js and createHoursPicker(containerId) in mobile/app.js
// for how each platform wires this up:
//   mount: HTMLElement to render into.
//   buildTimeField(defaultValue) -> { el, get value(), set value(v) } - the one piece of
//     real platform-specific DOM: web uses a custom pill dropdown, mobile an anchored
//     option-sheet button, both for the same reason (a native <select>'s own popup flips
//     upward for a field placed low on the page).
//   dayI18nKey(day) -> string - the i18n key for a day's display label (web and mobile
//     use different key namespaces, "pickers.days.<day>" vs "days.<day>").
//   onLanguageChange?: (cb) => void - registers cb to run again on a live language
//     switch. Web has a "site-language-changed" event for this; mobile doesn't, so it
//     omits this and the weekend note simply doesn't live-update there.
//   expandAdvancedIfNonUniform: boolean - true opens straight into the per-day view when
//     a loaded schedule's weekdays aren't all identical (web's behavior); false always
//     collapses to the "Working days" summary regardless, relying on the custom-hours
//     note to signal the days differ (mobile's behavior - a 7-row list on first load
//     reads as more overwhelming than useful there).

const HOURS_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
// English day names - this is the wire format saved to working_hours ("Monday: 09:00-
// 18:00") that the backend/AI parse, NOT display text, so it must stay fixed regardless
// of UI language. The visible label text is translated separately via config.dayI18nKey + t().
const HOURS_DAY_LABELS = {
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

// Most businesses using this picker work the same hours Mon-Fri and are closed
// weekends, so the picker starts collapsed into a single "Working days" summary (one
// open/close pair applied to all five weekdays, with a note that Saturday/Sunday are
// closed) instead of showing all 7 days up front. Clicking through to customize reveals
// all 7 days, Saturday and Sunday included - each with its own working/not-working
// toggle - and the collapsed note above adapts to whatever the user sets for the weekend.
const HOURS_WEEKEND_DAYS = ["saturday", "sunday"];

function createHoursPickerCore(config) {
    const { mount, buildTimeField, dayI18nKey, onLanguageChange, expandAdvancedIfNonUniform } = config;
    // The seven day rows (built further below) are the single source of truth for
    // what getValue() reports, whichever view is currently on screen - collapsing back
    // to the "Working days" summary is purely a display choice and never touches this
    // data, so a day customized individually is never at risk of being silently
    // overwritten by something unrelated to that day's own controls.
    const rows = {};
    const WEEKDAYS = HOURS_DAYS.filter((day) => !HOURS_WEEKEND_DAYS.includes(day));

    // Shows either the open/close fields or a "Closed" label in a row's middle slot,
    // without changing the row's own height either way - avoids the layout "jumping"
    // that hiding/showing the whole times block used to cause.
    function updateRowVisibility(day) {
        const r = rows[day];
        const open = r.checkbox.checked;
        r.times.classList.toggle("hidden", !open);
        r.closedLabel.classList.toggle("hidden", open);
    }

    // Shared by the collapsed view's own pair and the advanced view's quickfill pair -
    // both are deliberate "set every weekday to this" actions, unlike a single row's own
    // fields which only ever touch that one day.
    function applyPairToWeekdays(openTime, closeTime) {
        WEEKDAYS.forEach((day) => {
            const r = rows[day];
            r.checkbox.checked = true;
            r.openField.value = openTime;
            r.closeField.value = closeTime;
            updateRowVisibility(day);
        });
    }

    // Collapsed default view: one open/close pair covering all five weekdays, plus a
    // note that reflects the weekend and a link to switch to per-day editing below.
    const simpleView = document.createElement("div");
    simpleView.className = "hours-simple-view";

    const simpleRow = document.createElement("div");
    simpleRow.className = "hours-simple-row";
    const simpleLabel = document.createElement("span");
    simpleLabel.className = "hours-simple-label-btn";
    simpleLabel.setAttribute("data-i18n", "hoursPicker.workingDays");
    simpleLabel.textContent = t("hoursPicker.workingDays");
    const simpleTimes = document.createElement("div");
    simpleTimes.className = "hours-day-times";
    const simpleOpenField = buildTimeField("09:00");
    const simpleCloseField = buildTimeField("18:00");
    const simpleSep = document.createElement("span");
    simpleSep.className = "hours-time-sep";
    simpleSep.textContent = "–";
    simpleTimes.appendChild(simpleOpenField.el);
    simpleTimes.appendChild(simpleSep);
    simpleTimes.appendChild(simpleCloseField.el);
    simpleRow.appendChild(simpleLabel);
    simpleRow.appendChild(simpleTimes);
    simpleView.appendChild(simpleRow);

    // No static data-i18n here - the text depends on what's set for Saturday/Sunday
    // (updateWeekendNote below), so it's composed in JS instead of a single fixed string.
    const simpleNote = document.createElement("div");
    simpleNote.className = "hours-simple-note";
    simpleView.appendChild(simpleNote);

    // One line per weekend day (Saturday above Sunday), each showing its own hours or
    // "Closed". rows.saturday/rows.sunday don't exist until the day-list loop further
    // below runs, so this is a no-op until then; it's called for real once everything is
    // built (near the bottom of this function).
    function updateWeekendNote() {
        simpleNote.innerHTML = "";
        HOURS_WEEKEND_DAYS.forEach((day) => {
            const r = rows[day];
            if (!r) return;
            const line = document.createElement("p");
            line.className = "hours-simple-note-line";
            line.textContent = r.checkbox.checked
                ? t("hoursPicker.dayHours")
                    .replace("{day}", t(dayI18nKey(day)))
                    .replace("{open}", r.openField.value)
                    .replace("{close}", r.closeField.value)
                    .replace(/[.。]$/, "")
                : `${t(dayI18nKey(day))}: ${t("hoursPicker.closed")}`;
            simpleNote.appendChild(line);
        });
    }
    if (onLanguageChange) onLanguageChange(updateWeekendNote);

    // True iff every weekday is open with the exact same hours as Monday - i.e. the
    // collapsed pair above actually represents the whole week accurately. Shared by
    // setValue() (decides whether to open straight into the advanced view, per
    // expandAdvancedIfNonUniform) and updateCustomHoursNote() (decides whether to warn
    // that it doesn't).
    function weekdaysAreUniform() {
        return WEEKDAYS.every((day) => rows[day].checkbox.checked) &&
            WEEKDAYS.every((day) => rows[day].openField.value === rows.monday.openField.value &&
                rows[day].closeField.value === rows.monday.closeField.value);
    }

    // Warns that the pair above isn't the whole story whenever a weekday other than
    // Monday has been customized (different hours, or closed) - collapseToSimple() always
    // shows Monday's hours here regardless, so without this a customized Tuesday looks
    // like it silently reverted rather than just being one click/tap away under "Set
    // different hours for a specific day" below.
    const customHoursNote = document.createElement("p");
    customHoursNote.className = "hours-custom-note hidden";
    customHoursNote.setAttribute("data-i18n", "hoursPicker.customHoursNote");
    customHoursNote.textContent = t("hoursPicker.customHoursNote");
    simpleView.appendChild(customHoursNote);
    function updateCustomHoursNote() {
        customHoursNote.classList.toggle("hidden", weekdaysAreUniform());
    }

    const customizeLink = document.createElement("button");
    customizeLink.type = "button";
    customizeLink.className = "hours-customize-link";
    customizeLink.setAttribute("data-i18n", "hoursPicker.customizeLink");
    customizeLink.textContent = t("hoursPicker.customizeLink");
    simpleView.appendChild(customizeLink);
    mount.appendChild(simpleView);

    simpleOpenField.el.addEventListener("change", () => applyPairToWeekdays(simpleOpenField.value, simpleCloseField.value));
    simpleCloseField.el.addEventListener("change", () => applyPairToWeekdays(simpleOpenField.value, simpleCloseField.value));

    const advancedView = document.createElement("div");
    advancedView.className = "hours-advanced-view hidden";
    mount.appendChild(advancedView);

    // Just switches which view is on screen - the seven rows already hold whatever the
    // real current state is (kept up to date by applyPairToWeekdays and each row's own
    // controls), so there's nothing to seed here beyond the quickfill row's own display.
    function showAdvanced() {
        quickOpenField.value = rows.monday.openField.value;
        quickCloseField.value = rows.monday.closeField.value;
        simpleView.classList.add("hidden");
        advancedView.classList.remove("hidden");
    }
    customizeLink.addEventListener("click", showAdvanced);

    // Quick-fill row: lets a user set one open/close pair and apply it to every weekday
    // at once, instead of repeating the same two picks five times. Applies the moment
    // either time is changed here, same as the collapsed view's own pair above.
    const quickfill = document.createElement("div");
    quickfill.className = "hours-quickfill";
    const quickfillLabel = document.createElement("span");
    quickfillLabel.className = "hours-quickfill-label";
    quickfillLabel.setAttribute("data-i18n", "hoursPicker.sameEveryDay");
    quickfillLabel.textContent = t("hoursPicker.sameEveryDay");
    const quickOpenField = buildTimeField("09:00");
    const quickCloseField = buildTimeField("18:00");
    const quickSep = document.createElement("span");
    quickSep.className = "hours-time-sep";
    quickSep.textContent = "–";
    quickOpenField.el.addEventListener("change", () => applyPairToWeekdays(quickOpenField.value, quickCloseField.value));
    quickCloseField.el.addEventListener("change", () => applyPairToWeekdays(quickOpenField.value, quickCloseField.value));
    quickfill.appendChild(quickfillLabel);
    quickfill.appendChild(quickOpenField.el);
    quickfill.appendChild(quickSep);
    quickfill.appendChild(quickCloseField.el);
    advancedView.appendChild(quickfill);

    const dayList = document.createElement("div");
    dayList.className = "hours-day-list";
    advancedView.appendChild(dayList);

    HOURS_DAYS.forEach((day) => {
        const isWeekend = HOURS_WEEKEND_DAYS.includes(day);
        const row = document.createElement("div");
        row.className = "hours-day-row";
        // A visual break before Saturday, separating the working week from the weekend
        // pair that follows it.
        if (day === "saturday") row.classList.add("hours-day-row-weekend");

        const label = document.createElement("span");
        label.className = "hours-day-name";
        label.setAttribute("data-i18n", dayI18nKey(day));
        label.textContent = t(dayI18nKey(day));

        const times = document.createElement("div");
        times.className = "hours-day-times";
        const openField = buildTimeField("09:00");
        const closeField = buildTimeField("18:00");
        const sep = document.createElement("span");
        sep.className = "hours-time-sep";
        sep.textContent = "–";
        times.appendChild(openField.el);
        times.appendChild(sep);
        times.appendChild(closeField.el);

        const closedLabel = document.createElement("span");
        closedLabel.className = "hours-day-closed-label hidden";
        closedLabel.setAttribute("data-i18n", "hoursPicker.closed");
        closedLabel.textContent = t("hoursPicker.closed");

        const toggleLabel = document.createElement("label");
        toggleLabel.className = "hours-day-toggle";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        // Weekdays default to open, weekend days default to closed - matches what the
        // collapsed "Working days" summary already implies before anyone customizes it.
        checkbox.checked = !isWeekend;
        const track = document.createElement("span");
        track.className = "hours-day-toggle-track";
        const thumb = document.createElement("span");
        thumb.className = "hours-day-toggle-thumb";
        track.appendChild(thumb);
        toggleLabel.appendChild(checkbox);
        toggleLabel.appendChild(track);

        checkbox.addEventListener("change", () => {
            updateRowVisibility(day);
            if (isWeekend) updateWeekendNote();
        });
        openField.el.addEventListener("change", () => { if (isWeekend) updateWeekendNote(); });
        closeField.el.addEventListener("change", () => { if (isWeekend) updateWeekendNote(); });

        row.appendChild(label);
        row.appendChild(times);
        row.appendChild(closedLabel);
        row.appendChild(toggleLabel);
        dayList.appendChild(row);

        rows[day] = { checkbox, openField, closeField, times, closedLabel };
        updateRowVisibility(day);
    });

    updateWeekendNote();
    updateCustomHoursNote();

    // Best-effort parse of our own generated format (or something close enough to it)
    // back into picker state - used when loading a business's existing hours into the
    // form. Leaves a day's defaults alone if its line isn't found or doesn't parse,
    // rather than guessing.
    function setValue(text) {
        const lines = (text || "").split("\n");
        HOURS_DAYS.forEach((day) => {
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
            if (HOURS_TIME_OPTIONS.includes(openTime)) r.openField.value = openTime;
            if (HOURS_TIME_OPTIONS.includes(closeTime)) r.closeField.value = closeTime;
            r.checkbox.checked = true;
            updateRowVisibility(day);
        });

        // Shown regardless of which view ends up on screen below - the "which view"
        // decision never depends on this pair's own value, only on weekdaysAreUniform().
        simpleOpenField.value = rows.monday.openField.value;
        simpleCloseField.value = rows.monday.closeField.value;
        if (expandAdvancedIfNonUniform && !weekdaysAreUniform()) {
            showAdvanced();
        } else {
            advancedView.classList.add("hidden");
            simpleView.classList.remove("hidden");
        }
        updateWeekendNote();
        updateCustomHoursNote();
    }

    // Serializes the current row state to the plain-text wire format the backend/AI
    // expect ("Monday: 09:00-18:00" / "Sunday: closed" per line).
    function getValue() {
        return HOURS_DAYS.map((day) => {
            const r = rows[day];
            if (!r.checkbox.checked) return `${HOURS_DAY_LABELS[day]}: closed`;
            return `${HOURS_DAY_LABELS[day]}: ${r.openField.value}-${r.closeField.value}`;
        }).join("\n");
    }

    // Restores every day to the default open 09:00-18:00 for weekdays / closed for the
    // weekend, and collapses back to the "Working days" summary - NOT the same as
    // setValue("") (a no-op, since an empty string matches no day's line).
    function reset() {
        HOURS_DAYS.forEach((day) => {
            const r = rows[day];
            const isWeekend = HOURS_WEEKEND_DAYS.includes(day);
            r.checkbox.checked = !isWeekend;
            r.openField.value = "09:00";
            r.closeField.value = "18:00";
            updateRowVisibility(day);
        });
        simpleOpenField.value = "09:00";
        simpleCloseField.value = "18:00";
        advancedView.classList.add("hidden");
        simpleView.classList.remove("hidden");
        updateWeekendNote();
        updateCustomHoursNote();
    }

    // Collapses the advanced per-day list back to the "Working days" summary without
    // touching any day's values - meant to be called after a successful save, so the
    // section returns to its collapsed default regardless of whether the saved days
    // actually share the same hours (unlike setValue() above, which - on platforms with
    // expandAdvancedIfNonUniform - opens the advanced view whenever they don't). Since
    // the pair shown here is always Monday's regardless, updateCustomHoursNote() is what
    // keeps a customized Tuesday (say) from silently looking like it never saved.
    function collapseToSimple() {
        simpleOpenField.value = rows.monday.openField.value;
        simpleCloseField.value = rows.monday.closeField.value;
        advancedView.classList.add("hidden");
        simpleView.classList.remove("hidden");
        updateWeekendNote();
        updateCustomHoursNote();
    }

    // True iff every open day's closing time is after its opening time.
    function validate() {
        return HOURS_DAYS.every((day) => {
            const r = rows[day];
            return !r.checkbox.checked || r.closeField.value > r.openField.value;
        });
    }

    return { getValue, setValue, reset, validate, collapseToSimple };
}

window.HoursPicker = {
    DAYS: HOURS_DAYS,
    DAY_LABELS: HOURS_DAY_LABELS,
    TIME_OPTIONS: HOURS_TIME_OPTIONS,
    WEEKEND_DAYS: HOURS_WEEKEND_DAYS,
    create: createHoursPickerCore,
};
