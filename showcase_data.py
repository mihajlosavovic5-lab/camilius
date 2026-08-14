"""Fixture data for the "See it in action" showcase (/showcase/<slug>) on the marketing
landing page (templates/landing.html's #lp-examples section).

Camilius has no real "example accounts" anywhere - the earlier version of that section
was 10 hand-written fake chat bubbles left over from the pre-pivot product (an AI that
only auto-replied to messages), never connected to page_generator.py/themes.py at all.
This module replaces those with 8 fictional local-service businesses, authored to the
exact same content conventions the real product enforces, so app.py's showcase routes can
render them through the SAME public_page.html template every real business's page uses
(see app.py's onboarding_website_preview() for the precedent: rendering that template from
a plain dict, no business/page database rows involved) - what a visitor clicks into here is
pixel-for-pixel what Camilius actually builds, not a mockup of it.

Field conventions (kept in sync with the real writers, so nothing here could look
different from what the AI itself would produce):
  - about: pages.render_about_html's small markdown-like syntax (# / ## headings,
    - / 1. lists with an optional lead-in line, **bold**/*italic*, "> " callout,
    "-- " signature).
  - working_hours: the structured JSON shape booking.parse_working_hours reads
    ({"monday": {"open": "09:00", "close": "18:00"}, "tuesday": null, ...}) - the
    same wire format the real hours picker and page_generator.format_working_hours write.
  - price_list: pages.price_list_rows' "Name: Amount CODE" lines, one per line,
    "# Category" lines as section headers.
  - faq: pages.render_faq_html's "question line, then answer line(s), blank line
    between pairs" convention.
  - theme_id: one of themes.THEMES' 14 preset ids.
  - booking_enabled: mirrors the real booking_relevant heuristic - on for
    appointment-style services, off for a bakery/cafe/rental with nothing to "book".

Deliberately absent, same reasoning in every case: social_links (no real accounts to
link to - a fabricated Instagram/Facebook URL would be a fake link the platform's own
code produced), address (no geocoded location to point a map at), hero_video_url (no
uploaded video). Each of these is simply the same "not set" state a real business that
never filled that field in would have - public_page.html already hides the corresponding
section in that case, so nothing here needed a special case for it.
"""

