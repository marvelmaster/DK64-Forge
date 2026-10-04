"""Embedded prop matrix animation as sparse affine morph targets in glTF.

A 3x4 affine basis preserves pre/post matrices, including shear, without
forcing them into TRS. Each frame's weights reconstruct the same matrix used
by the preview. Textures are frozen at frame zero; timing/forward wrap use the
preview policy, not execution of object scripts.
"""
import json
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from . import static_model
from .core import texture_bank, prop_animation


def cycle_ticks(track, speed=1):
    key, fraction = 0, 0.
    for tick in range(1,6001):
        rate = track.speeds[key]+(track.speeds[key+1]-track.speeds[key])*fraction
        fraction += rate*speed/300.
        advance = int(fraction)
        fraction -= advance
        if key+advance >= len(track.speeds)-1:
            return tick
        key += advance
    raise ValueError("Selected prop track does not complete a forward cycle within 6000 preview ticks")


def export_animation(rom, entry, cache, track_index, path, name):
    rig = prop_animation.parse(texture_bank.table_entry(rom,4,entry))
    track = next((t for t in rig.tracks if t.index == track_index),None) if rig else None
    if track is None:
        raise ValueError("Select an embedded prop animation first")
    model = static_model.prop_model(rom,entry,cache)
    duration = cycle_ticks(track)
    destinations = sorted({row[0] for row in track.rows})
    if any(not np.allclose(rig.matrices.get(d,np.eye(4)),np.eye(4)) for d in destinations):
        raise ValueError("Non-identity prop bind matrix is not supported by affine export")
    with TemporaryDirectory(prefix="forge_prop_") as folder:
        temp = Path(folder)/"base.glb"
        static_model.export_glb(model,temp,name)
        blob = temp.read_bytes()
    length = struct.unpack_from('<I',blob,12)[0]
    doc = json.loads(blob[20:20+length])
    binary_length = struct.unpack_from('<I',blob,20+length)[0]
    binary = bytearray(blob[28+length:28+length+binary_length])
    def view(payload):
        binary.extend(b'\0'*(-len(binary)%4))
        index = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(payload)})
        binary.extend(payload)
        return index
    def accessor(values, kind):
        a = np.asarray(values,dtype='<f4')
        index = len(doc['accessors'])
        item = {'bufferView':view(a.tobytes()),'componentType':5126,'count':len(a),'type':kind}
        if kind == 'SCALAR':
            item.update(min=[float(a.min())],max=[float(a.max())])
        doc['accessors'].append(item)
        return index
    target_count = len(destinations)*12
    for primitive,batch in zip(doc['meshes'][0]['primitives'],model.render.batches):
        sl = slice(batch.first_vertex,batch.first_vertex+batch.vertex_count)
        points = np.asarray(model.render.positions)[sl]
        joints = np.asarray(model.rigid_joints)[sl]
        primitive['targets'] = []
        for destination in destinations:
            indices = np.flatnonzero(joints == destination//64).astype('<u4')
            index_view = view(indices.tobytes()) if len(indices) else None
            for row in range(3):
                for col in range(4):
                    values = np.zeros((len(indices),3),dtype='<f4')
                    if len(indices):
                        values[:,row] = points[indices,col] if col<3 else 1.
                    acc = {'componentType':5126,'count':len(points),'type':'VEC3','min':[0.,0.,0.],'max':[0.,0.,0.]}
                    if len(indices):
                        acc['sparse'] = {'count':len(indices),'indices':{'bufferView':index_view,'componentType':5125},'values':{'bufferView':view(values.tobytes())}}
                        acc['min'] = np.minimum(values.min(axis=0),0).tolist()
                        acc['max'] = np.maximum(values.max(axis=0),0).tolist()
                    primitive['targets'].append({'POSITION':len(doc['accessors'])})
                    doc['accessors'].append(acc)
    ticks = list(range(duration)) + [duration]
    weights = []
    for tick in ticks:
        matrices = rig.pose(track_index, tick if tick<duration else 0)
        weights.extend(float(value) for d in destinations for value in (matrices[d]-np.eye(4))[:3,:].flat)
    doc['meshes'][0]['weights'] = [0.]*target_count
    times = accessor(np.asarray(ticks)/30.,'SCALAR')
    output = accessor(weights,'SCALAR')
    doc['animations'] = [{'name':f"{name} animation {track_index+1}",
        'samplers':[{'input':times,'output':output,'interpolation':'LINEAR'}],
        'channels':[{'sampler':0,'target':{'node':0,'path':'weights'}}],
        'extras':{'track':track_index,'timing':'preview 30 ticks/s, speed 1, forward wrap','runtime_faithful':False,'textures':'frozen at frame zero'}}]
    binary.extend(b'\0'*(-len(binary)%4));doc['buffers'][0]['byteLength']=len(binary)
    text = json.dumps(doc,separators=(',',':')).encode('utf-8');text += b' '*(-len(text)%4)
    Path(path).write_bytes(struct.pack('<III',0x46546C67,2,28+len(text)+len(binary))+struct.pack('<II',len(text),0x4e4f534a)+text+struct.pack('<II',len(binary),0x004e4942)+binary)
    return {'samples':len(ticks),'targets':target_count,'triangles':model.triangles}
