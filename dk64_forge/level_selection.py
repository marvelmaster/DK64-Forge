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
        distances = rendered_hits(render, points, origin, direction)[starts//3]
        best = int(np.argmin(distances))
        distance = distances[best]
        if not np.isfinite(distance):
            return None
        # Static level surfaces hide actors behind walls; ignore transparent surfaces.
        if map_render is not None:
            blockers = rendered_hits(map_render, np.asarray(map_render.positions), origin, direction,
                                     blockers_only=True)
            if len(blockers) and np.min(blockers) < distance-0.1:
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


def rendered_hits(render, points, origin, direction, *, blockers_only=False):
    """Respect face culling, depth-writing surfaces and transparent texture holes.

    An invisible back face or alpha-cutout rectangle must not prevent clicking
    the object visible through it. Sample the source alpha at the ray's UV.
    """
    triangles = points.reshape(-1, 3, 3)
    distances = ray_triangles(origin, direction, triangles)
    textures = {t.texture_index: t for t in render.textures}
    for batch in render.batches:
        first = batch.first_vertex // 3
        last = first + batch.vertex_count // 3
        if blockers_only and (batch.alpha_mode == "BLEND" or not batch.depth_write):
            distances[first:last] = np.inf
            continue
        faces = triangles[first:last]
        if not batch.double_sided:
            normal = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
            distances[first:last][normal @ direction >= 0] = np.inf
        if batch.alpha_mode not in ("MASK", "BLEND"):
            continue
        hits = np.flatnonzero(np.isfinite(distances[first:last])) + first
        if not len(hits):
            continue
        texture = textures.get(batch.texture_index)
        alpha = np.full(len(hits), batch.fallback_rgba[3], dtype=float)
        if texture is not None:
            faces = triangles[hits]
            edge1, edge2 = faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]
            cross = np.cross(direction, edge2)
            inv_det = 1.0 / np.einsum('ij,ij->i', edge1, cross)
            relative = origin - faces[:, 0]
            u = np.einsum('ij,ij->i', relative, cross) * inv_det
            v = np.cross(relative, edge1) @ direction * inv_det
            uv = np.asarray(render.uvs).reshape(-1, 3, 2)[hits]
            sample_uv = uv[:, 0] + u[:, None] * (uv[:, 1] - uv[:, 0]) + v[:, None] * (uv[:, 2] - uv[:, 0])
            def wrap(values, mode, size):
                if mode == "CLAMP":
                    values = np.clip(values, 0, 1)
                elif mode == "MIRROR":
                    values = 1 - np.abs(np.mod(values, 2) - 1)
                else:
                    values = np.mod(values, 1)
                return np.minimum(size - 1, np.floor(values * size).astype(int))
            x = wrap(sample_uv[:, 0], texture.wrap_s, texture.width)
            y = wrap(sample_uv[:, 1], texture.wrap_t, texture.height)
            rgba = np.frombuffer(texture.rgba, dtype=np.uint8).reshape(texture.height, texture.width, 4)
            alpha *= rgba[y, x, 3] / 255.0
        distances[hits[alpha < (0.5 if batch.alpha_mode == "MASK" else 1/255)]] = np.inf
    return distances
