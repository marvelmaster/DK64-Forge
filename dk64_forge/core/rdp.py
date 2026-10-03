"""RDP material state decoded from F3DEX2 commands (SDK PR/gbi.h).

The mux is exact. Desktop evaluation uses floating point; RDP coverage,
dither, LOD and YUV conversion are outside the material preview.
"""
from dataclasses import dataclass, replace


def decode_mux(w0: int, w1: int) -> tuple[int, ...]:
    return ((w0 >> 20) & 15, (w1 >> 28) & 15, (w0 >> 15) & 31, (w1 >> 15) & 7,
            (w0 >> 12) & 7, (w1 >> 12) & 7, (w0 >> 9) & 7, (w1 >> 9) & 7,
            (w0 >> 5) & 15, (w1 >> 24) & 15, w0 & 31, (w1 >> 6) & 7,
            (w1 >> 21) & 7, (w1 >> 3) & 7, (w1 >> 18) & 7, w1 & 7)


def opaque_alpha(material, shade: float = 1.0) -> float:
    """Preview alpha for opaque texels, used to classify material transparency."""
    if material.mux is None or material.cycle in (2, 3):
        return shade
    combined = 0.0
    for cycle in range(2 if material.cycle == 1 else 1):
        a, b, c, d = material.mux[cycle*8+4:cycle*8+8]
        inputs = (combined, 1., 1., material.primitive[3], shade, material.environment[3], 1., 0.)
        multiplier = 0.0 if c == 0 else material.prim_lod if c == 6 else inputs[c]
        combined = (inputs[a] - inputs[b]) * multiplier + inputs[d]
    return max(0., min(1., combined))


@dataclass(frozen=True)
class MaterialState:
    mux: tuple[int, ...] | None = None
    cycle: int = 0
    primitive: tuple[float, ...] = (1., 1., 1., 1.)
    environment: tuple[float, ...] = (1., 1., 1., 1.)
    prim_lod: float = 0.
    key_center: tuple[float, ...] = (0., 0., 0.)
    key_scale: tuple[float, ...] = (0., 0., 0.)
    convert_k: tuple[float, ...] = (0., 0.)
    texture_lod: bool = False

    def command(self, w0: int, w1: int):
        op = w0 >> 24
        if op == 0xFC:
            return replace(self, mux=decode_mux(w0, w1))
        if op in (0xFA, 0xFB):
            color = tuple(((w1 >> shift) & 255) / 255. for shift in (24, 16, 8, 0))
            return (replace(self, primitive=color, prim_lod=(w0 & 255) / 255.) if op == 0xFA
                    else replace(self, environment=color))
        if op == 0xEA:
            return replace(self, key_center=(self.key_center[0], ((w1 >> 24) & 255) / 255., ((w1 >> 16) & 255) / 255.),
                           key_scale=(self.key_scale[0], ((w1 >> 8) & 255) / 255., (w1 & 255) / 255.))
        if op == 0xEB:
            return replace(self, key_center=(((w1 >> 8) & 255) / 255., *self.key_center[1:]),
                           key_scale=((w1 & 255) / 255., *self.key_scale[1:]))
        if op == 0xEC:
            signed = lambda value: value - 512 if value & 256 else value
            return replace(self, convert_k=(signed((w1 >> 9) & 511) / 255., signed(w1 & 511) / 255.))
        if op == 0xE3:
            length = (w0 & 255) + 1
            shift = 32 - ((w0 >> 8) & 255) - length
            changes = {}
            if shift <= 20 and shift + length >= 22:
                changes["cycle"] = (w1 >> 20) & 3
            if shift <= 16 and shift + length >= 17:
                changes["texture_lod"] = bool(w1 & 0x10000)
            return replace(self, **changes)
        return self

    def json(self):
        return {"mux": self.mux, "cycle": self.cycle, "primitive": self.primitive,
                "environment": self.environment, "prim_lod": self.prim_lod,
                "key_center": self.key_center, "key_scale": self.key_scale,
                "convert_k": self.convert_k, "texture_lod": self.texture_lod}

    @classmethod
    def from_json(cls, data):
        return cls(None if data.get("mux") is None else tuple(data["mux"]),
                   data.get("cycle", 0), tuple(data.get("primitive", (1.,)*4)),
                   tuple(data.get("environment", (1.,)*4)), data.get("prim_lod", 0.),
                   tuple(data.get("key_center", (0.,)*3)), tuple(data.get("key_scale", (0.,)*3)),
                   tuple(data.get("convert_k", (0.,)*2)), data.get("texture_lod", False))
