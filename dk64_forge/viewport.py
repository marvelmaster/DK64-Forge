"""JFG Forge MIT-licensed OpenGL viewport adapted for DK64 render data.

Original: jfg_forge.viewport (Copyright 2026 Marvelmaster, MIT).
DK64 geometry, texture, skeleton, and pose data enter through adapters only.
"""

from __future__ import annotations

from dataclasses import replace
import math
import time
import weakref
import ctypes

import numpy as np
import OpenGL

# PyOpenGL checks glGetError after every call by default; with ~20 calls per batch and
# thousands of batches on large maps that alone cost most of a frame. Shader compile and
# link status are still checked explicitly.
OpenGL.ERROR_CHECKING = False
from OpenGL.GL import (  # noqa: E402
    glGenerateMipmap,
    GL_LINEAR_MIPMAP_LINEAR,
    GL_ARRAY_BUFFER,
    GL_BACK,
    GL_BLEND,
    GL_CLAMP_TO_EDGE,
    GL_COLOR_BUFFER_BIT,
    GL_COMPILE_STATUS,
    GL_CULL_FACE,
    GL_DEPTH_BUFFER_BIT,
    GL_DEPTH_TEST,
    GL_DYNAMIC_DRAW,
    GL_FALSE,
    GL_FLOAT,
    GL_FRAGMENT_SHADER,
    GL_LINEAR,
    GL_LEQUAL,
    GL_LESS,
    GL_LINES,
    GL_LINK_STATUS,
    GL_MIRRORED_REPEAT,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_POINTS,
    GL_REPEAT,
    GL_RGBA,
    GL_SRC_ALPHA,
    GL_TEXTURE0,
    GL_TEXTURE1,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S,
    GL_TEXTURE_WRAP_T,
    GL_TRIANGLES,
    GL_TRUE,
    GL_UNSIGNED_BYTE,
    GL_VERTEX_SHADER,
    glActiveTexture,
    glAttachShader,
    glBindBuffer,
    glBindTexture,
    glBindVertexArray,
    glBlendFunc,
    glBufferData,
    glBufferSubData,
    glClear,
    glClearColor,
    glCompileShader,
    glCreateProgram,
    glCreateShader,
    glCullFace,
    glDepthMask,
    glDepthFunc,
    glDeleteBuffers,
    glDeleteProgram,
    glDeleteShader,
    glDeleteTextures,
    glDeleteVertexArrays,
    glDisable,
    glDrawArrays,
    glEnable,
    glEnableVertexAttribArray,
    glGenBuffers,
    glGenTextures,
    glGenVertexArrays,
    glGetProgramInfoLog,
    glGetProgramiv,
    glGetShaderInfoLog,
    glGetShaderiv,
    glGetUniformLocation,
    glLinkProgram,
    glPixelStorei,
    glShaderSource,
    glTexImage2D,
    glTexParameteri,
    glUniform1i,
    glUniform4f,
    glUniform4i,
    glUniform3f,
    glUniform2f,
    glGenerateMipmap,
    GL_TEXTURE_MAX_LEVEL,
    glUniform1f,
    glUniformMatrix4fv,
    glUseProgram,
    glVertexAttribPointer,
    glViewport,
    GL_UNPACK_ALIGNMENT,
)
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QLabel

from dk64_forge.camera import OrbitCamera
from dk64_forge.debug_view import DEFAULT_VIEW_MODE, PreparedSkeletonDebug, ViewMode
from dk64_forge.render_data import (
    PreparedBatch, PreparedRenderData, depth_comparison_for_batch,
    ordered_draw_batches, with_render_positions,
)


_VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec3 in_position;
layout(location = 1) in vec2 in_uv;
layout(location = 2) in vec4 in_color;
layout(location = 3) in vec2 in_uv1;
uniform mat4 mvp;
uniform bool is_billboard;
uniform vec3 billboard_center;
uniform vec2 billboard_right;
out vec2 uv;
out vec2 uv1;
out vec4 shade;
void main() {
    vec3 position = in_position;
    if (is_billboard) {
        vec3 relative = position - billboard_center;
        position.xz = billboard_center.xz + billboard_right * relative.x
                    + vec2(-billboard_right.y, billboard_right.x) * relative.z;
    }
    gl_Position = mvp * vec4(position, 1.0);
    uv = in_uv;
    uv1 = in_uv1;
    shade = in_color;
}
"""

_FRAGMENT_SHADER = """#version 330 core
in vec2 uv;
in vec2 uv1;
in vec4 shade;
uniform sampler2D color_texture;
uniform sampler2D second_texture;
uniform bool use_second_texture;
uniform bool use_texture;
uniform int alpha_mode;
uniform vec4 fallback_color;
uniform bool use_combiner;
uniform int cycle_type;
uniform ivec4 rgb0, rgb1, alpha0, alpha1;
uniform vec4 primitive_color, environment_color;
uniform float prim_lod;
uniform vec3 key_center, key_scale;
uniform vec2 convert_k;
uniform bool texture_lod;
float lod_fraction;
out vec4 fragment_color;
vec4 combined, tex0, tex1;
vec3 rgb(int n, int slot) {
    if (n == 0) return combined.rgb;
    if (n == 1) return tex0.rgb;
    if (n == 2) return tex1.rgb;
    if (n == 3) return primitive_color.rgb;
    if (n == 4) return shade.rgb;
    if (n == 5) return environment_color.rgb;
    if ((slot == 0 || slot == 3) && n == 6) return vec3(1.0);
    if (slot == 1 && n == 6) return key_center;
    if (slot == 1 && n == 7) return vec3(convert_k.x);
    if (slot == 2 && n == 6) return key_scale;
    if (slot == 2 && n == 13) return vec3(lod_fraction);
    if (slot == 2 && n == 15) return vec3(convert_k.y);
    if (slot == 0 && n == 7) return vec3(fract(sin(dot(gl_FragCoord.xy, vec2(12.9898,78.233))) * 43758.5453));
    if (slot == 2) {
        if (n == 7) return vec3(combined.a);
        if (n == 8) return vec3(tex0.a);
        if (n == 9) return vec3(tex1.a);
        if (n == 10) return vec3(primitive_color.a);
        if (n == 11) return vec3(shade.a);
        if (n == 12) return vec3(environment_color.a);
        if (n == 14) return vec3(prim_lod);
    }
    return vec3(0.0);
}
float alpha(int n, bool multiplier) {
    if (n == 0) return multiplier ? lod_fraction : combined.a;
    if (n == 1) return tex0.a;
    if (n == 2) return tex1.a;
    if (n == 3) return primitive_color.a;
    if (n == 4) return shade.a;
    if (n == 5) return environment_color.a;
    if (n == 6) return multiplier ? prim_lod : 1.0;
    return 0.0;
}
vec4 evaluate(ivec4 r, ivec4 a) {
    return vec4((rgb(r.x, 0) - rgb(r.y, 1)) * rgb(r.z, 2) + rgb(r.w, 3),
                (alpha(a.x, false) - alpha(a.y, false)) * alpha(a.z, true) + alpha(a.w, false));
}
void main() {
    tex0 = use_texture ? texture(color_texture, uv) : fallback_color;
    vec2 size = vec2(textureSize(color_texture, 0));
    float rho = max(length(dFdx(uv) * size), length(dFdy(uv) * size));
    float lod = texture_lod ? max(0.0, log2(max(rho, 0.00001))) : 0.0;
    lod_fraction = fract(lod);
    if (texture_lod && use_texture) tex0 = textureLod(color_texture, uv, floor(lod));
    tex1 = use_second_texture ? texture(second_texture, uv1) : (use_texture ? textureLod(color_texture, uv, floor(lod) + 1.0) : fallback_color);
    combined = vec4(0.0);
    if (!use_combiner) fragment_color = tex0 * shade;
    else if (cycle_type == 2) fragment_color = tex0;
    else if (cycle_type == 3) fragment_color = primitive_color;
    else {
        if (cycle_type == 1) combined = evaluate(rgb0, alpha0);
        fragment_color = clamp(evaluate(rgb1, alpha1), 0.0, 1.0);
    }
    if (alpha_mode == 1 && fragment_color.a < 0.5) discard;
    if (alpha_mode != 2) fragment_color.a = 1.0;
}
"""

_DEBUG_VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec3 in_position;
uniform mat4 mvp;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

_DEBUG_FRAGMENT_SHADER = """#version 330 core
uniform vec4 debug_color;
out vec4 fragment_color;
void main() {
    fragment_color = debug_color;
}
"""


def _compile_shader(source: str, shader_type: int) -> int:
    shader = glCreateShader(shader_type)
    glShaderSource(shader, source)
    glCompileShader(shader)
    if not glGetShaderiv(shader, GL_COMPILE_STATUS):
        message = glGetShaderInfoLog(shader).decode("utf-8", errors="replace")
        glDeleteShader(shader)
        raise RuntimeError(f"OpenGL shader compilation failed: {message}")
    return shader


def _create_program(
    vertex_source: str = _VERTEX_SHADER,
    fragment_source: str = _FRAGMENT_SHADER,
) -> int:
    vertex = _compile_shader(vertex_source, GL_VERTEX_SHADER)
    fragment = _compile_shader(fragment_source, GL_FRAGMENT_SHADER)
    program = glCreateProgram()
    try:
        glAttachShader(program, vertex)
        glAttachShader(program, fragment)
        glLinkProgram(program)
        if not glGetProgramiv(program, GL_LINK_STATUS):
            message = glGetProgramInfoLog(program).decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenGL program link failed: {message}")
        return program
    except Exception:
        glDeleteProgram(program)
        raise
    finally:
        glDeleteShader(vertex)
        glDeleteShader(fragment)


def texture_upload_rows(texture) -> np.ndarray:
    """RGBA rows for glTexImage2D. glTF UVs have their origin at the image's
    top-left and image rows are top-first, so the rows go up unflipped."""
    return np.ascontiguousarray(
        np.frombuffer(texture.rgba, dtype=np.uint8).reshape(texture.height, texture.width, 4))


VERTEX_COLUMNS = 11  # position xyz, UV0, RGBA shade, UV1


def _vertex_array(data: PreparedRenderData) -> np.ndarray:
    """Interleaved float32 vertex rows: position, uv and per-corner shade (white if absent)."""
    rows = np.ones((len(data.positions), VERTEX_COLUMNS), dtype=np.float32)
    rows[:, :3] = np.asarray(data.positions, dtype=np.float32).reshape(-1, 3)
    rows[:, 3:5] = np.asarray(data.uvs, dtype=np.float32).reshape(-1, 2)
    if data.colors is not None:
        rows[:, 5:9] = np.asarray(data.colors, dtype=np.float32).reshape(-1, 4)
    rows[:, 9:11] = np.asarray(data.uvs1 or data.uvs, dtype=np.float32).reshape(-1, 2)
    return rows


def _bind_vertex_attributes(itemsize: int) -> None:
    stride = VERTEX_COLUMNS * itemsize
    glEnableVertexAttribArray(0)
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(0))
    glEnableVertexAttribArray(1)
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(3 * itemsize))
    glEnableVertexAttribArray(2)
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(5 * itemsize))
    glEnableVertexAttribArray(3)
    glVertexAttribPointer(3, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(9 * itemsize))


def _wrap_constant(name: str) -> int:
    return {"CLAMP": GL_CLAMP_TO_EDGE, "MIRROR": GL_MIRRORED_REPEAT}.get(name, GL_REPEAT)


# Existing debug palette, independent from mesh material fallbacks.
DEBUG_BACKGROUND_RGBA = (0.075, 0.085, 0.105, 1.0)
DEBUG_SKELETON_RGBA = (0.15, 0.9, 1.0, 1.0)
DEBUG_JOINT_RGBA = (1.0, 0.85, 0.1, 1.0)
DEBUG_SELECTED_JOINT_RGBA = (1.0, 0.15, 0.1, 1.0)


GRID_RGBA = (0.42, 0.45, 0.5, 1.0)
GRID_AXIS_X_RGBA = (0.75, 0.3, 0.3, 1.0)
GRID_AXIS_Z_RGBA = (0.3, 0.45, 0.8, 1.0)
GRID_LINES_PER_SIDE = 10


def grid_lines(radius: float, height: float = 0.0):
    """Line-segment endpoints of a square ground grid around the origin.

    The spacing is a power of ten times 1, 2 or 5 so that about ten cells cover the
    model's radius; returns (minor lines, x axis, z axis) as float32 arrays of points."""
    raw = max(radius, 1.0) / GRID_LINES_PER_SIDE
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    extent = step * GRID_LINES_PER_SIDE
    minor = []
    for i in range(-GRID_LINES_PER_SIDE, GRID_LINES_PER_SIDE + 1):
        if i == 0:
            continue
        v = i * step
        minor += [(-extent, height, v), (extent, height, v), (v, height, -extent), (v, height, extent)]
    x_axis = [(-extent, height, 0.0), (extent, height, 0.0)]
    z_axis = [(0.0, height, -extent), (0.0, height, extent)]
    return tuple(np.asarray(points, dtype=np.float32) for points in (minor, x_axis, z_axis))


