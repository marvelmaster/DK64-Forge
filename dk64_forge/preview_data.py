"""DK64 backend adapter for JFG's generic OpenGL viewport contract.

The adapter consumes the already validated origin-centered glTF export. It
does not decode animation assets or invent a DK64 transform convention.
Playback displays exact exported interior integer samples at the selected
diagnostic display rate.
"""

from dataclasses import dataclass, replace
from .core.rdp import MaterialState
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np
from PySide6.QtGui import QImage

from .debug_view import PreparedSkeletonDebug
from .export import ExportKind, export_gltf
from .pipeline import static_dk
from .render_data import PreparedBatch, PreparedRenderData, PreparedTexture


_COMPONENT = {5126: "f", 5125: "I", 5123: "H"}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_WRAP = {10497: "REPEAT", 33648: "MIRROR", 33071: "CLAMP"}
UNRESOLVED_VIEWPORT_RGBA = (0.32, 0.18, 0.08, 1.0)


def _viewport_fallback_rgba(texture_index: int | None, pbr: dict) -> tuple[float, ...]:
    """Keep resolved material color intact; tint only unresolved viewport faces."""
    if texture_index is not None:
        return tuple(pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0]))
    return UNRESOLVED_VIEWPORT_RGBA


def _accessor(doc: dict, blob: bytes, index: int) -> tuple[tuple, ...]:
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    width = _WIDTH[accessor["type"]]
    fmt = "<" + _COMPONENT[accessor["componentType"]] * width
    size = struct.calcsize(fmt)
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    if (view.get("byteStride") is not None or offset < 0 or
            offset + accessor["count"] * size > len(blob) or
            accessor["count"] * size > view["byteLength"]):
        raise ValueError("preview accessor exceeds its glTF buffer view")
    return tuple(struct.unpack_from(fmt, blob, offset + size*i)
                 for i in range(accessor["count"]))


def _matrix(translation, rotation, scale):
    x, y, z, w = rotation
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = np.array((
        (1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)),
        (2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)),
        (2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)),
    ), dtype=np.float64) @ np.diag(scale)
    result[:3, 3] = translation
    return result


@dataclass(frozen=True)
class TexgenVertices:
    """Per-vertex G_TEXTURE_GEN tile state from the export's primitive extras."""
    mask: np.ndarray        # (n,) vertices whose UV the RSP generates
    linear: np.ndarray      # (n,) G_TEXTURE_GEN_LINEAR
    hilite: np.ndarray      # (n,) render tile origin set by gDPSetHilite1Tile
    scale: np.ndarray       # (n, 2) G_TEXTURE scale, 0.16
    size: np.ndarray        # (n, 2) tile width/height
    shift: np.ndarray       # (n, 2) G_SETTILE shifts
    upper_left: np.ndarray  # (n, 2) fixed tile origin in quarter texels
    center: np.ndarray      # (n,) bilerp texel-center offset
    stored_hilite: np.ndarray | None = None  # (n,) stored S/T under the hilite tile origin
    baked_upper_left: np.ndarray | None = None  # (n, 2) origin baked into the exported UVs
    image_size: np.ndarray | None = None  # (n, 2) wrap-mask size of the image (UV normalization)


def _shift_texels(value: np.ndarray, amount: np.ndarray) -> np.ndarray:
    return np.where((amount > 0) & (amount <= 10), value / 2.0 ** amount,
                    np.where(amount > 10, value * 2.0 ** (16 - amount), value))


