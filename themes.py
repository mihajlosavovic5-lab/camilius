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
    "ivory": {
        "label": "Soft Ivory",
        "blurb": "Off-white and warm taupe, understated and versatile.",
        "swatch": ["#a89078", "#faf7f2", "#3a3229"],
        "radius": "16px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#a89078", "on_primary": "#ffffff", "gold": "#c7b299",
            "background": "#faf7f2", "surface": "#ffffff", "text": "#3a3229",
            "text_muted": "#8a7f6f", "border": "#e8e1d4",
        },
        "dark": {
            "primary": "#d8c4a8", "on_primary": "#241f18", "gold": "#cbb790",
            "background": "#221e18", "surface": "#2c2620", "text": "#f2ede2",
            "text_muted": "#b3a690", "border": "#423a2e",
        },
    },
    "blush": {
        "label": "Blush Pink",
        "blurb": "Soft rose and cream, warm and friendly.",
        "swatch": ["#e8829c", "#fff6f8", "#3d2429"],
        "radius": "20px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#e8829c", "on_primary": "#2b1216", "gold": "#f5b8c8",
            "background": "#fff6f8", "surface": "#ffffff", "text": "#3d2429",
            "text_muted": "#8a6570", "border": "#f5dde2",
        },
        "dark": {
            "primary": "#f5a8bb", "on_primary": "#2b1216", "gold": "#f0c3cf",
            "background": "#241318", "surface": "#2e1a20", "text": "#f7e9ec",
            "text_muted": "#c79aa5", "border": "#452733",
        },
    },
    "lavender": {
        "label": "Lavender",
        "blurb": "Soft purple and lilac, calm and a little dreamy.",
        "swatch": ["#8b6bc7", "#f8f5fc", "#2e2440"],
        "radius": "16px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#8b6bc7", "on_primary": "#ffffff", "gold": "#b79ee0",
            "background": "#f8f5fc", "surface": "#ffffff", "text": "#2e2440",
            "text_muted": "#756a8f", "border": "#e6ddf2",
        },
        "dark": {
            "primary": "#b79ee0", "on_primary": "#1f1830", "gold": "#cdb8ee",
            "background": "#1c1628", "surface": "#251d33", "text": "#ede8f5",
            "text_muted": "#ab9dc4", "border": "#392e4d",
        },
    },
    "cocoa": {
        "label": "Cocoa",
        "blurb": "Warm brown and terracotta, grounded and inviting.",
        "swatch": ["#9c5b34", "#fbf5ef", "#3a2a1e"],
        "radius": "14px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#9c5b34", "on_primary": "#ffffff", "gold": "#cf9d6c",
            "background": "#fbf5ef", "surface": "#ffffff", "text": "#3a2a1e",
            "text_muted": "#8a7160", "border": "#ecdccb",
        },
        "dark": {
            "primary": "#d69a6c", "on_primary": "#241811", "gold": "#e0b88c",
            "background": "#24190f", "surface": "#2f2117", "text": "#f2e6da",
            "text_muted": "#bfa389", "border": "#45311f",
        },
    },
    "ocean": {
        "label": "Ocean Blue",
        "blurb": "Soft blue and sky tones, clean and trustworthy.",
        "swatch": ["#3d7bb8", "#f4f8fc", "#1e2c3a"],
        "radius": "14px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#3d7bb8", "on_primary": "#ffffff", "gold": "#7fb3dd",
            "background": "#f4f8fc", "surface": "#ffffff", "text": "#1e2c3a",
            "text_muted": "#5f7286", "border": "#d9e6f0",
        },
        "dark": {
            "primary": "#7fb3dd", "on_primary": "#0f1c26", "gold": "#a8cdea",
            "background": "#101c26", "surface": "#182633", "text": "#e8f1f8",
            "text_muted": "#9db3c4", "border": "#2b3d4d",
        },
    },
    "sunshine": {
        "label": "Sunshine",
        "blurb": "Buttery yellow and cream, bright and cheerful.",
        "swatch": ["#d9a012", "#fffaf0", "#3a2f12"],
        "radius": "18px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#d9a012", "on_primary": "#2b2008", "gold": "#f6d97a",
            "background": "#fffaf0", "surface": "#ffffff", "text": "#3a2f12",
            "text_muted": "#8a7a4f", "border": "#f2e4bc",
        },
        "dark": {
            "primary": "#f6d97a", "on_primary": "#2b2008", "gold": "#ffe9a3",
            "background": "#221c0c", "surface": "#2c2410", "text": "#f7edd2",
            "text_muted": "#bfae7f", "border": "#453a1c",
        },
    },
    "mint": {
        "label": "Mint",
        "blurb": "Fresh teal and mint, cool and clean.",
        "swatch": ["#2f9e8f", "#f3fbfa", "#163330"],
        "radius": "16px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#2f9e8f", "on_primary": "#ffffff", "gold": "#7fd4c4",
            "background": "#f3fbfa", "surface": "#ffffff", "text": "#163330",
            "text_muted": "#5c7f7a", "border": "#d3ece8",
        },
        "dark": {
            "primary": "#6fd4c1", "on_primary": "#0c211e", "gold": "#a0e8da",
            "background": "#0d201d", "surface": "#172a26", "text": "#e4f5f1",
            "text_muted": "#9dc4bb", "border": "#2a423d",
        },
    },
    "coral": {
        "label": "Coral",
        "blurb": "Soft coral and peach, warm sunset energy.",
        "swatch": ["#e8674a", "#fff6f3", "#3d241c"],
        "radius": "14px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#e8674a", "on_primary": "#ffffff", "gold": "#f4a184",
            "background": "#fff6f3", "surface": "#ffffff", "text": "#3d241c",
            "text_muted": "#8f6a5d", "border": "#f5ded6",
        },
        "dark": {
            "primary": "#f4917a", "on_primary": "#2b140e", "gold": "#f7b7a2",
            "background": "#241511", "surface": "#2e1c17", "text": "#f7e6e0",
            "text_muted": "#c7a397", "border": "#452c24",
        },
    },
    "slate": {
        "label": "Slate",
        "blurb": "Cool graphite gray, quietly professional.",
        "swatch": ["#4b5768", "#f5f6f8", "#22262c"],
        "radius": "10px",
        "heading_font": None,
        "fixed_dark": False,
        "base": {
            "primary": "#4b5768", "on_primary": "#ffffff", "gold": "#8593a3",
            "background": "#f5f6f8", "surface": "#ffffff", "text": "#22262c",
            "text_muted": "#667080", "border": "#dfe3e8",
        },
        "dark": {
            "primary": "#9aa7b8", "on_primary": "#161a1f", "gold": "#b9c3d0",
            "background": "#171a1e", "surface": "#202429", "text": "#eaedf0",
            "text_muted": "#a2acb8", "border": "#333941",
        },
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
