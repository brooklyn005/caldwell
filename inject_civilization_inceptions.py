"""
inject_civilization_inceptions.py

Injects civilization-building inception thoughts into characters at Day 317+.
Run once after installing the new topic_seeds.py.

These thoughts prime characters to think beyond survival toward what they're
actually building — roles, permanence, legacy, governance, fairness.
"""
import sys
import json
import random
sys.path.insert(0, '.')

from database.db import SessionLocal
from database.models import Character, InceptionEvent, Memory

# Civilization-focused inception thoughts by drive
CIVILIZATION_INCEPTIONS = {
    "Curiosity": [
        "You've been here long enough that something is becoming clear: this place is being made, right now, by what you and the others do and don't do. You want to understand how that works. How decisions get made. How things stick.",
        "You've started noticing which problems keep coming back and which ones got solved. There's a pattern. Some problems disappear when someone pays attention to them. Others need everyone to agree. You want to figure out which is which.",
    ],
    "Knowledge": [
        "You've been tracking things — patterns in the food, in the weather, in people's movements. The knowledge is accumulating but it's only in your head. You've been wondering what it would take to make it last beyond you.",
        "You know things others don't. Practical things. Real things. You've been thinking about whether that's useful only to you or whether it could be useful to everyone — and what it would mean to share it deliberately.",
    ],
    "Connection": [
        "The people you care about here are vulnerable in ways they might not know. You've been thinking about what you would build — not for yourself, but to make things safer for them. What would that even look like?",
        "You've been watching the people here for a long time now. You know who helps and who doesn't. Who can be counted on. You've been thinking about whether that knowledge could protect the people you care about.",
    ],
    "Power": [
        "You've been thinking about the fact that nothing here is organized. No one is in charge. That's either an opportunity or a problem — maybe both. You've been working out what kind of structure would make things better, and whether you're the one to push for it.",
        "You've noticed that some people here have more say in things than others. That didn't happen by accident. You've been thinking about whether the people with influence deserve it, and what you would do differently if you had more of it.",
    ],
    "Order": [
        "There are things breaking down here that no one is maintaining. You've been keeping a mental list. Some of it you can fix yourself. Some of it needs more than one person. You've been wondering whether to start saying that out loud.",
        "You've been thinking about what would make this place actually function well — not just survive, but function. The gaps are clear to you. What would it take to close them? What would you need from others?",
    ],
    "Comfort": [
        "You've been thinking about what would make you feel truly secure here — not just safe for today, but stable. And you've realized the answer has less to do with food or shelter and more to do with knowing what to expect from people. That's harder to build.",
        "You keep noticing the things that could go wrong. That's always been how your mind works. But lately you've been thinking about what it would take to actually prevent them — not just worry about them. What you would need others to agree to.",
    ],
    "Survival": [
        "You've been thinking about what it means to survive long-term here. Not just tomorrow. Longer. The answer involves other people — which is uncomfortable because people are unpredictable. But you've been working out who you would need and what you'd have to offer them.",
        "You've started thinking about what you would do if things got bad here. Not just for yourself — for the people you've decided matter. You've been quietly assessing what resources exist, what alliances would hold, what would need to exist that doesn't yet.",
    ],
    "Dominance": [
        "You've been watching how things work here — who leads and who follows — and you've been thinking about whether it's working. Whether the right people have influence. What would be different if you had more say in how things get organized.",
        "Something has been sitting with you: if this place is going to become something, someone is going to have to make real decisions. Not suggestions. Decisions. You've been thinking about whether that person is you.",
    ],
    "Belonging": [
        "You've been thinking about what this group actually is. Not just people who happened to end up in the same place — something more than that. Or not yet. You've been wondering what it would take for it to become something more.",
        "You want the people here to become something together. Not just survive next to each other. Something with more coherence. You've been thinking about what that would require — from you, from them.",
    ],
    "Envy": [
        "You've been watching what people here have managed to make for themselves — their relationships, their skills, their standing. And you've been thinking about what you want to build. Not out of competition. Out of a genuine want for something to be yours.",
        "You've been noticing what others have that you don't. Not just things — positions, relationships, trust. You've been thinking about how to build those things for yourself, and whether that's possible here.",
    ],
    "Purity": [
        "You've been thinking about what this place should be. What kind of people it should be for. What behaviors should be allowed and what shouldn't. You haven't talked about this with anyone but you have views, and they're getting clearer.",
        "There are things happening here that don't sit right with you. Not just wrong — out of alignment with what this place should be. You've been sitting with whether to say something, and what you would say.",
    ],
    "Status": [
        "You've been thinking about what standing means here. Not just who gets listened to — but why. What makes someone's opinion matter. You've been thinking about your own standing and whether it reflects what you're actually capable of.",
        "You've realized that this place is building its own version of who matters. Not titles. Something subtler. And you've been thinking about where you are in that and whether you want to be somewhere different.",
    ],
    "Tribalism": [
        "You've been thinking about who here is actually with you. Not just nearby — actually aligned. Whose interests overlap with yours enough that you could count on them. You've been building that list carefully.",
        "You've started thinking about what this group owes the people in it, and what people owe the group. There's a version of this place that takes care of its own. You've been wondering if that's what you're building or if something else is happening.",
    ],
    "Meaning": [
        "You've been thinking about what this place will become. Not just what it is — what it could be. Whether the people here are capable of building something that means something. Whether you're one of those people.",
        "You've been wondering whether what's happening here matters. Not to anyone outside — there is no outside. But in itself. Whether this struggle, this place, these people are becoming something worth becoming. You want to believe they are.",
    ],
    "Grief": [
        "You've been thinking about what you would leave behind if you weren't here. Not dramatically. Just — what would remain. What you've contributed. Whether it would last. It's made you want to build something that persists.",
        "You've been noticing the things here that won't last — relationships that are fragile, systems that could break, people whose presence isn't guaranteed. You've been wondering what can actually be made permanent, and whether it's worth trying.",
    ],
}

