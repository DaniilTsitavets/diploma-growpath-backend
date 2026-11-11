import uuid

from app.service.knowledge_parser import NS_GROWPATH_KB

# (journal species, question, slug of the document, anchor of the section that answers it)
PAIRS = [
    ("Basil", "How do I pinch the tips of my basil?", "basil", "pinching-and-pruning"),
    (
        "Basil",
        "Why did my basil leaves turn black after I put them in the fridge?",
        "basil",
        "harvesting-basil",
    ),
    ("Basil", "How often should I feed basil with fertiliser?", "basil", "feeding"),
    ("Mint", "How do I stop my mint from spreading everywhere?", "mint", "containing-the-spread"),
    (
        "Mint",
        "What are the orange spots under the leaves of my mint?",
        "mint",
        "pests-and-diseases",
    ),
    ("Mint", "Does mint like sun or shade?", "mint", "light"),
    (
        "Chili pepper",
        "My chili flowers keep dropping off before making fruit",
        "chili-pepper",
        "flowers-and-fruit-set",
    ),
    (
        "Chili pepper",
        "How warm does it need to be for chili seeds to sprout?",
        "chili-pepper",
        "growing-chili-from-seed",
    ),
    (
        "Chili pepper",
        "When is a chili ready to pick and how do I protect my hands?",
        "chili-pepper",
        "harvesting-chilies",
    ),
    ("Lavender", "What kind of soil does lavender need?", "lavender", "soil-and-drainage"),
    ("Lavender", "When and how should I cut back my lavender?", "lavender", "pruning"),
    ("Lavender", "Can lavender survive frost in winter?", "lavender", "winter-and-cold"),
    (
        "Basil",
        "How do I get rid of fungus gnats flying around the soil?",
        "general-care",
        "fungus-gnats-and-mould-on-the-soil",
    ),
    (
        "Lavender",
        "When should I repot and what size of pot do I need?",
        "general-care",
        "repotting",
    ),
]


def section_id(slug: str, anchor: str) -> uuid.UUID:
    return uuid.uuid5(uuid.uuid5(NS_GROWPATH_KB, slug), anchor)
