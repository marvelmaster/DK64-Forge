"""Facade over the DK64 core modules in ``dk64_forge.core``.

The attribute names below are the historical short names used across the app
(``pipeline.static_dk`` is ``core.rom_model``, and so on); the app imports the
core only through this module.
"""

from .core import (
    adjustment as dk_adjustment,
    anim_code_table as dk_anim_code_table,
    animation_census as dk_table11_animation_census,
    animation_labels as dk_animation_names,
    animation_reader as reproduce_unk0_reader,
    animation_timeline as dk_animation_timeline,
    bone_matrix as trace_one_bone,
    control_states as dk_control_states,
    entry4_preview as entry4_rootmotion_preview,
    entry4_retime as retime_entry4_preview,
    entry4_timing as entry4_playback_timing,
    pose_compose as compose_direct_pose,
    pose_gltf as dk_pose_to_gltf,
    pose_reconstruct as reconstruct_direct_pose,
    rom_model as static_dk,
    root_completion as entry4_completed_root,
    skeleton as dk_skeleton,
    texgen as dk_texgen,
)
