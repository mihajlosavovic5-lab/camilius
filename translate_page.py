"""Translates a business's public page (templates/public_page.html) for a visitor who
picks a different language from its header flag switcher, and detects which language the
owner's own content is written in so that switcher can offer it as an extra option
alongside the fixed EN/ES/FR/DE set. DB-agnostic, same separation as generate_reply.py/
page_generator.py - this module never touches the database; the caller (app.py) checks
database.get_page_translation()'s cache first and persists whatever this returns via
database.upsert_page_translation()/set_page_content_language() - see those functions'
own docstrings for the caching contract.
"""
import json
import unicodedata
from difflib import SequenceMatcher

from dotenv import load_dotenv
from openai import OpenAI

from generate_reply import MODEL

load_dotenv()
client = OpenAI()

# Same code/name/flag-file set as static/site-i18n.js's own LANGUAGES list (mobile/i18n.js
# mirrors it exactly) - reusing those codes and /static/flags/*.svg|png filenames keeps a
# language pick meaning the same thing everywhere in the product, even though this page
# is standalone and doesn't load site-i18n.js itself (see public_page.html's own module
# docstring on why its effects are all reimplemented inline).
LANGUAGES = {
    "en": {"name": "English", "flag": "gb.svg"},
    "es": {"name": "Español", "flag": "es.png"},
    "pt": {"name": "Português", "flag": "pt.svg"},
    "fr": {"name": "Français", "flag": "fr.svg"},
    "de": {"name": "Deutsch", "flag": "de.svg"},
    "it": {"name": "Italiano", "flag": "it.svg"},
    "ru": {"name": "Русский", "flag": "ru.svg"},
    "sr": {"name": "Srpski", "flag": "rs.png"},
    "zh": {"name": "中文", "flag": "cn.svg"},
    "tr": {"name": "Türkçe", "flag": "tr.svg"},
}

# The public page's own default flag row, before whatever extra language the business's
# own content turns out to be written in gets appended (see app.py's public_page()) - a
# handful of the world's most widely spoken languages, not the full 10-language LANGUAGES
# set above (a visitor picks from a short row of flags, not a menu of everything this
# product happens to support internally).
DEFAULT_PAGE_LANGUAGES = ["en", "es", "fr", "de"]

MAX_DETECT_TEXT_LENGTH = 500
MAX_TRANSLATE_FIELD_LENGTH = 4000
# A business with a substantial about/price_list/faq (each sliceable up to
# MAX_TRANSLATE_FIELD_LENGTH above) plus all ~26 PAGE_UI_STRINGS translated in the same
# call can need a lot of output - and some target languages (Spanish among them) reliably
# run 15-25% longer than the English source for the same meaning, so a business whose
# content happens to sit comfortably under this ceiling in French or German can still run
# past it once translated into a more verbose language. Running past it gets the response
# cut off mid-JSON - which usually fails to parse (caught below, surfaces as a 502 the
# page's own fetch already tolerates) but can also land on a syntactically-valid-but-
# incomplete object instead (caught by translation_is_usable below). Either way, a
# language that always runs a bit long for this one business would otherwise just never
# work, however many times it's retried - 8000 is comfortably under gpt-4o-mini's 16384
# output-token ceiling (generate_reply.MODEL), not tuned to any single business's content.
MAX_TRANSLATE_TOKENS = 8000

