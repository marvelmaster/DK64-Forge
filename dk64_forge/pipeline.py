"""Narrow bridge to the verified DK64 research pipeline.

This module contains no DK64 binary-format implementation. The research modules
remain the single source of truth until they are moved into a packaged core.
"""

from pathlib import Path
import sys


EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "phase1_static_dk"
if str(EXPERIMENT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT))

import static_dk  # noqa: E402
import dk_skeleton  # noqa: E402
import entry4_rootmotion_preview  # noqa: E402
import retime_entry4_preview  # noqa: E402
import trace_one_bone  # noqa: E402
import dk_table11_animation_census  # noqa: E402
import reproduce_unk0_reader  # noqa: E402
import reconstruct_direct_pose  # noqa: E402
import compose_direct_pose  # noqa: E402
import dk_anim_code_table  # noqa: E402
import dk_animation_names  # noqa: E402
import dk_control_states  # noqa: E402