class ModelViewport(QOpenGLWidget):
    """Render Forge topology with CPU-evaluated positions updated in-place."""

    initialization_failed = Signal(str)

    # Display options shared by every viewport (View menu); new viewports pick them up.
    show_fps = False
    trilinear = False
    _instances: "weakref.WeakSet[ModelViewport]" = weakref.WeakSet()

    @classmethod
    def set_show_fps_all(cls, enabled: bool) -> None:
        cls.show_fps = bool(enabled)
        for viewport in list(cls._instances):
            viewport._fps_label.setVisible(cls.show_fps)
            viewport.update()

    @classmethod
    def set_trilinear_all(cls, enabled: bool) -> None:
        cls.trilinear = bool(enabled)
        for viewport in list(cls._instances):
            viewport._apply_texture_filter()

    def __init__(
        self,
        data: PreparedRenderData,
        skeleton: PreparedSkeletonDebug,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._vertex_data = _vertex_array(data)
        self._skeleton = skeleton
        self._skeleton_vertex_data = np.asarray(
            skeleton.edge_positions + skeleton.joint_positions,
            dtype=np.float32,
        )
        self._view_mode = DEFAULT_VIEW_MODE
        self._selected_joint_id = 0
        self._camera = OrbitCamera.from_points(data.positions)
        self._last_pointer: QPoint | None = None
        self._program = 0
        self._debug_program = 0
        self._vao = 0
        self._vbo = 0
        self._skeleton_vao = 0
        self._skeleton_vbo = 0
        self._textures: dict[int, int] = {}
        self._attachment_data: PreparedRenderData | None = None
        self._attachment_vertex_data: np.ndarray | None = None
        self._attachment_vao = 0
        self._attachment_vbo = 0
        self._attachment_textures: dict[int, int] = {}
        self._failed = False
        self._grid_visible = False
        self._grid_height = 0.0
        self._grid_vao = 0
        self._grid_vbo = 0
        self._grid_counts = (0, 0, 0)
        self._grid_dirty = True
        self._frame_times: list[float] = []
        self._paint_ms = 0.0
        self._last_fps_text = 0.0
        self._fps_label = QLabel(self)
        self._fps_label.setStyleSheet(
            "QLabel { color: #e8f0ff; background: rgba(0, 0, 0, 140); padding: 2px 6px;"
            " font-family: Consolas, monospace; font-size: 11px; }")
        self._fps_label.move(6, 6)
        self._fps_label.setText("-- fps")
        self._fps_label.adjustSize()
        self._fps_label.setVisible(ModelViewport.show_fps)
        ModelViewport._instances.add(self)
        self._uniform_cache: dict = {}
        self._last_material = None
        self._bound_vao = None
        # Optional per-view UV source for RSP-generated (G_TEXTURE_GEN) coordinates.
        self._uv_provider = None
        self._uv_key = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_grid_visible(self, visible: bool, height: float = 0.0) -> None:
        """Draw a ground grid at y = height (sized from the loaded model)."""
        self._grid_visible = bool(visible)
        if height != self._grid_height:
            self._grid_height = height
            self._grid_dirty = True
        self.update()

    @property
    def grid_visible(self) -> bool:
        return self._grid_visible

    def _upload_grid(self) -> None:
        radius = max(abs(v) for v in (*self._data.bounds_minimum, *self._data.bounds_maximum))
        minor, x_axis, z_axis = grid_lines(radius, self._grid_height)
        points = np.concatenate([minor, x_axis, z_axis])
        if not self._grid_vao:
            self._grid_vao = int(glGenVertexArrays(1))
            self._grid_vbo = int(glGenBuffers(1))
        glBindVertexArray(self._grid_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._grid_vbo)
        glBufferData(GL_ARRAY_BUFFER, points.nbytes, points, GL_DYNAMIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * points.itemsize, ctypes.c_void_p(0))
        glBindVertexArray(0)
        self._grid_counts = (len(minor), len(x_axis), len(z_axis))
        self._grid_dirty = False

    def _paint_grid(self, mvp: np.ndarray) -> None:
        if self._grid_dirty:
            self._upload_grid()
        glEnable(GL_DEPTH_TEST)
        glDepthFunc(GL_LEQUAL)
        glDisable(GL_CULL_FACE)
        glUseProgram(self._debug_program)
        glUniformMatrix4fv(self._uniform(self._debug_program, "mvp"), 1, GL_TRUE, mvp)
        color = self._uniform(self._debug_program, "debug_color")
        glBindVertexArray(self._grid_vao)
        minor, x_count, z_count = self._grid_counts
        glUniform4f(color, *GRID_RGBA)
        glDrawArrays(GL_LINES, 0, minor)
        glUniform4f(color, *GRID_AXIS_X_RGBA)
        glDrawArrays(GL_LINES, minor, x_count)
        glUniform4f(color, *GRID_AXIS_Z_RGBA)
        glDrawArrays(GL_LINES, minor + x_count, z_count)
        glBindVertexArray(0)
        glUseProgram(0)
        glDepthFunc(GL_LESS)

    def _apply_texture_filter(self) -> None:
        """Trilinear (mipmapped) or plain bilinear minification for every texture."""
        if not self._program or self._failed:
            return
        self.makeCurrent()
        minify = GL_LINEAR_MIPMAP_LINEAR if ModelViewport.trilinear else GL_LINEAR
        for handle in list(self._textures.values()) + list(self._attachment_textures.values()):
            glBindTexture(GL_TEXTURE_2D, handle)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, minify)
        glBindTexture(GL_TEXTURE_2D, 0)
        self.doneCurrent()
        self.update()

    def _record_frame(self, started: float) -> None:
        now = time.perf_counter()
        self._paint_ms = 0.8 * self._paint_ms + 0.2 * (now - started) * 1000.0 if self._paint_ms else (now - started) * 1000.0
        self._frame_times.append(now)
        self._frame_times = [t for t in self._frame_times if now - t <= 1.0]
        if ModelViewport.show_fps and now - self._last_fps_text >= 0.25:
            self._last_fps_text = now
            frames = len(self._frame_times)
            self._fps_label.setText(f"{frames} fps  |  {self._paint_ms:.1f} ms/frame")
            self._fps_label.adjustSize()

    def frames_per_second(self) -> int:
        """Frames painted during the last second (what the FPS overlay shows)."""
        now = time.perf_counter()
        return sum(1 for t in self._frame_times if now - t <= 1.0)

    def _uniform(self, program: int, name: str) -> int:
        key = (program, name)
        location = self._uniform_cache.get(key)
        if location is None:
            location = self._uniform_cache[key] = glGetUniformLocation(program, name)
        return location

    def initializeGL(self) -> None:
        try:
            self._program = _create_program()
            self._debug_program = _create_program(_DEBUG_VERTEX_SHADER, _DEBUG_FRAGMENT_SHADER)
            self._upload_mesh()
            self._upload_skeleton()
            self._upload_textures()
            if self._attachment_data is not None:
                self._upload_attachment()
            glEnable(GL_DEPTH_TEST)
            glDisable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glCullFace(GL_BACK)
            glClearColor(*DEBUG_BACKGROUND_RGBA)
            context = self.context()
            if context is not None:
                context.aboutToBeDestroyed.connect(self._destroy_gl_resources)
        except Exception as error:
            self._failed = True
            self.initialization_failed.emit(str(error))

    def _upload_mesh(self) -> None:
        self._vao = int(glGenVertexArrays(1))
        self._vbo = int(glGenBuffers(1))
        glBindVertexArray(self._vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        glBufferData(GL_ARRAY_BUFFER, self._vertex_data.nbytes, self._vertex_data, GL_DYNAMIC_DRAW)
        _bind_vertex_attributes(self._vertex_data.itemsize)
        glBindVertexArray(0)

    def _upload_skeleton(self) -> None:
        self._skeleton_vao = int(glGenVertexArrays(1))
        self._skeleton_vbo = int(glGenBuffers(1))
        glBindVertexArray(self._skeleton_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._skeleton_vbo)
        glBufferData(
            GL_ARRAY_BUFFER,
            self._skeleton_vertex_data.nbytes,
            self._skeleton_vertex_data,
            GL_DYNAMIC_DRAW,
        )
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * self._skeleton_vertex_data.itemsize, ctypes.c_void_p(0))
        glBindVertexArray(0)

    def set_uv_provider(self, provider) -> None:
        """provider(eye, target) -> (n, 2) UVs or None; re-evaluated per view/pose."""
        self._uv_provider = provider
        self._uv_key = None
        self.update()

    def _refresh_generated_uvs(self) -> None:
        if self._uv_provider is None:
            return
        key = (self._camera.eye, self._camera.target, id(self._data))
        if key == self._uv_key:
            return
        uvs = self._uv_provider(self._camera.eye, self._camera.target)
        self._uv_key = key
        if uvs is None or len(uvs) != len(self._vertex_data):
            return
        self._vertex_data[:, 3:5] = np.asarray(uvs, dtype=np.float32)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        glBufferSubData(GL_ARRAY_BUFFER, 0, self._vertex_data.nbytes, self._vertex_data)
        glBindBuffer(GL_ARRAY_BUFFER, 0)

    def set_view_mode(self, mode: ViewMode) -> None:
        self._view_mode = ViewMode(mode)
        self.update()

    def set_selected_joint(self, joint_id: int) -> None:
        self._skeleton.offset_for_joint(joint_id)
        self._selected_joint_id = joint_id
        self.update()

    def set_scene_data(
        self,
        positions: tuple[tuple[float, float, float], ...],
        skeleton: PreparedSkeletonDebug,
    ) -> None:
        """Update mesh and skeleton from the same evaluated scene snapshot."""
        if len(positions) != len(self._vertex_data):
            raise ValueError("Animated position count differs from the loaded render mesh.")
        if skeleton.joint_ids != self._skeleton.joint_ids or len(skeleton.edge_positions) != len(self._skeleton.edge_positions):
            raise ValueError("Animated skeleton topology differs from the loaded skeleton.")
        self._vertex_data[:, :3] = np.asarray(positions, dtype=np.float32)
        self._data = with_render_positions(self._data, positions)
        self._skeleton_vertex_data[:] = np.asarray(
            skeleton.edge_positions + skeleton.joint_positions,
            dtype=np.float32,
        )
        self._skeleton = skeleton
        if self._vbo and self._skeleton_vbo and not self._failed:
            self.makeCurrent()
            glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
            glBufferSubData(GL_ARRAY_BUFFER, 0, self._vertex_data.nbytes, self._vertex_data)
            glBindBuffer(GL_ARRAY_BUFFER, self._skeleton_vbo)
            glBufferSubData(
                GL_ARRAY_BUFFER,
                0,
                self._skeleton_vertex_data.nbytes,
                self._skeleton_vertex_data,
            )
            glBindBuffer(GL_ARRAY_BUFFER, 0)
            self.doneCurrent()
        self.update()

    def set_model_data(
        self,
        data: PreparedRenderData,
        skeleton: PreparedSkeletonDebug,
    ) -> None:
        """Replace the active character model while preserving the viewport."""
        if self._program and not self._failed:
            self.makeCurrent()
            self._destroy_model_resources()
        self._data = data
        self._vertex_data = _vertex_array(data)
        self._skeleton = skeleton
        self._skeleton_vertex_data = np.asarray(
            skeleton.edge_positions + skeleton.joint_positions,
            dtype=np.float32,
        )
        self._selected_joint_id = skeleton.joint_ids[0]
        self._camera = OrbitCamera.from_points(data.positions)
        self._grid_dirty = True
        if self._program and not self._failed:
            self._upload_mesh()
            self._upload_skeleton()
            self._upload_textures()
            self.doneCurrent()
        self.update()

    def reset_view(self) -> None:
        """Reframe the loaded model using the original JFG orbit camera."""
        self._camera = OrbitCamera.from_points(self._data.positions)
        self.update()

    def set_vertex_colors(self, colors) -> None:
        if colors is None:
            return
        if len(colors) != len(self._vertex_data):
            raise ValueError("Shade count differs from mesh")
        self._vertex_data[:, 5:9] = np.asarray(colors, dtype=np.float32)
        self._data = replace(self._data, colors=tuple(colors))
        if self._program and not self._failed:
            self.makeCurrent()
            glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
            glBufferSubData(GL_ARRAY_BUFFER, 0, self._vertex_data.nbytes, self._vertex_data)
            glBindBuffer(GL_ARRAY_BUFFER, 0)
            self.doneCurrent()
        self.update()

    def set_textures(self, textures) -> None:
        self._data = replace(self._data, textures=tuple(textures))
        if self._program and not self._failed:
            self.makeCurrent()
            if self._textures:
                glDeleteTextures(list(self._textures.values()))
            self._upload_textures()
            self.doneCurrent()
        self.update()

    def set_attachment_data(self, data: PreparedRenderData | None) -> None:
        """Replace an optional generic scene mesh, unused by DK64's first UI."""
        if self._program and not self._failed:
            self.makeCurrent()
            self._destroy_attachment_resources()
        self._attachment_data = data
        if data is None:
            self._attachment_vertex_data = None
        else:
            self._attachment_vertex_data = _vertex_array(data)
            if self._program and not self._failed:
                self._upload_attachment()
        if self._program and not self._failed:
            self.doneCurrent()
        self.update()

    def set_attachment_positions(
        self,
        positions: tuple[tuple[float, float, float], ...],
    ) -> None:
        if self._attachment_data is None or self._attachment_vertex_data is None:
            raise ValueError("No attachment mesh is loaded.")
        if len(positions) != len(self._attachment_vertex_data):
            raise ValueError("Animated attachment position count differs from its render mesh.")
        self._attachment_vertex_data[:, :3] = np.asarray(positions, dtype=np.float32)
        self._attachment_data = with_render_positions(self._attachment_data, positions)
        if self._attachment_vbo and not self._failed:
            self.makeCurrent()
            glBindBuffer(GL_ARRAY_BUFFER, self._attachment_vbo)
            glBufferSubData(
                GL_ARRAY_BUFFER,
                0,
                self._attachment_vertex_data.nbytes,
                self._attachment_vertex_data,
            )
            glBindBuffer(GL_ARRAY_BUFFER, 0)
            self.doneCurrent()
        self.update()

    def _upload_attachment(self) -> None:
        if self._attachment_data is None or self._attachment_vertex_data is None:
            return
        self._attachment_vao = int(glGenVertexArrays(1))
        self._attachment_vbo = int(glGenBuffers(1))
        glBindVertexArray(self._attachment_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._attachment_vbo)
        glBufferData(
            GL_ARRAY_BUFFER,
            self._attachment_vertex_data.nbytes,
            self._attachment_vertex_data,
            GL_DYNAMIC_DRAW,
        )
        _bind_vertex_attributes(self._attachment_vertex_data.itemsize)
        glBindVertexArray(0)
        self._attachment_textures = self._create_textures(self._attachment_data)

    def _upload_textures(self) -> None:
        self._textures = self._create_textures(self._data)

    def _create_textures(self, data: PreparedRenderData) -> dict[int, int]:
        handles: dict[int, int] = {}
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        for texture in data.textures:
            handle = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D, handle)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                            GL_LINEAR_MIPMAP_LINEAR if ModelViewport.trilinear else GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, _wrap_constant(texture.wrap_s))
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, _wrap_constant(texture.wrap_t))
            pixels = texture_upload_rows(texture)
            glTexImage2D(
                GL_TEXTURE_2D,
                0,
                GL_RGBA,
                texture.width,
                texture.height,
                0,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                pixels,
            )
            glGenerateMipmap(GL_TEXTURE_2D)
            for level, (width, height, rgba) in enumerate(texture.mip_levels, 1):
                pixels = np.asarray(bytearray(rgba), dtype=np.uint8).reshape(height, width, 4).copy()
                glTexImage2D(GL_TEXTURE_2D, level, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels)
            handles[texture.texture_index] = handle
        glBindTexture(GL_TEXTURE_2D, 0)
        return handles

    def resizeGL(self, width: int, height: int) -> None:
        glViewport(0, 0, max(width, 1), max(height, 1))

    def paintGL(self) -> None:
        started = time.perf_counter()
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self._failed or not self._program:
            return
        try:
            self._paint_scene()
        finally:
            self._record_frame(started)

    def _paint_scene(self) -> None:
        aspect = max(self.width(), 1) / max(self.height(), 1)
        view = np.asarray(self._camera.view_matrix(), dtype=np.float32)
        projection = np.asarray(self._camera.projection_matrix(aspect), dtype=np.float32)
        mvp = projection @ view
        if self._grid_visible:
            self._paint_grid(mvp)
        if self._view_mode.shows_mesh:
            self._refresh_generated_uvs()
            sources = [(self._data, self._vao, self._textures)]
            if self._attachment_data is not None and self._attachment_vao:
                sources.append((self._attachment_data, self._attachment_vao, self._attachment_textures))
            self._begin_mesh_pass(mvp)
            for source_index, batch in ordered_draw_batches(
                tuple(source[0] for source in sources),
                self._camera.view_matrix(),
            ):
                data, vao, textures = sources[source_index]
                self._paint_mesh(mvp, data, vao, textures, batch)
            glBindVertexArray(0)
            glUseProgram(0)
            glDepthMask(GL_TRUE)
            glDepthFunc(GL_LESS)
            glDisable(GL_BLEND)
        if self._view_mode.shows_skeleton:
            self._paint_skeleton(mvp, overlay=self._view_mode is ViewMode.MESH_SKELETON)

    def _begin_mesh_pass(self, mvp: np.ndarray) -> None:
        """Frame-wide program state, set once instead of per batch."""
        program = self._program
        glUseProgram(program)
        glUniformMatrix4fv(self._uniform(program, "mvp"), 1, GL_TRUE, mvp)
        right = np.asarray(self._camera.view_matrix())[0, (0, 2)]
        right = right / (np.linalg.norm(right) or 1.)
        glUniform2f(self._uniform(program, "billboard_right"), *right)
        glUniform1i(self._uniform(program, "color_texture"), 0)
        glUniform1i(self._uniform(program, "second_texture"), 1)
        self._last_material = None
        self._bound_vao = None

    def _paint_mesh(
        self,
        mvp: np.ndarray,
        data: PreparedRenderData,
        vao: int,
        textures: dict[int, int],
        batch: PreparedBatch,
    ) -> None:
        program = self._program
        u = self._uniform
        if self._bound_vao != vao:
            glBindVertexArray(vao)
            self._bound_vao = vao
        if batch.double_sided:
            glDisable(GL_CULL_FACE)
        else:
            glEnable(GL_CULL_FACE)
        if batch.depth_compare:
            glEnable(GL_DEPTH_TEST)
            glDepthFunc(GL_LEQUAL if depth_comparison_for_batch(batch) == "LEQUAL" else GL_LESS)
        else:
            glDisable(GL_DEPTH_TEST)
        glDepthMask(GL_TRUE if batch.depth_write else GL_FALSE)
        if batch.alpha_mode == "BLEND":
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        texture_handle = None if batch.texture_index is None else textures.get(batch.texture_index)
        second_handle = textures.get(batch.texture1_index) if batch.texture1_index is not None else None
        material = batch.material
        # Only re-send uniforms when the batch's material state differs from the last one.
        key = (batch.alpha_mode, texture_handle is not None, batch.fallback_rgba, material,
               second_handle is not None, batch.billboard_center)
        if key != self._last_material:
            self._last_material = key
            glUniform1i(u(program, "is_billboard"), int(batch.billboard_center is not None))
            glUniform3f(u(program, "billboard_center"), *(batch.billboard_center or (0., 0., 0.)))
            glUniform1i(u(program, "alpha_mode"), {"OPAQUE": 0, "MASK": 1, "BLEND": 2}[batch.alpha_mode])
            glUniform1i(u(program, "use_texture"), int(texture_handle is not None))
            glUniform4f(u(program, "fallback_color"), *batch.fallback_rgba)
            glUniform1i(u(program, "use_combiner"), int(material.mux is not None))
            glUniform1i(u(program, "cycle_type"), material.cycle)
            glUniform4f(u(program, "primitive_color"), *material.primitive)
            glUniform4f(u(program, "environment_color"), *material.environment)
            glUniform1f(u(program, "prim_lod"), material.prim_lod)
            glUniform3f(u(program, "key_center"), *material.key_center)
            glUniform3f(u(program, "key_scale"), *material.key_scale)
            glUniform2f(u(program, "convert_k"), *material.convert_k)
            glUniform1i(u(program, "texture_lod"), int(material.texture_lod))
            if material.mux is not None:
                for name, values in zip(("rgb0", "alpha0", "rgb1", "alpha1"),
                                        (material.mux[0:4], material.mux[4:8], material.mux[8:12], material.mux[12:16])):
                    glUniform4i(u(program, name), *values)
            glUniform1i(u(program, "use_second_texture"), int(second_handle is not None))
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, second_handle or 0)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, 0 if texture_handle is None else texture_handle)
        glDrawArrays(GL_TRIANGLES, batch.first_vertex, batch.vertex_count)

    def _paint_skeleton(self, mvp: np.ndarray, *, overlay: bool) -> None:
        glDisable(GL_CULL_FACE)
        if overlay:
            # Debug overlay intentionally remains visible through the mesh.
            glDisable(GL_DEPTH_TEST)
        else:
            glEnable(GL_DEPTH_TEST)
        glUseProgram(self._debug_program)
        glUniformMatrix4fv(self._uniform(self._debug_program, "mvp"), 1, GL_TRUE, mvp)
        color_location = self._uniform(self._debug_program, "debug_color")
        glBindVertexArray(self._skeleton_vao)
        edge_vertex_count = len(self._skeleton.edge_positions)
        glUniform4f(color_location, *DEBUG_SKELETON_RGBA)
        glDrawArrays(GL_LINES, 0, edge_vertex_count)
        glUniform4f(color_location, *DEBUG_JOINT_RGBA)
        glDrawArrays(GL_POINTS, edge_vertex_count, self._skeleton.joint_count)
        selected_offset = self._skeleton.offset_for_joint(self._selected_joint_id)
        glUniform4f(color_location, *DEBUG_SELECTED_JOINT_RGBA)
        glDrawArrays(GL_POINTS, edge_vertex_count + selected_offset, 1)
        glBindVertexArray(0)
        glUseProgram(0)
        glEnable(GL_DEPTH_TEST)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._last_pointer = event.position().toPoint()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        current = event.position().toPoint()
        if self._last_pointer is not None:
            delta = current - self._last_pointer
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._camera.orbit(delta.x(), delta.y())
                self.update()
            elif event.buttons() & Qt.MouseButton.MiddleButton:
                self._camera.pan(delta.x(), delta.y())
                self.update()
        self._last_pointer = current
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_pointer = None
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._camera.zoom(event.angleDelta().y() / 120.0)
        self.update()
        event.accept()

    def _destroy_gl_resources(self) -> None:
        self._destroy_attachment_resources()
        self._destroy_model_resources()
        if self._program:
            glDeleteProgram(self._program)
            self._program = 0
        if self._debug_program:
            glDeleteProgram(self._debug_program)
            self._debug_program = 0
        if self._grid_vbo:
            glDeleteBuffers(1, [self._grid_vbo])
            glDeleteVertexArrays(1, [self._grid_vao])
            self._grid_vbo = self._grid_vao = 0
            self._grid_dirty = True

    def _destroy_model_resources(self) -> None:
        if self._textures:
            glDeleteTextures(list(self._textures.values()))
            self._textures.clear()
        if self._vbo:
            glDeleteBuffers(1, [self._vbo])
            self._vbo = 0
        if self._skeleton_vbo:
            glDeleteBuffers(1, [self._skeleton_vbo])
            self._skeleton_vbo = 0
        if self._vao:
            glDeleteVertexArrays(1, [self._vao])
            self._vao = 0
        if self._skeleton_vao:
            glDeleteVertexArrays(1, [self._skeleton_vao])
            self._skeleton_vao = 0

    def _destroy_attachment_resources(self) -> None:
        if self._attachment_textures:
            glDeleteTextures(list(self._attachment_textures.values()))
            self._attachment_textures.clear()
        if self._attachment_vbo:
            glDeleteBuffers(1, [self._attachment_vbo])
            self._attachment_vbo = 0
        if self._attachment_vao:
            glDeleteVertexArrays(1, [self._attachment_vao])
            self._attachment_vao = 0
