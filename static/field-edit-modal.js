// Tap-to-zoom field editor, ported from the mobile app's own shared #mysite-field-modal
// (mobile/app.js's openFieldEditModal/MYSITE_FIELD_MODAL_FIELDS). Any input/textarea
// marked class="tap-to-edit-field" becomes readonly and opens this one shared centered
// modal on tap instead of typing in place, but ONLY below TAP_TO_EDIT_MQ's breakpoint -
// desktop already has the room and keyboard for comfortable in-place editing, where a
// popup on every click would just be friction, so there this class is a no-op and the
// field behaves exactly as it always has.
//
// Deliberately data-attribute-driven (data-field-edit-*) rather than a per-field JS
// registration call/map like mobile's MYSITE_FIELD_MODAL_FIELDS - every field that needs
// this just gets the right class/data attributes where it's already defined (Jinja
// templates, or wherever pickers.js builds a row), and this file needs zero per-page
// wiring code to pick it up, including fields created after this file first runs
// (dynamically-added price/upsell rows, htmx-swapped settings.html/schedule.html
// content) - see the delegated click listener and TAP_TO_EDIT_MQ "change" listener below,
// both registered once here and never needing to know about individual fields.
//
// Recognized attributes on a .tap-to-edit-field element:
//   data-field-edit-title-i18n="key"  - explicit title (falls back to the field's own
//                                       .floating-field label, or its placeholder)
//   data-field-edit-textarea          - edit as a multi-line textarea (e.g. About)
//   data-field-edit-password          - mask the value, with its own show/hide toggle
//   data-field-edit-numeric           - numeric-only keyboard/input (e.g. price amount)
//   data-field-edit-address           - gate the modal's address-suggestions dropdown on
//                                       while this field is open (see initAddressAutocomplete
//                                       in address-autocomplete.js)
//   data-field-edit-list="id"         - carry over a <datalist> id (e.g. upsell fields)
//
// Same breakpoint style.css already uses elsewhere for "mobile-width" treatments.
const TAP_TO_EDIT_MQ = window.matchMedia("(max-width: 640px)");

// Applies (or removes) the readonly state on every currently-present .tap-to-edit-field
// under root, matching TAP_TO_EDIT_MQ's current state. Call this again after adding new
// such elements to the DOM (a fresh htmx swap, a new price/upsell row, ...) - it's not a
// one-time setup, since a field created after the last call would otherwise stay a plain
// always-editable field regardless of viewport width.
function syncTapToEditFields(root) {
    (root || document).querySelectorAll(".tap-to-edit-field").forEach((el) => {
        el.readOnly = TAP_TO_EDIT_MQ.matches;
    });
}
// Live viewport-width changes (resizing a browser window, rotating a tablet) - re-applies
// to every field currently in the document, not just ones created after the change.
TAP_TO_EDIT_MQ.addEventListener("change", () => syncTapToEditFields(document));

function resolveFieldEditTitle(el) {
    if (el.dataset.fieldEditTitleI18n) return t(el.dataset.fieldEditTitleI18n);
    const floatingField = el.closest(".floating-field");
    const label = floatingField && floatingField.querySelector("label");
    if (label && label.textContent.trim()) return label.textContent;
    return el.placeholder || "";
}

let _fieldEditModalEl = null;
let _fieldEditModalSourceEl = null;
// Gates the modal's shared address-suggestions dropdown on/off for whichever field is
// CURRENTLY open in it (see initAddressAutocomplete's isActive param, address-
// autocomplete.js) - this modal's one input is reused for many different fields (name,
// phone, address, ...), so suggestion fetches must stay off except while an
// data-field-edit-address field is the one open.
let _fieldEditModalAddressActive = false;

function fieldEditModalActiveControl(modal) {
    const textarea = modal.querySelector(".field-edit-modal-textarea");
    return textarea.classList.contains("hidden") ? modal.querySelector(".field-edit-modal-input") : textarea;
}

function closeFieldEditModal() {
    if (!_fieldEditModalEl) return;
    _fieldEditModalEl.classList.remove("open");
    _fieldEditModalSourceEl = null;
    _fieldEditModalAddressActive = false;
}

