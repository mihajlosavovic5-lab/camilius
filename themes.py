"""Visual presets for a business's public page (/p/<slug>) and embeddable widget
(/w/<slug>) - picked during the onboarding wizard's design step (app.py's
onboarding_website_design) and editable afterward from Settings (save_business_page).

Each theme is a plain dict of CSS custom-property values plus a couple of layout knobs
(radius, heading_font) - public_page.html/widget_frame.html read these through Jinja to
build their own <style> block rather than hardcoding one fixed palette, which is all
either template used to have. "base" is always the :root block; "dark" (when present) is
layered on top as a `@media (prefers-color-scheme: dark)` override, exactly the single
hardcoded pair both templates already had. A theme with fixed_dark=True has no light
mode at all - "base" IS its one deliberately-dark look (Bold/Luxury), so no `dark` block
is emitted for it.

on_primary is the contrast color for text/icons sitting ON TOP of --primary (send
buttons, the visitor's own chat bubbles) - both templates used to hardcode this as a
literal "#201a10" that only happened to read well against the one gold accent they had;
a theme with a light or saturated accent needs its own contrasting value instead.
"""
import json

DEFAULT_THEME_ID = "warm"

THEMES = {
    "minimal": {
        "label": "Minimal",
        "blurb": "Black and white, lots of breathing room, quietly confident.",
        "swatch": ["#141414", "#ffffff", "#f2f2f2"],
        "radius": "10px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#141414", "on_primary": "#ffffff", "gold": "#141414",
            "background": "#ffffff", "surface": "#ffffff", "text": "#141414",
            "text_muted": "#6b6b6b", "border": "#e6e6e6",
        },
        "dark": {
            "primary": "#f2f2f2", "on_primary": "#141414", "gold": "#f2f2f2",
            "background": "#0d0d0d", "surface": "#171717", "text": "#f2f2f2",
            "text_muted": "#9a9a9a", "border": "#2b2b2b",
        },
    },
    "warm": {
        "label": "Warm & elegant",
        "blurb": "Gold and cream, soft rounded cards - a classic, welcoming feel.",
        "swatch": ["#d69410", "#fdfcfa", "#2b2013"],
        "radius": "14px",
        "heading_font": "Georgia, 'Iowan Old Style', 'Palatino Linotype', serif",
        "fixed_dark": False,
        "base": {
            "primary": "#d69410", "on_primary": "#201a10", "gold": "#ffd83d",
            "background": "#fdfcfa", "surface": "#ffffff", "text": "#2b2013",
            "text_muted": "#6b5d47", "border": "#eee3d0",
        },
        "dark": {
            "primary": "#ffd83d", "on_primary": "#201a10", "gold": "#ffd83d",
            "background": "#201a10", "surface": "#2a2216", "text": "#f5efe2",
            "text_muted": "#b8ab90", "border": "#43392a",
        },
    },
    "bold": {
        "label": "Bold & contrast",
        "blurb": "Always-dark background with a vivid accent - modern and energetic.",
        "swatch": ["#0a0a0a", "#ff4d33", "#f5f5f5"],
        "radius": "6px",
        "heading_font": None,
        "fixed_dark": True,
        "base": {
            "primary": "#ff4d33", "on_primary": "#0a0a0a", "gold": "#ff8a3d",
            "background": "#0a0a0a", "surface": "#171717", "text": "#f5f5f5",
            "text_muted": "#a8a8a8", "border": "#2a2a2a",
        },
        "dark": None,
    },
    "nature": {
        "label": "Nature & organic",
        "blurb": "Green and cream, gently rounded - calm and grounded.",
        "swatch": ["#3f7d20", "#f6f5ef", "#24301f"],
        "radius": "20px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#3f7d20", "on_primary": "#ffffff", "gold": "#8aab5c",
            "background": "#f6f5ef", "surface": "#ffffff", "text": "#24301f",
            "text_muted": "#5c6b52", "border": "#dfe3d5",
        },
        "dark": {
            "primary": "#8fd18f", "on_primary": "#12190f", "gold": "#a9c98a",
            "background": "#12160f", "surface": "#1b2117", "text": "#eef1e9",
            "text_muted": "#a9b39d", "border": "#2d3327",
        },
    },
    "luxury": {
        "label": "Luxury",
        "blurb": "Black and gold, serif headings, always-dark premium feel.",
        "swatch": ["#0b0b0c", "#c9a227", "#f2ead9"],
        "radius": "2px",
        "heading_font": "Georgia, 'Times New Roman', serif",
        "fixed_dark": True,
        "base": {
            "primary": "#c9a227", "on_primary": "#14110a", "gold": "#e8c766",
            "background": "#0b0b0c", "surface": "#161616", "text": "#f2ead9",
            "text_muted": "#b8ab8a", "border": "#2a2a2a",
        },
        "dark": None,
    },
}

# Insertion order above IS display order in the wizard's design step - deliberately
# listed with the two most broadly-safe looks (Minimal, Warm) first.
THEME_IDS = list(THEMES.keys())


def theme_or_default(theme_id):
    """Never raises - an unrecognized/NULL theme id (a bad query param on the preview
    route, or a page saved before this column existed on a database that somehow skipped
    the migration's default) falls back to DEFAULT_THEME_ID rather than 500ing a public
    page."""
    return THEMES.get(theme_id, THEMES[DEFAULT_THEME_ID])


CUSTOM_THEME_ID = "custom"


def resolve_theme(theme_id, custom_theme_json=None):
    """Same never-raises contract as theme_or_default, extended for ai_theme.py's
    AI-generated per-business themes: when theme_id is CUSTOM_THEME_ID and
    custom_theme_json holds a valid JSON-encoded theme dict (business_pages.custom_theme_json
    - see ai_theme.py's _validate_option for the shape), returns that dict so callers never
    need to care whether a page is using one of the fixed THEMES presets or an AI-generated
    one. Any other theme_id, or a custom_theme_json that fails to parse, falls back to
    theme_or_default(theme_id) exactly as before."""
    if theme_id == CUSTOM_THEME_ID and custom_theme_json:
        try:
            custom = json.loads(custom_theme_json)
        except (TypeError, ValueError):
            custom = None
        if isinstance(custom, dict) and custom.get("base"):
            return custom
    return theme_or_default(theme_id)
