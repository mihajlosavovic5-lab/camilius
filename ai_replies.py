"""Shared inbound-message pipeline for every channel that produces an AI reply -
Instagram, WhatsApp, and Messenger webhooks today, and the public page/widget chat from
the hosted-page pivot onward. Each caller still does whatever's genuinely
channel-specific (looking a business up by its own id column, resolving a sender's
display name, and actually delivering a reply) and hands off here for the part that
was previously copy-pasted three times: gating, history, the AI call (including the
booking tools), saving the draft, dedup, auto-send, and the owner push.

resolve_sender and deliver are injected callables rather than this module importing any
channel's own functions directly - the same separation generate_reply.py already uses
for tool_impls, and booking.py for its DB calls. resolve_sender in particular matters
for cost, not just decoupling: Instagram's version calls a real API (get_sender_info)
to resolve a display name/picture, and the original per-channel code only ever paid for
that AFTER the subscription/daily-limit/dedup gates passed. Making it a callback invoked
here, at the same point in the sequence, keeps that ordering exactly - a skipped message
never triggers the API call.
"""
from database import (
    effective_subscription_active, replies_today_count, already_processed_messages,
    previous_conversations_with, has_pro_access, get_slot_duration, get_upsell_pairs,
    save_draft_reply, mark_message_processed, is_auto_send_enabled, mark_sent,
    log_client_error,
)
from generate_reply import generate_reply, generate_reply_with_booking
from push_notifications import send_push_to_business
import booking

DAILY_REPLY_LIMIT = 50

# save_draft_reply stores each channel's sender id under its own column/kwarg
# (sender_igsid/sender_wa_id/sender_psid) rather than one generic one - see
# database.py's own comment on conversations.channel for why. This maps the channel
# string to that kwarg so callers don't each have to know it. "web" (the public page's
# chat) reuses sender_igsid rather than getting a fourth column of its own - nothing
# reads that column as "must be an Instagram id" (send_channel_reply in app.py switches
# on the channel column first, not on which sender-id column is populated), and reusing
# it is what lets database.page_chat_session_messages_today() count a visitor session's
# own messages without a schema change.
_SENDER_KEY_KWARG = {"instagram": "sender_igsid", "whatsapp": "sender_wa_id", "messenger": "sender_psid", "web": "sender_igsid"}


