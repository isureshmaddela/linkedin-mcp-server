"""Monosage's offline designer: a good-looking site from a few words, with no model at all.

It reads the kind of site (a restaurant, a portfolio, a SaaS product, a shop, ...), a name, colors and the sections
asked for from the request, keeps them as a *spec* on the project, and renders ``index.html``, ``styles.css`` and
``script.js`` from it. Follow-up requests change the spec (``make it dark``, ``use green``, ``add a pricing
section``, ``remove the FAQ``, ``call it Nova``, ``headline: Fresh bread daily``) and the site is rendered again.
The output goes through the same tagged format as a model's answer (:func:`answer`), so a run looks the same.
"""

from __future__ import annotations

import html
import re
from typing import Any

from aira.suite.sites.templates import TEMPLATES  # noqa: F401 - the Build page's starters

COLORS = {
    "blue": ("#2563eb", "#06b6d4"),
    "navy": ("#1e3a8a", "#3b82f6"),
    "indigo": ("#4f46e5", "#8b5cf6"),
    "purple": ("#7c3aed", "#ec4899"),
    "violet": ("#7c3aed", "#a855f7"),
    "pink": ("#db2777", "#f97316"),
    "red": ("#dc2626", "#f97316"),
    "orange": ("#ea580c", "#facc15"),
    "amber": ("#d97706", "#f43f5e"),
    "yellow": ("#ca8a04", "#f97316"),
    "gold": ("#b45309", "#facc15"),
    "green": ("#16a34a", "#84cc16"),
    "emerald": ("#059669", "#14b8a6"),
    "teal": ("#0d9488", "#06b6d4"),
    "cyan": ("#0891b2", "#6366f1"),
    "brown": ("#92400e", "#d97706"),
    "black": ("#111827", "#6b7280"),
    "monochrome": ("#111827", "#6b7280"),
    "gray": ("#4b5563", "#9ca3af"),
}
FONTS = {
    "sans": (
        '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        "Inter:wght@400;600;800",
    ),
    "serif": (
        '"Playfair Display", Georgia, "Times New Roman", serif',
        "Playfair+Display:wght@500;700;800",
    ),
    "rounded": ('"Nunito", system-ui, sans-serif', "Nunito:wght@400;700;900"),
    "mono": (
        '"JetBrains Mono", ui-monospace, Menlo, monospace',
        "JetBrains+Mono:wght@400;700;800",
    ),
}
SECTION_WORDS = {
    "features": ("features", "services", "benefits", "what we do"),
    "about": ("about", "story", "our story", "mission"),
    "stats": ("stats", "numbers", "impact", "metrics"),
    "menu": ("menu", "dishes", "food menu"),
    "pricing": ("pricing", "prices", "plans", "packages"),
    "gallery": ("gallery", "photos", "portfolio", "projects", "work"),
    "products": ("products", "shop section", "catalog", "store section"),
    "listings": ("listings", "properties", "homes"),
    "schedule": ("schedule", "agenda", "timetable", "classes"),
    "courses": ("courses", "programs", "curriculum"),
    "team": ("team", "staff", "people", "speakers"),
    "testimonials": ("testimonials", "reviews", "quotes", "customers say"),
    "faq": ("faq", "questions", "q&a"),
    "posts": ("blog", "posts", "articles", "news"),
    "newsletter": ("newsletter", "subscribe", "mailing list", "signup", "sign up"),
    "contact": ("contact", "contact form", "get in touch", "location", "map"),
}
SECTION_LABELS = {
    "features": "Services",
    "about": "About",
    "stats": "Impact",
    "menu": "Menu",
    "pricing": "Pricing",
    "gallery": "Work",
    "products": "Shop",
    "listings": "Listings",
    "schedule": "Schedule",
    "courses": "Courses",
    "team": "Team",
    "testimonials": "Reviews",
    "faq": "FAQ",
    "posts": "Blog",
    "newsletter": "Newsletter",
    "contact": "Contact",
}
ORDER = list(SECTION_LABELS)

