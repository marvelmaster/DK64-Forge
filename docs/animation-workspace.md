# Animation workflow and saved sessions

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

## Sessions

**Session → Save session… / Open session…** writes and reads a JSON session. It
contains the ROM fingerprint, active tab, character/model/level selection, selected
clips and stored pose frames, mouth opening, eye/clothing states, camera positions,
interpolation and timing controls, search/filter settings, selected level objects,
level tick, texture bank/image and preview zoom. Favorite filters are restored;
personal clip names themselves live in the separate clip library.

**Remember session on close** is on by default in normal application launches and
restores the last session for that ROM at the next launch. This preference persists.
Automatic sessions and clip libraries live in `%USERPROFILE%\.dk64_forge`.
Manual session files can be stored elsewhere. No ROM bytes are included.

Restoration waits for asynchronous asset/content loads and ends paused, preserving
the saved pose. Files for another ROM or unsupported session versions are rejected.
Writes replace the JSON file atomically. Corrupt files produce a status message
instead of preventing ROM browsing.

## Validation

Local checks cover independent comparison panes, shared scrubbing and shutdown,
personal-library persistence/scoping/filtering, session validation and asynchronous
round trips for models, poses, level selection/ticks/cameras and texture filters/zoom.
All four irregular skeletons are sampled and exported; Toy Monster is also exercised
through the actual browser. Native OpenGL comparison/skeleton/HUD captures were
inspected. Research tests and ROM-derived captures stay in ignored local folders.
