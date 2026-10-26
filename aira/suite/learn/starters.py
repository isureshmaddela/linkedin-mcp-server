"""Starter decks a person adds in one click: written here, so they work without a writing model.

Each deck is ``{"key", "title", "description", "emoji", "age_group", "keywords", "cards": [(front, back, hint)]}``.
``age_group`` is who it is written for ("child", "adult", "senior" or "all"); ``keywords`` let "make a deck on any
topic" fall back to a matching starter deck when no model is running.
"""

from __future__ import annotations

from typing import Any


def _times(first: int, last: int, low: int, high: int) -> list[tuple[str, str, str]]:
    return [
        (f"{a} × {b}", str(a * b), f"{a} groups of {b}")
        for a in range(first, last + 1)
        for b in range(low, high + 1)
    ]


STARTERS: list[dict[str, Any]] = [
    {
        "key": "times-2-6",
        "title": "Times tables 2 to 6",
        "description": "The first times tables, a few at a time.",
        "emoji": "✖️",
        "age_group": "child",
        "keywords": ("times", "multiplication", "multiply", "tables", "maths", "math"),
        "cards": _times(2, 6, 2, 7),
    },
    {
        "key": "times-7-12",
        "title": "Times tables 7 to 12",
        "description": "The harder times tables.",
        "emoji": "🔢",
        "age_group": "child",
        "keywords": ("times", "multiplication", "multiply", "tables"),
        "cards": _times(7, 12, 2, 6),
    },
    {
        "key": "world-capitals",
        "title": "World capitals",
        "description": "Capital cities around the world.",
        "emoji": "🌍",
        "age_group": "all",
        "keywords": (
            "capital",
            "capitals",
            "countries",
            "geography",
            "world",
            "cities",
        ),
        "cards": [
            ("France", "Paris", "City of the Eiffel Tower"),
            ("Japan", "Tokyo", ""),
            ("India", "New Delhi", ""),
            ("Australia", "Canberra", "Not Sydney"),
            ("Canada", "Ottawa", "Not Toronto"),
            ("Brazil", "Brasília", ""),
            ("Egypt", "Cairo", "On the Nile"),
            ("Kenya", "Nairobi", ""),
            ("Germany", "Berlin", ""),
            ("Italy", "Rome", ""),
            ("Spain", "Madrid", ""),
            ("United Kingdom", "London", ""),
            ("United States", "Washington, D.C.", "Not New York"),
            ("Mexico", "Mexico City", ""),
            ("Argentina", "Buenos Aires", ""),
            ("China", "Beijing", ""),
            ("South Korea", "Seoul", ""),
            ("Russia", "Moscow", ""),
            ("Turkey", "Ankara", "Not Istanbul"),
            ("South Africa", "Pretoria", "One of three capitals"),
            ("Nigeria", "Abuja", "Not Lagos"),
            ("Norway", "Oslo", ""),
            ("Thailand", "Bangkok", ""),
            ("New Zealand", "Wellington", ""),
            ("Peru", "Lima", ""),
        ],
    },
    {
        "key": "spanish-phrases",
        "title": "Everyday Spanish",
        "description": "Phrases for a trip or a chat.",
        "emoji": "🇪🇸",
        "age_group": "all",
        "keywords": ("spanish", "español", "espanol", "spain", "language"),
        "cards": [
            ("Hello", "Hola", ""),
            ("Good morning", "Buenos días", ""),
            ("Good night", "Buenas noches", ""),
            ("Please", "Por favor", ""),
            ("Thank you", "Gracias", ""),
            ("You're welcome", "De nada", ""),
            ("Excuse me", "Perdón / Disculpe", ""),
            ("How are you?", "¿Cómo estás?", ""),
            ("I'm fine, thanks", "Estoy bien, gracias", ""),
            ("What is your name?", "¿Cómo te llamas?", ""),
            ("My name is ...", "Me llamo ...", ""),
            ("Where is the bathroom?", "¿Dónde está el baño?", ""),
            ("How much is it?", "¿Cuánto cuesta?", ""),
            ("I don't understand", "No entiendo", ""),
            ("Do you speak English?", "¿Hablas inglés?", ""),
            ("Water", "Agua", ""),
            ("The bill, please", "La cuenta, por favor", ""),
            ("Yes / No", "Sí / No", ""),
            ("See you later", "Hasta luego", ""),
            ("Goodbye", "Adiós", ""),
        ],
    },
    {
        "key": "french-phrases",
        "title": "Everyday French",
        "description": "Phrases for a trip or a chat.",
        "emoji": "🇫🇷",
        "age_group": "all",
        "keywords": ("french", "français", "francais", "france", "language"),
        "cards": [
            ("Hello", "Bonjour", ""),
            ("Good evening", "Bonsoir", ""),
            ("Please", "S'il vous plaît", ""),
            ("Thank you", "Merci", ""),
            ("You're welcome", "De rien", ""),
            ("Excuse me", "Excusez-moi", ""),
            ("How are you?", "Comment allez-vous ?", ""),
            ("I'm fine, thanks", "Je vais bien, merci", ""),
            ("What is your name?", "Comment vous appelez-vous ?", ""),
            ("My name is ...", "Je m'appelle ...", ""),
            ("Where is the station?", "Où est la gare ?", ""),
            ("How much is it?", "C'est combien ?", ""),
            ("I don't understand", "Je ne comprends pas", ""),
            ("Do you speak English?", "Parlez-vous anglais ?", ""),
            ("Water", "De l'eau", ""),
            ("The bill, please", "L'addition, s'il vous plaît", ""),
            ("Yes / No", "Oui / Non", ""),
            ("Good luck", "Bonne chance", ""),
            ("See you soon", "À bientôt", ""),
            ("Goodbye", "Au revoir", ""),
        ],
    },
    {
        "key": "spelling-kids",
        "title": "Spelling for children",
        "description": "Words children often spell wrong, with a clue for each.",
        "emoji": "✏️",
        "age_group": "child",
        "keywords": ("spelling", "spell", "words", "english", "reading"),
        "cards": [
            ("The day after Tuesday", "Wednesday", "There is a silent d"),
            ("The month after January", "February", "There is an r after the b"),
            ("A word for 'pals'", "friends", "i before e"),
            (
                "Not now, but ...",
                "because",
                "Big Elephants Can Always Understand Small Elephants",
            ),
            ("Shows something belongs to them", "their", "Not 'there'"),
            ("Something to eat at night", "dinner", "Two n's"),
            ("A place to learn", "school", "s-ch-oo-l"),
            ("Really nice", "beautiful", "b-e-a-u"),
            ("The opposite of 'easy'", "difficult", "Two f's"),
            ("Very big", "enormous", ""),
            ("To find out by asking", "question", "Starts with qu"),
            ("You walk in through it", "door", "Two o's"),
            ("What you hear with", "ears", ""),
            ("The colour of grass", "green", "Two e's"),
            ("A number after nine", "ten", ""),
            ("Your mother's mother", "grandmother", "grand + mother"),
            ("Something that is not true", "pretend", ""),
            ("To say you are sorry", "apologise", "Also spelled apologize"),
            ("A word for 'maybe'", "perhaps", "Ends in -haps"),
            ("When you are not sure", "probably", ""),
        ],
    },
    {
        "key": "healthy-living",
        "title": "Healthy living",
        "description": "Simple facts for feeling well every day.",
        "emoji": "🥦",
        "age_group": "senior",
        "keywords": (
            "health",
            "healthy",
            "wellbeing",
            "food",
            "exercise",
            "sleep",
            "nutrition",
        ),
        "cards": [
            (
                "How many minutes of moderate activity a week do health guides suggest for adults?",
                "About 150 minutes",
                "Around 30 minutes, 5 days a week",
            ),
            (
                "Why drink water through the day?",
                "It helps your body work well; older people often feel thirst less",
                "",
            ),
            (
                "A simple way to keep balance and avoid falls",
                "Gentle strength and balance exercise, like standing on one leg near a chair",
                "",
            ),
            ("How many portions of fruit and vegetables a day?", "At least five", ""),
            (
                "Why add fibre to meals?",
                "It helps digestion and keeps you fuller for longer",
                "Wholegrains, beans, vegetables",
            ),
            ("How much sleep do most adults need?", "About 7 to 9 hours", ""),
            (
                "A good habit before bed",
                "Same bedtime each night and screens off for an hour",
                "",
            ),
            ("Why cut down on salt?", "Too much salt raises blood pressure", ""),
            (
                "What does a short walk after a meal help with?",
                "Blood sugar and digestion",
                "",
            ),
            (
                "Why keep in touch with friends?",
                "Company is good for mood and memory",
                "",
            ),
            (
                "One way to keep your mind sharp",
                "Learn something new: a language, puzzles, a hobby",
                "",
            ),
            ("Why wear sunscreen?", "It protects skin from sun damage at any age", ""),
            (
                "When should you see a doctor about a new symptom?",
                "If it lasts, gets worse, or worries you",
                "In an emergency call your local emergency number",
            ),
            (
                "Why stand up often when sitting a lot?",
                "Moving every half hour helps circulation",
                "",
            ),
            (
                "A healthy snack idea",
                "A piece of fruit, a handful of nuts, or yoghurt",
                "",
            ),
            (
                "Why check your eyes and hearing regularly?",
                "Early help keeps you safe and connected",
                "",
            ),
            (
                "What helps with stress?",
                "Slow breathing, a walk, talking to someone",
                "",
            ),
            (
                "Why take medicines at the same time each day?",
                "It keeps the dose steady and is easier to remember",
                "",
            ),
        ],
    },
    {
        "key": "safe-online",
        "title": "Staying safe online",
        "description": "Spot scams and keep your accounts safe.",
        "emoji": "🛡️",
        "age_group": "senior",
        "keywords": (
            "scam",
            "scams",
            "safe",
            "safety",
            "online",
            "internet",
            "fraud",
            "phishing",
            "security",
        ),
        "cards": [
            (
                "A message says your bank account is locked and asks you to click a link. What do you do?",
                "Don't click. Call your bank on the number on your card.",
                "",
            ),
            (
                "Will a real bank ever ask for your full PIN or password?",
                "No, never.",
                "",
            ),
            (
                "Someone calls saying they are from tech support and your computer has a virus.",
                "Hang up. Real companies don't call out of the blue.",
                "",
            ),
            (
                "A 'grandchild' messages from a new number asking for money urgently.",
                "Call them on their old number or ask a question only they know.",
                "Scammers rush you",
            ),
            (
                "What makes a strong password?",
                "Three random words together, different for each account",
                "",
            ),
            (
                "What is two-step verification?",
                "A second check, like a code sent to your phone, when you sign in",
                "",
            ),
            (
                "You won a prize you never entered. It asks for a fee.",
                "It's a scam. Real prizes don't ask you to pay.",
                "",
            ),
            (
                "How can you check a website is the real one?",
                "Type the address yourself or use a saved bookmark",
                "",
            ),
            (
                "Someone asks you to pay with gift cards.",
                "It's a scam. No real business asks for gift cards.",
                "",
            ),
            (
                "Should you share a code someone sends to your phone?",
                "Never share it, even if they say they're from the bank",
                "",
            ),
            (
                "An email has spelling mistakes and an odd sender address.",
                "Be suspicious: it's probably fake",
                "",
            ),
            (
                "What should you do with software updates?",
                "Install them: they fix security holes",
                "",
            ),
            (
                "A pop-up says 'Call this number now!'",
                "Close the browser. Don't call.",
                "",
            ),
            (
                "Is it safe to use public Wi-Fi for banking?",
                "Better to wait for your home Wi-Fi or use mobile data",
                "",
            ),
            (
                "Someone you met online asks for money.",
                "Don't send it. Talk to family first.",
                "Romance scams are common",
            ),
            (
                "Where can you report a scam?",
                "Your bank, and your country's fraud reporting service",
                "",
            ),
            (
                "A friend's account sends a strange link.",
                "Check with the friend another way before clicking",
                "",
            ),
            (
                "What should you do if you think you were scammed?",
                "Call your bank straight away and change your passwords",
                "",
            ),
        ],
    },
    {
        "key": "first-aid",
        "title": "First aid basics",
        "description": "What to do first. In an emergency, call your local emergency number.",
        "emoji": "⛑️",
        "age_group": "all",
        "keywords": ("first aid", "aid", "emergency", "injury", "safety", "cpr"),
        "cards": [
            (
                "The first thing to check in any emergency",
                "Is it safe for you to help? Then call for help",
                "",
            ),
            (
                "Someone is not breathing normally",
                "Call emergency services and start chest compressions",
                "Push hard and fast in the centre of the chest",
            ),
            (
                "How fast should chest compressions be?",
                "About 100 to 120 a minute",
                "To the beat of 'Stayin' Alive'",
            ),
            (
                "A small burn",
                "Cool it under cool running water for 20 minutes",
                "No ice, no butter",
            ),
            ("A cut that is bleeding", "Press firmly on it with a clean cloth", ""),
            (
                "Someone is choking and can't breathe",
                "Up to 5 back blows, then up to 5 abdominal thrusts",
                "",
            ),
            (
                "Signs of a stroke (FAST)",
                "Face drooping, Arm weakness, Speech problems: Time to call",
                "",
            ),
            ("Someone faints", "Lay them down and raise their legs a little", ""),
            (
                "A nosebleed",
                "Sit up, lean forward, pinch the soft part of the nose for 10 minutes",
                "",
            ),
            (
                "A sprained ankle",
                "Rest, ice wrapped in cloth, gentle pressure, raise it",
                "",
            ),
            (
                "Someone has a seizure",
                "Move things away, cushion the head, don't hold them down, time it",
                "",
            ),
            (
                "A bee sting",
                "Scrape the sting out, wash, cold pack; watch for swelling of face or breathing trouble",
                "",
            ),
            (
                "Signs of a heart attack",
                "Chest pain or pressure, pain in arm or jaw, sweating, breathlessness",
                "Call emergency services",
            ),
            (
                "Someone is unconscious but breathing",
                "Put them in the recovery position and call for help",
                "",
            ),
            ("Heat exhaustion", "Move to a cool place, sip water, loosen clothing", ""),
            (
                "What should a home first-aid kit have?",
                "Plasters, bandages, gloves, antiseptic wipes, scissors, tweezers",
                "",
            ),
        ],
    },
    {
        "key": "solar-system",
        "title": "The solar system",
        "description": "Planets, the Sun and the Moon.",
        "emoji": "🪐",
        "age_group": "all",
        "keywords": (
            "space",
            "planets",
            "planet",
            "solar",
            "astronomy",
            "stars",
            "moon",
            "sun",
        ),
        "cards": [
            ("The planet closest to the Sun", "Mercury", ""),
            ("The hottest planet", "Venus", "Thick clouds trap the heat"),
            ("The planet we live on", "Earth", ""),
            ("The red planet", "Mars", ""),
            ("The biggest planet", "Jupiter", ""),
            ("The planet with the famous rings", "Saturn", ""),
            ("The planet that spins on its side", "Uranus", ""),
            ("The planet farthest from the Sun", "Neptune", ""),
            ("How many planets are in our solar system?", "Eight", ""),
            ("What is the Sun?", "A star", ""),
            (
                "How long does Earth take to go round the Sun?",
                "About 365 days (one year)",
                "",
            ),
            ("How long does Earth take to spin once?", "About 24 hours (one day)", ""),
            ("What is Earth's natural satellite?", "The Moon", ""),
            ("Who first walked on the Moon (1969)?", "Neil Armstrong", ""),
            ("What is a light-year?", "The distance light travels in a year", ""),
            ("What do we call a dwarf planet beyond Neptune?", "Pluto", ""),
            ("What is between Mars and Jupiter?", "The asteroid belt", ""),
            ("Why does the Moon shine?", "It reflects sunlight", ""),
        ],
    },
]

BY_KEY = {deck["key"]: deck for deck in STARTERS}


def match(topic: str) -> dict[str, Any] | None:
    """The starter deck whose keywords best match a topic, if any."""
    words = {word.strip(".,!?").lower() for word in topic.split()}
    text = topic.lower()
    best, score = None, 0
    for deck in STARTERS:
        hits = sum(
            1
            for keyword in deck["keywords"]
            if keyword in words or (" " in keyword and keyword in text)
        )
        if hits > score:
            best, score = deck, hits
    return best
