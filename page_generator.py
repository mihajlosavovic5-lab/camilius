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
        "about": {"type": "string", "description": "2-4 warm, concrete sentences about the business - no filler, no made-up history."},
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
                    "price": {"type": "string", "description": "Price as given, e.g. '20 EUR' - empty string if not mentioned."},
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
    },
    "required": ["tagline", "about", "hours", "services", "faq"],
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
- faq answers should sound like the business owner speaking, not a legal disclaimer.
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
    """services: the generate_page_draft() "services" list. One "Name: Price" per line -
    price_list has no parser to match (it's free text the AI itself reads, same as faq),
    so this is a readable convention, not a hard contract."""
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
