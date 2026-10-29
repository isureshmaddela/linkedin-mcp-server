"""The stages of a life, from before birth to remembrance, and what helps in each.

A life is read by age (in months, so a baby's first year is precise) into one of :data:`STAGES`. Each stage says
what matters then, in seven areas of life (:data:`AREAS`), as a checklist of things to do (``items``) and the
moments worth keeping (``milestones``). An item says how it is done (``do``):

* ``check``: tick it when it is done;
* ``habit`` / ``goal`` / ``goal:money``: turn it into a habit or a goal in Life;
* ``document:<kind>``: add that paper to the vault;
* ``record:<kind>``: write it in the life book (``growth``, ``vaccine``, ``checkup``, ...);
* ``wishes``: fill in the wishes and legacy page; ``interests``: choose interests;
* ``app:<id>``: done in another Monosage app (the Health app's medicine reminders).

An item marked ``carry`` stays on the list in later stages until it is done (a will made at 30 or at 70).

:data:`INTERESTS` and :data:`GUIDANCE` turn what a person loves into ideas for their age: something to learn, to
do, a career to explore, a way to earn (often with a Monosage app) or to give back. The words are general,
friendly and safe for everyone: health items say what to ask a doctor about, never doses or diagnoses.
"""

from __future__ import annotations

from typing import Any

AREAS: tuple[dict[str, str], ...] = (
    {"id": "health", "title": "Health and body"},
    {"id": "mind", "title": "Mind and learning"},
    {"id": "heart", "title": "Family and friends"},
    {"id": "money", "title": "Money"},
    {"id": "purpose", "title": "Work and purpose"},
    {"id": "papers", "title": "Papers and rights"},
    {"id": "legacy", "title": "Legacy and wishes"},
)
AREA_IDS = tuple(area["id"] for area in AREAS)

# The goal area a stage item or an interest becomes when it is turned into a goal.
GOAL_AREA = {
    "health": "health",
    "mind": "learning",
    "heart": "family",
    "money": "money",
    "purpose": "career",
    "papers": "personal",
    "legacy": "family",
}


def _item(
    stage: str,
    key: str,
    area: str,
    title: str,
    description: str,
    do: str = "check",
    *,
    carry: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"{stage}.{key}",
        "area": area,
        "title": title,
        "description": description,
        "do": do,
        "carry": carry,
    }


