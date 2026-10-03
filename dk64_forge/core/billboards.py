"""Model-two quad records: 8063524C builds Vtx; 80635468 selects the texture bank.

Header 70 points to count + 0x30-byte rows. Each row stores two images,
four XYZ corners in three separate arrays, four S10.5 UV pairs and image usage.
"""
import struct
from . import mesh_decoder, texture_bank


def decode(data: bytes, overrides=None):
    mesh = mesh_decoder.StaticMesh()
    offset = struct.unpack_from('>I', data, 0x70)[0]
    if offset + 4 > len(data):
        raise ValueError('Billboard section outside model')
    count = struct.unpack_from('>I', data, offset)[0]
    if count > 256 or offset + 4 + count * 48 > len(data):
        raise ValueError('Truncated billboard quad records')
    anim = struct.unpack_from('>I', data, 0x6C)[0]
    table = 7 if anim + 4 <= len(data) and struct.unpack_from('>I', data, anim)[0] else 25
    for index in range(count):
        at = offset + 4 + index * 48
        image, palette = struct.unpack_from('>2H', data, at)
        width, height, size, fmt = data[at+44:at+48]
        if not width or not height or fmt not in (0, 2, 3, 4) or size > 3:
            raise ValueError('Invalid billboard texture usage')
        image_table, image = (overrides or {}).get(image, (table, image))
        usage = texture_bank.TextureUsage(fmt, size, width, height, False,
                                          palette if fmt == 2 and palette != 0xFFFF else None, 'prop quad')
        texture = mesh_decoder.DrawTexture(image, usage, 2, 2, image_table)
        xyz = [struct.unpack_from('>4h', data, at + 4 + 8*axis) for axis in range(3)]
        uv = [struct.unpack_from('>2h', data, at + 28 + 4*corner) for corner in range(4)]
        for face in ((0, 1, 2), (0, 2, 3)):
            for corner in face:
                mesh.positions.append(tuple(float(axis[corner]) for axis in xyz))
                mesh.uvs.append((uv[corner][0] / 32 / width, uv[corner][1] / 32 / height))
                mesh.colors.append((1., 1., 1., 1.))
                mesh.joints.append(0)
            mesh.textures.append(texture)
            mesh.culled.append(False)
    mesh.stats = {'unsupported': {}}
    return mesh
