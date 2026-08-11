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
MAX_TRANSLATE_TOKENS = 2000

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
    "nav_prices": "Prices",
    "nav_questions": "Questions",
    "nav_contact": "Contact",
    "heading_about": "About",
    "heading_book": "Book an appointment",
    "heading_gallery": "Gallery",
    "heading_prices": "Prices",
    "heading_questions": "Questions",
    "heading_contact": "Contact",
    "book_loading": "Loading open times...",
    "book_empty": "No upcoming openings right now - message us in chat to check.",
    "book_error": "Couldn't load open times - please try again shortly.",
    "book_confirm": "Requested {day} at {time} - continue in the chat to confirm.",
    "chat_subtitle": "Usually replies in seconds",
    "chat_placeholder": "Ask a question or book a time...",
    "chat_send": "Send",
    "chat_greeting": "Hi! Ask me anything about {business}, or book a time that works for you.",
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


def translate_page_content(lang, tagline, about, price_list, faq):
    """One OpenAI call translating a business's tagline/about/price_list/faq plus the
    page's own fixed PAGE_UI_STRINGS into `lang`, all together so they read as one
    consistent voice. Returns a dict shaped like {"tagline", "about", "price_list",
    "faq", "ui": {...same keys as PAGE_UI_STRINGS}}. Raises ValueError the same way
    page_generator.generate_page_draft() does if the model call fails or returns
    something unparseable - the caller (app.py's public_page_translate route) decides
    how to surface that; nothing here is worth blocking the page itself over, since a
    visitor just keeps seeing the untranslated original on failure."""
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
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"model returned invalid JSON: {error}") from error
