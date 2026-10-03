"""Single-player Kong blink preview from 806CF580 and 8072881C.

The game uses a random 4% trigger after 50 ticks. The preview uses a fixed
seed for repeatable scrubbing; this is not a replay of the game's RNG state.
Other expression/mouth slots remain under manual control.
"""
import random


class KongBlink:
    def __init__(self, character: str):
        self.character = character
        self.reset()

    def reset(self):
        self._rng = random.Random(0xD064)
        self._tick = -1
        self._last = 0
        self._phase = None
        self._frame = 0

    def frames(self, tick: int, slots: dict) -> dict[int, int]:
        tick = max(0, int(tick))
        if tick < self._tick:
            self.reset()
        for current in range(self._tick + 1, tick + 1):
            if self._phase is not None:
                # Rate 1, range [0,3), ping-pong, two boundary crossings.
                sequence = (0, 1, 2, 2, 1, 0, 0)
                self._phase += 1
                if self._phase >= len(sequence):
                    self._phase = None
                    self._frame = 0
                else:
                    self._frame = sequence[self._phase]
            elif current > self._last + 50 and self._rng.random() < .04:
                self._last = current
                self._phase = 0
                self._frame = 0
        self._tick = tick
        ordinal_count = 2 if self.character in ('diddy', 'tiny') else 1
        return {slot: self._frame for slot, images in list(slots.items())[:ordinal_count] if len(images) >= 3}
