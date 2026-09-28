"""Generic JFG-style skeleton display state for DK64's 25 joints."""

from dataclasses import dataclass
from enum import StrEnum


class ViewMode(StrEnum):
    MESH = "Mesh"
    SKELETON = "Skeleton"
    MESH_SKELETON = "Mesh + Skeleton"

    @property
    def shows_mesh(self):
        return self in (ViewMode.MESH, ViewMode.MESH_SKELETON)

    @property
    def shows_skeleton(self):
        return self in (ViewMode.SKELETON, ViewMode.MESH_SKELETON)


DEFAULT_VIEW_MODE = ViewMode.MESH_SKELETON


@dataclass(frozen=True)
class PreparedSkeletonDebug:
    joint_ids: tuple[int, ...]
    joint_positions: tuple[tuple[float, float, float], ...]
    edge_positions: tuple[tuple[float, float, float], ...]

    @property
    def joint_count(self):
        return len(self.joint_ids)

    def offset_for_joint(self, joint_id: int):
        try:
            return self.joint_ids.index(joint_id)
        except ValueError as exc:
            raise KeyError(f"unknown DK joint {joint_id}") from exc
