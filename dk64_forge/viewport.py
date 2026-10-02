"""JFG Forge MIT-licensed OpenGL viewport adapted for DK64 render data.

Original: jfg_forge.viewport (Copyright 2026 Marvelmaster, MIT).
DK64 geometry, texture, skeleton, and pose data enter through adapters only.
"""

from __future__ import annotations

import ctypes

import numpy as np
from OpenGL.GL import (
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
    glUniformMatrix4fv,
    glUseProgram,
    glVertexAttribPointer,
    glViewport,
    GL_UNPACK_ALIGNMENT,
)
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

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
uniform mat4 mvp;
out vec2 uv;
out vec4 shade;
void main() {
    gl_Position = mvp * vec4(in_position, 1.0);
    uv = in_uv;
    shade = in_color;
}
"""

_FRAGMENT_SHADER = """#version 330 core
in vec2 uv;
in vec4 shade;
uniform sampler2D color_texture;
uniform bool use_texture;
uniform int alpha_mode;
uniform vec4 fallback_color;
out vec4 fragment_color;
void main() {
    // TEXEL0 * SHADE: the common DK64 combiner, approximated for every batch.
    fragment_color = (use_texture ? texture(color_texture, uv) : fallback_color) * shade;
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


VERTEX_COLUMNS = 9  # position xyz, uv, rgba shade


def _vertex_array(data: PreparedRenderData) -> np.ndarray:
    """Interleaved float32 vertex rows: position, uv and per-corner shade (white if absent)."""
    rows = np.ones((len(data.positions), VERTEX_COLUMNS), dtype=np.float32)
    rows[:, :3] = np.asarray(data.positions, dtype=np.float32).reshape(-1, 3)
    rows[:, 3:5] = np.asarray(data.uvs, dtype=np.float32).reshape(-1, 2)
    if data.colors is not None:
        rows[:, 5:9] = np.asarray(data.colors, dtype=np.float32).reshape(-1, 4)
    return rows


def _bind_vertex_attributes(itemsize: int) -> None:
    stride = VERTEX_COLUMNS * itemsize
    glEnableVertexAttribArray(0)
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(0))
    glEnableVertexAttribArray(1)
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(3 * itemsize))
    glEnableVertexAttribArray(2)
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(5 * itemsize))


def _wrap_constant(name: str) -> int:
    return {"CLAMP": GL_CLAMP_TO_EDGE, "MIRROR": GL_MIRRORED_REPEAT}.get(name, GL_REPEAT)


# Existing debug palette, independent from mesh material fallbacks.
DEBUG_BACKGROUND_RGBA = (0.075, 0.085, 0.105, 1.0)
DEBUG_SKELETON_RGBA = (0.15, 0.9, 1.0, 1.0)
DEBUG_JOINT_RGBA = (1.0, 0.85, 0.1, 1.0)
DEBUG_SELECTED_JOINT_RGBA = (1.0, 0.15, 0.1, 1.0)


class ModelViewport(QOpenGLWidget):
    """Render Forge topology with CPU-evaluated positions updated in-place."""

    initialization_failed = Signal(str)

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
        # Optional per-view UV source for RSP-generated (G_TEXTURE_GEN) coordinates.
        self._uv_provider = None
        self._uv_key = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
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
            handles[texture.texture_index] = handle
        glBindTexture(GL_TEXTURE_2D, 0)
        return handles

    def resizeGL(self, width: int, height: int) -> None:
        glViewport(0, 0, max(width, 1), max(height, 1))

    def paintGL(self) -> None:
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self._failed or not self._program:
            return
        aspect = max(self.width(), 1) / max(self.height(), 1)
        view = np.asarray(self._camera.view_matrix(), dtype=np.float32)
        projection = np.asarray(self._camera.projection_matrix(aspect), dtype=np.float32)
        mvp = projection @ view
        if self._view_mode.shows_mesh:
            self._refresh_generated_uvs()
            sources = [(self._data, self._vao, self._textures)]
            if self._attachment_data is not None and self._attachment_vao:
                sources.append((self._attachment_data, self._attachment_vao, self._attachment_textures))
            for source_index, batch in ordered_draw_batches(
                tuple(source[0] for source in sources),
                self._camera.view_matrix(),
            ):
                data, vao, textures = sources[source_index]
                self._paint_mesh(mvp, data, vao, textures, batch)
            glDepthMask(GL_TRUE)
            glDepthFunc(GL_LESS)
            glDisable(GL_BLEND)
        if self._view_mode.shows_skeleton:
            self._paint_skeleton(mvp, overlay=self._view_mode is ViewMode.MESH_SKELETON)

    def _paint_mesh(
        self,
        mvp: np.ndarray,
        data: PreparedRenderData,
        vao: int,
        textures: dict[int, int],
        batch: PreparedBatch,
    ) -> None:
        glUseProgram(self._program)
        glUniformMatrix4fv(glGetUniformLocation(self._program, "mvp"), 1, GL_TRUE, mvp)
        glUniform1i(glGetUniformLocation(self._program, "color_texture"), 0)
        glBindVertexArray(vao)
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
        glUniform1i(glGetUniformLocation(self._program, "alpha_mode"), {"OPAQUE": 0, "MASK": 1, "BLEND": 2}[batch.alpha_mode])
        texture_handle = None if batch.texture_index is None else textures.get(batch.texture_index)
        glUniform1i(glGetUniformLocation(self._program, "use_texture"), int(texture_handle is not None))
        glUniform4f(glGetUniformLocation(self._program, "fallback_color"), *batch.fallback_rgba)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, 0 if texture_handle is None else texture_handle)
        glDrawArrays(GL_TRIANGLES, batch.first_vertex, batch.vertex_count)
        glBindVertexArray(0)
        glUseProgram(0)

    def _paint_skeleton(self, mvp: np.ndarray, *, overlay: bool) -> None:
        glDisable(GL_CULL_FACE)
        if overlay:
            # Debug overlay intentionally remains visible through the mesh.
            glDisable(GL_DEPTH_TEST)
        else:
            glEnable(GL_DEPTH_TEST)
        glUseProgram(self._debug_program)
        glUniformMatrix4fv(glGetUniformLocation(self._debug_program, "mvp"), 1, GL_TRUE, mvp)
        color_location = glGetUniformLocation(self._debug_program, "debug_color")
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
