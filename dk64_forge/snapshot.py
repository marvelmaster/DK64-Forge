"""Freeze exactly the current preview geometry, textures and camera for export."""
from dataclasses import replace
import numpy as np
from . import static_model, level_content


def freeze_render(viewport):
    scene = viewport._data
    if viewport._uv_provider is not None:
        uvs = viewport._uv_provider(viewport._camera.eye, viewport._camera.target)
        if uvs is not None:
            scene = replace(scene, uvs=tuple(map(tuple, uvs)))
    scenes = [scene] + ([viewport._attachment_data] if viewport._attachment_data is not None else [])
    return level_content.merge_render([freeze_billboards(data, viewport._camera) for data in scenes])


def freeze_billboards(data, camera):
    right = np.asarray(camera.view_matrix())[0, (0, 2)]
    right /= np.linalg.norm(right) or 1.0
    points = np.asarray(data.positions, dtype=float).copy()
    for batch in data.batches:
        if batch.billboard_center is None:
            continue
        span = slice(batch.first_vertex, batch.first_vertex + batch.vertex_count)
        center = np.asarray(batch.billboard_center)
        local = points[span] - center
        points[span, 0] = center[0] + right[0]*local[:, 0] - right[1]*local[:, 2]
        points[span, 2] = center[2] + right[1]*local[:, 0] + right[0]*local[:, 2]
    return replace(data, positions=tuple(map(tuple, points)),
        batches=tuple(replace(b, billboard_center=None) for b in data.batches))


def export_view(viewport, path):
    if path.suffix.lower() == ".png":
        if not viewport.grabFramebuffer().save(str(path)):
            raise OSError("Could not write preview image")
        return
    render = freeze_render(viewport)
    model = static_model.StaticModel(render, sum(b.face_count for b in render.batches), 0,
                                     len(render.textures), 0, {})
    return static_model.export_glb(model, path, "Current view (frozen pose)", camera=viewport._camera)