# Each kind: words that pick it, a default name, copy, colors, sections and its own items.
KINDS: dict[str, dict[str, Any]] = {
    "restaurant": {
        "words": (
            "restaurant",
            "cafe",
            "café",
            "bistro",
            "pizza",
            "bakery",
            "coffee",
            "diner",
            "food",
            "kitchen",
            "trattoria",
            "sushi",
            "bar ",
        ),
        "name": "Bella Cucina",
        "tagline": "Seasonal Italian kitchen",
        "headline": "Handmade pasta, wood-fired pizza, good company",
        "sub": "Fresh ingredients from local farms, cooked the way Nonna taught us. Book a table or order to take away.",
        "cta": "Book a table",
        "colors": ("#b91c1c", "#f59e0b"),
        "font": "serif",
        "sections": ["features", "menu", "about", "gallery", "testimonials", "contact"],
        "features": [
            (
                "🍝",
                "Made from scratch",
                "Pasta rolled every morning and sauces simmered for hours.",
            ),
            (
                "🔥",
                "Wood-fired oven",
                "Neapolitan pizza baked at 450°C in ninety seconds.",
            ),
            (
                "🍷",
                "Natural wines",
                "A short list of Italian wines from small growers.",
            ),
        ],
        "items": [
            ("Tagliatelle al ragù", "Slow-cooked beef and pork ragù, parmigiano", "18"),
            ("Margherita", "San Marzano tomato, fior di latte, basil", "14"),
            ("Burrata", "Heirloom tomatoes, basil oil, sourdough", "12"),
            ("Tiramisu", "Mascarpone, espresso, cocoa", "8"),
        ],
    },
    "portfolio": {
        "words": (
            "portfolio",
            "photographer",
            "designer",
            "artist",
            "illustrator",
            "developer portfolio",
            "personal site",
            "personal website",
            "resume",
            "cv",
            "freelancer",
        ),
        "name": "Ava Chen",
        "tagline": "Product designer",
        "headline": "I design calm, useful products people love",
        "sub": "Ten years shaping apps for startups and global brands. Currently open to new projects.",
        "cta": "See my work",
        "colors": ("#7c3aed", "#ec4899"),
        "font": "sans",
        "dark": True,
        "sections": ["gallery", "about", "features", "testimonials", "contact"],
        "features": [
            (
                "✏️",
                "Product design",
                "From the first sketch to a shipped, tested interface.",
            ),
            (
                "🧭",
                "Design systems",
                "Components and guidelines that let teams move fast.",
            ),
            ("🔬", "Research", "Interviews and usability tests that settle debates."),
        ],
        "items": [
            ("Lumen Banking", "Mobile banking app, 2025"),
            ("Orbit", "Design system for 40 teams"),
            ("Fieldnotes", "Research tool for scientists"),
            ("Kite", "Travel planner, Apple Design Award finalist"),
        ],
    },
    "saas": {
        "words": (
            "saas",
            "startup",
            "software",
            "platform",
            "app ",
            " app",
            "product launch",
            "ai tool",
            "dashboard",
            "api",
            "tool for",
        ),
        "name": "Nimbus",
        "tagline": "Work, in sync",
        "headline": "The workspace that keeps your team moving",
        "sub": "Plan projects, share docs and ship faster together. Set up in two minutes, free for small teams.",
        "cta": "Start free",
        "colors": ("#4f46e5", "#06b6d4"),
        "font": "sans",
        "sections": [
            "features",
            "stats",
            "pricing",
            "testimonials",
            "faq",
            "newsletter",
        ],
        "features": [
            (
                "⚡",
                "Lightning fast",
                "Every page loads in under 100 ms, even with thousands of items.",
            ),
            (
                "🔒",
                "Secure by default",
                "Encryption, SSO and audit logs on every plan.",
            ),
            (
                "🤝",
                "Built for teams",
                "Comments, mentions and live cursors keep everyone aligned.",
            ),
            (
                "📈",
                "Insights",
                "Dashboards show what is on track and what is slipping.",
            ),
            ("🔌", "Integrations", "Connect Slack, GitHub, Google Drive and 50 more."),
            (
                "🌍",
                "Works everywhere",
                "Web, desktop and mobile apps that stay in sync.",
            ),
        ],
        "items": [
            ("Starter", "For individuals", "0"),
            ("Team", "For growing teams", "12"),
            ("Business", "Advanced controls", "29"),
        ],
    },
    "agency": {
        "words": (
            "agency",
            "consulting",
            "consultancy",
            "studio",
            "marketing firm",
            "creative",
        ),
        "name": "Northbound",
        "tagline": "Brand and digital studio",
        "headline": "We build brands that people remember",
        "sub": "Strategy, identity and websites for ambitious companies. Small senior team, big results.",
        "cta": "Start a project",
        "colors": ("#0f766e", "#f59e0b"),
        "font": "sans",
        "dark": True,
        "sections": ["features", "gallery", "stats", "team", "testimonials", "contact"],
        "features": [
            ("🎯", "Strategy", "Positioning and messaging grounded in research."),
            ("🎨", "Identity", "Logos, type and color systems built to last."),
            ("💻", "Websites", "Fast, accessible sites your team can update."),
        ],
        "items": [
            ("Helio Energy", "Rebrand and website"),
            ("Mapleworks", "Identity system"),
            ("Quill", "Product launch campaign"),
            ("Arcadia Hotels", "Digital experience"),
        ],
    },
    "shop": {
        "words": (
            "shop",
            "store",
            "ecommerce",
            "e-commerce",
            "boutique",
            "sell ",
            "products",
            "merch",
            "online store",
            "handmade",
        ),
        "name": "Fern & Clay",
        "tagline": "Handmade ceramics",
        "headline": "Pottery made by hand, for everyday rituals",
        "sub": "Small-batch mugs, bowls and planters thrown in our studio. Free shipping over $60.",
        "cta": "Shop the collection",
        "colors": ("#9a3412", "#65a30d"),
        "font": "serif",
        "sections": ["products", "features", "about", "testimonials", "newsletter"],
        "features": [
            ("🚚", "Free shipping", "On every order over $60."),
            ("♻️", "Plastic-free", "Packed in recycled paper and card."),
            ("↩️", "30-day returns", "Not right? Send it back, no questions."),
        ],
        "items": [
            ("Speckled mug", "Stoneware, 350 ml", "28"),
            ("Moss bowl set", "Set of two", "46"),
            ("Hanging planter", "With jute cord", "34"),
            ("Pour-over cone", "Fits most mugs", "38"),
        ],
    },
    "fitness": {
        "words": (
            "gym",
            "fitness",
            "yoga",
            "trainer",
            "crossfit",
            "pilates",
            "workout",
            "boxing",
            "martial",
        ),
        "name": "Pulse Studio",
        "tagline": "Strength, sweat and community",
        "headline": "Get stronger with coaches who know your name",
        "sub": "Small-group classes, personal training and a community that keeps you coming back. First class free.",
        "cta": "Claim your free class",
        "colors": ("#e11d48", "#f97316"),
        "font": "sans",
        "dark": True,
        "sections": [
            "features",
            "schedule",
            "pricing",
            "team",
            "testimonials",
            "contact",
        ],
        "features": [
            ("🏋️", "Strength", "Barbell and kettlebell classes for every level."),
            ("🔥", "HIIT", "Forty-five minutes that leave you smiling."),
            ("🧘", "Recovery", "Mobility and yoga to keep you moving well."),
        ],
        "items": [
            ("Drop-in", "One class", "20"),
            ("Unlimited", "Every class, every day", "119"),
            ("Personal", "Four 1:1 sessions", "240"),
        ],
    },
    "salon": {
        "words": (
            "salon",
            "spa",
            "beauty",
            "barber",
            "nails",
            "hair",
            "massage",
            "lashes",
        ),
        "name": "Velvet Salon",
        "tagline": "Hair, colour and care",
        "headline": "Leave feeling like the best version of you",
        "sub": "Cuts, colour and treatments by stylists who listen. Book online in thirty seconds.",
        "cta": "Book an appointment",
        "colors": ("#be185d", "#a78bfa"),
        "font": "serif",
        "sections": ["features", "pricing", "gallery", "testimonials", "contact"],
        "features": [
            ("✂️", "Cut & style", "Precision cuts tailored to your face and routine."),
            ("🎨", "Colour", "Balayage, highlights and gloss with gentle products."),
            ("💆", "Treatments", "Scalp care and deep conditioning rituals."),
        ],
        "items": [
            ("Cut & blow-dry", "45 minutes", "55"),
            ("Balayage", "From", "160"),
            ("Treatment", "30 minutes", "35"),
        ],
    },
    "clinic": {
        "words": (
            "clinic",
            "dentist",
            "dental",
            "doctor",
            "medical",
            "health",
            "therapy",
            "physio",
            "vet",
            "hospital",
            "care",
        ),
        "name": "Brightside Clinic",
        "tagline": "Family health care",
        "headline": "Care that puts you first, every visit",
        "sub": "Same-week appointments, friendly clinicians and clear advice for the whole family.",
        "cta": "Book a visit",
        "colors": ("#0369a1", "#10b981"),
        "font": "rounded",
        "sections": ["features", "about", "team", "faq", "contact"],
        "features": [
            ("🩺", "General care", "Check-ups, vaccinations and everyday health."),
            ("🦷", "Specialists", "Dental, physiotherapy and mental health on site."),
            ("📱", "Online booking", "Book, move or cancel from your phone."),
        ],
        "items": [],
    },
    "event": {
        "words": (
            "event",
            "wedding",
            "conference",
            "festival",
            "meetup",
            "summit",
            "party",
            "concert",
            "hackathon",
            "launch event",
        ),
        "name": "DevSummit 2026",
        "tagline": "Two days of ideas and code",
        "headline": "Where builders meet the future of the web",
        "sub": "40 speakers, hands-on workshops and the best hallway track of the year. 14–15 November, Lisbon.",
        "cta": "Get your ticket",
        "colors": ("#7c3aed", "#22d3ee"),
        "font": "sans",
        "dark": True,
        "sections": ["about", "schedule", "team", "pricing", "faq", "contact"],
        "features": [],
        "items": [
            ("Early bird", "Until 30 September", "199"),
            ("Regular", "Full conference", "299"),
            ("Workshop pass", "Conference + workshops", "449"),
        ],
    },
    "realestate": {
        "words": (
            "real estate",
            "realtor",
            "property",
            "properties",
            "homes for sale",
            "apartments",
            "rentals",
            "housing",
        ),
        "name": "Keystone Homes",
        "tagline": "Real estate, made simple",
        "headline": "Find the home that fits your life",
        "sub": "Local agents, honest advice and homes in the neighbourhoods you love.",
        "cta": "Browse homes",
        "colors": ("#1d4ed8", "#16a34a"),
        "font": "sans",
        "sections": ["listings", "features", "stats", "testimonials", "contact"],
        "features": [
            ("🏡", "Buy", "Tours, offers and negotiation handled for you."),
            ("🔑", "Sell", "Pricing, staging and marketing that gets results."),
            ("📊", "Value", "A free, honest valuation in 48 hours."),
        ],
        "items": [
            ("Garden cottage", "3 bed · 2 bath · Oak Park", "485,000"),
            ("City loft", "2 bed · 1 bath · Downtown", "362,000"),
            ("Family home", "4 bed · 3 bath · Riverside", "640,000"),
        ],
    },
    "education": {
        "words": (
            "school",
            "course",
            "academy",
            "tutor",
            "tutoring",
            "learning",
            "bootcamp",
            "classes for",
            "teach",
            "education",
            "university",
        ),
        "name": "Brightpath Academy",
        "tagline": "Learn skills that matter",
        "headline": "Learn from experts, at your own pace",
        "sub": "Practical courses with projects, mentors and a certificate employers recognise.",
        "cta": "Browse courses",
        "colors": ("#2563eb", "#f59e0b"),
        "font": "rounded",
        "sections": [
            "courses",
            "features",
            "stats",
            "testimonials",
            "faq",
            "newsletter",
        ],
        "features": [
            ("🎓", "Expert teachers", "Learn from people who do the work every day."),
            ("🛠️", "Real projects", "Build a portfolio while you learn."),
            ("🤝", "Mentors", "Weekly calls with a mentor who reviews your work."),
        ],
        "items": [
            ("Web development", "12 weeks · Beginner", "390"),
            ("Data analysis", "8 weeks · Intermediate", "320"),
            ("UX design", "10 weeks · Beginner", "350"),
        ],
    },
    "nonprofit": {
        "words": (
            "charity",
            "nonprofit",
            "non-profit",
            "ngo",
            "foundation",
            "volunteer",
            "donate",
            "fundraiser",
            "cause",
        ),
        "name": "Clearwater Fund",
        "tagline": "Clean water for every village",
        "headline": "Together we can bring clean water to 1 million people",
        "sub": "Every donation builds wells, filters and schools' taps in communities that need them most.",
        "cta": "Donate now",
        "colors": ("#0284c7", "#22c55e"),
        "font": "sans",
        "sections": [
            "about",
            "stats",
            "features",
            "testimonials",
            "newsletter",
            "contact",
        ],
        "features": [
            ("💧", "Wells", "Deep wells that serve a village for decades."),
            ("🏫", "Schools", "Taps and toilets so children stay in class."),
            ("🧑‍🔧", "Local teams", "Trained technicians who keep water flowing."),
        ],
        "items": [],
    },
    "blog": {
        "words": (
            "blog",
            "magazine",
            "journal",
            "writer",
            "newsletter site",
            "personal blog",
            "articles",
        ),
        "name": "Slow Mornings",
        "tagline": "Essays on living well",
        "headline": "Notes on slowing down in a fast world",
        "sub": "Weekly essays on habits, food, travel and the small things that make a good day.",
        "cta": "Read the latest",
        "colors": ("#57534e", "#d97706"),
        "font": "serif",
        "sections": ["posts", "about", "newsletter"],
        "features": [],
        "items": [
            ("The case for a morning walk", "6 min read"),
            ("Bread, patience and other lessons", "8 min read"),
            ("A week without notifications", "5 min read"),
        ],
    },
}
GENERIC: dict[str, Any] = {
    "name": "Lumina",
    "tagline": "Ideas, beautifully made",
    "headline": "Something wonderful starts here",
    "sub": "A fresh, modern website that tells your story and turns visitors into customers.",
    "cta": "Get started",
    "colors": ("#4f46e5", "#ec4899"),
    "font": "sans",
    "sections": ["features", "about", "stats", "testimonials", "faq", "contact"],
    "features": [
        ("✨", "Thoughtful design", "Every detail considered, nothing in the way."),
        ("🚀", "Fast and reliable", "Pages that load instantly on any device."),
        ("💬", "Real support", "Talk to a person who can help, any day."),
    ],
    "items": [],
}


