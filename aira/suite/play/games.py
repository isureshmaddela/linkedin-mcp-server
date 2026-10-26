"""The brain games: content, puzzles made from a seed, and checking on the server.

Every game is ``make(game, level, seed) -> (puzzle, secret)``: ``puzzle`` is what the browser may see, ``secret``
stays on the server (answers, the card layout, the hidden word). A score is never sent by the browser: it sends
answers or moves, and :func:`score` works the result out from the secret and the time the server measured.
The same seed always makes the same puzzle, which is what makes the daily challenge the same for everyone.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

GAMES: dict[str, dict[str, Any]] = {
    "memory": {
        "name": "Memory match",
        "tagline": "Turn over two cards at a time and find the pairs",
        "levels": {"easy": "6 pairs", "medium": "8 pairs", "hard": "12 pairs"},
    },
    "scramble": {
        "name": "Word scramble",
        "tagline": "Put the letters back in order",
        "levels": {
            "kids": "Short words",
            "easy": "Everyday words",
            "hard": "Longer words",
        },
    },
    "maths": {
        "name": "Quick maths",
        "tagline": "Ten sums to warm up your mind",
        "levels": {
            "easy": "Adding and taking away to 10",
            "medium": "To 100 and times tables",
            "hard": "Bigger sums and division",
        },
    },
    "trivia": {
        "name": "Trivia",
        "tagline": "Ten questions from a big quiz book",
        "levels": {
            "kids": "For children",
            "easy": "Easy",
            "medium": "Mixed",
            "hard": "Hard",
        },
    },
    "sudoku": {
        "name": "Sudoku",
        "tagline": "Fill the grid so no number repeats",
        "levels": {
            "kids": "4 by 4",
            "easy": "9 by 9, easy",
            "medium": "9 by 9, medium",
        },
    },
    "word": {
        "name": "Word of the day",
        "tagline": "Guess the five-letter word in six tries",
        "levels": {"daily": "Today's word", "practice": "A practice word"},
    },
}
GAME_IDS = tuple(GAMES)
LEVEL_MULTIPLIER = {
    "kids": 1.0,
    "easy": 1.0,
    "daily": 1.0,
    "practice": 1.0,
    "medium": 1.25,
    "hard": 1.5,
}

# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------

MEMORY_CARDS = (
    "🍎",
    "🍌",
    "🍇",
    "🍓",
    "🍒",
    "🍍",
    "🥕",
    "🌻",
    "🐶",
    "🐱",
    "🐸",
    "🦋",
    "🐟",
    "🚗",
    "⭐",
    "🎈",
)
MEMORY_PAIRS = {"easy": 6, "medium": 8, "hard": 12}

SCRAMBLE_WORDS = {
    "kids": (
        "cat",
        "dog",
        "sun",
        "fish",
        "frog",
        "bird",
        "cake",
        "ball",
        "tree",
        "book",
        "milk",
        "rain",
        "star",
        "moon",
        "duck",
        "lion",
        "bear",
        "ship",
        "kite",
        "apple",
        "house",
        "train",
        "water",
        "happy",
        "smile",
        "grass",
        "mouse",
        "horse",
        "plant",
        "cloud",
    ),
    "easy": (
        "garden",
        "orange",
        "pocket",
        "window",
        "flower",
        "family",
        "friend",
        "market",
        "summer",
        "winter",
        "autumn",
        "guitar",
        "puzzle",
        "butter",
        "bridge",
        "castle",
        "candle",
        "planet",
        "rocket",
        "silver",
        "travel",
        "island",
        "forest",
        "coffee",
        "ticket",
        "button",
        "letter",
        "number",
        "people",
        "school",
    ),
    "hard": (
        "kitchen",
        "holiday",
        "picture",
        "morning",
        "weather",
        "journey",
        "library",
        "blanket",
        "chicken",
        "pancake",
        "rainbow",
        "teacher",
        "village",
        "concert",
        "dolphin",
        "diamond",
        "lantern",
        "harvest",
        "mountain",
        "elephant",
        "sandwich",
        "umbrella",
        "notebook",
        "festival",
        "treasure",
        "calendar",
        "dinosaur",
        "football",
        "hospital",
        "vacation",
    ),
}

WORDS = (
    "about",
    "apple",
    "beach",
    "bread",
    "brick",
    "chair",
    "cloud",
    "dance",
    "dream",
    "earth",
    "field",
    "flame",
    "fruit",
    "ghost",
    "grape",
    "green",
    "happy",
    "heart",
    "horse",
    "house",
    "juice",
    "knife",
    "laugh",
    "lemon",
    "light",
    "money",
    "mouse",
    "music",
    "night",
    "ocean",
    "paint",
    "party",
    "peace",
    "piano",
    "pizza",
    "plant",
    "queen",
    "river",
    "robot",
    "salad",
    "scarf",
    "sheep",
    "shirt",
    "smile",
    "snake",
    "space",
    "spoon",
    "storm",
    "sugar",
    "table",
    "tiger",
    "toast",
    "train",
    "water",
    "whale",
    "world",
    "zebra",
    "bloom",
    "crown",
    "honey",
    "pearl",
    "candy",
    "magic",
    "sunny",
    "beard",
    "cabin",
    "daisy",
    "eagle",
    "flute",
    "globe",
)
WORD_TRIES = 6

# (category, difficulty, question, right answer, three wrong answers)
TRIVIA: tuple[tuple[str, str, str, str, tuple[str, str, str]], ...] = (
    # general knowledge
    (
        "general",
        "easy",
        "How many days are in a leap year?",
        "366",
        ("365", "364", "367"),
    ),
    ("general", "easy", "How many minutes are in an hour?", "60", ("100", "30", "90")),
    (
        "general",
        "easy",
        "What colour do you get by mixing blue and yellow?",
        "Green",
        ("Purple", "Orange", "Brown"),
    ),
    ("general", "easy", "How many sides does a hexagon have?", "6", ("5", "7", "8")),
    (
        "general",
        "easy",
        "At how many degrees Celsius does water freeze?",
        "0",
        ("32", "10", "-10"),
    ),
    (
        "general",
        "easy",
        "Which planet is known as the Red Planet?",
        "Mars",
        ("Venus", "Jupiter", "Saturn"),
    ),
    (
        "general",
        "easy",
        "What is the largest planet in our solar system?",
        "Jupiter",
        ("Saturn", "Neptune", "Earth"),
    ),
    ("general", "easy", "How many continents are there?", "7", ("5", "6", "8")),
    (
        "general",
        "medium",
        "Which gas do plants take in from the air to make their food?",
        "Carbon dioxide",
        ("Oxygen", "Nitrogen", "Helium"),
    ),
    (
        "general",
        "medium",
        "How many strings does a standard violin have?",
        "4",
        ("5", "6", "3"),
    ),
    (
        "general",
        "medium",
        "What is the hardest natural substance?",
        "Diamond",
        ("Gold", "Iron", "Quartz"),
    ),
    (
        "general",
        "easy",
        "Which organ pumps blood around the body?",
        "The heart",
        ("The liver", "The lungs", "The kidneys"),
    ),
    (
        "general",
        "hard",
        "How many bones are in the adult human body?",
        "206",
        ("186", "226", "306"),
    ),
    (
        "general",
        "medium",
        "What is the chemical symbol for gold?",
        "Au",
        ("Ag", "Gd", "Go"),
    ),
    (
        "general",
        "medium",
        "Which language has the most native speakers in the world?",
        "Mandarin Chinese",
        ("English", "Spanish", "Hindi"),
    ),
    (
        "general",
        "medium",
        "How many pieces does each player have at the start of a game of chess?",
        "16",
        ("12", "18", "20"),
    ),
    (
        "general",
        "easy",
        "What sweet food do bees make?",
        "Honey",
        ("Jam", "Syrup", "Sugar"),
    ),
    (
        "general",
        "medium",
        "In which year did people first walk on the Moon?",
        "1969",
        ("1959", "1972", "1965"),
    ),
    # nature
    (
        "nature",
        "easy",
        "What is the largest mammal in the world?",
        "Blue whale",
        ("African elephant", "Giraffe", "Sperm whale"),
    ),
    ("nature", "easy", "How many legs does a spider have?", "8", ("6", "10", "12")),
    (
        "nature",
        "easy",
        "What do caterpillars turn into?",
        "Butterflies or moths",
        ("Bees", "Beetles", "Dragonflies"),
    ),
    (
        "nature",
        "easy",
        "Which bird is famous for copying sounds and words?",
        "Parrot",
        ("Pigeon", "Owl", "Eagle"),
    ),
    (
        "nature",
        "easy",
        "What is the tallest animal?",
        "Giraffe",
        ("Elephant", "Camel", "Horse"),
    ),
    (
        "nature",
        "easy",
        "Which part of a plant takes in water from the soil?",
        "The roots",
        ("The leaves", "The flowers", "The seeds"),
    ),
    (
        "nature",
        "medium",
        "What is a baby kangaroo called?",
        "A joey",
        ("A cub", "A kid", "A calf"),
    ),
    (
        "nature",
        "easy",
        "Which animal is the fastest on land?",
        "Cheetah",
        ("Lion", "Horse", "Greyhound"),
    ),
    ("nature", "hard", "How many hearts does an octopus have?", "3", ("1", "2", "4")),
    (
        "nature",
        "easy",
        "What is the largest ocean on Earth?",
        "Pacific",
        ("Atlantic", "Indian", "Arctic"),
    ),
    (
        "nature",
        "easy",
        "Which tree do acorns come from?",
        "Oak",
        ("Maple", "Pine", "Birch"),
    ),
    (
        "nature",
        "medium",
        "In which half of the world do most penguins live?",
        "The Southern Hemisphere",
        ("The Northern Hemisphere", "Only the Arctic", "Only Europe"),
    ),
    (
        "nature",
        "medium",
        "What do we call animals that eat only plants?",
        "Herbivores",
        ("Carnivores", "Omnivores", "Insectivores"),
    ),
    (
        "nature",
        "medium",
        "Which planet is closest to the Sun?",
        "Mercury",
        ("Venus", "Mars", "Earth"),
    ),
    (
        "nature",
        "hard",
        "Which gas makes up most of the air we breathe?",
        "Nitrogen",
        ("Oxygen", "Carbon dioxide", "Hydrogen"),
    ),
    (
        "nature",
        "easy",
        "How long does the Earth take to go around the Sun?",
        "About a year",
        ("A month", "A day", "Ten years"),
    ),
    (
        "nature",
        "medium",
        "Which mammal can truly fly?",
        "The bat",
        ("The flying squirrel", "The sugar glider", "The flying lemur"),
    ),
    (
        "nature",
        "hard",
        "What colour is a polar bear's skin?",
        "Black",
        ("White", "Pink", "Yellow"),
    ),
    # history
    (
        "history",
        "easy",
        "Who was the first President of the United States?",
        "George Washington",
        ("Abraham Lincoln", "Thomas Jefferson", "John Adams"),
    ),
    (
        "history",
        "easy",
        "In which country are the ancient pyramids of Giza?",
        "Egypt",
        ("Mexico", "Greece", "Peru"),
    ),
    (
        "history",
        "medium",
        "In which year did the Second World War end?",
        "1945",
        ("1918", "1939", "1950"),
    ),
    (
        "history",
        "easy",
        "Who painted the Mona Lisa?",
        "Leonardo da Vinci",
        ("Michelangelo", "Vincent van Gogh", "Pablo Picasso"),
    ),
    (
        "history",
        "easy",
        "Which ship sank on its first voyage in 1912?",
        "Titanic",
        ("Lusitania", "Mayflower", "Queen Mary"),
    ),
    (
        "history",
        "medium",
        "Who was known as the Maid of Orleans?",
        "Joan of Arc",
        ("Marie Antoinette", "Queen Victoria", "Catherine the Great"),
    ),
    (
        "history",
        "easy",
        "Which empire built the Colosseum?",
        "The Roman Empire",
        ("The Greek Empire", "The Ottoman Empire", "The Persian Empire"),
    ),
    (
        "history",
        "medium",
        "Who was granted the first patent for the telephone in 1876?",
        "Alexander Graham Bell",
        ("Thomas Edison", "Nikola Tesla", "Guglielmo Marconi"),
    ),
    (
        "history",
        "medium",
        "In which year did the Berlin Wall fall?",
        "1989",
        ("1979", "1991", "1961"),
    ),
    (
        "history",
        "easy",
        "Who led India's non-violent movement for independence?",
        "Mahatma Gandhi",
        ("Winston Churchill", "Nelson Mandela", "Martin Luther King Jr."),
    ),
    (
        "history",
        "easy",
        "Which country gave the Statue of Liberty to the United States?",
        "France",
        ("Britain", "Spain", "Italy"),
    ),
    (
        "history",
        "medium",
        "Who was the first person to travel into space?",
        "Yuri Gagarin",
        ("Neil Armstrong", "Buzz Aldrin", "John Glenn"),
    ),
    (
        "history",
        "easy",
        "In which country is the Great Wall?",
        "China",
        ("Japan", "India", "Mongolia"),
    ),
    (
        "history",
        "medium",
        "Which queen reigned over the United Kingdom for more than 70 years?",
        "Elizabeth II",
        ("Victoria", "Mary I", "Anne"),
    ),
    (
        "history",
        "medium",
        "Which people built Machu Picchu?",
        "The Inca",
        ("The Aztecs", "The Maya", "The Olmecs"),
    ),
    (
        "history",
        "easy",
        "Who wrote the plays Romeo and Juliet and Hamlet?",
        "William Shakespeare",
        ("Charles Dickens", "Jane Austen", "Geoffrey Chaucer"),
    ),
    (
        "history",
        "medium",
        "In which year did Christopher Columbus first reach the Americas?",
        "1492",
        ("1592", "1392", "1520"),
    ),
    (
        "history",
        "hard",
        "Who discovered penicillin in 1928?",
        "Alexander Fleming",
        ("Louis Pasteur", "Marie Curie", "Edward Jenner"),
    ),
    # geography
    (
        "geography",
        "easy",
        "What is the capital of France?",
        "Paris",
        ("Lyon", "Marseille", "Nice"),
    ),
    (
        "geography",
        "easy",
        "What is the capital of Japan?",
        "Tokyo",
        ("Kyoto", "Osaka", "Seoul"),
    ),
    (
        "geography",
        "medium",
        "Which is the longest river in Africa?",
        "The Nile",
        ("The Congo", "The Niger", "The Zambezi"),
    ),
    (
        "geography",
        "medium",
        "What is the capital of Australia?",
        "Canberra",
        ("Sydney", "Melbourne", "Perth"),
    ),
    (
        "geography",
        "hard",
        "Since 2023, which country has the largest population?",
        "India",
        ("China", "United States", "Indonesia"),
    ),
    (
        "geography",
        "medium",
        "Mount Everest lies on the border of Nepal and which country?",
        "China",
        ("India", "Bhutan", "Pakistan"),
    ),
    (
        "geography",
        "easy",
        "What is the largest country by area?",
        "Russia",
        ("Canada", "China", "United States"),
    ),
    (
        "geography",
        "medium",
        "What is the capital of Canada?",
        "Ottawa",
        ("Toronto", "Vancouver", "Montreal"),
    ),
    (
        "geography",
        "medium",
        "What is the largest hot desert in the world?",
        "The Sahara",
        ("The Gobi", "The Kalahari", "The Arabian"),
    ),
    (
        "geography",
        "easy",
        "Which city is nicknamed the Big Apple?",
        "New York",
        ("Los Angeles", "Chicago", "Boston"),
    ),
    (
        "geography",
        "easy",
        "Which country is shaped like a boot?",
        "Italy",
        ("Spain", "Greece", "Portugal"),
    ),
    (
        "geography",
        "easy",
        "What is the capital of India?",
        "New Delhi",
        ("Mumbai", "Kolkata", "Chennai"),
    ),
    (
        "geography",
        "easy",
        "On which continent is Kenya?",
        "Africa",
        ("Asia", "South America", "Europe"),
    ),
    (
        "geography",
        "medium",
        "What is the smallest country in the world?",
        "Vatican City",
        ("Monaco", "Malta", "San Marino"),
    ),
    (
        "geography",
        "easy",
        "Which river flows through London?",
        "The Thames",
        ("The Seine", "The Danube", "The Severn"),
    ),
    (
        "geography",
        "hard",
        "What is the capital of Brazil?",
        "Brasilia",
        ("Rio de Janeiro", "Sao Paulo", "Salvador"),
    ),
    (
        "geography",
        "medium",
        "Which ocean lies between Africa and Australia?",
        "The Indian Ocean",
        ("The Atlantic Ocean", "The Pacific Ocean", "The Arctic Ocean"),
    ),
    (
        "geography",
        "easy",
        "In which country is the Taj Mahal?",
        "India",
        ("Pakistan", "Nepal", "Bangladesh"),
    ),
    # music and films
    (
        "music_film",
        "medium",
        "How many keys does a standard piano have?",
        "88",
        ("76", "92", "64"),
    ),
    (
        "music_film",
        "easy",
        "Which band sang Hey Jude?",
        "The Beatles",
        ("The Rolling Stones", "Queen", "ABBA"),
    ),
    (
        "music_film",
        "easy",
        "Who is known as the King of Pop?",
        "Michael Jackson",
        ("Elvis Presley", "Prince", "Stevie Wonder"),
    ),
    (
        "music_film",
        "easy",
        "Who is known as the King of Rock and Roll?",
        "Elvis Presley",
        ("Chuck Berry", "Little Richard", "Johnny Cash"),
    ),
    (
        "music_film",
        "medium",
        "Who composed the Moonlight Sonata?",
        "Ludwig van Beethoven",
        ("Wolfgang Amadeus Mozart", "Johann Sebastian Bach", "Frederic Chopin"),
    ),
    (
        "music_film",
        "medium",
        "What colour are Dorothy's slippers in the 1939 film The Wizard of Oz?",
        "Ruby red",
        ("Silver", "Gold", "Blue"),
    ),
    (
        "music_film",
        "easy",
        "Which Swedish group sang Dancing Queen?",
        "ABBA",
        ("Roxette", "Ace of Base", "Europe"),
    ),
    (
        "music_film",
        "easy",
        "What is the name of the snowman in Disney's Frozen?",
        "Olaf",
        ("Sven", "Kristoff", "Hans"),
    ),
    (
        "music_film",
        "easy",
        "Which film has a toy cowboy called Woody?",
        "Toy Story",
        ("Cars", "Shrek", "Up"),
    ),
    (
        "music_film",
        "medium",
        "How many lines are on a musical stave?",
        "5",
        ("4", "6", "7"),
    ),
    (
        "music_film",
        "medium",
        "Who sang My Heart Will Go On from the film Titanic?",
        "Celine Dion",
        ("Whitney Houston", "Mariah Carey", "Adele"),
    ),
    (
        "music_film",
        "hard",
        "Which orchestra instrument has 47 strings and seven pedals?",
        "The harp",
        ("The cello", "The piano", "The guitar"),
    ),
    (
        "music_film",
        "easy",
        "What is the name of the lion cub in The Lion King?",
        "Simba",
        ("Mufasa", "Nala", "Scar"),
    ),
    (
        "music_film",
        "hard",
        "Which 1995 Hindi film ran for decades at Mumbai's Maratha Mandir cinema?",
        "Dilwale Dulhania Le Jayenge",
        ("Sholay", "Lagaan", "Kuch Kuch Hota Hai"),
    ),
    (
        "music_film",
        "medium",
        "Who composed The Four Seasons?",
        "Antonio Vivaldi",
        ("Johann Sebastian Bach", "George Frideric Handel", "Joseph Haydn"),
    ),
    (
        "music_film",
        "easy",
        "Which film series has a school for wizards called Hogwarts?",
        "Harry Potter",
        ("Narnia", "The Lord of the Rings", "Percy Jackson"),
    ),
    (
        "music_film",
        "medium",
        "What is the highest female singing voice called?",
        "Soprano",
        ("Alto", "Tenor", "Bass"),
    ),
    (
        "music_film",
        "medium",
        "Which studio made the film Finding Nemo?",
        "Pixar",
        ("DreamWorks", "Studio Ghibli", "Aardman"),
    ),
    # sports
    (
        "sports",
        "easy",
        "How many players does a football (soccer) team have on the pitch?",
        "11",
        ("9", "10", "12"),
    ),
    (
        "sports",
        "medium",
        "In which sport do you score a try?",
        "Rugby",
        ("Cricket", "Tennis", "Golf"),
    ),
    (
        "sports",
        "easy",
        "How often are the Summer Olympic Games usually held?",
        "Every four years",
        ("Every two years", "Every year", "Every five years"),
    ),
    (
        "sports",
        "easy",
        "How many players does a basketball team have on court?",
        "5",
        ("6", "7", "4"),
    ),
    (
        "sports",
        "medium",
        "In cricket, how many balls are in a standard over?",
        "6",
        ("5", "8", "10"),
    ),
    (
        "sports",
        "medium",
        "Which country has won the most FIFA World Cups?",
        "Brazil",
        ("Germany", "Italy", "Argentina"),
    ),
    (
        "sports",
        "easy",
        "In tennis, what is a score of zero called?",
        "Love",
        ("Nil", "Duck", "Zero"),
    ),
    (
        "sports",
        "easy",
        "How many holes are played in a standard round of golf?",
        "18",
        ("9", "12", "21"),
    ),
    (
        "sports",
        "easy",
        "In which sport is a shuttlecock used?",
        "Badminton",
        ("Squash", "Table tennis", "Volleyball"),
    ),
    (
        "sports",
        "medium",
        "What colour is the centre of an archery target?",
        "Gold",
        ("Red", "Blue", "Black"),
    ),
    (
        "sports",
        "medium",
        "About how long is a marathon?",
        "42 kilometres",
        ("26 kilometres", "50 kilometres", "35 kilometres"),
    ),
    (
        "sports",
        "easy",
        "Which sport is played at Wimbledon?",
        "Tennis",
        ("Golf", "Cricket", "Rugby"),
    ),
    (
        "sports",
        "easy",
        "In chess, which piece moves only diagonally?",
        "The bishop",
        ("The rook", "The knight", "The king"),
    ),
    ("sports", "easy", "How many rings are on the Olympic flag?", "5", ("4", "6", "7")),
    (
        "sports",
        "easy",
        "How many pins are set up in ten-pin bowling?",
        "10",
        ("9", "12", "8"),
    ),
    (
        "sports",
        "medium",
        "In which country was judo created?",
        "Japan",
        ("China", "Korea", "Thailand"),
    ),
    (
        "sports",
        "hard",
        "What is the highest possible break in snooker without a free ball?",
        "147",
        ("100", "155", "180"),
    ),
    (
        "sports",
        "easy",
        "In which sport do players hit a puck?",
        "Ice hockey",
        ("Field hockey", "Curling", "Lacrosse"),
    ),
    # for children
    ("kids", "kids", "What sound does a cow make?", "Moo", ("Baa", "Woof", "Oink")),
    ("kids", "kids", "How many legs does a dog have?", "4", ("2", "6", "8")),
    (
        "kids",
        "kids",
        "What colour is the sky on a sunny day?",
        "Blue",
        ("Green", "Red", "Purple"),
    ),
    ("kids", "kids", "What is 2 + 3?", "5", ("4", "6", "23")),
    (
        "kids",
        "kids",
        "Which animal is called the king of the jungle?",
        "Lion",
        ("Tiger", "Elephant", "Bear"),
    ),
    (
        "kids",
        "kids",
        "What is a baby cat called?",
        "A kitten",
        ("A puppy", "A cub", "A chick"),
    ),
    ("kids", "kids", "How many days are in a week?", "7", ("5", "6", "10")),
    (
        "kids",
        "kids",
        "Which shape has three sides?",
        "A triangle",
        ("A square", "A circle", "A rectangle"),
    ),
    (
        "kids",
        "kids",
        "What do you wear on your feet?",
        "Shoes",
        ("Gloves", "Hats", "Scarves"),
    ),
    (
        "kids",
        "kids",
        "Which fruit is yellow and curved?",
        "A banana",
        ("An apple", "A grape", "A cherry"),
    ),
    (
        "kids",
        "kids",
        "What do frogs start life as?",
        "Tadpoles",
        ("Chicks", "Caterpillars", "Puppies"),
    ),
    ("kids", "kids", "How many colours are in a rainbow?", "7", ("5", "6", "8")),
    (
        "kids",
        "kids",
        "Which is the biggest?",
        "An elephant",
        ("An ant", "A mouse", "A cat"),
    ),
    ("kids", "kids", "What do we call frozen water?", "Ice", ("Steam", "Rain", "Sand")),
    (
        "kids",
        "kids",
        "Where do fish live?",
        "In water",
        ("In trees", "In the sand", "In the sky"),
    ),
    ("kids", "kids", "What is 10 take away 4?", "6", ("5", "7", "14")),
    (
        "kids",
        "kids",
        "Which season comes after winter?",
        "Spring",
        ("Summer", "Autumn", "Winter again"),
    ),
    (
        "kids",
        "kids",
        "Which animal has black and white stripes?",
        "A zebra",
        ("A giraffe", "A lion", "A hippo"),
    ),
)
TRIVIA_CATEGORIES = {
    "general": "General knowledge",
    "nature": "Nature and science",
    "history": "History",
    "geography": "Geography",
    "music_film": "Music and films",
    "sports": "Sports",
    "kids": "For children",
}
QUESTIONS_PER_ROUND = 10
SCRAMBLE_ROUNDS = 8


def seed_for(*parts: Any) -> int:
    """A seed from any values (the date and game for the daily challenge)."""
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def multiplier(level: str) -> float:
    return LEVEL_MULTIPLIER.get(level, 1.0)


# ---------------------------------------------------------------------------
# trivia (also used by live rooms)
# ---------------------------------------------------------------------------


def trivia_pool(level: str = "medium", category: str = "") -> list[int]:
    """Indexes into TRIVIA for a level ("kids", "easy", "medium" = everything but kids, "hard") and category."""
    out = []
    for index, (cat, difficulty, *_rest) in enumerate(TRIVIA):
        if category and cat != category:
            continue
        if level == "kids" and difficulty != "kids":
            continue
        if level == "easy" and difficulty != "easy":
            continue
        if level == "hard" and difficulty == "kids":
            continue
        if level == "medium" and difficulty == "kids":
            continue
        out.append(index)
    if level == "hard":
        hard = [i for i in out if TRIVIA[i][1] == "hard"]
        medium = [i for i in out if TRIVIA[i][1] == "medium"]
        out = hard + medium  # hard first, then medium to fill a round
    return out


def trivia_questions(
    rng: random.Random, count: int, level: str = "medium", category: str = ""
) -> list[dict[str, Any]]:
    """``count`` questions with shuffled choices; each item keeps its answer (``answer`` = index in ``choices``)."""
    pool = trivia_pool(level, category) or trivia_pool("medium")
    if level == "hard":
        chosen = pool[: max(count, 1) * 2]
        rng.shuffle(chosen)
        chosen = chosen[:count]
    else:
        chosen = rng.sample(pool, min(count, len(pool)))
    out = []
    for index in chosen:
        cat, difficulty, text, right, wrong = TRIVIA[index]
        choices = [right, *wrong]
        rng.shuffle(choices)
        out.append(
            {
                "id": index,
                "category": cat,
                "difficulty": difficulty,
                "text": text,
                "choices": choices,
                "answer": choices.index(right),
            }
        )
    return out


# ---------------------------------------------------------------------------
# sudoku
# ---------------------------------------------------------------------------


def _box(size: int) -> tuple[int, int]:
    return (2, 2) if size == 4 else (3, 3)


def _candidates(grid: list[int], size: int, cell: int) -> list[int]:
    row, col = divmod(cell, size)
    box_rows, box_cols = _box(size)
    used = set(grid[row * size : row * size + size])
    used.update(grid[r * size + col] for r in range(size))
    top, left = row - row % box_rows, col - col % box_cols
    for r in range(top, top + box_rows):
        for c in range(left, left + box_cols):
            used.add(grid[r * size + c])
    return [n for n in range(1, size + 1) if n not in used]


def count_solutions(grid: list[int], size: int, limit: int = 2) -> int:
    """How many ways the grid can be finished (stops at ``limit``): 1 means a fair puzzle."""
    work = list(grid)
    found = 0

    def solve() -> bool:
        nonlocal found
        best, best_options = -1, None
        for cell, value in enumerate(work):
            if value == 0:
                options = _candidates(work, size, cell)
                if not options:
                    return False
                if best_options is None or len(options) < len(best_options):
                    best, best_options = cell, options
                    if len(options) == 1:
                        break
        if best_options is None:
            found += 1
            return found >= limit
        for value in best_options:
            work[best] = value
            if solve():
                return True
        work[best] = 0
        return False

    solve()
    return found


def _full_grid(rng: random.Random, size: int) -> list[int]:
    grid = [0] * (size * size)

    def fill(cell: int = 0) -> bool:
        if cell == len(grid):
            return True
        options = _candidates(grid, size, cell)
        rng.shuffle(options)
        for value in options:
            grid[cell] = value
            if fill(cell + 1):
                return True
        grid[cell] = 0
        return False

    fill()
    return grid


SUDOKU_GIVENS = {"kids": (4, 8), "easy": (9, 38), "medium": (9, 30)}


def make_sudoku(rng: random.Random, level: str) -> tuple[list[int], list[int]]:
    """A puzzle with exactly one solution: remove cells from a full grid while the answer stays unique."""
    size, target = SUDOKU_GIVENS.get(level, SUDOKU_GIVENS["easy"])
    solution = _full_grid(rng, size)
    puzzle = list(solution)
    cells = list(range(size * size))
    rng.shuffle(cells)
    givens = size * size
    for cell in cells:
        if givens <= target:
            break
        keep = puzzle[cell]
        puzzle[cell] = 0
        if count_solutions(puzzle, size) != 1:
            puzzle[cell] = keep
        else:
            givens -= 1
    return puzzle, solution


def sudoku_valid(grid: list[int], size: int) -> bool:
    if len(grid) != size * size or any(
        not isinstance(v, int) or not 1 <= v <= size for v in grid
    ):
        return False
    box_rows, box_cols = _box(size)
    groups = []
    for i in range(size):
        groups.append(grid[i * size : i * size + size])
        groups.append([grid[r * size + i] for r in range(size)])
    for top in range(0, size, box_rows):
        for left in range(0, size, box_cols):
            groups.append(
                [
                    grid[r * size + c]
                    for r in range(top, top + box_rows)
                    for c in range(left, left + box_cols)
                ]
            )
    return all(sorted(group) == list(range(1, size + 1)) for group in groups)


# ---------------------------------------------------------------------------
# word of the day
# ---------------------------------------------------------------------------


def word_feedback(guess: str, answer: str) -> list[str]:
    """Per letter: "correct" (right place), "present" (elsewhere in the word) or "absent"; repeated letters count
    only as often as they appear in the answer."""
    result = ["absent"] * len(guess)
    remaining: dict[str, int] = {}
    for g, a in zip(guess, answer, strict=True):
        if g != a:
            remaining[a] = remaining.get(a, 0) + 1
    for i, (g, a) in enumerate(zip(guess, answer, strict=True)):
        if g == a:
            result[i] = "correct"
    for i, g in enumerate(guess):
        if result[i] == "correct":
            continue
        if remaining.get(g, 0) > 0:
            result[i] = "present"
            remaining[g] -= 1
    return result


# ---------------------------------------------------------------------------
# making puzzles
# ---------------------------------------------------------------------------


def _scramble(rng: random.Random, word: str) -> str:
    letters = list(word)
    for _ in range(20):
        rng.shuffle(letters)
        if "".join(letters) != word:
            return "".join(letters)
    return word[::-1] if word[::-1] != word else word


def _maths_question(rng: random.Random, level: str) -> tuple[str, int]:
    if level == "easy":
        a, b = rng.randint(0, 10), rng.randint(0, 10)
        if rng.random() < 0.5 or a < b:
            return f"{a} + {b}", a + b
        return f"{a} − {b}", a - b
    if level == "medium":
        kind = rng.choice(("+", "−", "×"))
        if kind == "×":
            a, b = rng.randint(2, 10), rng.randint(2, 10)
            return f"{a} × {b}", a * b
        a, b = rng.randint(10, 99), rng.randint(1, 99)
        if kind == "−":
            a, b = max(a, b), min(a, b)
            return f"{a} − {b}", a - b
        return f"{a} + {b}", a + b
    kind = rng.choice(("+", "−", "×", "÷"))
    if kind == "×":
        a, b = rng.randint(3, 12), rng.randint(3, 12)
        return f"{a} × {b}", a * b
    if kind == "÷":
        b, answer = rng.randint(2, 12), rng.randint(2, 12)
        return f"{b * answer} ÷ {b}", answer
    a, b = rng.randint(100, 999), rng.randint(10, 999)
    if kind == "−":
        a, b = max(a, b), min(a, b)
        return f"{a} − {b}", a - b
    return f"{a} + {b}", a + b


def check_level(game: str, level: str) -> str:
    if game not in GAMES:
        raise ValueError(f"Choose a game: {', '.join(GAME_IDS)}.")
    levels = GAMES[game]["levels"]
    level = str(level or "").strip().lower() or next(iter(levels))
    if level not in levels:
        raise ValueError(
            f"{GAMES[game]['name']} has these levels: {', '.join(levels)}."
        )
    return level


def make(game: str, level: str, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The puzzle the browser sees and the secret the server keeps."""
    rng = random.Random(seed)
    if game == "memory":
        pairs = MEMORY_PAIRS[level]
        faces = rng.sample(MEMORY_CARDS, pairs)
        layout = faces + faces
        rng.shuffle(layout)
        return {"cards": len(layout), "pairs": pairs}, {
            "layout": layout,
            "flips": [],
            "matched": [],
            "pending": None,
        }
    if game == "scramble":
        words = rng.sample(SCRAMBLE_WORDS[level], SCRAMBLE_ROUNDS)
        return (
            {
                "rounds": [
                    {"letters": _scramble(rng, word).upper(), "length": len(word)}
                    for word in words
                ]
            },
            {"words": words},
        )
    if game == "maths":
        questions = [_maths_question(rng, level) for _ in range(QUESTIONS_PER_ROUND)]
        return {"questions": [q for q, _ in questions]}, {
            "answers": [a for _, a in questions]
        }
    if game == "trivia":
        questions = trivia_questions(rng, QUESTIONS_PER_ROUND, level)
        return (
            {
                "questions": [
                    {
                        "text": q["text"],
                        "choices": q["choices"],
                        "category": TRIVIA_CATEGORIES[q["category"]],
                    }
                    for q in questions
                ]
            },
            {
                "answers": [q["answer"] for q in questions],
                "ids": [q["id"] for q in questions],
            },
        )
    if game == "sudoku":
        puzzle, solution = make_sudoku(rng, level)
        size = 4 if level == "kids" else 9
        return {"size": size, "grid": puzzle}, {"solution": solution, "hints": 0}
    if game == "word":
        answer = rng.choice(WORDS)
        return {"length": 5, "tries": WORD_TRIES}, {"answer": answer, "guesses": []}
    raise ValueError("Unknown game.")