SHOWCASE_BUSINESSES = {
    "bella-hair-studio": {
        "business_name": "Bella Hair Studio",
        "tagline": "Modern cuts and color, downtown",
        "theme_id": "warm",
        "booking_enabled": True,
        "contact_email": "hello@bellahairstudio.example",
        "phone_number": "+1 555-0142",
        "about": (
            "# Welcome to Bella\n\n"
            "We're a small, friendly studio focused on cuts and color that actually suit "
            "you - not whatever's trending this month.\n\n"
            "Kod nas mozete ocekivati:\n"
            "- Free consultation before every color service\n"
            "- Only sulfate-free products\n"
            "- A relaxed, no-rush appointment\n\n"
            "> Great hair shouldn't be stressful.\n\n"
            "-- Bella & the team"
        ),
        "working_hours": {
            "monday": None,
            "tuesday": {"open": "09:00", "close": "18:00"},
            "wednesday": {"open": "09:00", "close": "18:00"},
            "thursday": {"open": "09:00", "close": "18:00"},
            "friday": {"open": "09:00", "close": "19:00"},
            "saturday": {"open": "09:00", "close": "16:00"},
            "sunday": None,
        },
        "price_list": (
            "# Cuts\n"
            "Women's haircut: 35 USD\n"
            "Men's haircut: 22 USD\n"
            "# Color\n"
            "Full color: 65 USD\n"
            "Balayage: 95 USD\n"
            "Gloss & toner: 30 USD"
        ),
        "faq": (
            "Do you take walk-ins?\n"
            "Usually yes on weekdays, but booking ahead guarantees your slot.\n\n"
            "How long does balayage take?\n"
            "Around 2.5 hours including toner and a blow-dry.\n\n"
            "Do you sell the products you use?\n"
            "Yes, at the front desk - ask your stylist for a recommendation."
        ),
    },
    "brightsmile-dental": {
        "business_name": "BrightSmile Dental",
        "tagline": "Family dentistry, gentle and thorough",
        "theme_id": "ocean",
        "booking_enabled": True,
        "contact_email": "front-desk@brightsmiledental.example",
        "phone_number": "+1 555-0198",
        "about": (
            "# Care for the whole family\n\n"
            "BrightSmile has been treating patients of all ages for over a decade, from "
            "first checkups to whitening and root canals.\n\n"
            "1. A full exam and cleaning\n"
            "2. Digital X-rays only when needed\n"
            "3. A clear, no-surprise cost estimate before any treatment\n\n"
            "> We explain every step before we do it.\n\n"
            "-- Dr. Novak and the BrightSmile team"
        ),
        "working_hours": {
            "monday": {"open": "08:00", "close": "17:00"},
            "tuesday": {"open": "08:00", "close": "17:00"},
            "wednesday": {"open": "08:00", "close": "17:00"},
            "thursday": {"open": "08:00", "close": "17:00"},
            "friday": {"open": "08:00", "close": "14:00"},
            "saturday": None,
            "sunday": None,
        },
        "price_list": (
            "# Checkups\n"
            "Exam & cleaning: 60 USD\n"
            "X-ray (per image): 15 USD\n"
            "# Treatments\n"
            "Teeth whitening: 180 USD\n"
            "Filling: 90 USD\n"
            "Root canal: from 350 USD"
        ),
        "faq": (
            "Do you accept dental insurance?\n"
            "We accept most major plans - bring your card and we'll check on the spot.\n\n"
            "How often should I get a cleaning?\n"
            "Every 6 months for most patients, more often if you have gum sensitivity.\n\n"
            "Is teeth whitening safe?\n"
            "Yes, ours is done in-office under supervision, safer than most at-home kits."
        ),
    },
    "quickfix-phone-repair": {
        "business_name": "QuickFix Phone Repair",
        "tagline": "Screens, batteries, and water damage - same day",
        "theme_id": "bold",
        "booking_enabled": True,
        "contact_email": "hey@quickfixrepair.example",
        "phone_number": "+1 555-0177",
        "about": (
            "# Most repairs done in under an hour\n\n"
            "We fix phones, tablets and laptops - screens, batteries, charging ports, "
            "and water damage. Genuine and high-quality aftermarket parts, your choice.\n\n"
            "- Free diagnostic, no obligation\n"
            "- 90-day warranty on every repair\n"
            "- We also buy broken devices for parts\n\n"
            "-- The QuickFix crew"
        ),
        "working_hours": {
            "monday": {"open": "10:00", "close": "19:00"},
            "tuesday": {"open": "10:00", "close": "19:00"},
            "wednesday": {"open": "10:00", "close": "19:00"},
            "thursday": {"open": "10:00", "close": "19:00"},
            "friday": {"open": "10:00", "close": "19:00"},
            "saturday": {"open": "11:00", "close": "17:00"},
            "sunday": None,
        },
        "price_list": (
            "# Screens\n"
            "iPhone screen replacement: from 79 USD\n"
            "Android screen replacement: from 69 USD\n"
            "# Other\n"
            "Battery replacement: 39 USD\n"
            "Charging port repair: 45 USD\n"
            "Water damage diagnostic: free"
        ),
        "faq": (
            "How long does a screen repair take?\n"
            "Usually 30-45 minutes while you wait.\n\n"
            "Do you use original parts?\n"
            "We offer both original and high-quality aftermarket - we explain the "
            "difference before you decide.\n\n"
            "What if my phone won't turn on after water damage?\n"
            "Bring it in as soon as possible and don't try charging it - the diagnostic is free."
        ),
    },
    "harbor-view-bnb": {
        "business_name": "Harbor View B&B",
        "tagline": "Four rooms, one view, steps from the water",
        "theme_id": "luxury",
        "booking_enabled": False,
        "contact_email": "stay@harborviewbnb.example",
        "phone_number": "+1 555-0163",
        "about": (
            "# A small, quiet stay by the harbor\n\n"
            "Four rooms, each with a view of the water, a proper breakfast included, and "
            "a five-minute walk to the old town.\n\n"
            "- Breakfast included every morning\n"
            "- Free bikes for guests\n"
            "- Late checkout on request\n\n"
            "> Book direct and breakfast is on us.\n\n"
            "-- Mara, your host"
        ),
        "working_hours": {
            "monday": {"open": "08:00", "close": "20:00"},
            "tuesday": {"open": "08:00", "close": "20:00"},
            "wednesday": {"open": "08:00", "close": "20:00"},
            "thursday": {"open": "08:00", "close": "20:00"},
            "friday": {"open": "08:00", "close": "20:00"},
            "saturday": {"open": "08:00", "close": "20:00"},
            "sunday": {"open": "08:00", "close": "20:00"},
        },
        "price_list": (
            "# Rooms, per night\n"
            "Harbor view double: 140 USD\n"
            "Garden double: 110 USD\n"
            "Family room (up to 4): 190 USD\n"
            "# Extras\n"
            "Airport pickup: 25 USD"
        ),
        "faq": (
            "What time is check-in?\n"
            "From 3pm - let us know if you're arriving late, someone is always on site.\n\n"
            "Do you allow pets?\n"
            "Small, well-behaved pets are welcome in the garden rooms.\n\n"
            "Is breakfast included?\n"
            "Yes, a full breakfast is included with every room, every morning."
        ),
    },
    "flow-yoga-studio": {
        "business_name": "Flow Yoga Studio",
        "tagline": "Yoga and Pilates for every level",
        "theme_id": "nature",
        "booking_enabled": True,
        "contact_email": "namaste@flowyogastudio.example",
        "phone_number": "+1 555-0129",
        "about": (
            "# Move, breathe, reset\n\n"
            "Small classes, real attention from your instructor, and a schedule built "
            "around real life - early mornings, lunch breaks, evenings.\n\n"
            "Kod nas mozete ocekivati:\n"
            "- Beginner-friendly classes every day\n"
            "- Mats and props provided\n"
            "- A calm studio, never overcrowded\n\n"
            "-- The Flow instructors"
        ),
        "working_hours": {
            "monday": {"open": "06:30", "close": "20:30"},
            "tuesday": {"open": "06:30", "close": "20:30"},
            "wednesday": {"open": "06:30", "close": "20:30"},
            "thursday": {"open": "06:30", "close": "20:30"},
            "friday": {"open": "06:30", "close": "19:00"},
            "saturday": {"open": "09:00", "close": "13:00"},
            "sunday": None,
        },
        "price_list": (
            "# Drop-in\n"
            "Single class: 18 USD\n"
            "# Packages\n"
            "5-class pack: 80 USD\n"
            "Monthly unlimited: 120 USD\n"
            "Private 1:1 session: 60 USD"
        ),
        "faq": (
            "I've never done yoga before - can I still join?\n"
            "Absolutely, most of our morning classes are beginner-friendly.\n\n"
            "Do I need to bring my own mat?\n"
            "No, mats and props are provided free of charge.\n\n"
            "Can I freeze my monthly membership?\n"
            "Yes, once per month for up to two weeks, just let us know in advance."
        ),
    },
    "maple-and-co-bakery": {
        "business_name": "Maple & Co. Bakery",
        "tagline": "Fresh bread and pastries, baked before sunrise",
        "theme_id": "sunshine",
        "booking_enabled": False,
        "contact_email": "hello@mapleandco.example",
        "phone_number": "+1 555-0114",
        "about": (
            "# Baked fresh, every single morning\n\n"
            "Sourdough, croissants and seasonal pastries, made from scratch before the "
            "sun's up. We also take custom cake orders.\n\n"
            "- Sourdough baked daily\n"
            "- Vegan and gluten-free options available\n"
            "- Custom cakes with 48 hours notice\n\n"
            "> Come early - the croissants sell out by 10.\n\n"
            "-- Maple & Co."
        ),
        "working_hours": {
            "monday": {"open": "07:00", "close": "15:00"},
            "tuesday": {"open": "07:00", "close": "15:00"},
            "wednesday": {"open": "07:00", "close": "15:00"},
            "thursday": {"open": "07:00", "close": "15:00"},
            "friday": {"open": "07:00", "close": "15:00"},
            "saturday": {"open": "07:00", "close": "16:00"},
            "sunday": {"open": "08:00", "close": "13:00"},
        },
        "price_list": (
            "# Bread\n"
            "Sourdough loaf: 6 USD\n"
            "Baguette: 3 USD\n"
            "# Pastries\n"
            "Croissant: 3.50 USD\n"
            "Cinnamon roll: 4 USD\n"
            "# Cakes\n"
            "Custom cake (serves 10): from 45 USD"
        ),
        "faq": (
            "Do you take custom cake orders?\n"
            "Yes, with at least 48 hours notice - message us with the details.\n\n"
            "Are your pastries made fresh daily?\n"
            "Everything is baked from scratch before we open, nothing frozen.\n\n"
            "Do you have gluten-free options?\n"
            "A few items each day - ask at the counter for what's available."
        ),
    },
    "aperture-photography": {
        "business_name": "Aperture Photography",
        "tagline": "Weddings and portraits, told honestly",
        "theme_id": "blush",
        "booking_enabled": True,
        "contact_email": "studio@aperturephoto.example",
        "phone_number": "+1 555-0155",
        "about": (
            "# Photos that feel like the day actually felt\n\n"
            "Wedding and portrait photography with a documentary, unposed style - candid "
            "moments over stiff formal shots.\n\n"
            "1. A free 20-minute consultation call\n"
            "2. Full-day wedding coverage or short portrait sessions\n"
            "3. Edited gallery delivered within 3 weeks\n\n"
            "> Let's start with a quick call, no pressure.\n\n"
            "-- Aperture Photography"
        ),
        "working_hours": {
            "monday": {"open": "10:00", "close": "18:00"},
            "tuesday": {"open": "10:00", "close": "18:00"},
            "wednesday": {"open": "10:00", "close": "18:00"},
            "thursday": {"open": "10:00", "close": "18:00"},
            "friday": {"open": "10:00", "close": "18:00"},
            "saturday": {"open": "10:00", "close": "16:00"},
            "sunday": None,
        },
        "price_list": (
            "# Portraits\n"
            "Mini session (30 min): 90 USD\n"
            "Full session (90 min): 220 USD\n"
            "# Weddings\n"
            "Half-day coverage: 900 USD\n"
            "Full-day coverage: 1600 USD\n"
            "Engagement session: 180 USD"
        ),
        "faq": (
            "How far in advance should we book a wedding date?\n"
            "6-12 months is typical, especially for spring and summer dates.\n\n"
            "How long until we get our photos?\n"
            "A sneak peek within 48 hours, the full edited gallery within 3 weeks.\n\n"
            "Do you travel for destination weddings?\n"
            "Yes - message us the location and we'll send a travel quote."
        ),
    },
    "shift-auto-service": {
        "business_name": "Shift Auto Service",
        "tagline": "Honest repairs and maintenance, no upsells",
        "theme_id": "slate",
        "booking_enabled": True,
        "contact_email": "service@shiftauto.example",
        "phone_number": "+1 555-0188",
        "about": (
            "# Straightforward car care\n\n"
            "Oil changes, brakes, diagnostics and general repair - we show you what's "
            "actually wrong before we touch anything.\n\n"
            "- Free 20-point inspection with every visit\n"
            "- Written quote before any work starts\n"
            "- Loaner cars available for longer repairs\n\n"
            "-- The Shift Auto team"
        ),
        "working_hours": {
            "monday": {"open": "08:00", "close": "18:00"},
            "tuesday": {"open": "08:00", "close": "18:00"},
            "wednesday": {"open": "08:00", "close": "18:00"},
            "thursday": {"open": "08:00", "close": "18:00"},
            "friday": {"open": "08:00", "close": "18:00"},
            "saturday": {"open": "09:00", "close": "14:00"},
            "sunday": None,
        },
        "price_list": (
            "# Maintenance\n"
            "Oil change: 45 USD\n"
            "Tire rotation: 25 USD\n"
            "# Repairs\n"
            "Brake pad replacement (per axle): 120 USD\n"
            "Full diagnostic: 70 USD\n"
            "Battery replacement: 90 USD"
        ),
        "faq": (
            "Do I need an appointment for an oil change?\n"
            "Walk-ins are welcome, but booking ahead means less waiting.\n\n"
            "Do you offer a loaner car?\n"
            "For repairs expected to take more than a day, yes - ask when you book.\n\n"
            "Will you call before doing extra work?\n"
            "Always - we quote everything in writing before starting."
        ),
    },
}

# Insertion order above IS display order in the landing page carousel.
SHOWCASE_SLUGS = list(SHOWCASE_BUSINESSES.keys())
