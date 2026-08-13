"""Generates a first draft of a business's knowledge base (working_hours/price_list/faq)
and public-page presentation fields (tagline/about) from a short description the owner
types once during page setup. DB-agnostic, same separation as generate_reply.py - this
module never touches the database; the caller (app.py) persists the result.

The key design choice: this writes back into the SAME columns generate_reply.py's system
prompt and booking.py already read (working_hours/price_list/faq), rather than storing
generated content in a separate place. That keeps exactly one knowledge base in the
product - a business editing it later (in the existing Profile form) updates both what
the AI answers with and what the public page shows, in one place, never two copies that
can silently drift apart.
"""
import io
import json
import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from openai import OpenAI

from generate_reply import MODEL

load_dotenv()
client = OpenAI()

MAX_DESCRIPTION_LENGTH = 1000

# Second line of defense on top of document_extract.MAX_EXTRACTED_TEXT_LENGTH (which
# already caps what gets stored per document) - bounds what a single generate_page_draft()
# call sends even if a caller hands this multiple documents' text concatenated together.
MAX_DOCUMENT_TEXT_LENGTH = 8000

# Caps how large an uploaded voice recording this will bother sending to Whisper -
# app.py's own upload route enforces the same ceiling before this is ever called (so a
# huge file never even reaches here), this is a second line of defense for any other
# caller. ~6MB comfortably covers a 60-90s compressed recording (webm/opus, m4a, etc.)
# with room to spare, while keeping a single voice note's OpenAI cost bounded.
MAX_VOICE_AUDIO_BYTES = 6 * 1024 * 1024

# A whole page (tagline + about + 7 days of hours + several services + a few FAQ pairs)
# needs more room than generate_reply.py's MAX_REPLY_TOKENS (400, tuned for one short
# chat reply) - this is a one-time generation, not a per-message cost, so the larger
# budget is worth it.
MAX_GENERATION_TOKENS = 1200

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DAY_SCHEMA = {
    "type": "object",
    "properties": {
        "open": {"type": ["string", "null"], "description": "Opening time as HH:MM (24h), or null if closed this day."},
        "close": {"type": ["string", "null"], "description": "Closing time as HH:MM (24h), or null if closed this day."},
    },
    "required": ["open", "close"],
    "additionalProperties": False,
}

PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {"type": "string", "description": "One short, appealing sentence a customer sees first (under 80 characters)."},
        "about": {
            "type": "string",
            "description": (
                "3-6 warm, concrete sentences about the business - no filler, no made-up "
                "history. Give it real visual structure instead of one flat paragraph, using "
                "this formatting syntax (see SYSTEM_PROMPT for the full rules): blank lines "
                "between distinct ideas, '# text' for an occasional section heading, '- text' "
                "(repeated) for a list of 3+ concrete offerings/features - always introduced by "
                "a short lead-in line right above it, e.g. 'Kod nas mozete ocekivati:' - "
                "'**text**' for a key phrase, '*text*' for a softer aside, and optionally one "
                "'> text' pull-quote line or one '-- text' closing signature line. Only use a "
                "heading/list/quote/signature where the content genuinely calls for it."
            ),
        },
        "slug": {"type": "string", "description": "A short, URL-friendly suggested web address for this business's page, e.g. 'blue-star-cafe' - lowercase ASCII letters, digits, and hyphens only, transliterating any non-Latin script or accented letters to plain ASCII. 2-4 words, based on the business's name or what it does."},
        "hours": {
            "type": "object",
            "properties": {day: _DAY_SCHEMA for day in _WEEKDAYS},
            "required": _WEEKDAYS,
            "additionalProperties": False,
        },
        "services": {
            "type": "array",
            "description": "Services/products explicitly mentioned in the description, with their price if given.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price": {
                        "type": "string",
                        "description": (
                            "Normalized as '<number> <CODE>', e.g. '20 EUR' or '1500 RSD' - "
                            "CODE is a real 3-letter currency code (RSD, EUR, USD, GBP, CHF, "
                            "CAD, AUD, JPY, CNY, TRY, RUB, SEK, NOK, DKK, PLN, AED, INR, BRL), "
                            "the SAME code for every service, never a word like 'din'/'dinara' "
                            "or a symbol like '€'/'$' even if that's how the owner wrote it - "
                            "the app strips this exact code off each line to preselect the "
                            "currency shown above the whole price list, so a non-code suffix "
                            "leaves that selector wrong and the raw word stuck in the price "
                            "field instead. Infer the code from the description's own wording "
                            "or language (e.g. Serbian dinars/'din' -> RSD) when no code is "
                            "explicit. Empty string if no price was mentioned for this service."
                        ),
                    },
                },
                "required": ["name", "price"],
                "additionalProperties": False,
            },
        },
        "faq": {
            "type": "array",
            "description": "A handful of short, genuinely useful Q&A pairs a customer would actually ask - only from what's implied by the description, never invented specifics.",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["question", "answer"],
                "additionalProperties": False,
            },
        },
        "booking_relevant": {
            "type": "boolean",
            "description": (
                "True only if a customer of this specific business would plausibly want to "
                "reserve a specific time slot in advance - a hair salon, dentist, repair shop, "
                "consultant, personal trainer, tattoo studio, and the like. False for a business "
                "that just sells goods/products to walk-in or online customers with nothing to "
                "schedule - a cafe, bakery, coffee shop, convenience store, or retail shop - "
                "unless the description itself explicitly mentions taking reservations/booking a "
                "table/appointments."
            ),
        },
    },
    "required": ["tagline", "about", "slug", "hours", "services", "faq", "booking_relevant"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You turn a business owner's short, informal description of their business into a
structured first draft of their public page and AI knowledge base.

Rules:
- Only use information stated or clearly implied in the description. Never invent specific
  prices, hours, addresses, or services that weren't mentioned - leave hours closed (open/close
  both null) for any day not implied as open, leave services/faq empty rather than guessing.
