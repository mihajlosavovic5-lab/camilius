// Shared address-suggestion dropdown for every free-text address field on the web (Sign
// up, Settings) - queries address_lookup.py's /api/address-suggest (OpenStreetMap
// Nominatim under the hood, see that module's own docstring for why not Google Places)
// and renders the results as a clickable list under the field, the same "type, see
// matches, tap one" pattern a phone's own native address field uses. Debounced (300ms
// after the last keystroke) rather than firing on every keystroke, both to stay well
// under Nominatim's roughly-1-request/second fair-use ceiling and to avoid a results
// list that's rewriting itself out from under a still-typing visitor.
//
// One function shared by every field (register.html, settings.html, and the shared
// tap-to-edit field modal on narrow viewports - static/field-edit-modal.js) rather than a
// copy per page - each just calls initAddressAutocomplete(input, list) once with its own
// two elements. isActive is only needed by the field-edit modal, whose one shared input is
// reused for many different fields (name, phone, address, ...) - it gates suggestion
// fetches to only when the modal is CURRENTLY open for an address field, so typing a phone
// number into that same shared input doesn't also trigger an address search. Defaults to
// always-on for the plain in-page fields, which each have their own dedicated input.
function initAddressAutocomplete(input, list, isActive) {
    if (!input || !list) return;
    if (!isActive) isActive = function () { return true; };

    let debounceTimer = null;
    // Guards against a slow earlier response landing after a faster later one and
    // clobbering it with stale results - only the response matching the most recently
    // sent query is ever rendered.
    let latestQuery = "";

    function hideList() {
        list.classList.remove("visible");
        list.classList.add("hidden");
        list.innerHTML = "";
    }

    function renderSuggestions(suggestions) {
        list.innerHTML = "";
        if (!suggestions.length) {
            hideList();
            return;
        }
        suggestions.forEach((text) => {
            const item = document.createElement("button");
            item.type = "button";
            item.className = "address-suggest-item";
            item.textContent = text;
            item.addEventListener("click", () => {
                input.value = text;
                hideList();
                input.focus();
            });
            list.appendChild(item);
        });
        // "hidden" (display:none) comes off first, then "visible" (the actual fade/slide
        // transition, see style.css) goes on a frame later - added in the same tick, the
        // transition would have nothing to animate from since the element wasn't
        // rendered yet to begin with.
        list.classList.remove("hidden");
        requestAnimationFrame(() => list.classList.add("visible"));
    }

    function fetchSuggestions(query) {
        latestQuery = query;
        fetch("/api/address-suggest?q=" + encodeURIComponent(query))
            .then((response) => response.json())
            .then((data) => {
                // Superseded by a newer keystroke, or (shared modal only) the field this
                // was fetched for isn't the one open anymore by the time it lands.
                if (query !== latestQuery || !isActive()) return;
                renderSuggestions(data.suggestions || []);
            })
            .catch(() => {
                if (query !== latestQuery) return;
                hideList();
            });
    }

    function scheduleSearch() {
        if (!isActive()) return;
        const query = input.value.trim();
        if (debounceTimer) clearTimeout(debounceTimer);
        if (query.length < 3) {
            latestQuery = query;
            hideList();
            return;
        }
        debounceTimer = setTimeout(() => fetchSuggestions(query), 300);
    }
    // "input" alone missed real keystrokes on some mobile keyboards - composing text
    // (predictive/gesture typing, some IMEs) doesn't always fire it until the word is
    // committed (a space, autocorrect, or the keyboard's own Done/checkmark button),
    // which read as "nothing happens until I finish typing and tap Done". "keyup" and
    // "compositionend" both catch what "input" alone missed; scheduleSearch's own
    // clearTimeout/setTimeout make firing it more than once per keystroke harmless.
    input.addEventListener("input", scheduleSearch);
    input.addEventListener("keyup", scheduleSearch);
    input.addEventListener("compositionend", scheduleSearch);

    input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") hideList();
    });

    // Any click outside the field/list closes it - same click-outside convention every
    // other dropdown on this page (theme picker, language switcher) already follows.
    document.addEventListener("click", (event) => {
        if (event.target === input || list.contains(event.target)) return;
        hideList();
    });
}
