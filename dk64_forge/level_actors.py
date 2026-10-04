"""Bounded pose cache and stable vertex mapping for confirmed level actors."""
from collections import OrderedDict
from dataclasses import replace
import numpy as np
from . import level_content
from .actor_routes import confirmed_routes
from .actor_animation import ActorAnimations
from .core import rom_model

class ActorPlayback:
    def __init__(self, rom, captured, models):
        self.animations, self.instances, self.errors = {}, [], []
        self.poses = OrderedDict()
        order = []
        level_content.merge_render([scene for row, scene in captured], vertex_order=order)
        inverse = np.argsort(order)
        tables = level_content.load_actor_tables(rom)
        offset = 0
        for row, scene in captured:
            entry = level_content.actor_entry(tables, row)
            routes = confirmed_routes(rom, entry)
            if routes and entry not in self.animations:
                model = models.get(("actor", entry, 0))
                try:
                    assets = [(clip, *rom_model.extract_entry(rom, 11, clip)) for clip in routes]
                    animation = ActorAnimations(rom, entry, model, assets)
                    animation.select(animation.descriptors[0].table11_id)
                    self.animations[entry] = animation
                except (ValueError, IndexError, AttributeError) as exc:
                    self.errors.append(f"Actor {entry:03X}: {exc}")
                    self.animations[entry] = None
            if self.animations.get(entry) is not None:
                # Affine transform sampled from the same placement routine as rest geometry.
                basis = replace(scene, positions=((0.,0.,0.), (1.,0.,0.), (0.,1.,0.), (0.,0.,1.)))
                transform = np.asarray(level_content.placed_render(basis, row).positions)
                self.instances.append((entry, inverse[offset:offset+len(scene.positions)],
                                       transform[1:]-transform[0], transform[0]))
            offset += len(scene.positions)

        # Prepare the short, confirmed cycles once in the loading worker. Runtime
        # updates are vectorized scatter copies, not Python skinning per instance.
        self.cycles = []
        for entry, indices, matrix, origin in self.instances:
            animation = self.animations[entry]
            points, colors = [], []
            for frame in range(len(animation.samples)):
                key = entry, frame
                if key not in self.poses:
                    self.poses[key] = np.asarray(animation.pose(frame), dtype=np.float32)
                points.append(self.poses[key] @ matrix + origin)
                colors.append(animation.colors(frame, matrix))
            self.cycles.append((indices, np.asarray(points, dtype=np.float32), np.asarray(colors, dtype=np.float32)))
        self._base = None

    def render(self, render, tick):
        if not self.cycles:
            return render
        if self._base is None:
            self._base = (np.asarray(render.positions, dtype=np.float32), np.asarray(render.colors, dtype=np.float32))
        positions, colors = (a.copy() for a in self._base)
        for indices, poses, shades in self.cycles:
            positions[indices] = poses[tick % len(poses)]
            colors[indices] = shades[tick % len(shades)]
        positions.flags.writeable = colors.flags.writeable = False
        return replace(render, positions=positions, colors=colors)