- If the description gives a general sense of hours (e.g. "typical business hours",
  "open weekdays") without exact times, use a reasonable default (09:00-18:00 Monday-Friday,
  closed weekends) rather than leaving every day closed - this is meant to be a usable starting
  draft the owner reviews and edits, not a page with no hours at all.
- Write in the same language as the description.
- tagline and about are customer-facing marketing copy: warm and specific, never generic
  filler like "we provide quality service to every customer".
- about's renderer understands a small formatting syntax - use it to give the text real
  structure instead of one dense paragraph, but only where it genuinely fits:
    - A blank line starts a new paragraph.
    - "# text" on its own line is a section heading (e.g. "# Our story") - use at most
      one or two, only when there's an actual sub-topic to introduce, never for two
      sentences that already read fine as plain prose.
    - Consecutive lines each starting "- " become a bullet list - reach for this when
      there are 3+ concrete things to list (offerings, features, specialties), not for
      one or two. Never drop a list straight into the text with nothing above it - the
      line directly before it (no blank line in between) must be a short lead-in that
      introduces it, e.g. "Kod nas mozete ocekivati:" / "What we offer:", normally
      ending in a colon.
    - "**text**" bolds a key phrase (a standout feature, the business name the first
      time it's said); "*text*" italicizes a softer aside.
    - A single ">  text" line renders as a highlighted pull-quote - at most one, for a
      genuine standout line (a motto, a promise), never a paraphrase of the paragraph
      right above it.
    - A single "-- text" line at the very end renders right-aligned as a closing
      signature (e.g. "-- The Aroma team") - optional, only when it reads naturally.
  Every about text should still make sense as plain reading text with these markers
  removed - they're a presentation layer, not a substitute for real sentences.
- faq answers should sound like the business owner speaking, not a legal disclaimer.
- slug is not customer-facing copy - always fill it in, in plain ASCII (lowercase
  letters/digits/hyphens only), even when the description itself is in another language
  or script.
- Every service price must be normalized to "<number> <3-letter currency code>" (see the
  services.price field's own schema description for the exact rule) - never leave a
  currency word or symbol the owner typed (e.g. "din", "dinara", "€") in place of the code.
- booking_relevant: judge for yourself whether this specific business's customers would
  actually want to book a time slot, per that field's own schema description - don't
  default to true just because hours were mentioned; every business has hours.
"""


def generate_page_draft(description, document_text=None):
    """description: the owner's own short text (what the business does, hours, prices,
    anything else worth mentioning). document_text: optional text already extracted from
    an uploaded document (PDF/Word - e.g. a menu or price list, see document_extract.py) -
    sent as a second, clearly-labeled source the model must treat as ground truth rather
    than blend or override with guesswork, on top of (not instead of) description. Returns
    a dict matching PAGE_SCHEMA - raises ValueError if the model's response can't be
    parsed (caller decides how to surface that; this module has no opinion on retries or
    user-facing error text)."""
    description = (description or "").strip()[:MAX_DESCRIPTION_LENGTH]
    if not description:
        raise ValueError("description is required")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": description},
    ]

    document_text = (document_text or "").strip()[:MAX_DOCUMENT_TEXT_LENGTH]
    if document_text:
        messages.append({
            "role": "user",
            "content": (
                "Reference material extracted from a document the owner uploaded (e.g. a "
                "menu, price list, or brochure). Treat every price, hour, and service in "
                "it as ground truth - never alter or invent figures that contradict it, "
                "and prefer it over guesswork whenever the description above doesn't "
                "cover something it does:\n\n" + document_text
            ),
        })

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=MAX_GENERATION_TOKENS,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "page_draft", "strict": True, "schema": PAGE_SCHEMA},
        },
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("model returned no content")
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"model returned invalid JSON: {error}") from error


_BOOKING_RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {"booking_relevant": PAGE_SCHEMA["properties"]["booking_relevant"]},
    "required": ["booking_relevant"],
    "additionalProperties": False,
}

_BOOKING_RELEVANCE_SYSTEM_PROMPT = """You judge whether a business's customers would plausibly want to book a
specific time slot in advance, from its name and whatever it sells/offers below.

True only for businesses where reserving a specific time makes sense - a hair salon, dentist,
repair shop, consultant, personal trainer, tattoo studio, and the like.

False for a business that just sells goods/products to walk-in or online customers with nothing
to schedule - a cafe, bakery, coffee shop, convenience store, or retail shop - unless the text
itself explicitly mentions taking reservations/booking a table/appointments.

When genuinely unsure, answer True - a business that never uses the resulting booking feature
loses nothing, while one that needed it and didn't get it would."""


def classify_booking_relevance(business_name, price_list, faq):
    """Backfill path for a business that never went through generate_page_draft() above
    (predates this feature, or was created some other way) - classifies from whatever
    profile text already exists in the database instead of the original onboarding
    description, which isn't kept once a page draft is generated. Called from
    process_emails.classify_pending_booking_relevance_if_needed, a background worker loop
    that persists the result via database.set_booking_relevant and never re-classifies a
    business afterward - never called live off the back of a particular page view or chat
    message. Defaults to True (booking shown) on any empty input or model/parsing failure -
    the safer of the two wrong guesses, since a business that doesn't need booking simply
    never uses it, while one that does and doesn't get it is actually missing something."""
    text = "\n".join(filter(None, [
        f"Business name: {business_name}" if business_name else None,
        f"Price list:\n{price_list}" if price_list else None,
        f"FAQ:\n{faq}" if faq else None,
    ])).strip()[:MAX_DESCRIPTION_LENGTH]
    if not text:
        return True

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _BOOKING_RELEVANCE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=20,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "booking_relevance", "strict": True, "schema": _BOOKING_RELEVANCE_SCHEMA},
            },
        )
        content = response.choices[0].message.content
        return bool(json.loads(content).get("booking_relevant", True))
    except Exception:
        return True


