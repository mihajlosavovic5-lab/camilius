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
import json
import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from openai import OpenAI

from generate_reply import MODEL

load_dotenv()
client = OpenAI()

MAX_DESCRIPTION_LENGTH = 1000
# A whole page (tagline + about + 7 days of hours + several services + a few FAQ pairs)
# needs more room than generate_reply.py's MAX_REPLY_TOKENS (400, tuned for one short
# chat reply) - this is a one-time generation, not a per-message cost, so the larger
# budget is worth it.
MAX_GENERATION_TOKENS = 1200

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_LABELS = {
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday", "thursday": "Thursday",
    "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday",
}

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


def generate_page_draft(description):
    """description: the owner's own short text (what the business does, hours, prices,
    anything else worth mentioning). Returns a dict matching PAGE_SCHEMA - raises
    ValueError if the model's response can't be parsed (caller decides how to surface
    that; this module has no opinion on retries or user-facing error text)."""
    description = (description or "").strip()[:MAX_DESCRIPTION_LENGTH]
    if not description:
        raise ValueError("description is required")

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": description},
        ],
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


def format_working_hours(hours):
    """hours: the {day: {"open": "HH:MM"|None, "close": "HH:MM"|None}} dict from
    generate_page_draft()'s "hours" key. Produces exactly the "Monday: 09:00-18:00" /
    "Monday: closed" wire format static/pickers.js's createHoursPicker both emits and
    parses (see its own HOURS_DAY_LABELS comment) - so a generated page's hours picker
    re-opens correctly if the owner edits them afterward, not just a string that
    booking.parse_working_hours happens to also accept."""
    lines = []
    for day in _WEEKDAYS:
        day_hours = hours.get(day) or {}
        open_t, close_t = day_hours.get("open"), day_hours.get("close")
        if open_t and close_t:
            lines.append(f"{_DAY_LABELS[day]}: {open_t}-{close_t}")
        else:
            lines.append(f"{_DAY_LABELS[day]}: closed")
    return "\n".join(lines)


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
