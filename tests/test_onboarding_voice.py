"""Tests for the onboarding voice-input feature: page_generator.transcribe_voice_description
(unit, OpenAI client monkeypatched - same "never make a real API call" rule as
test_page_generator.py) and the /api/onboarding/voice-transcribe route that wraps it
(session+CSRF-authenticated, same as every other onboarding_website_* route)."""
import io

import pytest

import page_generator
from conftest import register_business_web, web_post, get_csrf_token
from tests.test_page_generator import VALID_DRAFT, _FakeResponse


class _FakeTranscript:
    def __init__(self, text):
        self.text = text


def _mock_transcription(monkeypatch, text="A cozy cafe downtown, open 8 to 22, coffee and cakes."):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeTranscript(text)

    monkeypatch.setattr(page_generator.client.audio.transcriptions, "create", fake_create)
    return captured


# --- page_generator.transcribe_voice_description (unit) ---

def test_transcribe_voice_description_returns_stripped_text(monkeypatch):
    _mock_transcription(monkeypatch, text="  A barbershop open weekdays.  ")
    result = page_generator.transcribe_voice_description(b"fake-audio-bytes", "recording.webm", "audio/webm")
    assert result == "A barbershop open weekdays."


def test_transcribe_voice_description_passes_filename_and_content_type(monkeypatch):
    captured = _mock_transcription(monkeypatch)
    page_generator.transcribe_voice_description(b"fake-audio-bytes", "onboarding.m4a", "audio/mp4")
    assert captured["model"] == "gpt-4o-transcribe"
    filename, file_obj, content_type = captured["file"]
    assert filename == "onboarding.m4a"
    assert content_type == "audio/mp4"
    assert file_obj.read() == b"fake-audio-bytes"


def test_transcribe_voice_description_does_not_force_a_language(monkeypatch):
    """No language hint is sent - it auto-detects from the audio itself, so an owner can
    speak any language regardless of the site's UI language."""
    captured = _mock_transcription(monkeypatch)
    page_generator.transcribe_voice_description(b"fake-audio-bytes", "recording.webm", "audio/webm")
    assert "language" not in captured


def test_transcribe_voice_description_rejects_empty_audio():
    with pytest.raises(ValueError):
        page_generator.transcribe_voice_description(b"", "recording.webm", "audio/webm")


def test_transcribe_voice_description_rejects_oversized_audio():
    with pytest.raises(ValueError):
        page_generator.transcribe_voice_description(
            b"x" * (page_generator.MAX_VOICE_AUDIO_BYTES + 1), "recording.webm", "audio/webm",
        )


def test_transcribe_voice_description_raises_on_empty_transcript(monkeypatch):
    _mock_transcription(monkeypatch, text="   ")
    with pytest.raises(ValueError):
        page_generator.transcribe_voice_description(b"fake-audio-bytes", "recording.webm", "audio/webm")


def test_transcribe_voice_description_raises_when_whisper_call_fails(monkeypatch):
    def fake_create(**kwargs):
        raise RuntimeError("upstream error")

    monkeypatch.setattr(page_generator.client.audio.transcriptions, "create", fake_create)
    with pytest.raises(ValueError):
        page_generator.transcribe_voice_description(b"fake-audio-bytes", "recording.webm", "audio/webm")


def test_transcribe_voice_description_truncates_long_transcript(monkeypatch):
    _mock_transcription(monkeypatch, text="x" * 5000)
    result = page_generator.transcribe_voice_description(b"fake-audio-bytes", "recording.webm", "audio/webm")
    assert len(result) == page_generator.MAX_DESCRIPTION_LENGTH


# --- /api/onboarding/voice-transcribe (route) ---

def _upload(client, csrf_token, audio_bytes=b"fake-audio-bytes", filename="recording.webm", content_type="audio/webm"):
    return client.post(
        "/api/onboarding/voice-transcribe",
        data={"csrf_token": csrf_token, "audio": (io.BytesIO(audio_bytes), filename, content_type)},
        content_type="multipart/form-data",
        headers={"Referer": "http://localhost/api/onboarding/voice-transcribe"},
    )