# ---------------------------------------------------------------------------
# reading the request
# ---------------------------------------------------------------------------


def _kind_of(text: str) -> str:
    lowered = f" {text.lower()} "
    best, score = "", 0
    for kind, spec in KINDS.items():
        hits = sum(1 for word in spec["words"] if word in lowered)
        if hits > score:
            best, score = kind, hits
    return best


_NAME_RE = re.compile(
    r"(?:called|named|name it|call it|rename(?: it)? to|for)\s+[\"“']?"
    r"([A-Z0-9][\w'’.\-]*(?:\s+(?:&|and|of|the|[A-Z0-9][\w'’.\-]*))*)"
)
_CONNECTORS = ("&", "and", "of", "the")
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,40})[\"”]")


def name_from(text: str, default: str = "") -> str:
    """A site's name from the request: quoted words, or "called/named/for X" with a capital letter."""
    quoted = _QUOTED_RE.search(text)
    if quoted and not re.search(
        r"(headline|title|heading)\s*(?:to|:|=)?\s*[\"“]",
        text[: quoted.start() + 1],
        re.I,
    ):
        return quoted.group(1).strip()
    match = _NAME_RE.search(text)
    if match:
        words = match.group(1).split()
        while words and words[-1].lower() in _CONNECTORS:
            words.pop()
        name = " ".join(words).strip(" .-'’")
        if name and not name.lower().startswith(("my ", "our ", "a ", "an ")):
            return name[:40]
    return default