GENERIC_INCEPTIONS = [
    "You've been here long enough that it's no longer survival. You are past the immediate. What happens next is being decided right now, by what people do and don't do. You want to be part of that.",
    "You've started thinking about what this place will look like in a year. What would have to happen for it to be better. What you would have to do. It's not abstract anymore.",
    "You've been watching how the things you all do every day are slowly becoming the rules — not because anyone decided, but because they keep happening. That's how norms form. You've realized you're living inside that process.",
    "You want something here to be yours. Not property — something you built, something you contributed, something that changed because you were here. That want is getting louder.",
    "You've been thinking about fairness. Not a rule — just whether what's happening here is fair. Who does what. Who gets what. Whether the people doing the most are the ones getting the most. You don't have an answer yet but the question won't leave.",
]


def inject(db, day: int):
    characters = db.query(Character).filter(
        Character.alive == True,
        Character.is_minor == False,
        Character.is_infant == False,
    ).all()

    injected = 0
    for char in characters:
        drive = char.core_drive or "Curiosity"
        options = CIVILIZATION_INCEPTIONS.get(drive, []) + GENERIC_INCEPTIONS
        thought = random.choice(options)

        # Write as both InceptionEvent and Memory
        ev = InceptionEvent(
            injected_at_day=day,
            thought_content=thought,
            target_roster_ids_json=json.dumps([char.roster_id]),
        )
        db.add(ev)

        mem = Memory(
            character_id=char.id,
            sim_day=day,
            memory_type="inception",
            content=thought,
            emotional_weight=0.88,
            is_inception=True,
        )
        db.add(mem)
        injected += 1
        print(f"  {char.roster_id} ({char.given_name or 'unnamed'}, {drive}): {thought[:70]}...")

    db.commit()
    print(f"\nInjected {injected} civilization inceptions at Day {day}")


if __name__ == "__main__":
    from database.db import SessionLocal
    from database.models import TickLog
    db = SessionLocal()
    last_tick = db.query(TickLog).order_by(TickLog.sim_day.desc()).first()
    day = last_tick.sim_day if last_tick else 317
    print(f"Injecting civilization inceptions at Day {day}...\n")
    inject(db, day)
    db.close()