# The page's own fixed chrome - section headings, button/placeholder text - translated
# alongside the business's actual content in the same call (see translate_page_content
# below) rather than hand-maintained per language: there's no bound on which language a
# business's own content (and so this switcher) might need to cover, so a static
# translation table for just these ~19 strings would still need to grow to match.
PAGE_UI_STRINGS = {
    "nav_home": "Home",
    "nav_about": "About",
    "nav_hours": "Working hours",
    "nav_book": "Book",
    "nav_gallery": "Gallery",
    "nav_photos": "Photos",
    "nav_prices": "Prices",
    "nav_questions": "Questions",
    "nav_contact": "Contact",
    "nav_location": "Location",
    "heading_about": "About",
    "heading_hours": "Working hours",
    "heading_book": "Book an appointment",
    "heading_gallery": "Gallery",
    "heading_photos": "Photos",
    "heading_prices": "Prices",
    "heading_questions": "Questions",
    "heading_contact": "Contact",
    "heading_location": "Location",
    "day_monday": "Monday",
    "day_tuesday": "Tuesday",
    "day_wednesday": "Wednesday",
    "day_thursday": "Thursday",
    "day_friday": "Friday",
    "day_saturday": "Saturday",
    "day_sunday": "Sunday",
    "hours_closed": "Closed",
    "cta_book": "Book now",
    "book_loading": "Loading open times...",
    "book_empty": "No upcoming openings right now - message us in chat to check.",
    "book_error": "Couldn't load open times - please try again shortly.",
    "book_confirm": "Requested {day} at {time} - continue in the chat to confirm.",
    "chat_subtitle": "Usually replies in seconds",
    "chat_placeholder": "Ask anything...",
    "chat_send": "Send",
    "chat_greeting": "Hi! How can I help you?",
    "chat_error": "Sorry, something went wrong - please try again.",
}

_DETECT_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "description": "ISO 639-1 two-letter code of the language this text is written in, e.g. 'en', 'sr', 'es'."},
    },
    "required": ["language"],
    "additionalProperties": False,
}

_TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {"type": "string"},
        "about": {"type": "string"},
        "price_list": {"type": "string"},
        "faq": {"type": "string"},
        "ui": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in PAGE_UI_STRINGS},
            "required": list(PAGE_UI_STRINGS.keys()),
            "additionalProperties": False,
        },
    },
    "required": ["tagline", "about", "price_list", "faq", "ui"],
    "additionalProperties": False,
}

_TRANSLATE_SYSTEM_PROMPT = """Translate the given business page content into {lang}. This
is a straight translation, not a rewrite - keep the same meaning, tone, and formatting
(line breaks, punctuation) as the original. Some fields may already be empty strings;
leave those as empty strings.

Proper nouns - the business's own name, a person's name, a place name, a brand - must be
copied through exactly as written in the source, never translated, transliterated, or
reworded, no matter which field they appear in or how much of that field they make up.
This matters most for "tagline": for many businesses it's nothing but their own name with
no other text at all, and that name must come back completely unchanged, not "translated"
into something that reads more like {lang}. A name does not change between languages.

Every other non-empty field in your response, "about" included, must actually be written
in {lang} - never copy the original source-language text through unchanged or partially
unchanged just because it already reads acceptably in {lang} or you're unsure how best to
phrase part of it (that rule does not apply to the proper nouns described above, which are
supposed to stay unchanged). A real, if imperfect, translation is always required for
everything else.

For "ui", translate every value in that object into {lang} the same way. Some values
contain literal {{token}} placeholders (e.g. {{day}}, {{time}}, {{business}}) - copy each
one through exactly as written, in the same position, without translating its contents
or the surrounding braces.

"about" may contain a small formatting syntax the page renders specially: a line
starting "# " (heading), lines starting "- " (bullet list), a line starting "> "
(highlighted line), a line starting "-- " (signature line), and inline "**bold**"/
"*italic*" markers. Translate the words inside these exactly like any other text, but
keep the markers themselves - the leading "#"/"- "/"> "/"-- " and the "**"/"*" pairs -
in the same positions, unchanged, so the translated result still has the identical
structure as the original.

Respond with plain text values only, no markdown formatting outside of "about"'s own
formatting syntax described above."""


def detect_content_language(text):
    """Best-effort ISO 639-1 code for whatever language `text` is written in - meant to
    be called once per business (see app.py's public_page()/database.
    set_page_content_language), not re-run on every page view. Falls back to "en" if the
    text is empty or the model call fails outright - a wrong guess here only costs one
    extra (still functional) flag in the switcher, never breaks the page, so this
    swallows errors rather than raising."""
    text = (text or "").strip()[:MAX_DETECT_TEXT_LENGTH]
    if not text:
        return "en"
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Identify the language the given text is written in."},
                {"role": "user", "content": text},
            ],
            max_tokens=20,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "detect_language", "strict": True, "schema": _DETECT_SCHEMA},
            },
        )
        code = json.loads(response.choices[0].message.content)["language"].strip().lower()
        return code if code in LANGUAGES else "en"
    except Exception:
        return "en"