// Lazily built the first time the delete badge is actually clicked, same reasoning as
// ensureFieldEditModal itself - most fields on a given page are never cleared through
// this, so there's no reason to pay for building it on every page load. One singleton
// instance whose Delete button always runs whatever's currently in
// _fieldEditDeleteConfirmOnConfirm (set fresh on every open, below) rather than a
// callback baked in at build time - the confirm dialog itself is only ever built once,
// but what "confirm" does needs to stay current across every later open.
let _fieldEditDeleteConfirmEl = null;
let _fieldEditDeleteConfirmOnConfirm = null;
function ensureFieldEditDeleteConfirm() {
    if (_fieldEditDeleteConfirmEl) return _fieldEditDeleteConfirmEl;
    const modal = document.createElement("div");
    modal.className = "confirm-modal";
    modal.setAttribute("role", "alertdialog");
    modal.setAttribute("aria-modal", "true");
    modal.innerHTML =
        '<div class="confirm-modal-backdrop"></div>' +
        '<div class="confirm-modal-card">' +
        '<p class="confirm-modal-message"></p>' +
        '<div class="confirm-modal-actions">' +
        '<button type="button" class="button button-secondary"></button>' +
        '<button type="button" class="button button-danger"></button>' +
        "</div>" +
        "</div>";
    document.body.appendChild(modal);

    modal.querySelector(".confirm-modal-message").textContent = t("fieldEditModal.deleteConfirm");
    const cancelBtn = modal.querySelector(".button-secondary");
    const confirmBtn = modal.querySelector(".button-danger");
    cancelBtn.textContent = t("fieldEditModal.cancel");
    confirmBtn.textContent = t("fieldEditModal.delete");

    function close() { modal.classList.remove("open"); }
    modal.querySelector(".confirm-modal-backdrop").addEventListener("click", close);
    cancelBtn.addEventListener("click", close);
    confirmBtn.addEventListener("click", () => {
        close();
        if (_fieldEditDeleteConfirmOnConfirm) _fieldEditDeleteConfirmOnConfirm();
    });

    _fieldEditDeleteConfirmEl = modal;
    return modal;
}
function openFieldEditDeleteConfirm(onConfirm) {
    _fieldEditDeleteConfirmOnConfirm = onConfirm;
    ensureFieldEditDeleteConfirm().classList.add("open");
}

function ensureFieldEditModal() {
    if (_fieldEditModalEl) return _fieldEditModalEl;
    const modal = document.createElement("div");
    modal.className = "field-edit-modal";
    modal.innerHTML =
        '<div class="field-edit-modal-backdrop"></div>' +
        '<div class="field-edit-modal-sheet">' +
        '<p class="field-edit-modal-title"></p>' +
        '<div class="field-edit-modal-input-wrap" id="field-edit-modal-input-wrap">' +
        '<button type="button" class="field-edit-modal-delete-btn" aria-label="Clear">&times;</button>' +
        '<input type="text" class="field-edit-modal-control field-edit-modal-input" autocapitalize="off" autocorrect="off">' +
        '<textarea class="field-edit-modal-control field-edit-modal-textarea hidden" rows="4"></textarea>' +
        '<button type="button" class="field-edit-modal-password-toggle hidden" aria-label="Show password">' +
        '<svg class="password-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">' +
        '<path d="M1.5 12S5 5 12 5s10.5 7 10.5 7-3.5 7-10.5 7S1.5 12 1.5 12z" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>' +
        '<circle cx="12" cy="12" r="3" stroke-width="1.6"/>' +
        '<line class="password-toggle-slash" x1="2.5" y1="21.5" x2="21.5" y2="2.5" stroke-width="1.6" stroke-linecap="round"/>' +
        "</svg>" +
        "</button>" +
        '<div class="address-suggest-list hidden" id="field-edit-modal-address-suggestions"></div>' +
        "</div>" +
        "</div>";
    document.body.appendChild(modal);

    const input = modal.querySelector(".field-edit-modal-input");
    const textarea = modal.querySelector(".field-edit-modal-textarea");
    const passwordToggle = modal.querySelector(".field-edit-modal-password-toggle");
    const deleteBtn = modal.querySelector(".field-edit-modal-delete-btn");
    const addressList = modal.querySelector("#field-edit-modal-address-suggestions");

    function syncBack() {
        if (!_fieldEditModalSourceEl) return;
        const control = fieldEditModalActiveControl(modal);
        _fieldEditModalSourceEl.value = control.value;
        _fieldEditModalSourceEl.dispatchEvent(new Event("input", { bubbles: true }));
        if (!passwordToggle.classList.contains("hidden")) {
            passwordToggle.classList.toggle("has-value", control.value.length > 0);
        }
        deleteBtn.classList.toggle("has-value", control.value.length > 0);
    }
    input.addEventListener("input", syncBack);
    textarea.addEventListener("input", syncBack);

    modal.querySelector(".field-edit-modal-backdrop").addEventListener("click", closeFieldEditModal);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && modal.classList.contains("open")) closeFieldEditModal();
    });

    passwordToggle.addEventListener("click", () => {
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        passwordToggle.classList.toggle("is-visible", !showing);
    });

    // Opens its own confirm dialog rather than clearing on the first tap - same reasoning
    // as the mobile app's own delete badge: an About paragraph or similar can be a lot to
    // lose to one misplaced tap.
    deleteBtn.addEventListener("click", () => {
        openFieldEditDeleteConfirm(() => {
            const control = fieldEditModalActiveControl(modal);
            control.value = "";
            control.dispatchEvent(new Event("input", { bubbles: true }));
        });
    });

    // Address fields only - same shared dropdown/fetch logic as the plain in-page address
    // fields (register-address-input, settings-page-address-input), gated by
    // _fieldEditModalAddressActive since this input is reused for every other field too.
    initAddressAutocomplete(input, addressList, () => _fieldEditModalAddressActive);

    _fieldEditModalEl = modal;
    return modal;
}

