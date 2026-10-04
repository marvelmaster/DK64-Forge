"""Cached level texture/UV playback; actor poses are layered on by level_actors."""
from dataclasses import replace
import numpy as np
from . import static_model, level_content
from .core import texture_bank, texture_animation


class TexturePlayback:
    def __init__(self, render, bindings, cache, *, map_id=None, scroll=(), mist=()):
        self.base = render
        self.bindings = bindings
        self.cache = cache
        self.map_id = map_id
        self.scroll = scroll
        self.mist = mist
        self.frames = {}
        self.last = render
        self.last_key = None
        self.shade_alpha = [static_model.mesh_decoder.rdp.opaque_alpha(batch.material,
            min((color[3] for color in (render.colors or ())[batch.first_vertex:batch.first_vertex+batch.vertex_count]), default=1.0))
            for batch in render.batches]

    def render(self, tick):
        key = tuple((index, tick // a.ticks_per_frame % len(a.frames),
                     tick % a.ticks_per_frame if a.interpolate else 0)
                    for index, (_source, a) in self.bindings.items())
        origin = 0
        if self.scroll:
            origin = (-tick) % 256 if self.map_id != 187 else (0 if tick <= 0 else 255 - 2*((tick-1) % 128))
        key += (origin, tick % 6656 if self.mist else 0)
        if key == self.last_key:
            return self.last
        textures = list(self.base.textures)
        for index, source_animation in self.bindings.items():
            source, animation = source_animation
            sample = tick // animation.ticks_per_frame
            fraction = tick % animation.ticks_per_frame / animation.ticks_per_frame if animation.interpolate else 0
            def frame(image):
                frame_key = (source, image)
                if frame_key not in self.frames:
                    draw = replace(source, image=image)
                    pixels = self.cache.pixels(draw)
                    raw = texture_bank.table_entry(self.cache.rom, draw.table, image) or b""
                    self.frames[frame_key] = (pixels, texture_bank.decode_mip_levels(raw, draw.usage))
                return self.frames[frame_key]
            pixels, mips = frame(animation.image(sample))
            if pixels is None:
                raise ValueError("Animated texture frame could not be decoded")
            if fraction:
                other, _ = frame(animation.image(sample + 1))
                if other is not None and len(other) == len(pixels):
                    a = np.frombuffer(pixels, dtype=np.uint8).astype(float)
                    b = np.frombuffer(other, dtype=np.uint8)
                    pixels = np.rint(a*(1-fraction)+b*fraction).astype(np.uint8).tobytes()
            textures[index] = replace(textures[index], rgba=pixels,
                                      mip_levels=() if animation.interpolate else mips)
        batches = []
        for ordinal, batch in enumerate(self.base.batches):
            rgba = None if batch.texture_index is None else textures[batch.texture_index].rgba
            mode = static_model._alpha_mode(rgba, self.shade_alpha[ordinal])
            batches.append(batch if mode == batch.alpha_mode else replace(batch, alpha_mode=mode, depth_write=mode != "BLEND"))
        batches = self.base.batches if tuple(batches) == self.base.batches else tuple(batches)
        uvs, uvs1 = self.base.uvs, self.base.uvs1
        if self.scroll:
            uvs, uvs1 = list(uvs), list(uvs1 or uvs)
            for first, count, height in self.scroll:
                for vertex in range(first, first+count):
                    u, v = uvs[vertex]; uvs[vertex] = (u, v-origin/(4*height))
                    u, v = uvs1[vertex]; uvs1[vertex] = (u, v-origin/(4*height))
            uvs, uvs1 = tuple(uvs), tuple(uvs1)
        if self.mist:
            uvs, uvs1 = list(uvs), list(uvs1 or uvs)
            a = 0 if tick <= 0 else 255 - 5*((tick-1) % 52)
            b = 0 if tick <= 0 else 255 - 2*((tick-1) % 128)
            for first, count in self.mist:
                for vertex in range(first, first+count):
                    u,v = uvs[vertex]; uvs[vertex] = (u,v-a/256.)
                    u,v = uvs1[vertex]; uvs1[vertex] = (u,v-b/256.)
            uvs, uvs1 = tuple(uvs), tuple(uvs1)
        self.last = replace(self.base, textures=tuple(textures), batches=batches, uvs=uvs, uvs1=uvs1)
        self.last_key = key
        return self.last


def bindings_for(model, animations):
    result = {}
    for texture, source in zip(model.render.textures, model.texture_sources):
        candidates = [a for a in animations if a.table == source.table and a.frames[0] == source.image]
        if candidates:
            if any((a.frames, a.ticks_per_frame, a.interpolate) !=
                   (candidates[0].frames, candidates[0].ticks_per_frame, candidates[0].interpolate)
                   for a in candidates[1:]):
                raise ValueError("Ambiguous texture animation binding")
            result[texture.texture_index] = source, candidates[0]
    return result


def map_playback(rom, entry, model, cache):
    data = texture_bank.table_entry(rom, 1, entry)
    bindings = bindings_for(model, texture_animation.map_animations(data))
    scroll, mist = [], []
    for batch in model.render.batches:
        if batch.texture_index is None:
            continue
        source = model.texture_sources[batch.texture_index]
        if source.table == 7 and source.image == 0x3E0:
            mist.append((batch.first_vertex, batch.vertex_count))
        if (source.table == 25 and source.image == 0x1765 and batch.material.mux ==
                (1,15,4,7,1,7,4,7,0,15,3,7,0,7,3,7)):
            scroll.append((batch.first_vertex, batch.vertex_count, source.usage.height))
    return TexturePlayback(model.render, bindings, cache, map_id=entry, scroll=scroll, mist=mist)


def content_playback(render, models, cache):
    lookup = {level_content.texture_signature(t): t.texture_index for t in render.textures}
    bindings = {}
    for (kind, entry, tick), model in models.items():
        if kind != "prop" or tick != 0 or model is None:
            continue
        data = texture_bank.table_entry(cache.rom, 4, entry)
        for index, binding in bindings_for(model, texture_animation.prop_animations(data)).items():
            target = lookup.get(level_content.texture_signature(model.render.textures[index]))
            if target is not None:
                if target in bindings and bindings[target] != binding:
                    raise ValueError("Ambiguous shared prop texture binding")
                bindings[target] = binding
    return TexturePlayback(render, bindings, cache)