# Minimum "about" length before an unchanged result counts as a suspected echo (see
# translation_is_usable below) - low mainly to still catch a genuinely short paragraph,
# not because short text is inherently risky the way it is for the fields this check
# deliberately does NOT cover (see the false-positive note below).
_ECHO_CHECK_MIN_LENGTH = 12

# How similar a translated "about" is allowed to be to its source before it counts as an
# echo (see _looks_echoed below) - a plain == comparison looked airtight but wasn't: a
# model reply can come back Unicode-normalized differently than the source (Serbian
# Latin's š/đ/č/ć/ž each have both a precomposed and a decomposed encoding - visually and
# semantically identical, but a different sequence of codepoints, so == sees "different"
# text) or with a token or two nudged without anything resembling an actual translation -
# both defeat exact equality while still being, for a visitor's purposes, the same
# unchanged source text. difflib's ratio() is a character-level similarity score from 0
# (nothing in common) to 1 (identical); real prose translated into a genuinely different
# language - different alphabet, different words - scores low here almost always, so this
# threshold is deliberately conservative (closer to "unmistakably the same text" than to
# "somewhat similar") to avoid flagging a translation that just happens to read alike.
_ECHO_SIMILARITY_THRESHOLD = 0.85


def _echo_similarity(source, translated):
    """Character-level similarity ratio (0-1) between `source` and `translated`, after
    Unicode-normalizing and collapsing whitespace in both so neither trips up the
    comparison - see _ECHO_SIMILARITY_THRESHOLD above for what counts as "close enough to
    read as the same text, not an actual translation." `source` is assumed already
    stripped and past the length threshold."""
    def normalize(text):
        return unicodedata.normalize("NFC", " ".join((text or "").split()))
    return SequenceMatcher(None, normalize(source), normalize(translated)).ratio()


def translation_is_usable(result, tagline, about, price_list, faq, lang=None):
    """False if `result` (shaped like translate_page_content's own return value, or the
    same shape rebuilt from a business_page_translations row - see app.py's
    public_page_translate) dropped any field that actually had source content, came back
    with an empty string for one of PAGE_UI_STRINGS' own always-non-empty keys, or left
    "about" reading as essentially the same text as the source (see _looks_echoed above,
    used rather than a plain == - real prose translated into a genuinely different
    language virtually never comes back this similar, so this means the model echoed the
    source back instead of translating it, not that a translation just happens to read
    the same).

    This used to be skipped when the requested language matched the business's own
    detected content_language (a visitor picking the flag that already matches the
    page's content is a legitimate no-op there). Dropped that exemption: it depended on
    content_language being correct, and a business whose content_language was ever wrong
    or stale - e.g. detected before this file's own upsert_business_page/update_business
    cache-invalidation existed, or from a brief window with placeholder content - would
    have this entire check silently disabled for exactly the one language matching that
    value, forever, with no way to tell from the outside (everything else about the
    response still looks fine - the UI chrome still translates, since that's unrelated to
    this check). The cost of dropping the exemption is small and one-sided: the rare
    visitor who deliberately clicks their page's own already-active flag gets a 502 (an
    unnecessary but harmless model call - "about" was already showing correctly and stays
    that way) instead of a silent no-op cache write. That's a better trade than a whole
    language being invisibly, permanently broken for a business with no way to notice why.

    Deliberately NOT applied to tagline/price_list/faq, even though the exact same echo
    bug reproduced for price_list once (see git history) - a short tagline, a price line,
    or a bare FAQ answer routinely contains loanwords ("Espresso", "Cappuccino"), numbers,
    or currency codes that a CORRECT translation leaves completely unchanged too, so this
    same check applied to those fields rejected genuinely good translations as false
    "echoes" - for a business whose price list happens to be loanword-heavy, EVERY
    language failed this way, every time, which is worse than the bug it was meant to
    catch. "about" is the one field long/free-form enough that an exact match (in any
    language pairing, including a same-language no-op) is still a reliable failure signal.

    Nothing here is a translation *quality* check otherwise (a bad but genuinely
    different-looking translation still passes) - only a check that this result is safe
    to show a visitor or cache for the next one, since public_page.html's
    applyTranslation() only ever gets to see the field, not the source it came from, and
    skips a field entirely rather than let a bad value overwrite real on-screen content
    (see its own comment). A response that slipped past this once and got cached would
    otherwise keep failing the exact same way forever - every later visitor hitting the
    cache, never a fresh model call again - so both translate_page_content below (before
    caching a fresh result) and public_page_translate (before trusting an already-cached
    row) call this.

    `lang` (the target language this result/cached row is for) is optional and only used
    for the English-specific staleness check documented on translation_rejection_reason
    below - pass it whenever it's known (both of this function's own call sites now do)
    so an "en" row generated before a later PAGE_UI_STRINGS wording change gets caught."""
    return translation_rejection_reason(result, tagline, about, price_list, faq, lang) is None