def _color_in(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    hexes = re.findall(r"#[0-9a-fA-F]{6}\b", text)
    if hexes:
        return (hexes[0], hexes[1] if len(hexes) > 1 else hexes[0])
    for word, pair in COLORS.items():
        if re.search(rf"\b{word}\b", lowered):
            return pair
    return None


def spec_from_prompt(prompt: str) -> dict[str, Any]:
    named = name_from(prompt)
    kind = _kind_of(
        prompt.replace(named, " ") if named else prompt
    )  # "Smile Studio" is a name, not an agency
    base = KINDS.get(kind, GENERIC)
    spec: dict[str, Any] = {
        "kind": kind or "landing",
        "name": named or base["name"],
        "tagline": base["tagline"],
        "headline": base["headline"],
        "sub": base["sub"],
        "cta": base["cta"],
        "primary": base["colors"][0],
        "accent": base["colors"][1],
        "font": base["font"],
        "dark": bool(base.get("dark")),
        "sections": list(base["sections"]),
    }
    _apply(spec, prompt, building=True)
    return spec


def _apply(spec: dict[str, Any], text: str, *, building: bool = False) -> list[str]:
    """Change the spec from a request; returns sentences saying what changed."""
    lowered = text.lower()
    notes: list[str] = []
    if re.search(
        r"\b(dark|night)\b(\s+(mode|theme|look|background|version))?", lowered
    ) and not re.search(r"\bnot dark\b|\bno dark\b", lowered):
        if not spec["dark"]:
            notes.append("Switched to a dark theme.")
        spec["dark"] = True
    elif (
        re.search(
            r"\b(light|bright|white)\s*(mode|theme|look|background|version)?\b", lowered
        )
        and not building
    ):
        if spec["dark"]:
            notes.append("Switched to a light theme.")
        spec["dark"] = False
    pair = _color_in(text)
    if pair:
        spec["primary"], spec["accent"] = pair
        notes.append(f"Changed the colors to {pair[0]} and {pair[1]}.")
    if (
        re.search(r"\bserif\b|elegant|classic|luxury", lowered)
        and "sans" not in lowered
    ):
        spec["font"] = "serif"
        notes.append("Using an elegant serif font.")
    elif re.search(r"playful|friendly|rounded|fun\b", lowered):
        spec["font"] = "rounded"
        notes.append("Using a friendly rounded font.")
    elif re.search(r"\bmono(space)?\b|techy|terminal", lowered):
        spec["font"] = "mono"
        notes.append("Using a monospace font.")
    elif re.search(r"\b(modern|clean|sans)\b", lowered) and not building:
        spec["font"] = "sans"
        notes.append("Using a clean sans-serif font.")
    for section, words in SECTION_WORDS.items():
        for word in words:
            if not re.search(rf"\b{re.escape(word)}\b", lowered):
                continue
            removing = re.search(
                rf"\b(remove|delete|drop|hide|without|no)\b[^.]*\b{re.escape(word)}\b",
                lowered,
            )
            if removing and not building:
                if section in spec["sections"]:
                    spec["sections"].remove(section)
                    notes.append(
                        f"Removed the {SECTION_LABELS[section].lower()} section."
                    )
            elif section not in spec["sections"] and (
                building
                or re.search(
                    r"\b(add|include|with|show|put|need|want|create)\b", lowered
                )
            ):
                _insert(spec["sections"], section)
                notes.append(f"Added a {SECTION_LABELS[section].lower()} section.")
            break
    headline = re.search(
        r"(?:headline|heading|title)\s*(?:to|:|=|says?|should be)\s*([^\"“”\n]{3,90})",
        text,
        re.I,
    )
    if headline:
        spec["headline"] = _unquote(headline.group(1)).rstrip(".")
        notes.append("Updated the headline.")
    if not building:
        rename = re.search(
            r"(?:call it|rename(?: it)?(?: to)?|name it|change the name to)\s+([^\"“”.,!\n]{2,40})",
            text,
            re.I,
        )
        if rename:
            spec["name"] = _unquote(rename.group(1))
            notes.append(f"Renamed the site to {spec['name']}.")
        cta = re.search(
            r"(?:button|cta|call to action)\s*(?:to|:|=|says?)\s*([^\"“”\n]{2,40})",
            text,
            re.I,
        )
        if cta:
            spec["cta"] = _unquote(cta.group(1))
            notes.append("Updated the main button.")
    return notes


def _unquote(text: str) -> str:
    """Words a person quoted, without the quotes ("'Fresh bread daily'" -> "Fresh bread daily"); apostrophes stay."""
    return text.strip().strip("'‘’").strip()


def _insert(sections: list[str], section: str) -> None:
    """A new section goes where it reads well: before the closing sections (newsletter, contact) it would follow."""
    rank = ORDER.index(section)
    for index, existing in enumerate(sections):
        if existing in ("newsletter", "contact") and ORDER.index(existing) > rank:
            sections.insert(index, section)
            return
    sections.append(section)


def change(spec: dict[str, Any], request: str) -> tuple[dict[str, Any], list[str]]:
    updated = {**spec, "sections": list(spec.get("sections") or [])}
    notes = _apply(updated, request)
    return updated, notes


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


PHOTO_TOPICS = {
    "restaurant": "restaurant,food",
    "portfolio": "design,workspace",
    "saas": "office,laptop",
    "agency": "office,design",
    "shop": "ceramics,pottery",
    "fitness": "gym,fitness",
    "salon": "salon,hair",
    "clinic": "clinic,doctor",
    "event": "conference,audience",
    "realestate": "house,interior",
    "education": "students,classroom",
    "nonprofit": "water,village",
    "blog": "coffee,morning",
}


def _photo(
    spec: dict[str, Any], seed: str, label: str, w: int = 800, h: int = 600
) -> str:
    """A photo that fits the kind of site (LoremFlickr by topic; the same picture for the same seed). If it cannot
    load, the gradient behind it shows (script.js removes a broken image)."""
    topic = (
        "portrait,person"
        if seed.endswith(" portrait")
        else PHOTO_TOPICS.get(spec.get("kind", ""), "nature,city")
    )
    lock = sum(ord(ch) for ch in seed) % 997 + 1
    return f'<div class="photo"><img src="https://loremflickr.com/{w}/{h}/{topic}?lock={lock}" alt="{_e(label)}" loading="lazy"></div>'


def _items(spec: dict[str, Any]) -> list[tuple[str, ...]]:
    return list(KINDS.get(spec["kind"], GENERIC).get("items") or [])


def _features(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    return list(KINDS.get(spec["kind"], GENERIC).get("features") or GENERIC["features"])


def _section(spec: dict[str, Any], section: str) -> str:
    name = _e(spec["name"])
    items = _items(spec)
    label = SECTION_LABELS[section]
    head = lambda title, sub="": (  # noqa: E731 - a small local template
        f'<div class="section-head reveal"><p class="eyebrow">{_e(label)}</p><h2>{_e(title)}</h2>'
        + (f"<p>{_e(sub)}</p>" if sub else "")
        + "</div>"
    )
    if section == "features":
        cards = "".join(
            f'<article class="card reveal"><span class="icon" aria-hidden="true">{icon}</span><h3>{_e(t)}</h3><p>{_e(d)}</p></article>'
            for icon, t, d in _features(spec)
        )
        return f'<section id="features" class="section">{head("What we do best")}<div class="grid three">{cards}</div></section>'
    if section == "about":
        return (
            f'<section id="about" class="section split"><div class="reveal">{_photo(spec, spec["name"] + " team", "Our team at work", 900, 700)}</div>'
            f'<div class="reveal"><p class="eyebrow">About</p><h2>The story behind {name}</h2>'
            f"<p>{name} started with a simple idea: {_e(spec['tagline'].lower())}, done properly. Today we are a small, "
            "dedicated team that cares about every detail and every person we work with.</p>"
            '<ul class="ticks"><li>Independent and locally owned</li><li>Honest prices, no surprises</li><li>Here to help, seven days a week</li></ul></div></section>'
        )
    if section == "stats":
        stats = [
            ("12k+", "happy customers"),
            ("4.9★", "average rating"),
            ("98%", "would recommend"),
            ("24/7", "support"),
        ]
        return (
            '<section id="stats" class="section band"><div class="stats">'
            + "".join(
                f'<div class="stat reveal"><strong>{v}</strong><span>{_e(t)}</span></div>'
                for v, t in stats
            )
            + "</div></section>"
        )
    if section == "menu":
        rows = items if spec["kind"] == "restaurant" else KINDS["restaurant"]["items"]
        body = "".join(
            f'<li class="reveal"><div><h3>{_e(r[0])}</h3><p>{_e(r[1])}</p></div><span class="price">${_e(r[2])}</span></li>'
            for r in rows
        )
        return f'<section id="menu" class="section">{head("From our kitchen", "A taste of the menu. It changes with the seasons.")}<ul class="menu">{body}</ul></section>'
    if section == "pricing":
        rows = [r for r in items if len(r) == 3] or KINDS["saas"]["items"]
        cards = []
        for index, row in enumerate(rows[:3]):
            featured = index == 1
            cards.append(
                f'<article class="card plan reveal{" featured" if featured else ""}">'
                + ('<span class="badge">Most popular</span>' if featured else "")
                + f'<h3>{_e(row[0])}</h3><p class="muted">{_e(row[1])}</p><p class="amount">${_e(row[2])}</p>'
                + '<ul class="ticks"><li>Everything you need to start</li><li>Friendly support</li><li>Cancel any time</li></ul>'
                + f'<a class="btn {"primary" if featured else "ghost"}" href="#contact">{_e(spec["cta"])}</a></article>'
            )
        return f'<section id="pricing" class="section">{head("Simple, honest pricing", "No hidden fees. Change or cancel any time.")}<div class="grid three">{"".join(cards)}</div></section>'
    if section in ("gallery", "products", "listings", "courses", "posts"):
        pool = {
            "gallery": items
            if spec["kind"] in ("portfolio", "agency")
            else [
                ("Project one", "Recent work"),
                ("Project two", "Recent work"),
                ("Project three", "Recent work"),
                ("Project four", "Recent work"),
            ],
            "products": items if spec["kind"] == "shop" else KINDS["shop"]["items"],
            "listings": items
            if spec["kind"] == "realestate"
            else KINDS["realestate"]["items"],
            "courses": items
            if spec["kind"] == "education"
            else KINDS["education"]["items"],
            "posts": items if spec["kind"] == "blog" else KINDS["blog"]["items"],
        }[section]
        titles = {
            "gallery": "Selected work",
            "products": "Shop the collection",
            "listings": "Homes for sale",
            "courses": "Popular courses",
            "posts": "Latest writing",
        }
        cards = []
        for row in pool:
            price = ""
            if len(row) > 2:
                price = f'<p class="price">${_e(row[2])}</p>'
            action = (
                '<button class="btn ghost add-to-cart" type="button">Add to cart</button>'
                if section == "products"
                else ""
            )
            cards.append(
                f'<article class="card media reveal">{_photo(spec, row[0], row[0])}<div class="card-body"><h3>{_e(row[0])}</h3>'
                f'<p class="muted">{_e(row[1])}</p>{price}{action}</div></article>'
            )
        grid = "four" if section in ("gallery", "products") else "three"
        return f'<section id="{section}" class="section">{head(titles[section])}<div class="grid {grid}">{"".join(cards)}</div></section>'
    if section == "schedule":
        if spec["kind"] == "event":
            rows = [
                ("09:00", "Doors open & coffee"),
                ("10:00", "Keynote: The next ten years of the web"),
                ("11:30", "Workshops and talks in three tracks"),
                ("13:00", "Lunch and community showcase"),
                ("17:30", "Closing party"),
            ]
        else:
            rows = [
                ("Mon & Wed", "06:30 Strength · 18:00 HIIT"),
                ("Tue & Thu", "07:00 Mobility · 19:00 Boxing"),
                ("Friday", "06:30 Strength · 17:30 Team WOD"),
                ("Weekend", "09:00 Community class · 10:30 Yoga"),
            ]
        body = "".join(
            f'<li class="reveal"><time>{_e(t)}</time><span>{_e(d)}</span></li>'
            for t, d in rows
        )
        return f'<section id="schedule" class="section">{head("Schedule")}<ol class="timeline">{body}</ol></section>'
    if section == "team":
        people = [
            ("Maya Patel", "Founder"),
            ("Leo Martins", "Lead"),
            ("Sara Okafor", "Specialist"),
            ("Tom Becker", "Coordinator"),
        ]
        cards = "".join(
            f'<article class="person reveal">{_photo(spec, n + " portrait", n, 500, 500)}<h3>{_e(n)}</h3><p class="muted">{_e(r)}</p></article>'
            for n, r in people
        )
        return f'<section id="team" class="section">{head("The people behind " + spec["name"])}<div class="grid four">{cards}</div></section>'
    if section == "testimonials":
        quotes = [
            (
                "“Absolutely wonderful. Everything was thoughtful, and the team made it easy.”",
                "Priya S.",
            ),
            (
                "“The best decision we made this year. I recommend them to everyone.”",
                "James O.",
            ),
            (
                "“Friendly, fast and genuinely great quality. We will be back.”",
                "Elena R.",
            ),
        ]
        cards = "".join(
            f'<figure class="card quote reveal"><blockquote>{_e(q)}</blockquote><figcaption>— {_e(w)}</figcaption></figure>'
            for q, w in quotes
        )
        return f'<section id="testimonials" class="section">{head("What people say")}<div class="grid three">{cards}</div></section>'
    if section == "faq":
        qa = [
            (
                "How do I get started?",
                f"Use the button at the top of the page or send us a message. {spec['name']} replies within one working day.",
            ),
            (
                "Can I change my mind later?",
                "Yes. You can change or cancel at any time with no fees.",
            ),
            (
                "Do you offer support?",
                "Of course: by email, chat and phone, seven days a week.",
            ),
        ]
        body = "".join(
            f'<details class="reveal"><summary>{_e(q)}</summary><p>{_e(a)}</p></details>'
            for q, a in qa
        )
        return f'<section id="faq" class="section narrow">{head("Questions, answered")}<div class="faq">{body}</div></section>'
    if section == "newsletter":
        return (
            f'<section id="newsletter" class="section band"><div class="newsletter reveal"><h2>Stay in the loop</h2>'
            f"<p>News, offers and ideas from {name}. One email a month, no spam.</p>"
            '<form class="inline-form" data-thanks="Thanks! Please check your inbox to confirm."><label class="sr-only" for="nl-email">Email</label>'
            '<input id="nl-email" type="email" name="email" placeholder="you@example.com" required><button class="btn primary" type="submit">Subscribe</button></form>'
            '<p class="form-note" role="status" aria-live="polite"></p></div></section>'
        )
    if section == "contact":
        return (
            f'<section id="contact" class="section split"><div class="reveal"><p class="eyebrow">Contact</p><h2>Let’s talk</h2>'
            f"<p>Questions, bookings or just hello: we would love to hear from you.</p>"
            '<ul class="contact-list"><li>📍 12 Market Street, Springfield</li><li>📞 (555) 012-3456</li><li>✉️ hello@example.com</li><li>🕘 Mon–Sat, 9:00–18:00</li></ul></div>'
            '<form class="card contact-form reveal" data-thanks="Thank you! We will get back to you within one working day.">'
            '<label>Name<input name="name" autocomplete="name" required></label>'
            '<label>Email<input type="email" name="email" autocomplete="email" required></label>'
            '<label>Message<textarea name="message" rows="4" required></textarea></label>'
            f'<button class="btn primary" type="submit">{_e(spec["cta"])}</button><p class="form-note" role="status" aria-live="polite"></p></form></section>'
        )
    return ""


def render(spec: dict[str, Any]) -> dict[str, str]:
    """The site's files for a spec."""
    name = _e(spec["name"])
    sections = [s for s in spec["sections"] if s in SECTION_LABELS]
    nav = "".join(
        f'<li><a href="#{s}">{_e(SECTION_LABELS[s])}</a></li>'
        for s in sections
        if s not in ("stats", "newsletter")
    )[:4000]
    first = next((s for s in sections if s not in ("stats",)), "contact")
    body = "\n".join(_section(spec, s) for s in sections)
    cart = (
        '<a class="cart" href="#products" aria-label="Cart">🛒 <span id="cart-count">0</span></a>'
        if "products" in sections
        else ""
    )
    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name} — {_e(spec["tagline"])}</title>
  <meta name="description" content="{_e(spec["sub"])}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family={FONTS.get(spec["font"], FONTS["sans"])[1]}&display=swap">
  <link rel="stylesheet" href="styles.css">
</head>
<body class="{"theme-dark" if spec["dark"] else "theme-light"}">
  <a class="skip" href="#main">Skip to content</a>
  <header class="site-header">
    <nav class="nav" aria-label="Main">
      <a class="logo" href="#top"><span class="logo-mark" aria-hidden="true">{name[:1]}</span>{name}</a>
      <button class="menu-toggle" type="button" aria-expanded="false" aria-controls="site-nav" aria-label="Open menu"><span></span><span></span><span></span></button>
      <ul id="site-nav" class="nav-links">{nav}</ul>
      {cart}
      <a class="btn primary nav-cta" href="#{_e(first)}">{_e(spec["cta"])}</a>
    </nav>
  </header>
  <main id="main">
    <section id="top" class="hero">
      <div class="hero-glow" aria-hidden="true"></div>
      <div class="hero-inner reveal">
        <p class="eyebrow">{_e(spec["tagline"])}</p>
        <h1>{_e(spec["headline"])}</h1>
        <p class="lead">{_e(spec["sub"])}</p>
        <div class="hero-actions"><a class="btn primary lg" href="#{_e(first)}">{_e(spec["cta"])}</a><a class="btn ghost lg" href="#{_e(sections[-1] if sections else "top")}">Learn more</a></div>
      </div>
    </section>
{body}
  </main>
  <footer class="site-footer">
    <p><strong>{name}</strong> · {_e(spec["tagline"])}</p>
    <p class="muted">© <span id="year">2026</span> {name}. All rights reserved.</p>
  </footer>
  <script src="script.js"></script>
</body>
</html>
"""
    return {"index.html": index, "styles.css": _css(spec), "script.js": SCRIPT}


def _css(spec: dict[str, Any]) -> str:
    font = FONTS.get(spec["font"], FONTS["sans"])[0]
    return f""":root {{
  --primary: {spec["primary"]};
  --accent: {spec["accent"]};
  --font: {font};
  --radius: 18px;
  --max: 1160px;
}}
.theme-light {{ --bg: #ffffff; --bg-soft: #f6f7fb; --text: #0f172a; --muted: #5b6475; --card: #ffffff; --line: #e6e8f0; }}
.theme-dark {{ --bg: #0b0d14; --bg-soft: #121522; --text: #eef0f7; --muted: #a3a9bb; --card: #151927; --line: #23283a; }}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; font-family: var(--font); background: var(--bg); color: var(--text); line-height: 1.6; -webkit-font-smoothing: antialiased; }}
img {{ max-width: 100%; display: block; }}
a {{ color: inherit; }}
h1, h2, h3 {{ line-height: 1.15; margin: 0 0 .5em; letter-spacing: -0.02em; }}
h1 {{ font-size: clamp(2.4rem, 6vw, 4.4rem); font-weight: 800; }}
h2 {{ font-size: clamp(1.8rem, 3.5vw, 2.6rem); }}
h3 {{ font-size: 1.2rem; }}
p {{ margin: 0 0 1em; }}
.muted {{ color: var(--muted); }}
.sr-only {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }}
.skip {{ position: absolute; left: -999px; top: 8px; background: var(--primary); color: #fff; padding: 8px 14px; border-radius: 8px; z-index: 100; }}
.skip:focus {{ left: 12px; }}
:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
.btn {{ display: inline-flex; align-items: center; justify-content: center; gap: .5rem; padding: .75rem 1.3rem; border-radius: 999px; font-weight: 700; text-decoration: none; border: 2px solid transparent; cursor: pointer; font: inherit; font-weight: 700; transition: transform .2s, box-shadow .2s, background .2s; }}
.btn.primary {{ background: linear-gradient(135deg, var(--primary), var(--accent)); color: #fff; box-shadow: 0 10px 30px -10px var(--primary); }}
.btn.primary:hover {{ transform: translateY(-2px); box-shadow: 0 16px 36px -12px var(--primary); }}
.btn.ghost {{ border-color: var(--line); background: transparent; color: var(--text); }}
.btn.ghost:hover {{ border-color: var(--primary); }}
.btn.lg {{ padding: 1rem 1.7rem; font-size: 1.05rem; }}
.site-header {{ position: sticky; top: 0; z-index: 50; backdrop-filter: blur(14px); background: color-mix(in srgb, var(--bg) 82%, transparent); border-bottom: 1px solid var(--line); }}
.nav {{ max-width: var(--max); margin: 0 auto; padding: .8rem 1.25rem; display: flex; align-items: center; gap: 1.5rem; }}
.logo {{ display: flex; align-items: center; gap: .6rem; font-weight: 800; font-size: 1.15rem; text-decoration: none; }}
.logo-mark {{ display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; color: #fff; background: linear-gradient(135deg, var(--primary), var(--accent)); }}
.nav-links {{ display: flex; gap: 1.4rem; list-style: none; margin: 0 0 0 auto; padding: 0; }}
.nav-links a {{ text-decoration: none; color: var(--muted); font-weight: 600; }}
.nav-links a:hover {{ color: var(--text); }}
.cart {{ text-decoration: none; font-weight: 700; }}
.menu-toggle {{ display: none; margin-left: auto; background: none; border: 0; padding: 8px; cursor: pointer; }}
.menu-toggle span {{ display: block; width: 22px; height: 2px; margin: 5px 0; background: var(--text); border-radius: 2px; }}
.hero {{ position: relative; overflow: hidden; padding: clamp(5rem, 12vw, 9rem) 1.25rem; text-align: center; }}
.hero-glow {{ position: absolute; inset: -30% -10% auto; height: 120%; background: radial-gradient(40% 50% at 30% 30%, color-mix(in srgb, var(--primary) 45%, transparent), transparent), radial-gradient(35% 45% at 70% 40%, color-mix(in srgb, var(--accent) 40%, transparent), transparent); filter: blur(30px); opacity: .55; z-index: -1; }}
.hero-inner {{ max-width: 820px; margin: 0 auto; }}
.lead {{ font-size: clamp(1.1rem, 2vw, 1.3rem); color: var(--muted); max-width: 640px; margin: 0 auto 2rem; }}
.hero-actions {{ display: flex; flex-wrap: wrap; gap: .8rem; justify-content: center; }}
.eyebrow {{ text-transform: uppercase; letter-spacing: .14em; font-size: .78rem; font-weight: 800; color: var(--primary); margin-bottom: .8rem; }}
.theme-dark .eyebrow {{ color: color-mix(in srgb, var(--primary) 55%, white); }}
.section {{ max-width: var(--max); margin: 0 auto; padding: clamp(3.5rem, 8vw, 6rem) 1.25rem; }}
.section.narrow {{ max-width: 820px; }}
.section-head {{ text-align: center; max-width: 680px; margin: 0 auto 3rem; }}
.section-head p:last-child {{ color: var(--muted); }}
.band {{ max-width: none; background: var(--bg-soft); }}
.grid {{ display: grid; gap: 1.4rem; }}
.grid.three {{ grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); }}
.grid.four {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 1.6rem; box-shadow: 0 20px 40px -32px rgba(15, 23, 42, .45); transition: transform .25s, box-shadow .25s; }}
.card:hover {{ transform: translateY(-4px); box-shadow: 0 30px 50px -30px rgba(15, 23, 42, .5); }}
.card .icon {{ font-size: 1.8rem; display: inline-grid; place-items: center; width: 56px; height: 56px; border-radius: 16px; background: color-mix(in srgb, var(--primary) 14%, transparent); margin-bottom: 1rem; }}
.card.media {{ padding: 0; overflow: hidden; }}
.card-body {{ padding: 1.2rem 1.3rem 1.4rem; }}
.photo {{ aspect-ratio: 4 / 3; background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 70%, #000), var(--accent)); border-radius: inherit; overflow: hidden; }}
.photo img {{ width: 100%; height: 100%; object-fit: cover; }}
.split {{ display: grid; grid-template-columns: 1fr 1fr; gap: clamp(2rem, 5vw, 4rem); align-items: center; }}
.split .photo {{ border-radius: var(--radius); aspect-ratio: 5 / 4; }}
.ticks {{ list-style: none; padding: 0; margin: 1rem 0 1.5rem; }}
.ticks li {{ padding-left: 1.8rem; position: relative; margin: .5rem 0; }}
.ticks li::before {{ content: "✓"; position: absolute; left: 0; color: var(--primary); font-weight: 800; }}
.stats {{ max-width: var(--max); margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1.5rem; text-align: center; }}
.stat strong {{ display: block; font-size: clamp(2rem, 4vw, 3rem); background: linear-gradient(135deg, var(--primary), var(--accent)); -webkit-background-clip: text; background-clip: text; color: transparent; }}
.stat span {{ color: var(--muted); font-weight: 600; }}
.menu {{ list-style: none; padding: 0; max-width: 760px; margin: 0 auto; }}
.menu li {{ display: flex; justify-content: space-between; gap: 1rem; padding: 1.2rem 0; border-bottom: 1px dashed var(--line); }}
.menu h3 {{ margin-bottom: .2rem; }}
.menu p {{ margin: 0; color: var(--muted); }}
.price {{ font-weight: 800; color: var(--primary); font-size: 1.15rem; }}
.theme-dark .price {{ color: color-mix(in srgb, var(--primary) 50%, white); }}
.plan {{ position: relative; display: flex; flex-direction: column; }}
.plan .amount {{ font-size: 2.6rem; font-weight: 800; margin: .4rem 0; }}
.plan .btn {{ margin-top: auto; }}
.plan.featured {{ border: 2px solid var(--primary); transform: scale(1.03); }}
.badge {{ position: absolute; top: -12px; right: 18px; background: linear-gradient(135deg, var(--primary), var(--accent)); color: #fff; font-size: .75rem; font-weight: 800; padding: .3rem .7rem; border-radius: 999px; }}
.timeline {{ list-style: none; padding: 0; max-width: 760px; margin: 0 auto; border-left: 3px solid var(--line); }}
.timeline li {{ position: relative; padding: .4rem 0 1.4rem 1.6rem; display: grid; gap: .2rem; }}
.timeline li::before {{ content: ""; position: absolute; left: -9px; top: .7rem; width: 15px; height: 15px; border-radius: 50%; background: linear-gradient(135deg, var(--primary), var(--accent)); }}
.timeline time {{ font-weight: 800; color: var(--primary); }}
.person {{ text-align: center; }}
.person .photo {{ aspect-ratio: 1; border-radius: 50%; width: 160px; margin: 0 auto 1rem; }}
.quote blockquote {{ margin: 0 0 1rem; font-size: 1.1rem; }}
.quote figcaption {{ color: var(--muted); font-weight: 700; }}
.faq details {{ border: 1px solid var(--line); border-radius: 14px; padding: 1rem 1.2rem; margin-bottom: .8rem; background: var(--card); }}
.faq summary {{ cursor: pointer; font-weight: 700; }}
.faq details p {{ margin: .8rem 0 0; color: var(--muted); }}
.newsletter {{ max-width: 640px; margin: 0 auto; text-align: center; }}
.inline-form {{ display: flex; gap: .6rem; max-width: 480px; margin: 1.2rem auto 0; }}
.inline-form input {{ flex: 1; }}
input, textarea {{ width: 100%; font: inherit; padding: .8rem 1rem; border-radius: 12px; border: 1px solid var(--line); background: var(--bg); color: var(--text); }}
input:focus, textarea:focus {{ border-color: var(--primary); outline: none; box-shadow: 0 0 0 4px color-mix(in srgb, var(--primary) 20%, transparent); }}
.contact-form {{ display: grid; gap: 1rem; }}
.contact-form label {{ display: grid; gap: .35rem; font-weight: 600; }}
.contact-list {{ list-style: none; padding: 0; display: grid; gap: .6rem; }}
.form-note {{ min-height: 1.4em; color: var(--primary); font-weight: 700; margin: 0; }}
.site-footer {{ border-top: 1px solid var(--line); padding: 2.5rem 1.25rem; text-align: center; }}
.reveal {{ opacity: 0; transform: translateY(18px); transition: opacity .7s ease, transform .7s ease; }}
.reveal.visible {{ opacity: 1; transform: none; }}
@media (prefers-reduced-motion: reduce) {{ .reveal {{ opacity: 1; transform: none; transition: none; }} html {{ scroll-behavior: auto; }} }}
@media (max-width: 820px) {{
  .menu-toggle {{ display: block; }}
  .nav-links {{ display: none; position: absolute; top: 100%; left: 0; right: 0; flex-direction: column; gap: 0; padding: .5rem 1.25rem 1rem; background: var(--bg); border-bottom: 1px solid var(--line); }}
  .nav-links.open {{ display: flex; }}
  .nav-links a {{ display: block; padding: .7rem 0; }}
  .nav-cta {{ display: none; }}
  .split {{ grid-template-columns: 1fr; }}
  .plan.featured {{ transform: none; }}
  .inline-form {{ flex-direction: column; }}
}}
"""


SCRIPT = """// Menu on phones, reveal on scroll, friendly forms and a tiny cart.
(function () {
  var toggle = document.querySelector(".menu-toggle");
  var menu = document.getElementById("site-nav");
  if (toggle && menu) {
    toggle.addEventListener("click", function () {
      var open = menu.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    menu.addEventListener("click", function (event) {
      if (event.target.closest("a")) { menu.classList.remove("open"); toggle.setAttribute("aria-expanded", "false"); }
    });
  }
  var items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add("visible"); observer.unobserve(entry.target); }
      });
    }, { threshold: 0.12 });
    items.forEach(function (item) { observer.observe(item); });
  } else {
    items.forEach(function (item) { item.classList.add("visible"); });
  }
  document.querySelectorAll("form[data-thanks]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var note = form.querySelector(".form-note") || (form.parentNode && form.parentNode.querySelector(".form-note"));
      if (note) note.textContent = form.getAttribute("data-thanks");
      form.reset();
    });
  });
  var count = 0;
  var badge = document.getElementById("cart-count");
  document.querySelectorAll(".add-to-cart").forEach(function (button) {
    button.addEventListener("click", function () {
      count += 1;
      if (badge) badge.textContent = String(count);
      button.textContent = "Added ✓";
      setTimeout(function () { button.textContent = "Add to cart"; }, 1400);
    });
  });
  document.querySelectorAll(".photo img").forEach(function (img) {
    var drop = function () { img.remove(); };
    if (img.complete && img.naturalWidth === 0) drop(); else img.addEventListener("error", drop);
  });
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
"""


def answer(
    spec: dict[str, Any],
    files: dict[str, str],
    *,
    plan: str,
    summary: str,
    previous: dict[str, str] | None = None,
) -> str:
    """The designer's output in the model's tagged format (only files that changed)."""
    parts = [f"<plan>{plan}</plan>\n"]
    for path, content in files.items():
        if previous is not None and previous.get(path) == content:
            continue
        parts.append(f'<file path="{path}">\n{content}</file>\n')
    parts.append(f"<summary>{summary}</summary>")
    return "".join(parts)
