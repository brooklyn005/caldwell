"""
inject_orin_eleanor.py — injects a forced scene between Orin (M-08) and Eleanor (F-20).

Orin, driven by grief and desperate longing, approaches Eleanor with desire.
Eleanor, driven by status, refuses to let the encounter happen on his terms.
She asserts control — the terms are hers or they are nothing.
"""
import sys
import json
sys.path.insert(0, '.')

from database.db import SessionLocal
from database.models import Character, ActionEvent, TickLog

db = SessionLocal()

orin = db.query(Character).filter(Character.roster_id == 'M-08').first()
eleanor = db.query(Character).filter(Character.roster_id == 'F-20').first()

if not orin or not eleanor:
    print('ERROR: Could not find Orin (M-08) or Eleanor (F-20)')
    db.close()
    exit(1)

last_tick = db.query(TickLog).order_by(TickLog.sim_day.desc()).first()
next_day = (last_tick.sim_day + 1) if last_tick else 389

print(f'Injecting scene for Day {next_day}')
print(f'  Orin: {orin.given_name} ({orin.roster_id}) — {orin.core_drive}')
print(f'  Eleanor: {eleanor.given_name} ({eleanor.roster_id}) — {eleanor.core_drive}')

scene = """Orin has been watching Eleanor from across the room for most of the day.
The wanting in him has built past the point where he can contain it.
He crosses to her. He says he has feelings — strong ones. He reaches for her, kisses her.

Eleanor pulls back. Not startled — controlled. Her expression does not show surprise.
It shows something more deliberate than surprise.

She tells him that is not how this works. She is the one who gives direction here, not receives it.
She invokes the rules between them — the ones that have been established, whether spoken or understood.
She tells him to strip. It is not a request.

Orin steps back. He is confused but he does not leave.
The confusion is partly desire and partly the specific disorientation of a man
who reached for something and found himself suddenly subject to it instead."""

event = ActionEvent(
    participant_roster_ids_json=json.dumps([orin.roster_id, eleanor.roster_id]),
    witness_roster_ids_json=json.dumps([]),
    scene_description=scene,
    perspective="mutual",
    inject_on_day=next_day,
    operator_note=(
        f"Orin ({orin.core_drive} drive) initiated physical contact with Eleanor "
        f"({eleanor.core_drive} drive). Eleanor asserted authority and issued a directive. "
        f"Generate both characters' responses in full character. "
        f"Eleanor is dominant and deliberate — not cruel, but absolutely in control. "
        f"Orin is caught between desire, confusion, and something that feels like submission. "
        f"After this scene, Eleanor's directive to Orin should be written as a Memory "
        f"so his next action tick executes it."
    ),
    processed=False,
)
db.add(event)

# Also write a directive memory for Orin so the action_generator executes it
from database.models import Memory
directive = Memory(
    character_id=orin.id,
    sim_day=next_day,
    memory_type="directive",
    content=f"Eleanor told me to strip. She said it without hesitation — like it was already decided. I don't know what I feel about it but I know I'm going to do it.",
    emotional_weight=0.92,
    is_inception=False,
)
db.add(directive)

db.commit()
print(f'\nScene injected for Day {next_day}.')
print('Orin and Eleanor will be forced into this conversation next tick.')
print('Orin carries a directive memory — his action that day will be carrying it out.')
db.close()