STAGES: tuple[dict[str, Any], ...] = (
    {
        "id": "pregnancy",
        "title": "Before birth",
        "label": "Pregnancy",
        "from_months": -10,
        "to_months": 0,
        "description": "A healthy pregnancy and a calm welcome: check-ups, a plan for the birth and a home ready "
        "for the baby.",
        "items": (
            _item(
                "pregnancy",
                "checkups",
                "health",
                "Go to every pregnancy check-up",
                "Regular visits catch problems early. Ask about scans, blood tests and the vitamins to take.",
                "record:checkup",
            ),
            _item(
                "pregnancy",
                "birth_plan",
                "health",
                "Make a birth plan",
                "Where the baby will be born, who will be there and how you will get there, day or night.",
            ),
            _item(
                "pregnancy",
                "home_ready",
                "health",
                "Get the home ready and safe",
                "A safe place to sleep, a car seat, and what you need for the first weeks.",
            ),
            _item(
                "pregnancy",
                "baby_care",
                "mind",
                "Learn the basics of baby care",
                "Feeding, bathing, sleep and when to call a doctor: a class or a trusted guide helps.",
            ),
            _item(
                "pregnancy",
                "share_care",
                "heart",
                "Agree how you will share the care",
                "Night feeds, leave from work and help from family: talk about it before the baby comes.",
            ),
            _item(
                "pregnancy",
                "first_year_money",
                "money",
                "Plan the money for the first year",
                "Leave from work, medical costs, clothes, a cot, nappies and childcare.",
                "goal:money",
            ),
            _item(
                "pregnancy",
                "register_birth",
                "papers",
                "Find out how to register the birth",
                "Ask the hospital or your local office what you need for the birth certificate.",
            ),
        ),
        "milestones": (
            {"title": "Heard the heartbeat"},
            {"title": "First scan"},
            {"title": "Chose a name"},
            {"title": "Baby shower or blessing"},
        ),
    },
    {
        "id": "baby",
        "title": "Baby",
        "label": "0 to 1 year",
        "from_months": 0,
        "to_months": 12,
        "description": "Love, food, sleep and safety. Every smile, sound and cuddle builds a baby's brain.",
        "items": (
            _item(
                "baby",
                "vaccines",
                "health",
                "Keep vaccines on schedule",
                "Follow your country's schedule and keep the record here; Monosage reminds you of the next dose.",
                "record:vaccine",
            ),
            _item(
                "baby",
                "growth",
                "health",
                "Go to every baby check-up",
                "Weight, length and head size show healthy growth. Write them here after each visit.",
                "record:growth",
            ),
            _item(
                "baby",
                "feeding",
                "health",
                "Feed well",
                "Breast milk or formula at first; ask your doctor when to start other foods, usually around six "
                "months.",
            ),
            _item(
                "baby",
                "safe_sleep",
                "health",
                "Safe sleep, every time",
                "On the back, on a firm flat surface, with nothing soft in the cot.",
            ),
            _item(
                "baby",
                "talk_read",
                "mind",
                "Talk, sing and read every day",
                "Babies learn language from the voices they hear. Name things, describe your day, share picture "
                "books.",
                "habit",
            ),
            _item(
                "baby",
                "cuddles",
                "heart",
                "Hold, smile and answer their cries",
                "Being comforted quickly teaches a baby that the world is safe. You cannot spoil a baby with love.",
            ),
            _item(
                "baby",
                "parent_care",
                "heart",
                "Look after yourself too",
                "Sleep when you can, accept help, and see a doctor if you feel low for more than two weeks.",
            ),
            _item(
                "baby",
                "birth_certificate",
                "papers",
                "Register the birth and keep the certificate",
                "The birth certificate is the first paper of a life. Keep a copy in the vault.",
                "document:birth_certificate",
            ),
            _item(
                "baby",
                "id_insurance",
                "papers",
                "Get the baby's ID and health cover",
                "A national ID or passport if you need one, and add the baby to your health insurance.",
                "document:id_card",
            ),
            _item(
                "baby",
                "future_fund",
                "money",
                "Start saving for their future",
                "Even a small amount each month grows a lot over 18 years.",
                "goal:money",
            ),
        ),
        "milestones": (
            {"title": "First smile"},
            {"title": "Rolled over"},
            {"title": "Sat up alone"},
            {"title": "First tooth"},
            {"title": "First word"},
            {"title": "First steps"},
        ),
    },
    {
        "id": "toddler",
        "title": "Toddler",
        "label": "1 to 3 years",
        "from_months": 12,
        "to_months": 36,
        "description": "Walking, talking and exploring. Play is how toddlers learn, and calm routines are how they "
        "feel safe.",
        "items": (
            _item(
                "toddler",
                "vaccines",
                "health",
                "Keep vaccines and check-ups on time",
                "Write each dose here and Monosage reminds you of the next one.",
                "record:vaccine",
            ),
            _item(
                "toddler",
                "dentist",
                "health",
                "First visit to the dentist",
                "By the first birthday or when the first teeth come; brush twice a day with a little toothpaste.",
                "record:checkup",
            ),
            _item(
                "toddler",
                "childproof",
                "health",
                "Make the home safe for an explorer",
                "Gates on stairs, locks on cupboards, medicines and cleaners out of reach, covers on sockets.",
            ),
            _item(
                "toddler",
                "screens",
                "health",
                "Keep screens small",
                "Almost none before two apart from video calls; after that, short times, watched together.",
            ),
            _item(
                "toddler",
                "read",
                "mind",
                "Read together every day",
                "Ten minutes of stories a day builds words, attention and closeness.",
                "habit",
            ),
            _item(
                "toddler",
                "play",
                "mind",
                "Play, draw, build and pretend",
                "Blocks, crayons, water, sand and pretend games grow the mind and the hands.",
            ),
            _item(
                "toddler",
                "development",
                "mind",
                "Watch speech and movement",
                "If they are not walking or saying any words by 18 months, ask your doctor. Early help works best.",
            ),
            _item(
                "toddler",
                "routines",
                "heart",
                "Simple routines and gentle limits",
                "Regular meals and bedtimes, and a calm, consistent no, help a toddler feel secure.",
            ),
            _item(
                "toddler",
                "other_children",
                "heart",
                "Time with other children",
                "Playgroups, parks and family visits teach sharing and taking turns.",
            ),
        ),
        "milestones": (
            {"title": "First steps"},
            {"title": "First words"},
            {"title": "First sentence"},
            {"title": "Toilet trained"},
            {"title": "First day at nursery or playgroup"},
            {"title": "First haircut"},
        ),
    },
    {
        "id": "preschool",
        "title": "Early childhood",
        "label": "3 to 5 years",
        "from_months": 36,
        "to_months": 72,
        "description": "Curiosity, friendship and getting ready for school. Endless questions are a sign of a "
        "growing mind.",
        "items": (
            _item(
                "preschool",
                "eyes_ears",
                "health",
                "Eye and hearing checks",
                "Small problems with sight or hearing can look like trouble with learning. A check before school "
                "helps.",
                "record:checkup",
            ),
            _item(
                "preschool",
                "vaccines",
                "health",
                "Vaccines before school",
                "Many schools ask for the vaccine record. Keep it up to date here.",
                "record:vaccine",
            ),
            _item(
                "preschool",
                "active",
                "health",
                "Active play every day",
                "Running, climbing and dancing for at least three hours across the day.",
                "habit",
            ),
            _item(
                "preschool",
                "body_safety",
                "health",
                "Teach body safety",
                "Their body belongs to them, which grown-ups to tell, and your phone number and address.",
            ),
            _item(
                "preschool",
                "preschool",
                "mind",
                "Choose a preschool or nursery",
                "Visit a few, ask how they play and learn, and trust how your child feels there.",
                "record:education",
            ),
            _item(
                "preschool",
                "letters",
                "mind",
                "Letters, numbers and lots of stories",
                "Counting stairs, finding letters on signs and a story every night.",
                "habit",
            ),
            _item(
                "preschool",
                "interests",
                "mind",
                "Notice what they love",
                "Music, animals, building, drawing: choose their interests here and see ideas for their age.",
                "interests",
            ),
            _item(
                "preschool",
                "feelings",
                "heart",
                "Name feelings together",
                "Help them say I am angry or I am sad, and show calm ways to handle it.",
            ),
            _item(
                "preschool",
                "chores",
                "purpose",
                "Small jobs at home",
                "Putting toys away and laying the table: helping builds confidence.",
            ),
        ),
        "milestones": (
            {"title": "First day of preschool"},
            {"title": "Rode a tricycle or bike"},
            {"title": "Wrote their name"},
            {"title": "First best friend"},
            {"title": "Learned to swim"},
        ),
    },
    {
        "id": "child",
        "title": "School years",
        "label": "6 to 12 years",
        "from_months": 72,
        "to_months": 156,
        "description": "School, friends and finding what they are good at. Effort, kindness and confidence matter "
        "more than marks.",
        "items": (
            _item(
                "child",
                "school_start",
                "mind",
                "A good start at school",
                "Meet the teachers, keep a homework routine and a quiet place to work.",
                "record:education",
            ),
            _item(
                "child",
                "reading",
                "mind",
                "Read for fun every day",
                "Children who read for pleasure do better in every subject.",
                "habit",
            ),
            _item(
                "child",
                "activities",
                "purpose",
                "Try many activities",
                "Sport, music, art, coding, scouts: trying many is how they find what they love.",
                "interests",
            ),
            _item(
                "child",
                "sleep",
                "health",
                "Sleep 9 to 12 hours",
                "Enough sleep helps learning, mood and growth. No screens in the last hour before bed.",
                "habit",
            ),
            _item(
                "child",
                "checks",
                "health",
                "Dental and eye checks every year",
                "Write each visit here; Monosage reminds you when the next one is due.",
                "record:checkup",
            ),
            _item(
                "child",
                "swim_roads",
                "health",
                "Learn to swim and to cross roads safely",
                "Two skills that save lives.",
            ),
            _item(
                "child",
                "pocket_money",
                "money",
                "Pocket money and saving",
                "A small allowance teaches saving, spending and waiting for something they want.",
                "goal:money",
            ),
            _item(
                "child",
                "family_meals",
                "heart",
                "Family meals and talks",
                "Eating together a few times a week keeps you close and tells you what is going on.",
                "habit",
            ),
            _item(
                "child",
                "online_safety",
                "heart",
                "Online safety and screen rules",
                "Agree screen times, use parental controls and talk about what to do if something online feels "
                "wrong.",
            ),
            _item(
                "child",
                "bullying",
                "heart",
                "Watch for bullying",
                "Ask about friends. A sudden change of mood or not wanting to go to school can be a sign.",
            ),
        ),
        "milestones": (
            {"title": "First day of school"},
            {"title": "Learned to ride a bike"},
            {"title": "Read a whole book alone"},
            {"title": "First match or performance"},
            {"title": "First prize or certificate"},
            {"title": "Finished primary school"},
        ),
    },
    {
        "id": "teen",
        "title": "Teenage years",
        "label": "13 to 17 years",
        "from_months": 156,
        "to_months": 216,
        "description": "Becoming yourself: studies, friends, first responsibilities and choices about the future.",
        "items": (
            _item(
                "teen",
                "study",
                "mind",
                "Study habits for exams",
                "A regular time and place, short breaks, and asking for help early.",
                "habit",
            ),
            _item(
                "teen",
                "life_skills",
                "mind",
                "Learn practical skills",
                "Cook a meal, wash clothes, basic first aid and getting around on your own.",
            ),
            _item(
                "teen",
                "careers",
                "purpose",
                "Explore careers",
                "Talk to people in jobs you like, try work experience, and match your interests to paths.",
                "goal",
            ),
            _item(
                "teen",
                "volunteer",
                "purpose",
                "Volunteer or a first part-time job",
                "Real responsibility, new skills and something to show later.",
                "record:work",
            ),
            _item(
                "teen",
                "after_school",
                "purpose",
                "Plan what comes after school",
                "College, a trade, an apprenticeship or work: compare costs, entry needs and dates.",
                "goal",
            ),
            _item(
                "teen",
                "bank",
                "money",
                "Open a bank account and learn to budget",
                "Know what comes in, what goes out and how to save.",
            ),
            _item(
                "teen",
                "sleep_move",
                "health",
                "Sleep, food and movement",
                "Teens need 8 to 10 hours of sleep and an hour of activity a day.",
                "habit",
            ),
            _item(
                "teen",
                "mental_health",
                "health",
                "Talk about how you feel",
                "Stress, low mood and worry are common. Talking to someone you trust, or a doctor, helps.",
            ),
            _item(
                "teen",
                "relationships",
                "heart",
                "Healthy friendships and relationships",
                "Respect, consent, and knowing it is always fine to say no.",
            ),
            _item(
                "teen",
                "online",
                "heart",
                "A safe online life",
                "Think before you post, keep accounts private, and never share passwords or private photos.",
            ),
            _item(
                "teen",
                "id",
                "papers",
                "Get an ID card or passport",
                "You will need it for exams, travel, a bank account and your first job.",
                "document:id_card",
            ),
        ),
        "milestones": (
            {"title": "Started secondary school"},
            {"title": "First job or volunteering"},
            {"title": "Passed important exams"},
            {"title": "Learned to drive"},
            {"title": "Finished school"},
            {"title": "Got into college or training"},
        ),
    },
    {
        "id": "young_adult",
        "title": "Young adult",
        "label": "18 to 25 years",
        "from_months": 216,
        "to_months": 312,
        "description": "Independence: study or training, first work, your own money and your own choices.",
        "items": (
            _item(
                "young_adult",
                "study",
                "purpose",
                "Finish your study or training",
                "A degree, a diploma, an apprenticeship or a certificate: set the date and the steps.",
                "goal",
            ),
            _item(
                "young_adult",
                "first_job",
                "purpose",
                "Get your first real job",
                "A good CV, a professional profile and practice for interviews. The Careers app helps.",
                "record:work",
            ),
            _item(
                "young_adult",
                "budget",
                "money",
                "Make a budget and keep to it",
                "Rent, food, travel, fun and savings: know where your money goes.",
            ),
            _item(
                "young_adult",
                "emergency_fund",
                "money",
                "Build an emergency fund",
                "Aim for three months of costs. Put a little aside from every pay.",
                "goal:money",
                carry=True,
            ),
            _item(
                "young_adult",
                "credit",
                "money",
                "Use credit carefully",
                "Pay cards in full, avoid expensive loans and know your credit score.",
            ),
            _item(
                "young_adult",
                "doctor",
                "health",
                "Your own doctor and health cover",
                "Register with a doctor, get health insurance and know your blood group.",
                "document:insurance",
                carry=True,
            ),
            _item(
                "young_adult",
                "routines",
                "health",
                "Healthy routines that last",
                "Exercise you enjoy, good food, enough sleep, and care with alcohol and drugs.",
                "habit",
            ),
            _item(
                "young_adult",
                "papers",
                "papers",
                "Keep your papers in one place",
                "ID, passport, certificates, bank and tax papers: add them to your vault.",
                "document:passport",
            ),
            _item(
                "young_adult",
                "vote",
                "papers",
                "Register to vote",
                "Have your say in your community and your country.",
            ),
            _item(
                "young_adult",
                "friends",
                "heart",
                "Keep friends and family close",
                "New cities and jobs pull people apart. Call, visit, and make new friends too.",
            ),
            _item(
                "young_adult",
                "keep_learning",
                "mind",
                "Keep learning",
                "A course, a language or a new skill each year keeps doors open.",
                "goal",
            ),
        ),
        "milestones": (
            {"title": "Moved out"},
            {"title": "Graduated"},
            {"title": "First full-time job"},
            {"title": "First pay"},
            {"title": "First trip on my own"},
            {"title": "Bought my first vehicle"},
        ),
    },
    {
        "id": "building",
        "title": "Building years",
        "label": "26 to 39 years",
        "from_months": 312,
        "to_months": 480,
        "description": "Building a career, a home and perhaps a family, while keeping your health and friendships "
        "strong.",
        "items": (
            _item(
                "building",
                "career",
                "purpose",
                "Grow in your career",
                "Set a goal for the next three years: a skill, a role, a business or a raise.",
                "goal",
            ),
            _item(
                "building",
                "retirement",
                "money",
                "Start saving for retirement",
                "Start early: a fixed share of every pay into a pension or long-term savings.",
                "goal:money",
                carry=True,
            ),
            _item(
                "building",
                "insurance",
                "money",
                "Life and health insurance",
                "If people depend on you, life cover protects them if something happens to you.",
                "document:insurance",
                carry=True,
            ),
            _item(
                "building",
                "home",
                "money",
                "Plan for a home",
                "Rent or buy: save a deposit and know what you can afford.",
                "goal:money",
            ),
            _item(
                "building",
                "will",
                "legacy",
                "Make a will",
                "Say who gets what and who would look after your children. It is not only for the old.",
                "document:will",
                carry=True,
            ),
            _item(
                "building",
                "nominees",
                "papers",
                "Name nominees and beneficiaries",
                "On bank accounts, insurance and pensions, so money goes to the right people.",
                carry=True,
            ),
            _item(
                "building",
                "together",
                "heart",
                "Make time for the people you love",
                "Put regular time with your partner, children and friends in your week.",
                "habit",
            ),
            _item(
                "building",
                "checkup",
                "health",
                "A health check every year or two",
                "Blood pressure, weight, sugar and cholesterol; ask your doctor what suits your age.",
                "record:checkup",
                carry=True,
            ),
            _item(
                "building",
                "move",
                "health",
                "Move every day",
                "About 150 minutes of activity a week keeps the heart and the mind healthy.",
                "habit",
            ),
            _item(
                "building",
                "stress",
                "mind",
                "Protect your mental health",
                "Stress, burnout and poor sleep creep up. Notice the signs and get help early.",
            ),
        ),
        "milestones": (
            {"title": "Got married or moved in together"},
            {"title": "Became a parent"},
            {"title": "Bought a home"},
            {"title": "Got a promotion"},
            {"title": "Started a business"},
            {"title": "A big trip"},
        ),
    },
    {
        "id": "midlife",
        "title": "Midlife",
        "label": "40 to 54 years",
        "from_months": 480,
        "to_months": 660,
        "description": "Taking stock: health screenings, the middle of a career, children growing up and parents "
        "growing older.",
        "items": (
            _item(
                "midlife",
                "screenings",
                "health",
                "Screenings for your age",
                "Ask your doctor about blood pressure, diabetes, cholesterol, eyes and the cancer screenings that "
                "suit you.",
                "record:checkup",
                carry=True,
            ),
            _item(
                "midlife",
                "strength",
                "health",
                "Keep your strength",
                "Strength exercises twice a week protect muscles, bones and balance for later.",
                "habit",
            ),
            _item(
                "midlife",
                "retirement_check",
                "money",
                "Check your retirement plan",
                "Are you on track? Save more as income grows and pay off expensive debt.",
                "goal:money",
            ),
            _item(
                "midlife",
                "education_fund",
                "money",
                "Plan for your children's education",
                "College or training costs: start a fund and know the dates.",
                "goal:money",
            ),
            _item(
                "midlife",
                "parents",
                "heart",
                "Plan care for your parents",
                "Talk with them about their health, money and wishes while it is still easy. Add them to Life "
                "stages to keep track.",
            ),
            _item(
                "midlife",
                "partner_friends",
                "heart",
                "Your partner and your friends",
                "Busy years drift people apart. Plan time together on purpose.",
            ),
            _item(
                "midlife",
                "rethink_work",
                "purpose",
                "Rethink your work",
                "Is it still right for you? A new skill, a change of role or a business of your own.",
                "goal",
            ),
            _item(
                "midlife",
                "learn",
                "mind",
                "Learn something new",
                "A language, an instrument or a course keeps the mind sharp.",
                "goal",
            ),
            _item(
                "midlife",
                "review_will",
                "legacy",
                "Review your will and insurance",
                "Lives change: check them every few years.",
                carry=True,
            ),
            _item(
                "midlife",
                "wishes",
                "legacy",
                "Write down your wishes",
                "Who decides for you if you cannot, the care you want and where your papers are.",
                "wishes",
                carry=True,
            ),
        ),
        "milestones": (
            {"title": "Children started college or work"},
            {"title": "Changed career"},
            {"title": "Paid off a loan"},
            {"title": "Reached a fitness goal"},
            {"title": "Became a mentor"},
        ),
    },
    {
        "id": "late_career",
        "title": "Later working years",
        "label": "55 to 64 years",
        "from_months": 660,
        "to_months": 780,
        "description": "Preparing for the next chapter: money for retirement, good health, and passing on what you "
        "know.",
        "items": (
            _item(
                "late_career",
                "retirement_income",
                "money",
                "Plan your retirement date and income",
                "Pension, savings and other income: how much will you need each month?",
                "goal:money",
            ),
            _item(
                "late_career",
                "debts",
                "money",
                "Clear your debts before you retire",
                "Retiring without loans makes a smaller income go much further.",
                "goal:money",
            ),
            _item(
                "late_career",
                "screenings",
                "health",
                "Regular screenings and check-ups",
                "Write each one here; Monosage reminds you when the next is due.",
                "record:checkup",
                carry=True,
            ),
            _item(
                "late_career",
                "senses",
                "health",
                "Eyes, hearing and teeth",
                "Small problems are easier to fix early, and they matter a lot later.",
                "record:checkup",
            ),
            _item(
                "late_career",
                "active",
                "health",
                "Stay active",
                "Walking, swimming, yoga or a sport: something you will still enjoy at 80.",
                "habit",
            ),
            _item(
                "late_career",
                "mentor",
                "purpose",
                "Mentor someone",
                "Pass on what you know at work or in your community.",
            ),
            _item(
                "late_career",
                "next_chapter",
                "purpose",
                "Decide what retirement will look like",
                "Work less, volunteer, travel, a hobby or a small business of your own.",
                "goal",
            ),
            _item(
                "late_career",
                "attorney",
                "legacy",
                "Choose who decides if you cannot",
                "A power of attorney for money and for health, so someone you trust can act for you.",
                "document:will",
                carry=True,
            ),
            _item(
                "late_career",
                "circle",
                "heart",
                "Grow your circle outside work",
                "Clubs, faith groups and neighbours matter more once work ends.",
            ),
        ),
        "milestones": (
            {"title": "Became a grandparent"},
            {"title": "Last day at work"},
            {"title": "Celebrated a big anniversary"},
            {"title": "Moved to a smaller home"},
        ),
    },
    {
        "id": "retirement",
        "title": "Retirement",
        "label": "65 to 79 years",
        "from_months": 780,
        "to_months": 960,
        "description": "Freedom and purpose: health, friends and family, and time for what you always wanted to do.",
        "items": (
            _item(
                "retirement",
                "purpose",
                "purpose",
                "Find a daily purpose",
                "Volunteering, grandchildren, a hobby or part-time work give the days meaning.",
                "interests",
            ),
            _item(
                "retirement",
                "vaccines",
                "health",
                "Vaccines for older adults",
                "Ask your doctor about flu, pneumonia and shingles vaccines.",
                "record:vaccine",
            ),
            _item(
                "retirement",
                "falls",
                "health",
                "Prevent falls",
                "Strength and balance exercises, good lighting, rails on stairs and checked eyesight.",
            ),
            _item(
                "retirement",
                "mind",
                "mind",
                "Keep the mind active",
                "Reading, puzzles, learning and talking with people every day.",
                "habit",
            ),
            _item(
                "retirement",
                "technology",
                "mind",
                "Get comfortable with new technology",
                "Video calls, online banking and health apps make life easier. Ask family to show you.",
            ),
            _item(
                "retirement",
                "connected",
                "heart",
                "Stay connected",
                "Loneliness harms health. Call someone, visit, or join a group every week.",
                "habit",
            ),
            _item(
                "retirement",
                "savings",
                "money",
                "Make your savings last",
                "A simple monthly budget, and watch out for scams by phone and email.",
            ),
            _item(
                "retirement",
                "papers",
                "papers",
                "Keep your papers easy to find",
                "Pension, insurance, property and bank papers in the vault, and tell someone where they are.",
                "document:property",
            ),
            _item(
                "retirement",
                "wishes",
                "legacy",
                "Write down your wishes",
                "The care you want, who decides for you, and your funeral wishes.",
                "wishes",
                carry=True,
            ),
            _item(
                "retirement",
                "story",
                "legacy",
                "Share your story",
                "Write or record your memories for your family. Your timeline and journal are a good start.",
            ),
        ),
        "milestones": (
            {"title": "Retired"},
            {"title": "A dream trip"},
            {"title": "Started a new hobby"},
            {"title": "Golden or silver anniversary"},
            {"title": "Became a great-grandparent"},
        ),
    },
    {
        "id": "elder",
        "title": "Elder years",
        "label": "80 years and over",
        "from_months": 960,
        "to_months": 1800,
        "description": "Comfort, dignity and closeness: care that fits your wishes, and time with the people you "
        "love.",
        "items": (
            _item(
                "elder",
                "care_plan",
                "health",
                "A care plan with your doctor",
                "Medicines reviewed, regular check-ups, and help at home when it is needed.",
                "record:checkup",
            ),
            _item(
                "elder",
                "medicines",
                "health",
                "Medicines on time",
                "The Health app reminds you and tells a caregiver if a dose is missed.",
                "app:care",
            ),
            _item(
                "elder",
                "contact",
                "heart",
                "Daily contact with someone",
                "A call or a visit every day, and an easy way to ask for help.",
                "habit",
            ),
            _item(
                "elder",
                "living",
                "heart",
                "Decide where you want to live",
                "At home with help, with family, or in a care home: talk about it before it is urgent.",
            ),
            _item(
                "elder",
                "advance_care",
                "legacy",
                "Your wishes for care",
                "The treatments you would want or not want, and who speaks for you.",
                "wishes",
                carry=True,
            ),
            _item(
                "elder",
                "funeral",
                "legacy",
                "Funeral or memorial wishes",
                "Writing them down spares your family hard guesses at a hard time.",
                "wishes",
            ),
            _item(
                "elder",
                "messages",
                "legacy",
                "Words for the people you love",
                "Letters, recordings or a few lines about what they mean to you.",
                "wishes",
            ),
            _item(
                "elder",
                "in_order",
                "papers",
                "Will, papers and accounts in order",
                "Check your will, tell someone where things are, and list your online accounts.",
                carry=True,
            ),
            _item(
                "elder",
                "joy",
                "mind",
                "Enjoy every day",
                "Music, stories, photos, nature, faith and small pleasures.",
            ),
        ),
        "milestones": (
            {"title": "Celebrated 80"},
            {"title": "Celebrated 90"},
            {"title": "A family reunion"},
            {"title": "Celebrated 100"},
        ),
    },
    {
        "id": "remembered",
        "title": "Remembered",
        "label": "After their life",
        "from_months": None,
        "to_months": None,
        "description": "Their life is complete. Keep their story, follow their wishes and look after the people who "
        "loved them.",
        "items": (
            _item(
                "remembered",
                "certificate",
                "papers",
                "Get the death certificate",
                "Banks, insurers and offices will ask for it. Keep copies in the vault.",
                "document:other",
            ),
            _item(
                "remembered",
                "tell",
                "papers",
                "Tell banks, pension and insurers",
                "Ask about their accounts, pension and any life insurance.",
            ),
            _item(
                "remembered",
                "wishes",
                "legacy",
                "Follow their will and wishes",
                "Their will, their care and funeral wishes, and who they wanted to look after what.",
            ),
            _item(
                "remembered",
                "accounts",
                "papers",
                "Close or remember their online accounts",
                "Phone, email and social media: many can be closed or turned into a memorial page.",
            ),
            _item(
                "remembered",
                "stories",
                "legacy",
                "Gather their photos and stories",
                "Ask family and friends to share memories, and add them to the record of their life.",
                "record:milestone",
            ),
            _item(
                "remembered",
                "grief",
                "heart",
                "Look after yourself",
                "Grief takes time and comes in waves. Talk to people, keep simple routines, and ask for help if "
                "you need it.",
            ),
            _item(
                "remembered",
                "special_days",
                "heart",
                "Remember them on special days",
                "Birthdays and anniversaries can be hard. Plan something gentle to remember them.",
            ),
        ),
        "milestones": (),
    },
)
BY_ID = {stage["id"]: stage for stage in STAGES}
LIVING_STAGES = tuple(stage for stage in STAGES if stage["from_months"] is not None)
ITEMS = {item["id"]: item for stage in STAGES for item in stage["items"]}