def transcribe_voice_description(audio_bytes, filename, content_type=None):
    """Speech-to-text for the onboarding "describe your business" step, via the same
    OpenAI client as generate_page_draft() above - lets an owner speak their description
    instead of typing it, transcribed text then flows through the exact same
    generate_page_draft() call (this function has no opinion on that, it only turns audio
    into text). audio_bytes is held in memory for the duration of this call only - never
    written to disk or persisted anywhere; the caller (app.py) discards it right after
    this returns and keeps only the resulting text, same as it would for typed input.

    No language is passed - it auto-detects the spoken language from the audio itself, so
    an owner can speak any language regardless of what language the site's UI happens to be
    set to. gpt-4o-transcribe (rather than the older whisper-1) both transcribes and
    auto-detects language more reliably, particularly for closely-related languages
    whisper-1 tended to confuse on short recordings (e.g. Czech misheard as Serbian).

    Raises ValueError if the recording is missing/too large/unintelligible - caller
    decides how to surface that, same contract as generate_page_draft()."""
    if not audio_bytes:
        raise ValueError("audio is required")
    if len(audio_bytes) > MAX_VOICE_AUDIO_BYTES:
        raise ValueError("audio file is too large")

    try:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=(filename or "recording.webm", io.BytesIO(audio_bytes), content_type or "application/octet-stream"),
        )
    except Exception as error:
        raise ValueError(f"transcription failed: {error}") from error

    text = (transcript.text or "").strip()
    if not text:
        raise ValueError("couldn't make out any speech in that recording")
    return text[:MAX_DESCRIPTION_LENGTH]


def format_working_hours(hours):
    """hours: the {day: {"open": "HH:MM"|None, "close": "HH:MM"|None}} dict from
    generate_page_draft()'s "hours" key. Produces exactly the JSON wire format
    static/hours-picker.js's shared picker core both emits and parses (see its own
    getValue/setValue) - so a generated page's hours picker re-opens correctly if the
    owner edits them afterward, not just a string that booking.parse_working_hours
    happens to also accept."""
    result = {}
    for day in _WEEKDAYS:
        day_hours = hours.get(day) or {}
        open_t, close_t = day_hours.get("open"), day_hours.get("close")
        result[day] = {"open": open_t, "close": close_t} if open_t and close_t else None
    return json.dumps(result)


def format_price_list(services):
    """services: the generate_page_draft() "services" list. One "Name: Price" per line,
    Price already normalized to "<number> <CODE>" by the model per PAGE_SCHEMA's own
    description - static/pickers.js's and mobile/app.js's price-list picker both parse
    that exact "<number> <CODE>" shape back out (stripping CODE into the currency
    selector shown above the list), so this is a hard contract now, not just a readable
    convention."""
    lines = []
    for service in services:
        name = (service.get("name") or "").strip()
        if not name:
            continue
        price = (service.get("price") or "").strip()
        lines.append(f"{name}: {price}" if price else name)
    return "\n".join(lines)


def format_faq(faq):
    """faq: the generate_page_draft() "faq" list. Question on its own line, answer on
    the next, a blank line between pairs - readable in the Profile form's plain textarea
    and unambiguous for generate_reply.py's system prompt to read back."""
    blocks = []
    for pair in faq:
        question = (pair.get("question") or "").strip()
        answer = (pair.get("answer") or "").strip()
        if question and answer:
            blocks.append(f"{question}\n{answer}")
    return "\n\n".join(blocks)