@dataclass
class PreviewScene:
    render_data: PreparedRenderData
    bind_positions: np.ndarray
    rigid_joints: np.ndarray
    parent_ordinals: tuple[int | None, ...]
    inverse_binds: tuple[np.ndarray, ...]
    local_samples: tuple[tuple[np.ndarray, ...], ...]
    bind_globals: tuple[np.ndarray, ...]
    metadata: dict
    bind_normals: np.ndarray | None = None
    texgen: TexgenVertices | None = None
    posed_normals: np.ndarray | None = None
    dynamic_materials: dict | None = None
    texture_sources: dict | None = None

    def texture_frame(self, source, frame: int, *, slot_frames=None):
        from .core import texture_bank, rom_model
        slots = rom_model.parse_dynamic_textures(source.actor)
        textures = []
        for texture in self.render_data.textures:
            image_info = (self.texture_sources or {}).get(texture.texture_index)
            slot_info = (self.dynamic_materials or {}).get(texture.texture_index)
            if image_info:
                image, interleaved = image_info
                raw = texture_bank.table_entry(source.normalized, 25, image)
                usage = texture_bank.TextureUsage(0, 2, texture.width, texture.height, interleaved, None, "actor")
                if raw:
                    texture = replace(texture, mip_levels=texture_bank.decode_mip_levels(raw, usage))
            if slot_info and slots.get(slot_info[0]):
                slot, interleaved = slot_info
                frames = slots[slot]
                raw = texture_bank.table_entry(source.normalized, 25, frames[(slot_frames or {}).get(slot, frame) % len(frames)])
                usage = texture_bank.TextureUsage(0, 2, texture.width, texture.height, interleaved, None, "actor")
                rgba = texture_bank.decode(raw, usage) if raw else None
                if rgba is not None:
                    texture = replace(texture, rgba=rgba, mip_levels=texture_bank.decode_mip_levels(raw, usage))
            textures.append(texture)
        return tuple(textures)

    def shade_colors(self):
        normals = self.posed_normals if self.posed_normals is not None else self.bind_normals
        if normals is None:
            return None
        light = np.asarray((0.35, 0.8, 0.5))
        light /= np.linalg.norm(light)
        level = 0.55 + 0.45 * np.maximum(0, normals @ light)
        return tuple((float(v), float(v), float(v), 1.0) for v in level)

    def texgen_uvs(self, eye, at, up=(0.0, 1.0, 0.0)) -> np.ndarray | None:
        """Regenerate RSP texgen UVs for the current pose and a viewer camera."""
        if self.texgen is None:
            return None
        tg = self.texgen
        stored = tg.stored_hilite if tg.stored_hilite is not None else np.zeros_like(tg.mask)
        if not np.any(tg.mask) and not np.any(stored):
            return None
        normals = self.posed_normals if self.posed_normals is not None else self.bind_normals
        camera = {"eye": tuple(eye), "at": tuple(at), "up": tuple(up)}
        right, new_up, _ = static_dk.texgen.lookat_basis(eye, at, up)
        d = np.clip(np.stack((normals @ right, normals @ new_up), axis=-1), -1.0, 1.0)
        base = np.where(tg.linear[:, None], np.arccos(-d) * static_dk.texgen.TEXGEN_LINEAR_SCALE,
                        (d + 1.0) * static_dk.texgen.TEXGEN_RANGE)
        st = _shift_texels(base * tg.scale / 65536.0, tg.shift)
        image = tg.image_size if tg.image_size is not None else tg.size
        upper_left = tg.upper_left.astype(np.float64).copy()
        for size in {tuple(row) for row in tg.size[tg.hilite]}:
            rows = tg.hilite & np.all(tg.size == size, axis=1)
            upper_left[rows] = static_dk.dk64_hilite_upper_left(camera, *size)
        uvs = np.asarray(self.render_data.uvs, dtype=np.float64).copy()
        generated = (st - upper_left / 4.0 + tg.center[:, None]) / image
        uvs[tg.mask] = generated[tg.mask]
        if np.any(stored):
            # Stored S/T are camera independent; only the hilite tile origin moves.
            hilite_ul = np.zeros_like(upper_left)
            for size in {tuple(row) for row in tg.size[stored]}:
                rows = stored & np.all(tg.size == size, axis=1)
                hilite_ul[rows] = static_dk.dk64_hilite_upper_left(camera, *size)
            st_stored = (uvs * image - tg.center[:, None]
                         + tg.baked_upper_left / 4.0)
            moved = (st_stored - hilite_ul / 4.0 + tg.center[:, None]) / image
            uvs[stored] = moved[stored]
        return uvs

    @property
    def safe_first(self) -> int:
        limits = self.metadata.get("safe_sample_range", [0, len(self.local_samples) - 1])
        return int(limits[0])

    @property
    def safe_last(self) -> int:
        limits = self.metadata.get("safe_sample_range", [0, len(self.local_samples) - 1])
        return int(limits[1])

    @classmethod
    def from_rom(cls, source) -> "PreviewScene":
        """Default clip: DK's Entry 4, or the character's slot-0 idle clip."""
        character = getattr(source, "character", None)
        return cls.from_animation(source, character.default_animation if character else 4)

    @classmethod
    def from_animation(cls, source, animation_id: int, *, procedural_hair=False) -> "PreviewScene":
        with TemporaryDirectory(prefix="dk64_forge_preview_") as folder:
            gltf = Path(folder) / f"dk_anim_{animation_id:04X}_preview.gltf"
            result = export_gltf(source, gltf, ExportKind.ANIMATED,
                                 animation_id=animation_id, procedural_hair=procedural_hair)
            doc = json.loads(gltf.read_text(encoding="utf-8"))
            blob = gltf.with_name(doc["buffers"][0]["uri"]).read_bytes()
            character = getattr(source, "character", None)
            expected_triangles = character.triangles if character else 704
            scene = cls._from_gltf(doc, blob, gltf.parent, result.validation, expected_triangles)
            scene.render_data = replace(scene.render_data, textures=scene.texture_frame(source, 0))
            return scene

    @classmethod
    def _from_gltf(cls, doc: dict, blob: bytes, base: Path, validation: dict,
                   expected_triangles: int = 704):
        skin = doc["skins"][0]
        joint_nodes = tuple(skin["joints"])
        joint_count = len(joint_nodes)
        if validation["triangles"] != expected_triangles or validation.get("bones", joint_count) != joint_count:
            raise ValueError("preview requires the canonical character skin")
        node_to_ordinal = {node: ordinal for ordinal, node in enumerate(joint_nodes)}
        parent_node = {}
        for parent, node in enumerate(doc["nodes"]):
            for child in node.get("children", []):
                parent_node[child] = parent
        parents = tuple(node_to_ordinal.get(parent_node.get(node)) for node in joint_nodes)
        if parents[0] is not None or any(p is None or p >= i for i, p in enumerate(parents) if i):
            raise ValueError("preview joint hierarchy differs from canonical DK")
        inverse_binds = tuple(np.asarray(row, dtype=np.float64).reshape((4, 4), order="F")
                              for row in _accessor(doc, blob, skin["inverseBindMatrices"]))

        positions = []
        uvs = []
        normals = []
        texgen_rows = []
        joints = []
        batches = []
        used_textures = set()
        dynamic_materials = {}
        texture_sources = {}
        for primitive in doc["meshes"][0]["primitives"]:
            attrs = primitive["attributes"]
            source_positions = _accessor(doc, blob, attrs["POSITION"])
            source_joints = _accessor(doc, blob, attrs["JOINTS_0"])
            source_uvs = (_accessor(doc, blob, attrs["TEXCOORD_0"])
                          if "TEXCOORD_0" in attrs else None)
            source_normals = _accessor(doc, blob, attrs["NORMAL"])
            indices = tuple(row[0] for row in _accessor(doc, blob, primitive["indices"]))
            extras = primitive.get("extras", {})
            tile = extras.get("texgen_tile")
            stored_tile = extras.get("hilite_tile")
            texgen_row = None if tile is None else (
                True, bool(extras.get("G_TEXTURE_GEN_LINEAR")), bool(extras.get("hilite_tile_origin")),
                tile["scale"], tile["size"], tile["shift"], tile["upper_left_quarter_texels"],
                0.5 if tile["texture_filter"] in (static_dk.G_TF_BILERP, static_dk.G_TF_AVERAGE) else 0.0,
                False, (0, 0), tile.get("image_size", tile["size"]))
            if stored_tile is not None:
                texgen_row = (False, False, False, (0, 0), stored_tile["size"], (0, 0), (0, 0),
                              0.5 if stored_tile["texture_filter"] in
                              (static_dk.G_TF_BILERP, static_dk.G_TF_AVERAGE) else 0.0,
                              True, stored_tile["baked_upper_left_quarter_texels"],
                              stored_tile.get("image_size", stored_tile["size"]))
            material = doc["materials"][primitive["material"]]
            pbr = material.get("pbrMetallicRoughness", {})
            texture = pbr.get("baseColorTexture")
            texture_index = texture["index"] if texture and source_uvs is not None else None
            if texture_index is not None:
                used_textures.add(texture_index)
            first = len(positions)
            for index in indices:
                positions.append(source_positions[index])
                joints.append(source_joints[index][0])
                normals.append(source_normals[index])
                texgen_rows.append(texgen_row or (False, False, False, (0, 0), (1, 1), (0, 0), (0, 0), 0.0,
                                               False, (0, 0), (1, 1)))
                uvs.append(source_uvs[index] if source_uvs is not None else (0.0, 0.0))
            if texture_index is not None and extras.get("source_texture_entry") is not None:
                texture_sources[texture_index] = (extras["source_texture_entry"], extras.get("odd_lines_swapped", False))
            if texture_index is not None and extras.get("dynamic_slot") is not None:
                dynamic_materials[texture_index] = (extras["dynamic_slot"], extras.get("odd_lines_swapped", False))
            untextured = "dk64_untextured_shade" in extras
            batches.append(PreparedBatch(
                first_vertex=first, vertex_count=len(indices),
                texture_index=texture_index,
                double_sided=material.get("doubleSided", False),
                uses_verified_texture=texture_index is not None,
                # A brown neutral viewport placeholder for runtime-generated UVs;
                # the exported material and ROM texture data remain untouched.
                # Shade-only combiner surfaces are untextured by design (white).
                fallback_rgba=(tuple(pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])) if untextured
                               else _viewport_fallback_rgba(texture_index, pbr)),
                fallback_reason=None if texture_index is not None or untextured else
                    "G_TEXTURE_GEN has no fixed UV in the verified export",
                alpha_mode=material.get("alphaMode", "OPAQUE"),
                z_mode=3 if extras.get("dk64_z_mode") == "decal" else 0,
                material=MaterialState.from_json(extras.get("dk64_rdp_material", {})),
            ))
        if len(positions) != expected_triangles * 3 or any(j >= joint_count for j in joints):
            raise ValueError("preview triangle/joint assignment count differs from the character")

        textures = []
        for index in sorted(used_textures):
            entry = doc["textures"][index]
            image_path = base / doc["images"][entry["source"]]["uri"]
            image = QImage(str(image_path)).convertToFormat(QImage.Format.Format_RGBA8888)
            if image.isNull():
                raise ValueError(f"preview cannot decode texture {image_path}")
            sampler = doc["samplers"][entry["sampler"]]
            textures.append(PreparedTexture(
                index, image.width(), image.height(), bytes(image.bits()),
                _WRAP[sampler["wrapS"]], _WRAP[sampler["wrapT"]],
            ))
        bind_points = np.asarray(positions, dtype=np.float64)
        minimum = tuple(float(v) for v in bind_points.min(axis=0))
        maximum = tuple(float(v) for v in bind_points.max(axis=0))
        data = PreparedRenderData(tuple(tuple(float(v) for v in p) for p in positions),
                                  tuple(tuple(float(v) for v in uv) for uv in uvs),
                                  tuple(batches), tuple(textures), minimum, maximum)

        channels = doc["animations"][0]["channels"]
        samplers = doc["animations"][0]["samplers"]
        paths = {}
        for channel in channels:
            if channel["target"]["node"] not in node_to_ordinal:
                # Separate attachment skins have their own preview/player.
                continue
            ordinal = node_to_ordinal[channel["target"]["node"]]
            path = channel["target"]["path"]
            paths[(ordinal, path)] = _accessor(doc, blob,
                                               samplers[channel["sampler"]]["output"])
        sample_count = validation["samples"]
        if len(paths) != 3 * joint_count or sample_count < 2 or any(
            len(rows) != sample_count for rows in paths.values()
        ):
            raise ValueError("preview does not contain all sampled joint TRS values")
        samples = tuple(tuple(_matrix(
            paths[(bone, "translation")][frame],
            paths[(bone, "rotation")][frame],
            paths[(bone, "scale")][frame],
        ) for bone in range(joint_count)) for frame in range(sample_count))
        bind_locals = tuple(_matrix(doc["nodes"][node].get("translation", (0, 0, 0)),
                                    (0, 0, 0, 1), (1, 1, 1)) for node in joint_nodes)
        bind_globals = cls._globals(bind_locals, parents)
        metadata = {**validation, **doc["animations"][0].get("extras", {})}
        column = lambda i, dtype: np.asarray([row[i] for row in texgen_rows], dtype=dtype)
        texgen = TexgenVertices(column(0, bool), column(1, bool), column(2, bool),
                                column(3, np.float64), column(4, np.float64),
                                column(5, np.int32), column(6, np.float64), column(7, np.float64),
                                column(8, bool), column(9, np.float64),
                                column(10, np.float64))
        return cls(data, bind_points, np.asarray(joints, dtype=np.int32), parents,
                   inverse_binds, samples, bind_globals, metadata,
                   np.asarray(normals, dtype=np.float64), texgen, dynamic_materials=dynamic_materials, texture_sources=texture_sources)

    @staticmethod
    def _globals(locals_, parents):
        rows = []
        for index, local in enumerate(locals_):
            parent = parents[index]
            rows.append(local if parent is None else rows[parent] @ local)
        return tuple(rows)

    def _skeleton(self, globals_) -> PreparedSkeletonDebug:
        points = tuple(tuple(float(v) for v in matrix[:3, 3]) for matrix in globals_)
        edges = tuple(point for child, parent in enumerate(self.parent_ordinals)
                      if parent is not None for point in (points[parent], points[child]))
        return PreparedSkeletonDebug(tuple(range(len(points))), points, edges)

    def bind_pose(self):
        self.posed_normals = self.bind_normals
        return self.render_data.positions, self._skeleton(self.bind_globals)

    def pose(self, frame: int):
        """Display one exact exported sample; no guessed fractional evaluation."""
        index = frame - self.safe_first
        if not 0 <= index < len(self.local_samples) or frame > self.safe_last:
            raise ValueError("preview frame is outside the selected safe interval")
        globals_ = self._globals(self.local_samples[index], self.parent_ordinals)
        posed = np.empty_like(self.bind_positions)
        normals = None if self.bind_normals is None else np.empty_like(self.bind_normals)
        for joint in range(len(self.inverse_binds)):
            mask = self.rigid_joints == joint
            if not np.any(mask):
                continue
            transform = globals_[joint] @ self.inverse_binds[joint]
            points = self.bind_positions[mask]
            posed[mask] = points @ transform[:3, :3].T + transform[:3, 3]
            if normals is not None:
                rotated = self.bind_normals[mask] @ transform[:3, :3].T
                lengths = np.linalg.norm(rotated, axis=1, keepdims=True)
                normals[mask] = np.divide(rotated, lengths, out=np.zeros_like(rotated),
                                          where=lengths > 0)
        self.posed_normals = normals
        return tuple(tuple(float(v) for v in p) for p in posed), self._skeleton(globals_)
