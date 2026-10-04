"""Map tree procedural geometry (8062CEA8) and effects 4 / 7 (8063CF3C / 8063D638).

Effect 4 is the dual-tile IA8 surface; effect 7 the scrolling RGBA16 surface.
Other effect IDs remain unsupported;
portal/transform conditions are not evaluated by this preview.
"""
import struct


def records(data):
    root = int.from_bytes(data[0x30:0x34], 'big')
    stack, seen, result = [root], set(), []
    while stack:
        at = stack.pop()
        if at in seen:
            continue
        seen.add(at)
        if at < 0x78 or at + 0xC8 > len(data):
            raise ValueError('Map effect tree outside file')
        kind, count = data[at+0xB8], data[at+0xC5]
        if kind == 0:
            for relative in struct.unpack_from('>2I', data, at):
                if relative:
                    stack.append(root + relative)
        if count > 12:
            raise ValueError('Invalid map effect count')
        for index in range(count):
            effect = struct.unpack_from('>H', data, at+0x70+index*2)[0]
            display = struct.unpack_from('>I', data, at+0x1C+index*4)[0]
            chunk = struct.unpack_from('>h', data, at+0x88+index*2)[0]
            result.append((effect, display, chunk))
    return tuple(result)


def _mux(values):
    a,b,c,d,aa,ab,ac,ad,a1,b1,c1,d1,aa1,ab1,ac1,ad1 = values
    return (0xFC000000 | a<<20 | c<<15 | aa<<12 | ac<<9 | a1<<5 | c1,
            b<<28 | b1<<24 | aa1<<21 | ac1<<18 | d<<15 | ab<<12 | ad<<9 | d1<<6 | ab1<<3 | ad1)


def scrolling_surface(map_id, tick):
    # Integer -1 (Castle Barrel Blast: -2), reset to 255 below zero.
    step = 2 if map_id == 187 else 1
    origin = (-max(0, tick)) % 256 if step == 1 else (0 if tick <= 0 else 255 - 2*((tick-1) % 128))
    alpha = 255 if map_id in (34, 187) else 150 if map_id in (54, 188) else 80
    combine = _mux((1,15,4,7,1,7,4,7,0,15,3,7,0,7,3,7))
    commands = ((0xD9000000, 0x200005), (0xD7000002, 0xFFFFFFFF),
                (0xE3000A01, 0x100000), combine, (0xFA000000, 0xFFFFFF00|alpha),
                (0xFD100000, 0x1765), (0xF5100000, 0x07014050),
                (0xF3000000, 0x073FF100), (0xF5101000, 0x00014050),
                # Keep mask-derived image dimensions while moving the tile origin.
                (0xF2000000|origin, 0x0007C000|(origin+124)))
    return b''.join(struct.pack('>2I', *command) for command in commands)


def mist_surface(tick):
    """Effect 4: 8063CF3C/8063D1D8; IA8 64x64, two shifted texture tiles."""
    # Runtime values start at zero, subtract then reset to 255 (not modulo 256).
    def origin(step):
        return 0 if tick <= 0 else 255-step*((tick-1) % (255//step+1))
    a, b = origin(5), origin(2)
    combine = _mux((15,15,31,7,1,7,2,7,2,15,1,7,0,7,4,7))
    commands = ((0xD9000000, 0x200005), (0xD7000002, 0xFFFFFFFF),
                (0xE3000A01, 0x100000), combine,
                (0xFD700000, 0x00FFFF04), (0xF5700000, 0x07018060),
                (0xF3000000, 0x077FF100),
                (0xF5681000, 0x00018461), (0xF2000000|a, 0x000FC000|(a+252)),
                (0xF5681000, 0x01018461), (0xF2000000|b, 0x010FC000|(b+252)))
    return b"".join(struct.pack(">2I", *command) for command in commands)


def append_geometry(data, map_id, tick=0, chunks=None):
    dl = int.from_bytes(data[0x34:0x38], 'big')
    vertices = int.from_bytes(data[0x38:0x3C], 'big')
    ranges, unsupported = [], set()
    for effect, offset, chunk in records(data):
        if chunks is not None and chunk != -1 and chunk not in chunks:
            continue
        if effect not in (4, 7):
            unsupported.add(effect)
            continue
        begin = dl + offset
        if not dl <= begin < vertices:
            raise ValueError('Map effect display list outside block')
        end = next((at+8 for at in range(begin, vertices-7, 8) if data[at] == 0xDF), None)
        if end is None:
            raise ValueError('Unterminated map effect display list')
        start = len(data)
        data += (scrolling_surface(map_id, tick) if effect == 7 else mist_surface(tick)) + data[begin:end]
        ranges.append((start, len(data), {6: vertices, 7: dl}, chunk, False))
    return data, ranges, unsupported