# ---------------------------------------------------------------------------
# moves (games played step by step on the server)
# ---------------------------------------------------------------------------


def move(game: str, secret: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one move to ``secret`` (changed in place) and say what the player may now see."""
    action = str(payload.get("action") or "")
    if game == "memory" and action == "flip":
        layout = secret["layout"]
        try:
            index = int(str(payload.get("index")))
        except (TypeError, ValueError):
            raise ValueError("Choose a card.") from None
        if not 0 <= index < len(layout):
            raise ValueError("Choose a card on the board.")
        if index in secret["matched"]:
            raise ValueError("That pair is already found.")
        pending = secret.get("pending")
        if pending == index:
            raise ValueError("Choose a different card.")
        secret["flips"].append(index)
        if pending is None:
            secret["pending"] = index
            return {"index": index, "card": layout[index], "first": True}
        secret["pending"] = None
        matched = layout[pending] == layout[index]
        if matched:
            secret["matched"].extend([pending, index])
        return {
            "index": index,
            "card": layout[index],
            "other": pending,
            "other_card": layout[pending],
            "matched": matched,
            "found": len(secret["matched"]) // 2,
            "complete": len(secret["matched"]) == len(layout),
            "moves": len(secret["flips"]) // 2,
        }
    if game == "word" and action == "guess":
        guess = str(payload.get("word") or "").strip().lower()
        if len(guess) != 5 or not guess.isascii() or not guess.isalpha():
            raise ValueError("Type a word of five letters.")
        if (
            secret["answer"] in secret["guesses"]
            or len(secret["guesses"]) >= WORD_TRIES
        ):
            raise ValueError("This word is finished.")
        secret["guesses"].append(guess)
        feedback = word_feedback(guess, secret["answer"])
        solved = guess == secret["answer"]
        out_of_tries = len(secret["guesses"]) >= WORD_TRIES
        result = {
            "word": guess,
            "feedback": feedback,
            "solved": solved,
            "tries_left": WORD_TRIES - len(secret["guesses"]),
        }
        if out_of_tries and not solved:
            result["answer"] = secret["answer"]
        result["complete"] = solved or out_of_tries
        return result
    if game == "sudoku" and action == "hint":
        grid = payload.get("grid") or []
        solution = secret["solution"]
        if not isinstance(grid, list) or len(grid) != len(solution):
            raise ValueError("Send the grid as it is now.")
        empty = [i for i, v in enumerate(grid) if v != solution[i]]
        if not empty:
            return {"hint": None}
        cell = empty[0]
        secret["hints"] = int(secret.get("hints") or 0) + 1
        return {
            "hint": {"index": cell, "value": solution[cell]},
            "hints": secret["hints"],
        }
    raise ValueError("That move is not part of this game.")


# ---------------------------------------------------------------------------
# scoring (from answers and the time the server measured)
# ---------------------------------------------------------------------------


def _time_bonus(seconds: float, budget: int, fraction: float) -> int:
    return max(0, int(round((budget - seconds) * fraction))) if budget > seconds else 0


def score(
    game: str,
    level: str,
    mode: str,
    puzzle: dict[str, Any],
    secret: dict[str, Any],
    payload: dict[str, Any],
    seconds: float,
) -> tuple[int, dict[str, Any]]:
    """The score and what to show. Raises ValueError for answers that do not fit the puzzle (and for a sudoku
    that is not solved yet, so the player can keep going)."""
    timed = mode == "timed"
    factor = multiplier(level)
    if game == "memory":
        pairs = len(secret["layout"]) // 2
        if len(secret["matched"]) != len(secret["layout"]):
            raise ValueError("Find every pair first.")
        moves = len(secret["flips"]) // 2
        base = max(50, 1000 - (moves - pairs) * 30)
        bonus = _time_bonus(seconds, pairs * 15, 4) if timed else 0
        return int(round((base + bonus) * factor)), {
            "moves": moves,
            "pairs": pairs,
            "perfect": moves == pairs,
        }
    if game in ("scramble", "maths", "trivia"):
        answers = payload.get("answers")
        expected: list[Any] = (
            secret["words"] if game == "scramble" else secret["answers"]
        )
        if not isinstance(answers, list) or len(answers) != len(expected):
            raise ValueError(f"Send {len(expected)} answers, one for each question.")
        marks = []
        for given, right in zip(answers, expected, strict=True):
            if game == "scramble":
                marks.append(str(given or "").strip().lower() == right)
            elif game == "maths":
                try:
                    marks.append(int(str(given).strip()) == right)
                except (TypeError, ValueError):
                    marks.append(False)
            else:
                marks.append(
                    isinstance(given, int)
                    and not isinstance(given, bool)
                    and given == right
                )
        correct = sum(marks)
        bonus = (
            _time_bonus(seconds, len(expected) * 12, 3) * correct // len(expected)
            if timed
            else 0
        )
        result: dict[str, Any] = {
            "correct": correct,
            "total": len(expected),
            "marks": marks,
        }
        if game == "scramble":
            result["words"] = expected
        elif game == "maths":
            result["answers"] = expected
        else:
            result["answers"] = expected
        return int(round((correct * 100 + bonus) * factor)), result
    if game == "sudoku":
        grid = payload.get("grid")
        size = int(puzzle["size"])
        if not isinstance(grid, list) or len(grid) != size * size:
            raise ValueError("Send the whole grid.")
        cleaned = []
        for value in grid:
            try:
                cleaned.append(int(value or 0))
            except (TypeError, ValueError):
                cleaned.append(0)
        if any(given and cleaned[i] != given for i, given in enumerate(puzzle["grid"])):
            raise ValueError("The numbers you were given cannot change.")
        wrong = sum(
            1 for i, value in enumerate(cleaned) if value != secret["solution"][i]
        )
        if wrong or not sudoku_valid(cleaned, size):
            raise ValueError(
                f"Not solved yet: {wrong} square{'s' if wrong != 1 else ''} to fix."
            )
        hints = int(secret.get("hints") or 0)
        base = 500 if not timed else max(150, 1500 - int(seconds))
        return max(10, int(round(base * factor)) - hints * 50), {
            "hints": hints,
            "solved": True,
        }
    if game == "word":
        guesses = secret["guesses"]
        solved = secret["answer"] in guesses
        if not solved and len(guesses) < WORD_TRIES:
            raise ValueError("Keep guessing: the word is not found yet.")
        tries = guesses.index(secret["answer"]) + 1 if solved else len(guesses)
        return ((WORD_TRIES + 1 - tries) * 100 if solved else 0), {
            "solved": solved,
            "tries": tries,
            "answer": secret["answer"],
        }
    raise ValueError("Unknown game.")
