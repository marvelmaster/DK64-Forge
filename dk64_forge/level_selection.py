"""Stable placement IDs, render subsets and triangle picking after batch merging."""
from dataclasses import replace
import numpy as np
from .level_content import merge_render, actor_entry
from .core import names, texture_bank


def placement_name(rom, tables, row):
    if row.kind == "prop":
        return texture_bank.prop_name(rom, row.type_id) or f"Prop {row.type_id:03X}"
    entry = actor_entry(tables, row)
    if entry is not None and entry < len(names.ACTOR_MODEL_NAMES):
        return names.ACTOR_MODEL_NAMES[entry]
    definition = tables.setup_def(row.type_id) if row.kind == "actor" else tables.enemy_def(row.type_id)
    return (definition.name if definition else "") or f"Controller / effect {row.type_id:03X}"


class PlacementIndex:
    def __init__(self, captured):
        order = []
        merge_render([scene for _, scene in captured], vertex_order=order)
        inverse = np.argsort(order)
        self.indices = {}
        at = 0
        for row, scene in captured:
            self.indices[row.kind, row.index] = inverse[at:at+len(scene.positions)]
            at += len(scene.positions)
        self.owners = np.empty(at, dtype=np.int32)
        self.keys = list(self.indices)
        for owner, key in enumerate(self.keys):
            self.owners[self.indices[key]] = owner

    def subset(self, render, keys):
        if len(keys) == len(self.indices):
            return render
        mask = np.zeros(len(render.positions), dtype=bool)
        for key in keys:
            mask[self.indices[key]] = True
        indices = np.flatnonzero(mask)
        if not len(indices):
            return None
        if len(indices) == len(mask):
            return render
        batches = []
        at = 0
        for batch in render.batches:
            count = int(mask[batch.first_vertex:batch.first_vertex+batch.vertex_count].sum())
            if count:
                batches.append(replace(batch, first_vertex=at, vertex_count=count))
                at += count
        points = np.asarray(render.positions)[indices]
        return replace(render, positions=points, uvs=tuple(map(tuple,np.asarray(render.uvs)[indices])),
            uvs1=tuple(map(tuple,np.asarray(render.uvs1 or render.uvs)[indices])), colors=np.asarray(render.colors)[indices],
            batches=tuple(batches), bounds_minimum=tuple(points.min(axis=0)), bounds_maximum=tuple(points.max(axis=0)))

    def pick(self, render, camera, x, y, width, height, visible, map_render=None):
        from .snapshot import freeze_billboards
        mvp = np.asarray(camera.projection_matrix(width/height)) @ camera.view_matrix()
        inv = np.linalg.inv(mvp)
        ray = [inv @ (2*x/width-1, 1-2*y/height, z, 1) for z in (-1, 1)]
        origin, end = [p[:3]/p[3] for p in ray]
        direction = end-origin
        direction /= np.linalg.norm(direction)
        points = np.asarray(freeze_billboards(render, camera).positions)
        mask = np.zeros(len(points), dtype=bool)
        for key in visible:
            mask[self.indices[key]] = True
        starts = np.flatnonzero(mask[::3])*3
        if not len(starts):
            return None
        distances = ray_triangles(origin, direction, points.reshape(-1,3,3)[starts//3])
        best = int(np.argmin(distances))
        distance = distances[best]
        if not np.isfinite(distance):
            return None
        # Static level surfaces hide actors behind walls; ignore transparent surfaces.
        if map_render is not None:
            opaque = [np.asarray(map_render.positions)[b.first_vertex:b.first_vertex+b.vertex_count]
                      for b in map_render.batches if b.alpha_mode != "BLEND"]
            if opaque and np.min(ray_triangles(origin, direction, np.concatenate(opaque).reshape(-1,3,3))) < distance-0.1:
                return None
        return self.keys[self.owners[starts[best]]]


def ray_triangles(origin, direction, triangles):
    """Two-sided Moller-Trumbore distances; misses are infinity."""
    a = triangles[:,0]; e1 = triangles[:,1]-a; e2 = triangles[:,2]-a
    p = np.cross(direction, e2); determinant = np.einsum('ij,ij->i', e1, p)
    valid = np.abs(determinant)>1e-9
    reciprocal = np.divide(1., determinant, out=np.zeros_like(determinant), where=valid)
    t = origin-a; u = np.einsum('ij,ij->i', t,p)*reciprocal
    q = np.cross(t,e1); v = q@direction*reciprocal
    distance = np.einsum('ij,ij->i',e2,q)*reciprocal
    valid &= (u>=0)&(v>=0)&(u+v<=1)&(distance>=0)
    return np.where(valid,distance,np.inf)
