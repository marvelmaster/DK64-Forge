"""DK's separate bongo actor: source-selected clips, diagnostic sample timing.

Actor 239 dispatches to 8069E040, which starts Table-13 script 299. That
script plays clips 494, 495, 493. Attachment follows DK's root at scale 1.25.
"""
from .actor_animation import ActorAnimations
from .core import anim_code_table, rom_model, texture_bank


def animation(rom, model):
    table = anim_code_table.parse_anim_code(texture_bank.table_entry(rom, 13, 0))
    routes = table.script_clips(0x299, 0)
    ids = tuple(dict.fromkeys(row[0] for row in routes))
    assets = tuple((index, *rom_model.extract_entry(rom, 11, index)) for index in ids)
    result = ActorAnimations(rom, 0xA5, model, assets)
    samples = []
    for index in ids:
        result.select(index)
        samples.extend(result.samples)
    result.samples = tuple(samples)
    result.selected_id = ids[0]
    result.source_script = 0x299
    result.clip_sequence = ids
    return result
