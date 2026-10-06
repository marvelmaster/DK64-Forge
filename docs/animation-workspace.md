# Animation workflow

## Compare and organize clips

Characters and Other models now offer **Compare clips…**. Choose two animations
of the same model and play or scrub them side by side. Each pane has independent
clip data and a camera; playback shares the normalized cycle position. Clips with
different lengths finish a cycle together. Actor and embedded prop tracks work in
this window. Timing is the viewer's 30-unit preview convention, not a reconstruction
of game transitions. Closing the comparison stops its timers and background jobs.

**Favorite** and **Only favorites** organize each model's list. **Name / associate
selected clip…** saves a personal name and optional association. Labels explicitly
say **User name** or **User association**. An association may appear in the model's
owned-clip filter, but it does not modify recovered ROM ownership, GLB ownership
metadata or the curated level autoplay routes. Original evidence remains in the
clip tooltip. Records are scoped by model/variant and ROM fingerprint.

## Irregular actor skeletons

- **K. Lumsy (0x44):** 37 physical bones use 39 animation scratch groups. Missing
  group numbers no longer reject its clips or GLB export.
- **Beanstalk (0x47):** physical bone records are not in local-index order. Parsing,
  animation evaluation and exported joints now agree on the 50 local bone slots.
- **Bananaporter Zipper (0x97):** 17 bones use 18 scratch groups. All 45 structurally
  compatible clips passed complete interior sampling. The catalog's presumed
  dispatch routes have incompatible channel counts, so they remain filtered;
  structural compatibility alone does not establish the original animation.
- **Toy Monster (0x27):** its actor asset has 12 bones and no triangles. The browser
  shows and animates its skeleton, supports comparison and exports joints plus
  animation. Static mesh export is disabled. The game's assembly of separate toy
  component meshes remains unimplemented.

Bone indices must remain unique and bounded, and parents must be valid. The five
Kongs retain their existing channel counts and adjustment rules. Parent-relative
GLB transforms are checked against the sampled composed matrices.

## Additional HUD textures

Table 14 now decodes **103 of 167 entries**, up from 44. New layouts come from
literal `displayImage` arguments, bounded image-index calculations and tile/load
commands in the original draw routines. These include DKTV, health images, race
and menu icons, overlays, and the blueprint atlas. The 0x83–0x8E and 0x8F–0x9E
sequences are available as animated strips; source timing advances every two
object-timer units. A layout is added only when the current ROM's raw entry is
large enough for the source load. Dimensions are not selected from byte lengths.
The remaining **64** images still lack usable draw-layout evidence. Usage rows
identify the routine/context; labels are derived descriptions, not official names.

## ROM folder preference

As of 2026-10-07 the Session menu, manual save/open controls and automatic
session save/restore are removed. Forge starts with the default view for the
loaded ROM. Existing session files are not loaded or rewritten.

**File → Load ROM…** uses Qt's native Windows file dialog. Its starting folder is
the directory of the last successfully loaded ROM. This also applies to ROMs
loaded with `--rom`. The directory persists through `QSettings` (Windows registry
under `HKEY_CURRENT_USER\Software\Marvelmaster\DK64 Forge`, key
`last_rom_directory`). Cancelled selections and failed ROM loads do not replace
it. If the folder no longer exists, the picker falls back to its normal location.

Personal clip names, associations and favorites remain independent and still
live in `%USERPROFILE%\.dk64_forge` as ROM-scoped clip-library JSON files.

## Faster animation switching (2026-10-07)

Character clip changes reuse the current model's skin, textures, bind matrices and
material data. They prepare animation channels directly in memory instead of
exporting/reloading temporary glTF and PNG files for every clip. The path uses the
same checked pose reader, TRS conversion and float32 channel precision as export.
Interpolation, mouth offsets and Tiny's optional procedural hair remain supported.

Uncached character and actor clips are prepared on serial background workers.
The window keeps processing input while loading; another selection cancels obsolete
sampling between poses, removes queued requests, and ignores stale results.
Closing the window or changing model cancels pending work. Actor playback intent
is preserved across switches, including rapid selections; pausing during a load
prevents its completion from restarting playback.

Characters retain up to 24 clip scenes, with a 64 MiB limit on cached matrix
payload (one oversized current clip is permitted). Actor models retain up to 12
sampled clips. The shared model data is not duplicated per character clip. A
revisited cached clip needs no resampling or glTF work.

Local measurements for DK clips 0..4, in milliseconds:

| Clip | Samples | Former export/reload path | In-memory preparation |
|---|---:|---:|---:|
| 0 | 28 | 170 | 49 |
| 1 | 15 | 153 | 28 |
| 2 | 15 | 158 | 29 |
| 3 | 15 | 154 | 27 |
| 4 | 98 | 347 | 173 |

These are backend preparation measurements on the local machine, excluding
painting/GPU work. A first visit to a long clip can still need CPU time, but that
work no longer blocks the Qt event loop. Initial ROM/model loading and explicit
file exports still use their existing paths.

## Validation

Animation-switch validation on 2026-10-07: 16 focused switching, ROM-dialog and
workspace checks passed (18.535 seconds, offscreen Qt). Integer local matrices
match exported previews exactly for all five Kongs; tests also cover Tiny hair,
interpolated poses, mouth offsets, event-loop responsiveness, stale-result
rejection, sampling cancellation, cached revisits and actor browser/comparison.

Validation on 2026-10-07: all four checks in `tools.test_rom_dialog` passed. They
cover persisted/reopened directory settings, cancellation, first use, missing
folders and a loaded window with no Session menu or automatic session writes.

The session-related checks below are historical results for the former feature;
the current application no longer offers session persistence.

Local checks cover independent comparison panes, shared scrubbing and shutdown,
personal-library persistence/scoping/filtering, session validation and asynchronous
round trips for models, poses, level selection/ticks/cameras and texture filters/zoom.
All four irregular skeletons are sampled and exported; Toy Monster is also exercised
through the actual browser. Native OpenGL comparison/skeleton/HUD captures were
inspected. Research tests and ROM-derived captures stay in ignored local folders.

Validation on 2026-10-04: the complete application suite passed 175 tests at its
five-workspace-test checkpoint. The expanded workspace suite then passed all
eight tests, bringing current coverage to 178 application tests; the native Qt
suite independently passed 50 tests. The reference suite passed 127 tests
(305 application/reference tests in total). Focused final checks passed for
skeleton-only comparisons and library filtering. An integration smoke check
verified automatic session save/restore and persistence of the remember option.
The source-route audit sampled 105 model/clip pairs, filtered 22 incompatible
routes and reported no sampling failures. These are local viewer/export checks;
no fresh emulator comparison established runtime fidelity.
