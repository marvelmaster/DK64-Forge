"""ROM water surfaces, separate from the map's terrain/effect display lists.

US rev 0: 8065F1C0 reads header +4C, a u32 count and 0x6C records.
80660D38 builds the grid/UVs; 80660520/806608FC build height and alpha.
806616A0 / 806618A0 draw water types 0 / 3 with two moving samples of
table-7 texture 03C5. Type 6 uses the 80662618 single-tile material.
See docs/water-rendering.md for offsets, evidence and preview limits.
"""
from dataclasses import dataclass
import math
import struct
from functools import lru_cache
import numpy as np

from . import bone_matrix, mesh_decoder, rdp, texture_bank

SUPPORTED_TYPES = (0, 3, 6)


@dataclass(frozen=True)
class Surface:
    index: int
    values: tuple
    phases: tuple
    step: int
    bounds: tuple
    height: int
    rgb: tuple
    alpha: int
    alpha_wave: int
    kind: int
    chunks: tuple


def records(data):
    if len(data) < 0x78:
        raise ValueError('Truncated map water header')
    at = struct.unpack_from('>I', data, 0x4C)[0]
    if not 0x78 <= at <= len(data)-4:
        raise ValueError('Water section outside map file')
    count = struct.unpack_from('>I', data, at)[0]
    if count > 255 or at+4+count*0x6C > len(data):
        raise ValueError('Truncated map water records')
    result = []
    for index in range(count):
        row = data[at+4+index*0x6C:at+4+(index+1)*0x6C]
        values = struct.unpack_from('>17f', row)
        step, x0, z0, x1, z1, height = struct.unpack_from('>6h', row, 0x44)
        chunks = row[0x67]
        if (step <= 0 or x0 >= x1 or z0 >= z1 or chunks > 8 or
                not all(math.isfinite(values[i]) for i in (0,1,2,3,4,7,8,9,10,13,14,15,16))):
            raise ValueError('Invalid map water grid')
        if ((x1-x0)//step+2)*((z1-z0)//step+2) > 32767:
            raise ValueError('Map water grid too large')
        result.append(Surface(index, values, tuple(struct.unpack_from('>I', row, i)[0]
                      for i in (0x14,0x18,0x2C,0x30)), step, (x0,z0,x1,z1), height,
                      tuple(row[0x61:0x64]), row[0x64], row[0x65], row[0x66],
                      struct.unpack_from(f'>{chunks}h', row, 0x50)))
    return tuple(result)


def grid(surface):
    x0,z0,x1,z1 = surface.bounds
    # The native final row/column is clamped to the rectangle. Repeated boundary
    # vertices would only produce degenerate triangles, so keep each once.
    xs = sorted(set(min(x0+i*surface.step,x1) for i in range((x1-x0)//surface.step+2)))
    zs = sorted(set(min(z0+i*surface.step,z1) for i in range((z1-z0)//surface.step+2)))
    points = [(x,z) for z in zs for x in xs]
    triangles = []
    for z in range(len(zs)-1):
        for x in range(len(xs)-1):
            a = z*len(xs)+x
            # Native 80660258: (right, left, below), (below, below-right, right).
            triangles.extend((a+1,a,a+len(xs), a+len(xs),a+len(xs)+1,a+1))
    return np.asarray(points, dtype=np.float32)[triangles]


def sine(angles, table):
    """80612794: quarter table with half-step interpolation, 12-bit phase."""
    angles = np.asarray(angles, dtype=np.int64)
    offset = angles & 1023
    offset = np.where(angles & 1024, 1024-offset, offset)
    index = offset >> 1
    value = table[index]
    value = np.where(angles & 1, (value+table[index+1])*np.float32(.5), value)
    return np.where(angles & 2048, -value, value)


def wave_table(rom):
    words = bone_matrix.quarter_table_words_from_rom(rom)
    return np.asarray([struct.unpack('>f',struct.pack('>I',w))[0] for w in words], dtype=np.float32)


def sample(surface, points, tick, table):
    tick = max(0,int(tick))
    f = surface.values
    x,z = points[:,0], points[:,1]
    phases = [((tick*p)&0xFFFFFFFF) for p in surface.phases]
    def angle(frequency, coordinate, phase, remainder=False):
        raw = np.trunc(coordinate*np.float32(frequency)).astype(np.int64)+phase
        raw = ((raw+0x80000000)&0xFFFFFFFF)-0x80000000
        return np.sign(raw)*(np.abs(raw)%4095) if remainder else raw & 4095
    # All three supported materials use normal waves (material table byte +15=0).
    height = ((np.float32(surface.height)+sine(angle(f[1],x,phases[0],True),table)*np.float32(f[3])) +
              sine(angle(f[2],z,phases[1],True),table)*np.float32(f[4]))
    wave = height-np.float32(surface.height)
    y = np.trunc(height*np.float32(3))/np.float32(3)
    dx = sine(angle(f[7],x,phases[2]),table)*np.float32(f[9])
    dz = sine(angle(f[8],z,phases[3]),table)*np.float32(f[10])
    positions = np.column_stack((np.trunc(x+dx),y,np.trunc(z+dz)))
    alpha = np.full(len(points),surface.alpha,dtype=np.float32)
    amplitude = f[3]+f[4]
    if amplitude:
        alpha += wave/np.float32(amplitude)*np.float32(surface.alpha_wave)
    alpha = (np.trunc(alpha).astype(np.int64)&255)/255.
    colors = np.column_stack((np.tile(np.array(surface.rgb)/255.,(len(points),1)),alpha))
    # 80660D38 uses unperturbed coordinates, signed remainder, S10.5 and
    # G_TEXTURE 0xFFFF. Water tiles then multiply by four/eight (shifts 14/13).
    st = np.trunc(points*np.float32(f[0])).astype(np.int64)
    st = np.sign(st)*(np.abs(st)%32767)
    texels = st.astype(float)/32.*(65535./65536.)
    a = scroll_origin(f[15],f[13],tick)
    b = scroll_origin(f[16],f[14],tick)
    if surface.kind == 6:
        uv = (texels+(.5, .5-a/4.))/32.
        uv1 = uv.copy()
    else:
        uv = (texels*4.+(.5,.5-a/4.))/32.
        uv1 = (texels*8.+(.5-b/4.,.5-b/4.))/32.
    return positions,colors,uv,uv1


def scroll_origin(initial, speed, tick):
    """Float32 subtraction/reset to 255, not wrapping by 256."""
    value, speed = np.float32(initial), np.float32(speed)
    if speed == 0 or tick == 0:
        return math.trunc(float(value))
    # Short cycles are cached; this also keeps seeking independent of frame history.
    return _scroll(float(value),float(speed),int(tick))


@lru_cache(maxsize=64)
def _scroll_cycle(initial,speed):
    value = np.float32(initial)
    values,seen = [],{}
    for _ in range(100000):
        key = value.tobytes()
        if key in seen:
            return tuple(values),seen[key]
        seen[key] = len(values)
        values.append(math.trunc(float(value)))
        value = np.float32(value-np.float32(speed))
        if value < 0:
            value = np.float32(255)
    raise ValueError('Unsupported water scroll period')


def _scroll(initial,speed,tick):
    values,start = _scroll_cycle(initial,speed)
    index = tick if tick < len(values) else start+(tick-start)%(len(values)-start)
    return values[index]


def mesh(surface, points, tick, table):
    positions,colors,uv,uv1 = sample(surface,points,tick,table)
    image = 0x3D2 if surface.kind == 6 else 0x3C5
    usage = texture_bank.TextureUsage(0,2 if surface.kind == 6 else 3,32,32,False,None,'water')
    texture = mesh_decoder.DrawTexture(image,usage,0,0,7)
    if surface.kind == 0:
        material = rdp.MaterialState(mux=rdp.decode_mux(0xFC20FE04,0xFF13F3FF),cycle=1)
    elif surface.kind == 3:
        material = rdp.MaterialState(mux=(2,15,1,7,7,7,7,1,0,15,4,7,0,7,3,7),cycle=1,
                                    primitive=(0.,0.,0.,surface.alpha/255.))
    else:
        material = rdp.MaterialState(mux=(1,15,4,7,1,7,4,7)*2)
    triangles = len(points)//3
    return mesh_decoder.StaticMesh(positions=positions.tolist(),uvs=uv.tolist(),colors=colors.tolist(),
        textures=[texture]*triangles,secondary_textures=[texture]*triangles,
        secondary_uvs=uv1.tolist(),materials=[material]*triangles,culled=[False]*triangles)
