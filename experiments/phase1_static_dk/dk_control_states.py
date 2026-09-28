"""Player control states -> handler code -> animation calls (static, Phase 2 names).

Evidence: research/DK_CONTROL_STATES.md.
- code_CEAE0.c:34-38/431: D_80750B50[control_state] is {void (*handler)(), s16 flags, s16 pad};
  the handler runs every frame for the current player state. The table is read from the
  global_asm .data overlay (ROM gzip 0xC29D4, vram base 0x80744460).
- Handler bodies and their callees come from the decomp C source; functions that
  are still GLOBAL_ASM are reported as opaque.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import re
import struct
import zlib

GLOBAL_ASM_DATA_ROM = 0xC29D4
GLOBAL_ASM_DATA_GZIP_SIZE = 0x949C + 8
GLOBAL_ASM_DATA_VRAM = 0x80744460
GLOBAL_ASM_CODE_VRAM = (0x805FB300, 0x80744460)
STATE_TABLE_VRAM = 0x80750B50
# code_E4090.c: ControlStateInputHandler D_80751004[input_set], 17 handler pointers.
INPUT_TABLE_VRAM = 0x80751004
INPUT_SLOTS = ("start", "always_before", "A", "B", "Z", "A_released", "B_released",
               "Z_released", "L", "R", "unk28", "R_released", "C_up", "C_left", "C_right",
               "C_down", "always_after")

ANIMATION_CALLS = {
    "playAnimation": "play_slot",
    "func_global_asm_80613C48": "clip", "func_global_asm_80613CA8": "clip",
    "func_global_asm_80614014": "clip",
    "func_global_asm_80613AF8": "slot", "func_global_asm_80613BA0": "slot",
    "func_global_asm_80613FB0": "slot",
    "playActorAnimation": "script",
}
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_ANIM = re.compile(r"\b(" + "|".join(ANIMATION_CALLS) + r")\(\s*[^,()]+?\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*[,)]")
_DEFINITION = re.compile(r"^[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;{]*\)\s*\{\s*$")
_ASM = re.compile(r'#pragma GLOBAL_ASM\("[^"]*/(\w+)\.s"\)')
_STATE_WRITE = re.compile(r"control_state\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;")
_INPUT_SET = re.compile(r"handleInputsForControlState\(\s*(0x[0-9A-Fa-f]+|\d+)\s*\)")
_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "case", "else", "do"}


def global_asm_data(rom: bytes) -> bytes:
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    return stream.decompress(rom[GLOBAL_ASM_DATA_ROM:GLOBAL_ASM_DATA_ROM + GLOBAL_ASM_DATA_GZIP_SIZE])


def read_state_table(data: bytes, base: int = GLOBAL_ASM_DATA_VRAM) -> list[tuple[int, int, int]]:
    """(state, handler address, flags) until the first non-code handler pointer."""
    states, at = [], STATE_TABLE_VRAM - base
    while at + 8 <= len(data):
        handler, flags, _pad = struct.unpack_from(">Ihh", data, at)
        if not GLOBAL_ASM_CODE_VRAM[0] <= handler < GLOBAL_ASM_CODE_VRAM[1]:
            break
        states.append((len(states), handler, flags & 0xFFFF))
        at += 8
    return states


def read_input_table(data: bytes, count: int, base: int = GLOBAL_ASM_DATA_VRAM) -> list[dict[str, int]]:
    rows, at = [], INPUT_TABLE_VRAM - base
    for _ in range(count):
        pointers = struct.unpack_from(f">{len(INPUT_SLOTS)}I", data, at)
        rows.append(dict(zip(INPUT_SLOTS, pointers)))
        at += 4 * len(INPUT_SLOTS)
    return rows


def handler_name(address: int) -> str:
    return f"func_global_asm_{address:08X}"


@dataclass
class Function:
    name: str
    file: str
    line: int
    callees: set[str] = field(default_factory=set)
    animation_calls: list[dict] = field(default_factory=list)
    state_writes: set[int] = field(default_factory=set)      # control_state = N
    input_sets: set[int] = field(default_factory=set)        # handleInputsForControlState(N)


@dataclass
class DecompIndex:
    functions: dict[str, Function]
    asm_only: set[str]

    def reach(self, root: str, depth: int = 3) -> tuple[list[dict], set[str]]:
        """Animation calls reachable from root within depth, plus opaque asm callees."""
        found, opaque, seen = [], set(), {root}
        queue = deque([(root, 0)])
        while queue:
            name, level = queue.popleft()
            function = self.functions.get(name)
            if function is None:
                if name in self.asm_only:
                    opaque.add(name)
                continue
            found.extend({**call, "depth": level} for call in function.animation_calls)
            if level < depth:
                for callee in sorted(function.callees - seen):
                    seen.add(callee)
                    queue.append((callee, level + 1))
        return found, opaque


def index_decomp(src: Path) -> DecompIndex:
    functions, asm_only = {}, set()
    for path in sorted(src.rglob("*.c")):
        rel = path.relative_to(src).as_posix()
        current = None
        depth = 0
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            asm = _ASM.search(line)
            if asm and depth == 0:
                asm_only.add(asm.group(1))
                continue
            if depth == 0:
                match = _DEFINITION.match(line)
                if match:
                    current = Function(match.group(1), rel, number)
                    functions[current.name] = current
            if current is not None and depth > 0 or (current is not None and "{" in line):
                for call in _ANIM.finditer(line):
                    name, value = call.groups()
                    current.animation_calls.append({
                        "kind": ANIMATION_CALLS[name], "call": name,
                        "value": int(value, 0) & ~0x4000, "site": f"{rel}:{number}",
                        "function": current.name})
                current.state_writes.update(int(v, 0) for v in _STATE_WRITE.findall(line))
                current.input_sets.update(int(v, 0) for v in _INPUT_SET.findall(line))
                current.callees.update(name for name in _CALL.findall(line)
                                       if name not in _KEYWORDS and name != current.name)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                depth, current = 0, (None if depth == 0 and "}" in line else current)
    asm_only -= set(functions)
    return DecompIndex(functions, asm_only)


COMMENT = re.compile(r"control_state\s*(?:==|!=|<=|>=|<|>)\s*(0x[0-9A-Fa-f]+|\d+)\s*\)*\s*[;{|&]*\s*[{]?\s*//\s*(.+)$")


def randomizer_state_comments(src: Path) -> dict[int, set[str]]:
    """Community names: `control_state == N // comment` in the Randomizer base-hack."""
    names: dict[int, set[str]] = {}
    for path in sorted(src.rglob("*.c")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = COMMENT.search(line)
            if match and "==" in line:
                names.setdefault(int(match.group(1), 0), set()).add(match.group(2).strip())
    return names


def state_reach(index: DecompIndex, states, inputs, depth: int = 3) -> dict[str, set[tuple[int, str]]]:
    """function -> {(state, route)}: which player states' per-frame code reaches it.

    Routes: 'handler' (state handler call tree) or '<button>' (an input handler of an
    input set the state handler selects via handleInputsForControlState)."""
    reached: dict[str, set[tuple[int, str]]] = {}

    def walk(root: str, state: int, route: str) -> None:
        seen, queue = {root}, deque([(root, 0)])
        while queue:
            name, level = queue.popleft()
            reached.setdefault(name, set()).add((state, route))
            function = index.functions.get(name)
            if function is None or level >= depth:
                continue
            for callee in function.callees - seen:
                seen.add(callee)
                queue.append((callee, level + 1))

    for state, address, _flags in states:
        root = handler_name(address)
        walk(root, state, "handler")
        handler = index.functions.get(root)
        for input_set in sorted(handler.input_sets if handler else ()):
            if input_set < len(inputs):
                for slot, pointer in inputs[input_set].items():
                    if slot not in ("always_before", "always_after"):
                        walk(handler_name(pointer), state, slot)
    return reached


def nearby_state_writes(src: Path, site: str, radius: int = 6) -> list[int]:
    """control_state = N written within `radius` lines of an animation call site."""
    file, line = site.rsplit(":", 1)
    lines = (src / file).read_text(encoding="utf-8", errors="replace").splitlines()
    number = int(line)
    window = lines[max(0, number - 1 - radius):number + radius]
    return sorted({int(v, 0) for text in window for v in _STATE_WRITE.findall(text)})