function openFieldEditModal(sourceEl) {
    const modal = ensureFieldEditModal();
    const input = modal.querySelector(".field-edit-modal-input");
    const textarea = modal.querySelector(".field-edit-modal-textarea");
    const inputWrap = modal.querySelector("#field-edit-modal-input-wrap");
    const passwordToggle = modal.querySelector(".field-edit-modal-password-toggle");
    const deleteBtn = modal.querySelector(".field-edit-modal-delete-btn");
    const addressList = modal.querySelector("#field-edit-modal-address-suggestions");
    const titleEl = modal.querySelector(".field-edit-modal-title");

    _fieldEditModalSourceEl = sourceEl;
    titleEl.textContent = resolveFieldEditTitle(sourceEl);

    const isTextarea = "fieldEditTextarea" in sourceEl.dataset;
    const isPassword = "fieldEditPassword" in sourceEl.dataset;
    const isNumeric = "fieldEditNumeric" in sourceEl.dataset;
    const isAddress = "fieldEditAddress" in sourceEl.dataset;
    const listId = sourceEl.dataset.fieldEditList || "";

    textarea.classList.toggle("hidden", !isTextarea);
    input.classList.toggle("hidden", isTextarea);

    const active = isTextarea ? textarea : input;
    active.value = sourceEl.value;
    if (sourceEl.maxLength >= 0) active.setAttribute("maxlength", sourceEl.maxLength);
    else active.removeAttribute("maxlength");

    input.type = isPassword ? "password" : isNumeric ? "number" : "text";
    input.inputMode = isNumeric ? "decimal" : "";
    if (isNumeric) {
        input.min = "0";
        input.step = "0.01";
    } else {
        input.removeAttribute("min");
        input.removeAttribute("step");
    }
    if (listId) input.setAttribute("list", listId);
    else input.removeAttribute("list");

    inputWrap.classList.toggle("field-edit-modal-input-wrap-password", isPassword);
    passwordToggle.classList.toggle("hidden", !isPassword);
    if (isPassword) {
        passwordToggle.classList.remove("is-visible");
        passwordToggle.classList.toggle("has-value", active.value.length > 0);
    }
    deleteBtn.classList.toggle("has-value", active.value.length > 0);

    _fieldEditModalAddressActive = isAddress;
    addressList.classList.add("hidden");
    addressList.innerHTML = "";

    // Deliberately never calls .focus() on the modal's own control - same reasoning as
    // the mobile app's own openFieldEditModal: it appears with no keyboard up, which only
    // shows once the user taps inside it, same as tapping any ordinary field would.
    sourceEl.blur();
    modal.classList.add("open");
}

// Delegated (not one listener per field) so it also covers fields that don't exist yet at
// this point - dynamically-added price/upsell rows, htmx-swapped settings.html/
// schedule.html content - with zero extra wiring needed anywhere else.
document.addEventListener("click", (e) => {
    if (!TAP_TO_EDIT_MQ.matches) return;
    const el = e.target.closest(".tap-to-edit-field");
    if (!el) return;
    openFieldEditModal(el);
});

syncTapToEditFields(document);
