"""JFG Forge MIT-licensed orbit camera reused for DK64 Forge.

Original: jfg_forge.camera (Copyright 2026 Marvelmaster, MIT).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


Vec3 = tuple[float, float, float]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


def _add(a: Vec3, b: Vec3) -> Vec3:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(value: Vec3, factor: float) -> Vec3:
    return value[0] * factor, value[1] * factor, value[2] * factor


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(value: Vec3) -> Vec3:
    length = math.sqrt(_dot(value, value))
    if length == 0.0:
        raise ValueError("Cannot normalize a zero-length camera vector.")
    return _scale(value, 1.0 / length)


@dataclass
class OrbitCamera:
    target: Vec3 = (0.0, 0.0, 0.0)
    # The Kongs face +Z, so yaw 0 looks at their front; 20 degrees turns slightly to the side.
    yaw: float = math.radians(20.0)
    pitch: float = math.radians(18.0)
    distance: float = 10.0
    scene_radius: float = 1.0

    @classmethod
    def from_points(cls, points: Iterable[Vec3]) -> "OrbitCamera":
        values = tuple(points)
        if not values:
            raise ValueError("Cannot frame an empty point set.")
        minimum = tuple(min(point[axis] for point in values) for axis in range(3))
        maximum = tuple(max(point[axis] for point in values) for axis in range(3))
        target = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
        radius = max(math.dist(target, point) for point in values)
        radius = max(radius, 1.0)
        return cls(target=target, distance=radius * 2.6, scene_radius=radius)

    @property
    def eye(self) -> Vec3:
        horizontal = math.cos(self.pitch) * self.distance
        return (
            self.target[0] + math.sin(self.yaw) * horizontal,
            self.target[1] + math.sin(self.pitch) * self.distance,
            self.target[2] + math.cos(self.yaw) * horizontal,
        )

    def orbit(self, delta_x: float, delta_y: float) -> None:
        self.yaw = (self.yaw - delta_x * 0.01) % (2.0 * math.pi)
        limit = math.radians(89.0)
        self.pitch = max(-limit, min(limit, self.pitch + delta_y * 0.01))

    def pan(self, delta_x: float, delta_y: float) -> None:
        forward = _normalize(_sub(self.target, self.eye))
        right = _normalize(_cross(forward, (0.0, 1.0, 0.0)))
        up = _normalize(_cross(right, forward))
        scale = self.distance * 0.0015
        self.target = _add(self.target, _add(_scale(right, -delta_x * scale), _scale(up, delta_y * scale)))

    def zoom(self, wheel_steps: float) -> None:
        factor = math.exp(-wheel_steps * 0.14)
        minimum = max(self.scene_radius * 0.03, 0.01)
        maximum = max(self.scene_radius * 100.0, minimum)
        self.distance = max(minimum, min(maximum, self.distance * factor))

    def view_matrix(self) -> Matrix4:
        eye = self.eye
        forward = _normalize(_sub(self.target, eye))
        right = _normalize(_cross(forward, (0.0, 1.0, 0.0)))
        up = _cross(right, forward)
        return (
            (right[0], right[1], right[2], -_dot(right, eye)),
            (up[0], up[1], up[2], -_dot(up, eye)),
            (-forward[0], -forward[1], -forward[2], _dot(forward, eye)),
            (0.0, 0.0, 0.0, 1.0),
        )

    def projection_matrix(self, aspect: float) -> Matrix4:
        if not math.isfinite(aspect) or aspect <= 0.0:
            raise ValueError("Camera aspect ratio must be positive and finite.")
        near = max(self.scene_radius * 0.005, 0.01)
        far = max(self.distance + self.scene_radius * 8.0, near + 1.0)
        f = 1.0 / math.tan(math.radians(45.0) * 0.5)
        return (
            (f / aspect, 0.0, 0.0, 0.0),
            (0.0, f, 0.0, 0.0),
            (0.0, 0.0, (far + near) / (near - far), (2.0 * far * near) / (near - far)),
            (0.0, 0.0, -1.0, 0.0),
        )