def translation_rejection_reason(result, tagline, about, price_list, faq, lang=None):
    """None if translation_is_usable(...) would accept `result`; otherwise a short
    human-readable string saying which check it failed and why, for server-side
    diagnostics only (app.py's public_page_translate logs this via
    database.log_client_error whenever a translation gets rejected - see its own call
    site) - never shown to a visitor. Split out from translation_is_usable itself so that
    function's return type stays a plain bool for its two existing call sites, while
    still sharing this same logic rather than a second copy of it that could drift."""
    for field, source in (("tagline", tagline), ("about", about), ("price_list", price_list), ("faq", faq)):
        if (source or "").strip() and not (result.get(field) or "").strip():
            return f"{field} came back empty; source was {(source or '').strip()[:80]!r}"
    ui = result.get("ui") or {}
    missing_ui = [key for key in PAGE_UI_STRINGS if not (ui.get(key) or "").strip()]
    if missing_ui:
        return f"ui missing/empty for keys: {missing_ui[:5]}"
    # "en" is special: PAGE_UI_STRINGS IS the English chrome, so a correct "en" result's
    # ui must be byte-for-byte PAGE_UI_STRINGS, never something merely "close" - translate_
    # page_content below enforces this directly rather than trusting the model to reliably
    # echo it back unchanged, which makes this check exact-equality rather than fuzzy. Its
    # real purpose is catching an already-cached "en" row saved before a later edit to
    # PAGE_UI_STRINGS itself (e.g. renaming a nav label) - without this, that edit would
    # never reach an existing business's already-cached "en" translation, only ever a
    # fresh one, and reproduce the exact bug this exists to catch (a stale value served
    # forever, this time for the page's own native/original language rather than a
    # translated one).
    if lang == "en" and ui != PAGE_UI_STRINGS:
        stale = {key: ui.get(key) for key in PAGE_UI_STRINGS if ui.get(key) != PAGE_UI_STRINGS[key]}
        return f"en ui doesn't match current PAGE_UI_STRINGS: {dict(list(stale.items())[:5])}"
    about_stripped = (about or "").strip()
    if len(about_stripped) >= _ECHO_CHECK_MIN_LENGTH:
        ratio = _echo_similarity(about_stripped, result.get("about"))
        if ratio >= _ECHO_SIMILARITY_THRESHOLD:
            return f"about looks echoed (similarity={ratio:.2f}); result was {(result.get('about') or '')[:120]!r}"
    return None


# How many times translate_page_content below retries an unusable result before giving
# up - production evidence (a business's real /admin/client-errors log) showed gpt-4o-mini
# repeatedly leaving "about" completely untranslated for one specific content+language
# pairing, consistent enough across several manual attempts to look almost deterministic,
# while every other language for the same business translated correctly. Chat completions
# aren't run at temperature 0 here, so a second attempt at the exact same prompt is a
# real, independent draw, not a guaranteed repeat of the first - worth spending a couple
# of extra model calls on ONLY when the first actually failed, to turn "fails often
# enough to be visibly broken" into "occasionally needs a second try, invisibly."
_MAX_TRANSLATE_ATTEMPTS = 3


