"""Implementations behind the "Draft this with AI" chat's write-access tools (Faza 19) -
lets the assistant actually apply what the owner asks for (the page's tagline/about,
working hours, price list, FAQ, or social links) instead of only ever talking about it.
Mirrors booking.py's own role for generate_reply.generate_reply_with_booking's tools:
website_assistant.py stays free of any database import (its own WEBSITE_TOOLS there is
just the schema/prompt side), this is where each tool's real effect happens, and
build_tool_impls() below is what api.py/app.py hand to answer_website_chat() as
tool_impls.

Every function here is scoped to the one business_id its caller already resolved from
the request's own Bearer/session auth - nothing here ever takes a business_id from the
model itself. Each returns a small {"status": "ok"|"error", "message": ...} dict, fed
back to the model as the tool result exactly like booking.py's own tool functions -
the model only ever describes what actually happened, never claims a change stuck on
its own say-so."""
import json

from database import (
    business_by_id, update_business, update_business_social_links,
    get_business_page, upsert_business_page, set_upsell_pairs, has_pro_access,
    UniqueViolation,
)
import booking

_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

TAGLINE_MAX_LENGTH = 200
ABOUT_MAX_LENGTH = 2000


def _ok():
    return {"status": "ok", "message": "Saved."}


def _error(message):
    return {"status": "error", "message": message}


def update_website_content(business_id, tagline=None, about=None):
    if tagline is None and about is None:
        return _error("Nothing to update.")
    page = get_business_page(business_id)
    if page is None or not page[2]:
        return _error(
            "This business hasn't chosen a web address yet - tell the owner to pick one "
            "on the My Site tab first (My Site > Choose a web address), then this can be saved."
        )
    upsert_business_page(
        business_id, page[2], page[3],
        tagline.strip()[:TAGLINE_MAX_LENGTH] if tagline is not None else page[4],
        about.strip()[:ABOUT_MAX_LENGTH] if about is not None else page[5],
        widget_domain=page[8], pending_custom_domain=page[11],
    )
    return _ok()


def update_working_hours(business_id, days):
    business = business_by_id(business_id)
    if business is None:
        return _error("Business not found.")
    current = booking.parse_working_hours(business[7])
    changes = {}
    for entry in (days or []):
        name = (entry.get("day") or "").strip().lower()
        if name not in _DAY_NAMES:
            continue
        weekday = _DAY_NAMES.index(name)
        if entry.get("closed"):
            changes[weekday] = None
            continue
        open_s, close_s = entry.get("open"), entry.get("close")
        if open_s and close_s:
            changes[weekday] = (open_s, close_s)
    if not changes:
        return _error("No valid day/time given - specify a day plus either open+close times or closed=true.")

    result = {}
    for weekday, name in enumerate(_DAY_NAMES):
        if weekday in changes:
            value = changes[weekday]
            result[name] = {"open": value[0], "close": value[1]} if value else None
        else:
            existing = current.get(weekday)
            result[name] = {"open": existing[0].strftime("%H:%M"), "close": existing[1].strftime("%H:%M")} if existing else None

    update_business(business_id, business[1], business[3], business[10], json.dumps(result), business[8], business[9])
    return _ok()


def update_price_list(business_id, price_list):
    business = business_by_id(business_id)
    if business is None:
        return _error("Business not found.")
    update_business(business_id, business[1], business[3], business[10], business[7], price_list, business[9])
    return _ok()


def update_faq(business_id, faq):
    business = business_by_id(business_id)
    if business is None:
        return _error("Business not found.")
    update_business(business_id, business[1], business[3], business[10], business[7], business[8], faq)
    return _ok()


def update_business_profile(business_id, name=None, contact_email=None, phone_number=None):
    if name is None and contact_email is None and phone_number is None:
        return _error("Nothing to update.")
    business = business_by_id(business_id)
    if business is None:
        return _error("Business not found.")
    new_contact_email = contact_email.strip() if contact_email is not None else business[3]
    new_phone_number = (phone_number.strip() or None) if phone_number is not None else business[10]
    try:
        update_business(
            business_id, name.strip() if name is not None else business[1],
            new_contact_email, new_phone_number, business[7], business[8], business[9],
        )
    except UniqueViolation:
        return _error(
            "That contact email or phone number is already used by another account on "
            "this app - ask the owner for a different one."
        )
    return _ok()


def update_related_services(business_id, pairs):
    # Pro-only feature, same gate as the website's own /settings route (api.py's
    # api_update_settings) - deferred import since app.py itself imports this whole
    # module at load time, so a top-level import here would be circular.
    from app import BETA_FREE_MODE
    if not (BETA_FREE_MODE or has_pro_access(business_id)):
        return _error(
            "Related services (AI upsell suggestions) is a Pro feature - tell the owner "
            "they'd need to upgrade their plan to use it."
        )
    set_upsell_pairs(business_id, pairs)
    return _ok()


def update_social_links(business_id, instagram=None, facebook=None, x=None, tiktok=None, youtube=None):
    business = business_by_id(business_id)
    if business is None:
        return _error("Business not found.")
    update_business_social_links(
        business_id,
        instagram if instagram is not None else business[24],
        facebook if facebook is not None else business[25],
        x if x is not None else business[26],
        tiktok if tiktok is not None else business[27],
        youtube if youtube is not None else business[28],
    )
    return _ok()


def build_tool_impls(business_id):
    """{tool_name: callable(**kwargs) -> dict}, matching every WEBSITE_TOOLS entry in
    website_assistant.py - the shape answer_website_chat() expects for its tool_impls
    argument, one closure per tool pinning it to this one business_id."""
    return {
        "update_website_content": lambda **kwargs: update_website_content(business_id, **kwargs),
        "update_working_hours": lambda **kwargs: update_working_hours(business_id, **kwargs),
        "update_price_list": lambda **kwargs: update_price_list(business_id, **kwargs),
        "update_faq": lambda **kwargs: update_faq(business_id, **kwargs),
        "update_business_profile": lambda **kwargs: update_business_profile(business_id, **kwargs),
        "update_related_services": lambda **kwargs: update_related_services(business_id, **kwargs),
        "update_social_links": lambda **kwargs: update_social_links(business_id, **kwargs),
    }
