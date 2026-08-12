"""Tests for website_assistant.answer_website_chat's tool-calling loop (Faza 19) - mocks
the OpenAI client itself (same boundary test_page_generator.py/test_ai_theme.py mock at)
to verify a model response with tool_calls actually invokes the matching tool_impls
callable, feeds its result back, and returns the follow-up call's text - not whether the
real model decides to call a tool (that's OpenAI's own behavior, not ours to test)."""
import json
from types import SimpleNamespace

import website_assistant


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _response(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_answer_website_chat_executes_a_tool_call_and_returns_follow_up_text(monkeypatch):
    tool_message = SimpleNamespace(
        content=None,
        tool_calls=[_tool_call("call_1", "update_price_list", {"price_list": "Cut: 100"})],
    )
    follow_up_message = SimpleNamespace(content="Done - I've updated your price list.", tool_calls=None)
    calls = []

    def fake_create(model, messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        if len(calls) == 1:
            return _response(tool_message)
        return _response(follow_up_message)

    monkeypatch.setattr(website_assistant.client.chat.completions, "create", fake_create)

    received_args = {}

    def fake_update_price_list(**kwargs):
        received_args.update(kwargs)
        return {"status": "ok", "message": "Saved."}

    reply = website_assistant.answer_website_chat(
        "Change my price list to Cut: 100",
        tool_impls={"update_price_list": fake_update_price_list},
    )

    assert reply == "Done - I've updated your price list."
    assert received_args == {"price_list": "Cut: 100"}
    assert len(calls) == 2
    # First call offered the tools; the tool result was fed back as a "tool" message
    # before the follow-up call.
    assert calls[0]["kwargs"]["tools"] == website_assistant.WEBSITE_TOOLS
    tool_result_messages = [m for m in calls[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_result_messages) == 1
    assert json.loads(tool_result_messages[0]["content"]) == {"status": "ok", "message": "Saved."}


def test_answer_website_chat_skips_unknown_tool_calls_gracefully(monkeypatch):
    tool_message = SimpleNamespace(
        content=None,
        tool_calls=[_tool_call("call_1", "not_a_real_tool", {})],
    )
    follow_up_message = SimpleNamespace(content="Okay.", tool_calls=None)
    calls = []

    def fake_create(model, messages, **kwargs):
        calls.append(messages)
        return _response(tool_message) if len(calls) == 1 else _response(follow_up_message)

    monkeypatch.setattr(website_assistant.client.chat.completions, "create", fake_create)

    reply = website_assistant.answer_website_chat(
        "do something", tool_impls={"update_price_list": lambda **kwargs: {"status": "ok"}},
    )

    assert reply == "Okay."


def test_answer_website_chat_without_tool_impls_never_offers_tools(monkeypatch):
    plain_message = SimpleNamespace(content="Sure, tell me more about your business.", tool_calls=None)
    captured_kwargs = {}

    def fake_create(model, messages, **kwargs):
        captured_kwargs.update(kwargs)
        return _response(plain_message)

    monkeypatch.setattr(website_assistant.client.chat.completions, "create", fake_create)

    reply = website_assistant.answer_website_chat("hello")

    assert reply == "Sure, tell me more about your business."
    assert "tools" not in captured_kwargs