def translate_page_content(lang, tagline, about, price_list, faq):
    """One OpenAI call (retried up to _MAX_TRANSLATE_ATTEMPTS times - see its own comment
    - on an unusable result) translating a business's tagline/about/price_list/faq plus
    the page's own fixed PAGE_UI_STRINGS into `lang`, all together so they read as one
    consistent voice. Returns a dict shaped like {"tagline", "about", "price_list",
    "faq", "ui": {...same keys as PAGE_UI_STRINGS}}. Raises ValueError the same way
    page_generator.generate_page_draft() does if every attempt's model call fails,
    returns something unparseable, or returns something translation_rejection_reason()
    rejects (e.g. cut off mid-response and missing a field that should have real content,
    or echoing the source language straight back instead of translating it - see that
    function's own docstring) - the caller (app.py's public_page_translate route) decides
    how to surface that; nothing here is worth blocking the page itself over, since a
    visitor just keeps seeing the untranslated original on failure.

    lang == "en" is special-cased: "ui" is forced to exactly PAGE_UI_STRINGS rather than
    trusting whatever the model came back with for that sub-object, since PAGE_UI_STRINGS
    IS the English chrome by definition - there's nothing for the model to translate there
    at all, only tagline/about/price_list/faq (the business's own content) genuinely need
    a real English translation when the business's content isn't already written in
    English. Forcing this deterministically (rather than just relying on the model to
    echo it back unchanged, which it isn't guaranteed to) is what makes translation_
    rejection_reason's own "en" staleness check exact-equality reliable: a freshly
    generated "en" row always matches current PAGE_UI_STRINGS, so a cached row that
    doesn't match is unambiguously stale, not a false positive from ordinary model
    variance."""
    lang_name = LANGUAGES.get(lang, {}).get("name", lang)
    messages = [
        {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT.format(lang=lang_name)},
        {"role": "user", "content": json.dumps({
            "tagline": (tagline or "")[:MAX_TRANSLATE_FIELD_LENGTH],
            "about": (about or "")[:MAX_TRANSLATE_FIELD_LENGTH],
            "price_list": (price_list or "")[:MAX_TRANSLATE_FIELD_LENGTH],
            "faq": (faq or "")[:MAX_TRANSLATE_FIELD_LENGTH],
            "ui": PAGE_UI_STRINGS,
        })},
    ]

    last_reason = "unknown failure"
    for _attempt in range(_MAX_TRANSLATE_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=MAX_TRANSLATE_TOKENS,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "page_translation", "strict": True, "schema": _TRANSLATE_SCHEMA},
                },
            )
        except Exception as error:
            # Anything the OpenAI call itself can raise (a rate limit, a timeout, a
            # content-policy refusal, ...) used to propagate straight past this function
            # as some exception type the caller's `except ValueError` never catches - an
            # unhandled 500 with nothing logged anywhere, indistinguishable from every
            # other silent failure mode this whole file exists to avoid (see
            # public_page_translate's own log_client_error call, which only ever runs for
            # a ValueError). Wrapped the same way detect_content_language/page_generator.
            # generate_page_draft already treat a failed OpenAI call elsewhere in this
            # codebase - and, being an ordinary loop iteration here, retried the same as
            # any other kind of failed attempt rather than aborting immediately.
            last_reason = f"model call failed: {error}"
            continue
        content = response.choices[0].message.content
        if not content:
            last_reason = "model returned no content"
            continue
        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            last_reason = f"model returned invalid JSON: {error}"
            continue
        if lang == "en":
            result["ui"] = dict(PAGE_UI_STRINGS)
        rejection_reason = translation_rejection_reason(result, tagline, about, price_list, faq, lang)
        if rejection_reason is None:
            return result
        last_reason = rejection_reason

    raise ValueError(f"model returned an unusable translation after {_MAX_TRANSLATE_ATTEMPTS} attempts: {last_reason}")


_UI_TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in PAGE_UI_STRINGS},
    "required": list(PAGE_UI_STRINGS.keys()),
    "additionalProperties": False,
}

_UI_TRANSLATE_SYSTEM_PROMPT = """Translate this fixed set of short website UI labels and messages into
{lang}. Every value in your response must actually be written in {lang} - never leave one
in English or copy it through unchanged.

Some values contain literal {{token}} placeholders (e.g. {{day}}, {{time}}) - copy each one
through exactly as written, in the same position, without translating its contents or the
surrounding braces.

Respond with plain text values only, no markdown formatting."""