# Which ideas for an interest suit which stages (babies and toddlers are too young: play, talk and read).
GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "child", "title": "Children", "stages": ("preschool", "child")},
    {"id": "teen", "title": "Teenagers", "stages": ("teen",)},
    {"id": "young", "title": "Young adults", "stages": ("young_adult",)},
    {
        "id": "adult",
        "title": "Adults",
        "stages": ("building", "midlife", "late_career"),
    },
    {"id": "senior", "title": "Older adults", "stages": ("retirement", "elder")},
)
GROUP_OF = {stage: group["id"] for group in GROUPS for stage in group["stages"]}

# Kinds of ideas: something to learn, to do, a career path, a way to earn, a way to give.
IDEA_KINDS = ("learn", "do", "career", "earn", "give")


def _idea(
    title: str, description: str, kind: str = "do", app: str = ""
) -> dict[str, str]:
    return {"title": title, "description": description, "kind": kind, "app": app}


INTERESTS: tuple[dict[str, Any], ...] = (
    {
        "id": "music",
        "title": "Music",
        "mark": "🎵",
        "goal_area": "creative",
        "ideas": {
            "child": (
                _idea(
                    "Learn an instrument",
                    "Keyboard, guitar, recorder or drums: fifteen minutes of practice a day.",
                    "learn",
                ),
                _idea(
                    "Sing in a choir or group",
                    "Singing with others builds confidence and friendships.",
                ),
            ),
            "teen": (
                _idea(
                    "Join a band or an orchestra",
                    "Play with others and perform; school and community groups are a good start.",
                ),
                _idea(
                    "Make your own music",
                    "Free apps let you record, write songs and share them.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Turn music into a skill",
                    "Sound engineering, music production, teaching or performing: try a course.",
                    "career",
                ),
                _idea(
                    "Play live",
                    "Open mic nights, weddings and events help you find an audience.",
                ),
            ),
            "adult": (
                _idea(
                    "Teach music and earn",
                    "Give lessons, or sell an online course in the Courses app.",
                    "earn",
                    "experiences",
                ),
                _idea(
                    "Keep playing for joy",
                    "A weekly session with friends or a community group.",
                ),
            ),
            "senior": (
                _idea(
                    "Music every day",
                    "Listening and playing lift the mood and help memory.",
                ),
                _idea(
                    "Share music with the young",
                    "Teach grandchildren songs, or volunteer at a school.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "art",
        "title": "Art and design",
        "mark": "🎨",
        "goal_area": "creative",
        "ideas": {
            "child": (
                _idea(
                    "Draw, paint and make",
                    "A box of materials and a place where mess is allowed.",
                ),
                _idea(
                    "Visit museums and galleries",
                    "Many have free days and workshops for children.",
                    "learn",
                ),
            ),
            "teen": (
                _idea(
                    "Build a portfolio",
                    "Keep your best work: it opens doors to art and design courses.",
                    "career",
                ),
                _idea(
                    "Try digital design",
                    "Illustration, photography, video and graphic design with free tools.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Study or freelance in design",
                    "Design, architecture, animation or fashion: compare courses and apprenticeships.",
                    "career",
                ),
                _idea(
                    "Sell your first work",
                    "Prints, commissions, or designs for small businesses.",
                    "earn",
                    "commerce",
                ),
            ),
            "adult": (
                _idea(
                    "Earn from your art",
                    "Sell prints and crafts in your own shop, or design websites for clients with Sites.",
                    "earn",
                    "sites",
                ),
                _idea(
                    "Join a class or a studio",
                    "Making things with others is relaxing and social.",
                ),
            ),
            "senior": (
                _idea(
                    "Paint, knit or craft",
                    "Making things calms the mind and keeps the hands nimble.",
                ),
                _idea(
                    "Show or give your work",
                    "Local shows, markets, or gifts for the family.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "sports",
        "title": "Sport and fitness",
        "mark": "⚽",
        "goal_area": "health",
        "ideas": {
            "child": (
                _idea(
                    "Join a team or a club",
                    "Football, cricket, swimming or martial arts: teamwork and fitness together.",
                ),
                _idea(
                    "Play outside every day",
                    "An hour of active play keeps a growing body strong.",
                ),
            ),
            "teen": (
                _idea(
                    "Train for something",
                    "A race, a tournament or a grading: a target makes training fun.",
                ),
                _idea(
                    "Coach younger children",
                    "Helping at a club builds leadership.",
                    "give",
                ),
            ),
            "young": (
                _idea(
                    "Keep a sport in your life",
                    "Adult leagues and clubs keep you fit and bring new friends.",
                ),
                _idea(
                    "Careers in sport",
                    "Coaching, physiotherapy, sports science or event management.",
                    "career",
                ),
            ),
            "adult": (
                _idea(
                    "Set a fitness goal",
                    "A 5K, 10,000 steps a day, or a sport you used to play.",
                ),
                _idea(
                    "Coach or run classes and earn",
                    "Take bookings for training sessions and classes.",
                    "earn",
                    "commerce",
                ),
            ),
            "senior": (
                _idea(
                    "Gentle, regular movement",
                    "Walking, yoga, swimming or tai chi for strength and balance.",
                ),
                _idea(
                    "Walk with others",
                    "Walking groups combine exercise and company.",
                ),
            ),
        },
    },
    {
        "id": "nature",
        "title": "Nature and animals",
        "mark": "🌿",
        "goal_area": "personal",
        "ideas": {
            "child": (
                _idea(
                    "Grow something",
                    "A plant on the windowsill or a small patch of garden.",
                ),
                _idea(
                    "Explore outside",
                    "Parks, forests and beaches: collect leaves, watch birds, learn their names.",
                    "learn",
                ),
            ),
            "teen": (
                _idea(
                    "Volunteer for nature",
                    "Clean-ups, tree planting or an animal shelter.",
                    "give",
                ),
                _idea(
                    "Study the science of nature",
                    "Biology, geography and environmental science lead to many careers.",
                    "career",
                ),
            ),
            "young": (
                _idea(
                    "Green careers",
                    "Farming, forestry, conservation, animal care or clean energy.",
                    "career",
                ),
                _idea(
                    "Adventures outdoors",
                    "Hiking, camping and climbing with friends.",
                ),
            ),
            "adult": (
                _idea(
                    "Grow your own food",
                    "A kitchen garden saves money and calms the mind.",
                ),
                _idea(
                    "Earn from nature",
                    "Sell plants or produce, lead nature walks, or care for pets.",
                    "earn",
                    "commerce",
                ),
            ),
            "senior": (
                _idea(
                    "Garden for health",
                    "Gardening keeps you moving and outdoors.",
                ),
                _idea(
                    "Feed the birds and watch nature",
                    "A peaceful joy for every day.",
                ),
            ),
        },
    },
    {
        "id": "science",
        "title": "Science",
        "mark": "🔬",
        "goal_area": "learning",
        "ideas": {
            "child": (
                _idea(
                    "Ask why, and experiment",
                    "Kitchen experiments, a magnifying glass and lots of why questions.",
                    "learn",
                ),
                _idea(
                    "Science clubs and museums",
                    "Hands-on science centres make it exciting.",
                ),
            ),
            "teen": (
                _idea(
                    "Science fairs and olympiads",
                    "Projects and competitions stretch you and stand out on applications.",
                ),
                _idea(
                    "Choose subjects for science careers",
                    "Medicine, engineering and research need maths and the sciences.",
                    "career",
                ),
            ),
            "young": (
                _idea(
                    "Work in research",
                    "Internships in labs, universities and companies.",
                    "career",
                ),
                _idea(
                    "Share what you learn",
                    "Write, make videos or tutor others.",
                    "give",
                    "videos",
                ),
            ),
            "adult": (
                _idea(
                    "Tutor and earn",
                    "Teach maths or science online, or sell a course.",
                    "earn",
                    "experiences",
                ),
                _idea(
                    "Citizen science",
                    "Count birds, log the weather or classify stars for real research.",
                    "give",
                ),
            ),
            "senior": (
                _idea(
                    "Keep learning science",
                    "Talks, documentaries and free online courses.",
                    "learn",
                ),
                _idea(
                    "Inspire the grandchildren",
                    "Do experiments together.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "technology",
        "title": "Technology and coding",
        "mark": "💻",
        "goal_area": "learning",
        "ideas": {
            "child": (
                _idea(
                    "Start coding with games",
                    "Block coding apps and robot kits teach logic through play.",
                    "learn",
                ),
                _idea(
                    "Make, do not just watch",
                    "Create a game, an animation or a story on the computer.",
                ),
            ),
            "teen": (
                _idea(
                    "Build a real project",
                    "A website, an app or a robot, and share it with friends.",
                ),
                _idea(
                    "Join a coding club or a hackathon",
                    "You learn faster with others.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Choose a tech career",
                    "Software, data, design, security or IT support: pick a path and build a portfolio.",
                    "career",
                ),
                _idea(
                    "Get certified",
                    "Short, recognised certificates help you get hired.",
                    "learn",
                ),
            ),
            "adult": (
                _idea(
                    "Earn with your tech skills",
                    "Build websites for clients with Sites, or take on freelance projects.",
                    "earn",
                    "sites",
                ),
                _idea(
                    "Teach others",
                    "Help colleagues, run a workshop, or make tutorial videos.",
                    "give",
                    "videos",
                ),
            ),
            "senior": (
                _idea(
                    "Get comfortable with technology",
                    "Video calls, online banking and staying safe online.",
                    "learn",
                ),
                _idea(
                    "Share your experience",
                    "Mentor young people starting in tech.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "writing",
        "title": "Reading and writing",
        "mark": "📚",
        "goal_area": "learning",
        "ideas": {
            "child": (
                _idea(
                    "A library card and a book a week",
                    "Choosing their own books makes reading a joy.",
                ),
                _idea(
                    "Keep a diary or write stories",
                    "Writing about the day builds words and confidence.",
                ),
            ),
            "teen": (
                _idea(
                    "Write for a school paper or a blog",
                    "Write often and ask for feedback.",
                ),
                _idea(
                    "Enter writing competitions",
                    "Poems, essays and short stories.",
                ),
            ),
            "young": (
                _idea(
                    "Careers with words",
                    "Journalism, marketing, law, publishing or teaching.",
                    "career",
                ),
                _idea(
                    "Build a writing habit",
                    "Five hundred words a day add up to a book a year.",
                ),
            ),
            "adult": (
                _idea(
                    "Earn from writing",
                    "Write for businesses, publish guides, or turn what you know into a course.",
                    "earn",
                    "experiences",
                ),
                _idea(
                    "Join a book club",
                    "Reading with others brings new books and new friends.",
                ),
            ),
            "senior": (
                _idea(
                    "Write your life story",
                    "Your timeline and journal are a great start for your family.",
                    "give",
                ),
                _idea(
                    "Read to children",
                    "Libraries and schools welcome volunteer readers.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "business",
        "title": "Business and money",
        "mark": "💼",
        "goal_area": "money",
        "ideas": {
            "child": (
                _idea(
                    "A first little business",
                    "A lemonade stand, crafts or a school sale: earning, counting and saving.",
                ),
                _idea(
                    "Save for something",
                    "A jar, a goal and a chart on the wall.",
                ),
            ),
            "teen": (
                _idea(
                    "Learn how money works",
                    "Saving, interest, budgets and how businesses make money.",
                    "learn",
                ),
                _idea(
                    "Try a small side business",
                    "Tutoring, design, baking or reselling.",
                    "earn",
                ),
            ),
            "young": (
                _idea(
                    "Start investing early",
                    "Small regular amounts in low-cost funds grow over decades.",
                ),
                _idea(
                    "Business and finance careers",
                    "Accounting, sales, marketing, management or a company of your own.",
                    "career",
                ),
            ),
            "adult": (
                _idea(
                    "Start or grow a business",
                    "Sell online, take bookings and send invoices with Monosage's business apps.",
                    "earn",
                    "studio",
                ),
                _idea(
                    "Build more than one income",
                    "A side business, investments or rent.",
                    "earn",
                    "stocks",
                ),
            ),
            "senior": (
                _idea(
                    "Advise and mentor",
                    "Your experience is valuable to young business owners.",
                    "give",
                ),
                _idea(
                    "Protect your savings",
                    "Simple investments, and care with scams.",
                ),
            ),
        },
    },
    {
        "id": "helping",
        "title": "Helping people",
        "mark": "🤝",
        "goal_area": "relationships",
        "ideas": {
            "child": (
                _idea(
                    "Help at home and at school",
                    "Kindness and helping are skills worth praising.",
                ),
                _idea(
                    "Join a charity event",
                    "Fun runs, food drives and collections.",
                    "give",
                ),
            ),
            "teen": (
                _idea(
                    "Volunteer regularly",
                    "A shelter, a hospital, or tutoring younger children.",
                    "give",
                ),
                _idea(
                    "Learn first aid",
                    "A short course can help you save a life.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Caring careers",
                    "Nursing, teaching, social work, medicine or counselling.",
                    "career",
                ),
                _idea(
                    "Volunteer at home or abroad",
                    "Experience, skills and a wider view of the world.",
                    "give",
                ),
            ),
            "adult": (
                _idea(
                    "Give your time and skills",
                    "Mentor someone, serve on a committee, or help a neighbour.",
                    "give",
                ),
                _idea(
                    "Earn by helping",
                    "Coaching, tutoring or care work, booked through your own page.",
                    "earn",
                    "commerce",
                ),
            ),
            "senior": (
                _idea(
                    "Volunteer with your experience",
                    "Charities need wise, patient people.",
                    "give",
                ),
                _idea(
                    "Be there for family",
                    "Caring for grandchildren or a friend gives purpose.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "cooking",
        "title": "Cooking and food",
        "mark": "🍳",
        "goal_area": "home",
        "ideas": {
            "child": (
                _idea(
                    "Cook together",
                    "Washing, mixing and measuring teach maths and healthy eating.",
                ),
                _idea(
                    "Grow herbs",
                    "Children eat what they grow.",
                ),
            ),
            "teen": (
                _idea(
                    "Cook a family meal each week",
                    "A real life skill and a gift to everyone.",
                ),
                _idea(
                    "Learn about food",
                    "Nutrition, other cultures' dishes and cooking on a budget.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Cook on a budget",
                    "Plan meals and shop smart: it saves money and keeps you well.",
                ),
                _idea(
                    "Food careers",
                    "Chef, baker, nutritionist, catering or a food business.",
                    "career",
                ),
            ),
            "adult": (
                _idea(
                    "Earn from cooking",
                    "Sell food, take catering orders, or run cooking classes.",
                    "earn",
                    "commerce",
                ),
                _idea(
                    "Host friends",
                    "Regular dinners keep friendships alive.",
                ),
            ),
            "senior": (
                _idea(
                    "Pass on family recipes",
                    "Write them down, or cook them with the grandchildren.",
                    "give",
                ),
                _idea(
                    "Cook to stay well",
                    "Simple, healthy meals, and eating with others.",
                ),
            ),
        },
    },
    {
        "id": "travel",
        "title": "Travel and cultures",
        "mark": "✈️",
        "goal_area": "travel",
        "ideas": {
            "child": (
                _idea(
                    "Explore near home",
                    "Day trips, maps, and stories about other countries.",
                ),
                _idea(
                    "Learn words of another language",
                    "Children pick up languages easily.",
                    "learn",
                ),
            ),
            "teen": (
                _idea(
                    "Exchanges and school trips",
                    "See how others live and grow your independence.",
                ),
                _idea(
                    "Learn a language seriously",
                    "A second language opens study and work abroad.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Travel and work",
                    "A gap year, work abroad, or a study exchange.",
                ),
                _idea(
                    "Careers in travel",
                    "Tourism, hospitality, airlines or translation.",
                    "career",
                ),
            ),
            "adult": (
                _idea(
                    "Plan a trip to save for",
                    "Make it a money goal and watch it grow.",
                ),
                _idea(
                    "Earn from travel",
                    "Lead tours, host travellers, or share guides and videos.",
                    "earn",
                    "videos",
                ),
            ),
            "senior": (
                _idea(
                    "Travel while you can",
                    "Easy trips, or visiting family far away.",
                ),
                _idea(
                    "Share your travels",
                    "Photos and stories for your family.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "making",
        "title": "Building and fixing",
        "mark": "🔧",
        "goal_area": "home",
        "ideas": {
            "child": (
                _idea(
                    "Build and take apart",
                    "Blocks, kits and old gadgets to explore.",
                ),
                _idea(
                    "Simple tools with a grown-up",
                    "Woodwork and crafts teach care and patience.",
                    "learn",
                ),
            ),
            "teen": (
                _idea(
                    "Learn a trade skill",
                    "Electrics, carpentry, mechanics or 3D printing.",
                    "learn",
                ),
                _idea(
                    "Fix things at home",
                    "A bike, a shelf, a dripping tap.",
                ),
            ),
            "young": (
                _idea(
                    "Skilled trades and engineering",
                    "Apprenticeships pay you while you learn.",
                    "career",
                ),
                _idea(
                    "Make and sell",
                    "Furniture, repairs or things made to order.",
                    "earn",
                    "commerce",
                ),
            ),
            "adult": (
                _idea(
                    "Earn from repairs and making",
                    "Take bookings for repairs, or sell what you make.",
                    "earn",
                    "commerce",
                ),
                _idea(
                    "Improve your home",
                    "Projects that save money and add value.",
                ),
            ),
            "senior": (
                _idea(
                    "A workshop hobby",
                    "Woodwork, models, or restoring old things.",
                ),
                _idea(
                    "Teach others to fix things",
                    "Repair groups in the community welcome skilled hands.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "performing",
        "title": "Drama and dance",
        "mark": "🎭",
        "goal_area": "creative",
        "ideas": {
            "child": (
                _idea(
                    "Drama or dance classes",
                    "Confidence, rhythm and friends.",
                ),
                _idea(
                    "Put on a show at home",
                    "Stories and costumes for the family.",
                ),
            ),
            "teen": (
                _idea(
                    "School plays or a dance group",
                    "Performing builds courage.",
                ),
                _idea(
                    "Film and share",
                    "Make short videos with friends.",
                    "do",
                    "videos",
                ),
            ),
            "young": (
                _idea(
                    "Train seriously",
                    "Auditions, drama school or a dance academy.",
                    "learn",
                ),
                _idea(
                    "Performing careers",
                    "Acting, dance, teaching, events and media.",
                    "career",
                ),
            ),
            "adult": (
                _idea(
                    "Teach classes and earn",
                    "Run dance or drama classes, or publish your videos and earn from the ads.",
                    "earn",
                    "videos",
                ),
                _idea(
                    "Join an amateur group",
                    "Theatre or dance, just for joy.",
                ),
            ),
            "senior": (
                _idea(
                    "Dance for health",
                    "Good for balance, the heart and the mood.",
                ),
                _idea(
                    "Tell stories",
                    "Share stories at libraries, at schools or with family.",
                    "give",
                ),
            ),
        },
    },
    {
        "id": "games",
        "title": "Games and puzzles",
        "mark": "🧩",
        "goal_area": "personal",
        "ideas": {
            "child": (
                _idea(
                    "Board games together",
                    "Taking turns, counting, and learning to lose well.",
                ),
                _idea(
                    "Puzzles and riddles",
                    "They build patience and logic.",
                ),
            ),
            "teen": (
                _idea(
                    "Chess or strategy clubs",
                    "Competition and thinking ahead.",
                ),
                _idea(
                    "Make your own game",
                    "Design a board game or code a video game.",
                    "learn",
                ),
            ),
            "young": (
                _idea(
                    "Game design careers",
                    "Design, programming, art or testing.",
                    "career",
                ),
                _idea(
                    "Keep gaming in balance",
                    "Enjoy it, and put sleep, study and friends first.",
                ),
            ),
            "adult": (
                _idea(
                    "Game nights with friends",
                    "A regular, cheap way to keep in touch.",
                ),
                _idea(
                    "Brain games",
                    "A daily challenge in the Play app.",
                    "do",
                    "play",
                ),
            ),
            "senior": (
                _idea(
                    "Daily puzzles",
                    "Crosswords, sudoku and cards keep the mind active.",
                    "do",
                    "play",
                ),
                _idea(
                    "Play with the grandchildren",
                    "Games bring generations together.",
                ),
            ),
        },
    },
)
INTEREST_IDS = tuple(interest["id"] for interest in INTERESTS)
INTERESTS_BY_ID = {interest["id"]: interest for interest in INTERESTS}


def stage_for(months: int | None, *, passed: bool = False) -> dict[str, Any]:
    """The stage of a life at this age in months (negative before birth); ``passed`` is a life that has ended."""
    if passed:
        return BY_ID["remembered"]
    if months is None:
        return BY_ID["building"]
    for stage in LIVING_STAGES:
        if stage["from_months"] <= months < stage["to_months"]:
            return stage
    return BY_ID["pregnancy"] if months < 0 else BY_ID["elder"]


def ideas_for(stage_id: str, interest_ids: list[str]) -> list[dict[str, Any]]:
    """Ideas for each chosen interest at this stage (none for babies and toddlers, or after a life)."""
    group = GROUP_OF.get(stage_id)
    if not group:
        return []
    out = []
    for interest_id in interest_ids:
        interest = INTERESTS_BY_ID.get(interest_id)
        if not interest:
            continue
        for index, idea in enumerate(interest["ideas"][group]):
            out.append(
                {
                    **idea,
                    "id": f"{interest_id}.{group}.{index}",
                    "interest": interest_id,
                    "interest_title": interest["title"],
                    "mark": interest["mark"],
                    "goal_area": interest["goal_area"],
                }
            )
    return out
