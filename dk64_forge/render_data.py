"""JFG Forge viewport data contract adapted to DK64 glTF preview content.

PreparedTexture/Batch/RenderData and draw ordering follow the generic MIT
licensed JFG Forge renderer contract. No JFG asset semantics are used.
"""

from dataclasses import dataclass, replace
import weakref
from .core.rdp import MaterialState


@dataclass(frozen=True)
class PreparedTexture:
    texture_index: int
    width: int
    height: int
    rgba: bytes
    wrap_s: str
    wrap_t: str
    mip_levels: tuple[tuple[int, int, bytes], ...] = ()


@dataclass(frozen=True)
class PreparedBatch:
    first_vertex: int
    vertex_count: int
    texture_index: int | None
    double_sided: bool
    uses_verified_texture: bool
    fallback_rgba: tuple[float, float, float, float]
    fallback_reason: str | None
    alpha_mode: str = "OPAQUE"
    depth_write: bool = True
    depth_compare: bool = True
    z_mode: int = 0
    material: MaterialState = MaterialState()
    texture1_index: int | None = None

    @property
    def face_count(self) -> int:
        return self.vertex_count // 3


@dataclass(frozen=True)
class PreparedRenderData:
    positions: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    batches: tuple[PreparedBatch, ...]
    textures: tuple[PreparedTexture, ...]
    bounds_minimum: tuple[float, float, float]
    bounds_maximum: tuple[float, float, float]
    # Optional per-corner RGBA (0..1), e.g. DK64 map/prop vertex colours (shade). None = white.
    colors: tuple[tuple[float, float, float, float], ...] | None = None
    uvs1: tuple[tuple[float, float], ...] | None = None


def depth_comparison_for_batch(batch: PreparedBatch) -> str:
    return "LEQUAL" if batch.z_mode == 3 else "LESS"


_CENTROIDS: dict = {}


def _blend_centroids(scene: PreparedRenderData) -> dict:
    """Per-blended-batch vertex centroid, cached per positions tuple (sorting key only)."""
    key = (id(scene.positions), len(scene.positions), id(scene.batches))
    entry = _CENTROIDS.get(key)
    owner = entry[0]() if entry is not None else None
    if owner is None or owner.positions is not scene.positions or owner.batches is not scene.batches:
        import numpy as np
        blended = [b for b in scene.batches if b.alpha_mode == "BLEND"]
        cached = {}
        if blended:
            points = np.asarray(scene.positions, dtype=np.float64)
            for b in blended:
                cached[b.first_vertex] = points[b.first_vertex:b.first_vertex + b.vertex_count].mean(axis=0)
        if len(_CENTROIDS) > 16:
            _CENTROIDS.clear()
        _CENTROIDS[key] = (weakref.ref(scene), cached)
    else:
        cached = entry[1]
    return cached


def ordered_draw_batches(scenes: tuple[PreparedRenderData, ...], view_matrix):
    """Opaque first, blended groups back to front, as in JFG Forge.

    A blended batch sorts by the camera depth of its vertex centroid (equal to the mean of
    its vertices' depths, because depth is affine in position)."""
    sortable = []
    view_z = view_matrix[2]
    for scene_index, scene in enumerate(scenes):
        centroids = None
        for batch in scene.batches:
            if batch.alpha_mode == "BLEND":
                phase = 2
                if centroids is None:
                    centroids = _blend_centroids(scene)
                c = centroids[batch.first_vertex]
                camera_z = view_z[0] * c[0] + view_z[1] * c[1] + view_z[2] * c[2] + view_z[3]
            else:
                phase = 0 if batch.depth_write else 1
                camera_z = 0.0
            sortable.append((phase, camera_z, scene_index, batch.first_vertex, batch))
    sortable.sort(key=lambda item: item[:4])
    return tuple((scene_index, batch) for _, _, scene_index, _, batch in sortable)


def with_render_positions(data: PreparedRenderData, positions):
    if len(positions) != len(data.positions):
        raise ValueError("animated position count differs from loaded render mesh")
    return replace(data, positions=positions)