# Minimum source-string length (after stripping) before an unchanged PAGE_UI_STRINGS value
# counts as a suspected echo in ui_translation_rejection_reason below - short entries
# ("Home", "Send", "Location", ...) can legitimately come back identical in another
# language (a loanword, or just a short word that happens to spell the same), so only
# longer phrases - where a coincidental identical translation is effectively impossible -
# are used as a reliable "the model didn't actually translate this" signal. Mirrors
# translate_page.py's own _ECHO_CHECK_MIN_LENGTH/_ECHO_SIMILARITY_THRESHOLD reasoning for
# "about", scoped down here to a plain post-strip equality check since these values are
# copied straight from the English source when they echo, not merely close to it.
_UI_ECHO_MIN_LENGTH = 10


def ui_translation_rejection_reason(result):
    """None if `result` (shaped like translate_ui_strings' own return value, or a
    previously cached ui_string_translations row rebuilt to that shape) is usable;
    otherwise a short human-readable string saying which check it failed, for
    diagnostics. Rejects a missing/empty value for any PAGE_UI_STRINGS key, and also -
    unlike the original version of this check - a value left as an unchanged copy of its
    English source for any key long enough that a coincidental identical translation is
    effectively impossible (see _UI_ECHO_MIN_LENGTH above). That second check exists
    because gpt-4o-mini was observed leaving one single key ("heading_hours": "Working
    hours") copied through untranslated for a specific language while every other key
    (including the same section's own "nav_hours") translated correctly - a missing/empty
    check alone never catches that, and since translate_ui_strings' result is cached
    forever per language (see database.upsert_ui_translation), a single bad key like that
    used to stay wrong for every business and every visitor in that language until
    someone noticed and manually fixed the cached row."""
    missing = [key for key in PAGE_UI_STRINGS if not (result.get(key) or "").strip()]
    if missing:
        return f"missing/empty keys: {missing[:5]}"
    echoed = [
        key for key, source in PAGE_UI_STRINGS.items()
        if len(source.strip()) >= _UI_ECHO_MIN_LENGTH
        and (result.get(key) or "").strip().casefold() == source.strip().casefold()
    ]
    if echoed:
        return f"keys left untranslated (identical to English source): {echoed[:5]}"
    return None


def translate_ui_strings(lang):
    """PAGE_UI_STRINGS (the page's own fixed chrome - nav labels, section headings,
    booking/chat messages, no business content at all) translated into `lang`. Kept
    entirely separate from translate_page_content: this exists specifically so
    app.py's _render_public_page can localize that chrome even when `lang` is the
    business's own content_language - which translate_page_content can't do, since its
    about-echo rejection (translation_rejection_reason) exists precisely to catch
    business prose that LOOKS untranslated, and "about" translated into the language
    it's already written in always looks exactly like that by definition. These are
    short, generic, business-agnostic strings instead - see ui_translation_rejection_reason
    above for the (missing/empty key, or unchanged-from-English) checks applied to each
    attempt. Raises ValueError the same way translate_page_content does if every attempt
    fails; the caller (app.py) is expected to fall back to plain English chrome on failure
    rather than block rendering the page over this."""
    lang_name = LANGUAGES.get(lang, {}).get("name", lang)
    messages = [
        {"role": "system", "content": _UI_TRANSLATE_SYSTEM_PROMPT.format(lang=lang_name)},
        {"role": "user", "content": json.dumps(PAGE_UI_STRINGS)},
    ]
    last_reason = "unknown failure"
    for _attempt in range(_MAX_TRANSLATE_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=2000,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "ui_string_translation", "strict": True, "schema": _UI_TRANSLATE_SCHEMA},
                },
            )
        except Exception as error:
            last_reason = f"model call failed: {error}"
            continue
        content = response.choices[0].message.content
        if not content:
            last_reason = "model returned no content"
            continue
        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            last_reason = f"model returned invalid JSON: {error}"
            continue
        rejection_reason = ui_translation_rejection_reason(result)
        if rejection_reason is None:
            return result
        last_reason = rejection_reason

    raise ValueError(f"model returned an unusable ui translation after {_MAX_TRANSLATE_ATTEMPTS} attempts: {last_reason}")
