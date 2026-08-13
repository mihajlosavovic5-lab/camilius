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
    "nav_book": "Book",
    "nav_gallery": "Gallery",
    "nav_photos": "Photos",
    "nav_prices": "Prices",
    "nav_questions": "Questions",
    "nav_contact": "Contact",
    "nav_location": "Location",
    "heading_about": "About",
    "heading_book": "Book an appointment",
    "heading_gallery": "Gallery",
    "heading_photos": "Photos",
    "heading_prices": "Prices",
    "heading_questions": "Questions",
    "heading_contact": "Contact",
    "heading_location": "Location",
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

For "ui", translate every value in that object into {lang} the same way. Some values
contain literal {{token}} placeholders (e.g. {{day}}, {{time}}, {{business}}) - copy each
one through exactly as written, in the same position, without translating its contents
or the surrounding braces.

Respond with plain text values only, no markdown formatting."""


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


def translation_is_usable(result, tagline, about, price_list, faq, lang=None, source_lang=None):
    """False if `result` (shaped like translate_page_content's own return value, or the
    same shape rebuilt from a business_page_translations row - see app.py's
    public_page_translate) dropped any field that actually had source content, came back
    with an empty string for one of PAGE_UI_STRINGS' own always-non-empty keys, or (when
    `lang`/`source_lang` are given and actually differ) left "about" byte-for-byte
    identical to the source - free-form prose essentially never survives translation
    between two different languages completely unchanged, so an unchanged "about" there
    means the model echoed the source back instead of translating it. That check is
    skipped when lang == source_lang (e.g. a visitor picking the flag that already
    matches this business's own content) since an unchanged "about" there is the correct
    result, not a failure - and skipped entirely when either isn't given, since callers
    that don't know the source language can't tell the two cases apart.

    Deliberately NOT applied to tagline/price_list/faq, even though the exact same echo
    bug reproduced for price_list once (see git history) - a short tagline, a price line,
    or a bare FAQ answer routinely contains loanwords ("Espresso", "Cappuccino"), numbers,
    or currency codes that a CORRECT translation leaves completely unchanged too, so this
    same check applied to those fields rejected genuinely good translations as false
    "echoes" - for a business whose price list happens to be loanword-heavy, EVERY
    language failed this way, every time, which is worse than the bug it was meant to
    catch. "about" is the one field long/free-form enough that an exact cross-language
    match is still a reliable failure signal.

    Nothing here is a translation *quality* check otherwise (a bad but genuinely
    different-looking translation still passes) - only a check that this result is safe
    to show a visitor or cache for the next one, since public_page.html's
    applyTranslation() only ever gets to see the field, not the source it came from, and
    skips a field entirely rather than let a bad value overwrite real on-screen content
    (see its own comment). A response that slipped past this once and got cached would
    otherwise keep failing the exact same way forever - every later visitor hitting the
    cache, never a fresh model call again - so both translate_page_content below (before
    caching a fresh result) and public_page_translate (before trusting an already-cached
    row) call this."""
    for field, source in (("tagline", tagline), ("about", about), ("price_list", price_list), ("faq", faq)):
        if (source or "").strip() and not (result.get(field) or "").strip():
            return False
    ui = result.get("ui") or {}
    if not all((ui.get(key) or "").strip() for key in PAGE_UI_STRINGS):
        return False
    if lang and source_lang and lang != source_lang:
        about_stripped = (about or "").strip()
        if len(about_stripped) >= _ECHO_CHECK_MIN_LENGTH and (result.get("about") or "").strip() == about_stripped:
            return False
    return True


def translate_page_content(lang, tagline, about, price_list, faq, source_lang=None):
    """One OpenAI call translating a business's tagline/about/price_list/faq plus the
    page's own fixed PAGE_UI_STRINGS into `lang`, all together so they read as one
    consistent voice. Returns a dict shaped like {"tagline", "about", "price_list",
    "faq", "ui": {...same keys as PAGE_UI_STRINGS}}. Raises ValueError the same way
    page_generator.generate_page_draft() does if the model call fails, returns something
    unparseable, or returns something translation_is_usable() rejects (e.g. cut off
    mid-response and missing a field that should have real content, or echoing the source
    language straight back instead of translating it - see that function's own docstring)
    - the caller (app.py's public_page_translate route) decides how to surface that;
    nothing here is worth blocking the page itself over, since a visitor just keeps
    seeing the untranslated original on failure. source_lang is this business's own
    detected content_language (see app.py's public_page()), passed through only so
    translation_is_usable can tell a legitimate same-language no-op from an echoed
    failure - never sent to the model itself."""
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

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=MAX_TRANSLATE_TOKENS,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "page_translation", "strict": True, "schema": _TRANSLATE_SCHEMA},
        },
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("model returned no content")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"model returned invalid JSON: {error}") from error
    if not translation_is_usable(result, tagline, about, price_list, faq, lang=lang, source_lang=source_lang):
        raise ValueError("model returned a translation missing content the source actually has, or echoed it back untranslated")
    return result
