"""Texture sequences from explicit ROM descriptors; no neighboring-ID guesses."""
from .core import texture_bank, texture_animation, rom_model

def sequences(rom, table):
    result = {}
    def add(frames, label, ticks):
        if len(set(frames)) > 1:
            row = (tuple(frames), label, ticks)
            for image in frames:
                if row not in result.setdefault(image, []):
                    result[image].append(row)
    if table == 25:
        for index in range(texture_bank.entry_count(rom, 5)):
            try:
                actor = rom_model.parse_actor(texture_bank.table_entry(rom, 5, index))
                for slot, frames in rom_model.parse_dynamic_textures(actor).items():
                    add(frames, f"Actor {index:03X}, texture slot {slot} (manual preview)", 3)
            except (ValueError, IndexError, TypeError):
                continue
    if table == 7:
        for bank, parser, label in ((4, texture_animation.prop_animations, "Prop"),
                                    (1, texture_animation.map_animations, "Map")):
            for index in range(texture_bank.entry_count(rom, bank)):
                try:
                    for animation in parser(texture_bank.table_entry(rom, bank, index) or b""):
                        add(animation.frames, f"{label} {index:03X}, sequence {animation.key}", animation.ticks_per_frame)
                except (ValueError, IndexError):
                    continue
    return result
