"""Tiny's two procedural eight-joint hair chains.

Source: 806C8220 / 806F1EB0 ROM instructions and the common pendulum
806F1A18 in code_F56F0.c. Inputs are game Actor motion and anchor positions;
an offline origin-centred clip cannot reconstruct those world inputs.
"""
import math
import struct
from .control_states import global_asm_data, GLOBAL_ASM_DATA_VRAM


class TinyHair:
    def __init__(self, rom: bytes, clip: int):
        data = global_asm_data(rom)
        self.rest = struct.unpack_from(">8f", data, 0x80750AD8 - GLOBAL_ASM_DATA_VRAM)
        self.target_weight = struct.unpack_from("b", data, 0x80752CBF - GLOBAL_ASM_DATA_VRAM + clip)[0]
        self.weight = -128.0
        self.angles = [[[0., 0.] for _ in range(8)] for _ in range(2)]
        self.velocities = [[[0., 0.] for _ in range(8)] for _ in range(2)]
        self.last_y = [None, None]
        self.last_delta = [0., 0.]

    def step(self, anchor_y=(0., 0.), *, heading_delta=0., speed_force=0.):
        """Return terminated adjustment rows. One call is one 30 Hz preview tick.

        heading_delta is the game's wrapped delta / 2*pi; speed_force is the
        result of 806F1AE0. Neither is inferred from an animation clip here.
        """
        self.weight += (self.target_weight - self.weight) * 0.3
        weight = math.trunc(self.weight)
        if weight == 0:
            return bytes(8)
        rows = bytearray()
        for chain in range(2):
            sign = 1. if chain == 0 else -1.
            y = anchor_y[chain]
            delta = 0. if self.last_y[chain] is None else y - self.last_y[chain]
            acceleration = delta - self.last_delta[chain]
            self.last_y[chain], self.last_delta[chain] = y, delta
            drives = (sign * (0.3 * abs(heading_delta) - 0.015 * acceleration - 0.0008 * delta),
                      sign * speed_force + 0.4 * heading_delta)
            accumulated = [0., 0.]
            for joint in range(8):
                for axis in range(2):
                    angle = self.angles[chain][joint][axis]
                    velocity = self.velocities[chain][joint][axis]
                    velocity += drives[axis] * math.cos(accumulated[axis]) * math.cos(angle)
                    velocity -= 0.06 * math.sin(angle)
                    velocity *= 0.93
                    angle += velocity
                    self.angles[chain][joint][axis] = angle
                    self.velocities[chain][joint][axis] = velocity
                    rest = sign * self.rest[joint] if axis == 0 else 0.
                    accumulated[axis] += angle + rest
                z = math.trunc(self.angles[chain][joint][1] * 10430.218905280077)
                y_angle = math.trunc((sign * self.rest[joint] + self.angles[chain][joint][0]) * 10430.218905280077)
                signed = lambda n: (n + 32768) % 65536 - 32768
                rows.extend(struct.pack(">bBhhh", weight, 22 + chain * 8 + joint, 0, signed(z), signed(y_angle)))
        return bytes(rows) + bytes(8)