def test_voice_transcribe_route_returns_text(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _mock_transcription(monkeypatch, text="A cozy cafe downtown, open 8 to 22, coffee and cakes.")
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = _upload(client, csrf_token)
    assert response.status_code == 200
    assert response.get_json()["text"] == "A cozy cafe downtown, open 8 to 22, coffee and cakes."


def test_voice_transcribe_route_requires_login(client):
    # CSRF tokens are session- not user-scoped, so an anonymous visitor can still get a
    # valid one (e.g. off the login page) - this isolates "no session" as the actual
    # reason for the redirect, rather than failing on the CSRF check first.
    csrf_token = get_csrf_token(client, "/login")
    response = _upload(client, csrf_token)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_voice_transcribe_route_requires_an_audio_file(client, unique_username, unique_email):
    register_business_web(client, unique_username, unique_email)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = web_post(client, "/api/onboarding/voice-transcribe", {"csrf_token": csrf_token})
    assert response.status_code == 400
    assert "No recording" in response.get_json()["error"]


def test_voice_transcribe_route_rejects_oversized_upload(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)
    _mock_transcription(monkeypatch)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    oversized = b"x" * (page_generator.MAX_VOICE_AUDIO_BYTES + 1)
    response = _upload(client, csrf_token, audio_bytes=oversized)
    assert response.status_code == 400
    assert "too long" in response.get_json()["error"]


def test_voice_transcribe_route_surfaces_transcription_failure_as_400(client, unique_username, unique_email, monkeypatch):
    register_business_web(client, unique_username, unique_email)

    def fake_create(**kwargs):
        raise RuntimeError("upstream error")

    monkeypatch.setattr(page_generator.client.audio.transcriptions, "create", fake_create)
    csrf_token = get_csrf_token(client, "/onboarding/website")

    response = _upload(client, csrf_token)
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_voice_transcribe_route_does_not_touch_the_business_row(client, unique_username, unique_email, monkeypatch):
    """The endpoint only ever returns transcribed text - it never calls
    generate_page_draft or persists anything itself (that still only happens when the
    owner reviews the text and submits the normal /onboarding/website form)."""
    register_business_web(client, unique_username, unique_email)
    _mock_transcription(monkeypatch, text="A cozy cafe downtown.")
    csrf_token = get_csrf_token(client, "/onboarding/website")

    import database
    before = database.business_by_username(unique_username)

    response = _upload(client, csrf_token, audio_bytes=b"unique-marker-bytes-12345")
    assert response.status_code == 200

    after = database.business_by_username(unique_username)
    assert before == after


def test_voice_transcribe_route_transcript_flows_into_the_normal_describe_step(client, unique_username, unique_email, monkeypatch):
    """The whole point of returning plain text instead of generating the page draft
    directly: the owner can review/edit it, then it goes through the exact same
    onboarding_website_describe() POST as anything typed by hand."""
    register_business_web(client, unique_username, unique_email)
    _mock_transcription(monkeypatch, text="A barbershop open weekdays 9-6, haircuts 20 EUR.")
    csrf_token = get_csrf_token(client, "/onboarding/website")

    transcribed = _upload(client, csrf_token).get_json()["text"]

    import json
    import ai_theme
    monkeypatch.setattr(
        page_generator.client.chat.completions, "create",
        lambda **kwargs: _FakeResponse(json.dumps(VALID_DRAFT)),
    )
    # onboarding_website_describe() also calls ai_theme.generate_theme_options() (Faza 14) -
    # mocked here too so this stays off the network, same reasoning as test_onboarding_wizard.py's
    # own _mock_ai_draft().
    monkeypatch.setattr(ai_theme, "generate_theme_options", lambda *args, **kwargs: [])
    csrf_token = get_csrf_token(client, "/onboarding/website")
    response = web_post(client, "/onboarding/website", {"csrf_token": csrf_token, "description": transcribed})
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/website/design"
