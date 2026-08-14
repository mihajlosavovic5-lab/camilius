// Shared address-suggestion dropdown for every free-text address field on the web (Sign
// up, Settings) - queries address_lookup.py's /api/address-suggest (OpenStreetMap
// Nominatim under the hood, see that module's own docstring for why not Google Places)
// and renders the results as a clickable list under the field, the same "type, see
// matches, tap one" pattern a phone's own native address field uses. Debounced (300ms
// after the last keystroke) rather than firing on every keystroke, both to stay well
// under Nominatim's roughly-1-request/second fair-use ceiling and to avoid a results
// list that's rewriting itself out from under a still-typing visitor.
//
// One function shared by every field (register.html, settings.html) rather than a copy
// per page - each just calls initAddressAutocomplete(input, list) once with its own two
// elements.
function initAddressAutocomplete(input, list) {
    if (!input || !list) return;

    let debounceTimer = null;
    // Guards against a slow earlier response landing after a faster later one and
    // clobbering it with stale results - only the response matching the most recently
    // sent query is ever rendered.
    let latestQuery = "";

    function hideList() {
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
        list.classList.remove("hidden");
    }

    function fetchSuggestions(query) {
        latestQuery = query;
        fetch("/api/address-suggest?q=" + encodeURIComponent(query))
            .then((response) => response.json())
            .then((data) => {
                if (query !== latestQuery) return; // superseded by a newer keystroke
                renderSuggestions(data.suggestions || []);
            })
            .catch(() => {
                if (query !== latestQuery) return;
                hideList();
            });
    }

    input.addEventListener("input", () => {
        const query = input.value.trim();
        if (debounceTimer) clearTimeout(debounceTimer);
        if (query.length < 3) {
            latestQuery = query;
            hideList();
            return;
        }
        debounceTimer = setTimeout(() => fetchSuggestions(query), 300);
    });

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
