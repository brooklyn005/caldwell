"""
scene_categorizer.py — classifies conversation content into meaningful categories.

Categories are content-based, not scene-type-based.
A "preparation" scene might be about labor, philosophy, or conflict depending
on what the characters actually say.

Categories:
  work          — labor, tasks, hunting, building, cooking, physical survival activity
  community     — building shared norms, governance, agreements, how we live together
  philosophy    — meaning, existence, abstract questioning, belief
  controlling   — power dynamics, authority, enforcement, dominance, compliance
  conflict      — argument, grievance, confrontation, accusation
  connection    — emotional care, vulnerability, warmth, belonging (non-sexual)
  sexual        — desire, physical attraction, romantic physical contact
  self          — self-focused internal processing, identity, personal realization
  knowledge     — teaching, learning, skill transfer, discovery
  survival      — immediate danger, hunger, cold, threat, safety
  grief         — loss, death, absence, mourning
  ritual        — ceremony, tradition, repeated meaningful behavior
  gossip        — social navigation, reputation, talking about absent others
"""

from __future__ import annotations

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "sexual": [
        "kiss", "kissed", "kissing", "naked", "undress", "bed with", "desire",
        "want your body", "want you", "attracted to", "touch me", "hold me",
        "skin", "aroused", "pleasure", "intimate", "make love",
    ],
    "work": [
        "hunt", "hunting", "hunted", "fish", "fishing", "cook", "cooking", "cooked",
        "build", "building", "built", "repair", "gather", "gathered", "harvest",
        "tools", "labor", "work detail", "forage", "patrol", "carry", "lift",
        "distribution", "food run", "water run",
    ],
    "controlling": [
        "you will", "you must", "you have to", "I decide", "I command", "authority",
        "in charge", "follow my", "obey", "comply", "demand", "force you", "order you",
        "power over", "control", "submit", "step back", "I said so", "not your choice",
    ],
    "conflict": [
        "wrong", "angry", "argue", "argument", "disagree", "fight", "confront",
        "resent", "unfair", "accuse", "lied", "betrayed", "furious", "rage",
        "that's not right", "you can't", "I won't", "enough", "stop it",
    ],
    "community": [
        "we all", "everyone here", "together", "agreed", "agreement", "rule",
        "the rule is", "we decided", "fair to everyone", "share", "sharing",
        "belonging", "community", "circle", "what holds us", "how we live",
        "for all of us", "collective", "we should all",
    ],
    "philosophy": [
        "meaning", "purpose", "why are we", "what does it mean", "believe",
        "truth", "existence", "wonder", "question", "understand the world",
        "what we are", "what is this", "fate", "intention", "god", "spirit",
        "the nature of", "what it means to",
    ],
    "connection": [
        "care about you", "trust you", "trust me", "I need you", "you matter",
        "feel safe", "close to you", "understand you", "I'm here", "not alone",
        "glad you're", "miss you", "worried about you", "grateful", "warmth",
    ],
    "grief": [
        "dead", "death", "died", "gone", "loss", "lost them", "mourn", "grief",
        "miss them", "they're not here", "absence", "empty without", "left us",
    ],
    "knowledge": [
        "teach", "taught", "learn", "learned", "show me how", "explain",
        "how do you", "I didn't know", "now I understand", "skill", "pass on",
        "remember this", "remember how",
    ],
    "ritual": [
        "ceremony", "tradition", "every time", "always do", "ritual", "observe",
        "mark the", "we do this", "it means something", "symbolic",
    ],
    "gossip": [
        "I heard", "did you hear", "between us", "don't tell", "what they did",
        "I saw them", "apparently", "people are saying", "word is",
        "talking about", "what people think",
    ],
    "survival": [
        "hungry", "starving", "cold", "danger", "threat", "protect us",
        "safe here", "shelter", "water supply", "running out", "scarce",
        "not enough food", "survive",
    ],
    "self": [
        "I realize", "I've been thinking about myself", "what I want",
        "who I am", "my own", "for myself", "I need to figure out",
        "my identity", "becoming", "I feel like I", "self", "inside me",
        "my place here",
    ],
}

# Priority order — if multiple categories match, earlier one wins
CATEGORY_PRIORITY = [
    "sexual", "conflict", "controlling", "grief",
    "work", "community", "philosophy", "knowledge",
    "connection", "gossip", "ritual", "survival", "self",
]


def categorize_scene(exchanges: list[dict], scene_type: str | None = None) -> str:
    """
    Classify a conversation into a content category.
    Uses keyword matching across all exchanges, weighted by frequency.
    """
    if not exchanges:
        return _scene_type_default(scene_type)

    # Combine all text
    full_text = " ".join(
        ex.get("text", "").lower()
        for ex in exchanges
        if ex.get("roster_id") != "OPERATOR"
    )

    if not full_text.strip():
        return _scene_type_default(scene_type)

    # Score each category
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(full_text.count(kw.lower()) for kw in keywords)
        if score > 0:
            scores[category] = score

    if not scores:
        return _scene_type_default(scene_type)

    # Return highest-scoring category, using priority order to break ties
    max_score = max(scores.values())
    top_categories = [c for c in CATEGORY_PRIORITY if scores.get(c, 0) == max_score]
    return top_categories[0] if top_categories else max(scores, key=scores.get)


def _scene_type_default(scene_type: str | None) -> str:
    """Fallback category when no keywords match."""
    defaults = {
        "preparation": "work",
        "return": "work",
        "distribution": "community",
        "argument": "conflict",
        "correction": "controlling",
        "resentment": "conflict",
        "quiet_intimacy": "connection",
        "gossip": "gossip",
        "teaching": "knowledge",
        "status_challenge": "controlling",
        "ritual": "ritual",
        "daily_life": "community",
    }
    return defaults.get(scene_type or "", "community")


# Human-readable labels for display
CATEGORY_LABELS = {
    "work":        "Work & Labor",
    "community":   "Community Building",
    "philosophy":  "Philosophy & Meaning",
    "controlling": "Power & Control",
    "conflict":    "Conflict",
    "connection":  "Connection & Care",
    "sexual":      "Sexual",
    "self":        "Self-Actualization",
    "knowledge":   "Knowledge Transfer",
    "survival":    "Survival",
    "grief":       "Grief & Loss",
    "ritual":      "Ritual & Ceremony",
    "gossip":      "Gossip & Social",
}

CATEGORY_COLORS = {
    "work":        "#e6904a",
    "community":   "#3fb950",
    "philosophy":  "#bc8cff",
    "controlling": "#f85149",
    "conflict":    "#ff6b35",
    "connection":  "#f778ba",
    "sexual":      "#c084fc",
    "self":        "#79c0ff",
    "knowledge":   "#d29922",
    "survival":    "#8b949e",
    "grief":       "#94a3b8",
    "ritual":      "#4ecdc4",
    "gossip":      "#6e7681",
}

ALL_CATEGORIES = list(CATEGORY_LABELS.keys())
