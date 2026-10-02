"""JFG Forge viewport data contract adapted to DK64 glTF preview content.

PreparedTexture/Batch/RenderData and draw ordering follow the generic MIT
licensed JFG Forge renderer contract. No JFG asset semantics are used.
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class PreparedTexture:
    texture_index: int
    width: int
    height: int
    rgba: bytes
    wrap_s: str
    wrap_t: str


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


def depth_comparison_for_batch(batch: PreparedBatch) -> str:
    return "LEQUAL" if batch.z_mode == 3 else "LESS"


def ordered_draw_batches(scenes: tuple[PreparedRenderData, ...], view_matrix):
    """Opaque first, blended groups back to front, as in JFG Forge."""
    sortable = []
    view_z = view_matrix[2]
    for scene_index, scene in enumerate(scenes):
        for batch in scene.batches:
            if batch.alpha_mode == "BLEND":
                phase = 2
                points = scene.positions[batch.first_vertex:batch.first_vertex + batch.vertex_count]
                camera_z = sum(view_z[0]*p[0] + view_z[1]*p[1] +
                               view_z[2]*p[2] + view_z[3] for p in points) / len(points)
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