def handle_incoming_message(business, business_id, channel, message_id, text, resolve_sender, deliver, log_prefix=None):
    """Runs one inbound customer message through gating, the AI, and delivery.

    business/business_id: the already-looked-up business row (lookup is channel-specific,
        done by the caller before this is called).
    channel: "instagram" | "whatsapp" | "messenger" | "web" - selects the prompt wording
        (generate_reply.py), the save_draft_reply column, and log line prefixes.
    message_id: the channel's own id for this message, used for dedup - Meta redelivers
        on retry, and this is what stops a duplicate reply. None if the channel has
        nothing to dedup on.
    resolve_sender: callable() -> (sender_key, sender_display, profile_pic_url). Called
        exactly once, only if the message passes every gate below - see module docstring
        for why that ordering matters. sender_key is the channel's own opaque id for this
        customer (IGSID/WA id/PSID/web session key): the identity history and the
        booking tools key off. sender_display is what a human sees (falls back to
        sender_key itself when no display name is available - not this function's
        concern, each resolve_sender already handles its own fallback).
    deliver: callable(reply_text) -> None, called only when auto-send is on for this
        business (always true for channel == "web" - see the auto_sent block below).
        Must raise on failure - caught broadly here (not narrowed to e.g.
        requests.RequestException, since a future channel's deliver won't necessarily
        raise that) and treated as "leave the draft pending", never as a reason to
        abort the rest of this function.

    Returns None if the message was skipped for a gating reason (inactive subscription,
    daily limit, already processed) - callers that need to distinguish "skipped" from
    "handled" check for that. Otherwise a dict with new_conversation_id, sender_display,
    auto_sent, and reply_text, for anything the caller wants beyond the owner push
    already sent here.
    """
    from app import BETA_FREE_MODE  # deferred: avoid a circular import at module load, same as reminders.py

    prefix = log_prefix or f"[{channel}]"

    if not BETA_FREE_MODE and not effective_subscription_active(business_id):
        print(f"{prefix} business_id={business_id} subscription not active - skipping", flush=True)
        return None
    if replies_today_count(business_id) >= DAILY_REPLY_LIMIT:
        print(f"{prefix} business_id={business_id} daily limit reached - skipping", flush=True)
        return None
    if message_id and already_processed_messages(business_id, [message_id]):
        print(f"{prefix} message_id={message_id} already processed - skipping (dedup)", flush=True)
        return None

    sender_key, sender_display, profile_pic_url = resolve_sender()

    business_info = {
        "business_name": business[1],
        "working_hours": business[8],
        "price_list": business[9],
        "faq": business[10],
    }

    print(f"{prefix} Generating AI reply for text={text!r} from sender={sender_key!r}", flush=True)
    history = previous_conversations_with(business_id, sender_display)

    # Faza 8/9: Pro-plan (or trialing) businesses get a smarter AI that can check the
    # Schedule for a free slot and book, cancel, or reschedule an appointment directly
    # during the conversation - see generate_reply.generate_reply_with_booking/booking.py.
    if BETA_FREE_MODE or has_pro_access(business_id):
        slot_minutes = get_slot_duration(business_id)
        upsell_pairs_text = get_upsell_pairs(business_id)
        # The business's own clock, not the server's - this string is what the model
        # resolves "tomorrow" / "this Friday" against before calling a booking tool.
        now_local_display = booking.now_local(booking.tz_for(business)).strftime("%A, %Y-%m-%d %H:%M")

        def book_tool(requested_datetime, service=None):
            return booking.attempt_ai_booking(
                business, business_id, slot_minutes, requested_datetime, sender_display, sender_key, service, upsell_pairs_text,
            )

        def cancel_tool():
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_key, None, upsell_pairs_text,
            )

        def reschedule_tool(new_requested_datetime):
            return booking.attempt_ai_cancel_or_reschedule(
                business, business_id, slot_minutes, sender_key, new_requested_datetime, upsell_pairs_text,
            )

        tool_impls = {
            "check_and_book_appointment": book_tool,
            "cancel_appointment": cancel_tool,
            "reschedule_appointment": reschedule_tool,
        }
        suggested_reply = generate_reply_with_booking(text, business_info, history, now_local_display, tool_impls, channel)
    else:
        suggested_reply = generate_reply(text, business_info, history=history, channel=channel)
    print(f"{prefix} AI reply generated: {suggested_reply!r}", flush=True)
    # Mirrored to /admin/client-errors alongside Railway's own (unreliable) log viewer -
    # originally added just for Instagram to chase down a "None" reply bug (now fixed by
    # generate_reply.py's own empty-content guard), kept as a standing diagnostic and now
    # covering every channel since unification, not just the one it started on.
    log_client_error(business_id, f"AI reply generated: {suggested_reply!r} for text={text!r}", "", "ai-reply-debug", "")

    save_kwargs = {}
    sender_kwarg = _SENDER_KEY_KWARG.get(channel)
    if sender_kwarg:
        save_kwargs[sender_kwarg] = sender_key
    new_conversation_id = save_draft_reply(
        business_id, sender_display, text, suggested_reply, channel=channel,
        profile_pic_url=profile_pic_url, **save_kwargs,
    )
    print(f"{prefix} Draft saved as conversation_id={new_conversation_id}", flush=True)
    if message_id:
        mark_message_processed(business_id, message_id)

    # Opt-out (default on, see database.py's businesses.auto_send migration) - the AI's
    # draft goes straight out unless a business turns this off in Settings, in which
    # case it waits for manual approval instead. Falls back to a normal pending draft if
    # delivery itself fails (most likely Meta's 24h reply window), same as the
    # dashboard's own manual send route.
    #
    # Web chat always bypasses that setting: a page visitor is watching a live spinner
    # for `deliver`'s return value (see public_page_chat() in app.py, whose `deliver` is
    # a no-op - the HTTP response itself is the delivery), and there's no such thing as
    # "leave it pending for manual approval" for someone who is mid-conversation right
    # now. auto_send off only ever meant "don't auto-reply on Instagram/WhatsApp/
    # Messenger without a human checking first" - it was never a statement about the page.
    auto_sent = False
    if channel == "web" or is_auto_send_enabled(business_id):
        try:
            deliver(suggested_reply)
            mark_sent(new_conversation_id, suggested_reply)
            auto_sent = True
            print(f"{prefix} Auto-sent reply for conversation_id={new_conversation_id}", flush=True)
        except Exception as error:
            print(f"{prefix} Auto-send failed for conversation_id={new_conversation_id}, left as pending: {error}", flush=True)

    try:
        send_push_to_business(
            business_id,
            title="Auto-replied to a customer" if auto_sent else "New customer question",
            body=f"{sender_display}: {text[:80]}",
            data={"conversation_id": str(new_conversation_id)},
        )
    except Exception as error:
        # A push failure must never block draft processing
        print(f"{prefix} Push notification failed: {error}", flush=True)

    return {
        "new_conversation_id": new_conversation_id, "sender_display": sender_display, "auto_sent": auto_sent,
        # Meta channels' `deliver` already sent this through their own API - only
        # public_page_chat() (app.py) needs the text itself back, to put in its HTTP
        # response for the widget to show.
        "reply_text": suggested_reply,
    }
