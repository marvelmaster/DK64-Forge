"""Core DK64 modules: ROM access, model and texture decoding, skeleton, animation.

rom_model          ROM/pointer tables, actor and mesh decoding, textures, static glTF
texgen             G_TEXTURE_GEN and hilite formulas
skeleton           actor skeleton parser
anim_code_table    Table-13 animation code (which clip a Kong plays)
animation_labels   derived clip labels with evidence
control_states     player control-state, input and action tables
animation_census   Table-11 animation census (skeleton compatibility)
animation_reader   Table-11 animation asset reader
adjustment         animation adjustment records
animation_timeline animation cursor/time helpers
bone_matrix        bone local matrices and the quarter-wave table
pose_reconstruct   direct-loop pose reconstruction
pose_compose       hierarchy composition
pose_gltf          pose to glTF TRS conversion
root_completion    completed root reconstruction (Entry 4)
entry4_timing      Entry-4 playback timing
entry4_preview     Entry-4 reference clip preview
entry4_retime      Entry-4 retiming
names              actor model and map names (Randomizer / decomp lists)
texture_bank       pointer-table access, table-25 texture bank, texture decoders
mesh_decoder       generic F3DEX2 static decoder for actors, props and maps
audio_rom          sound banks (mode-2 LZSS, ALBankFile), songs, VADPCM
audio_sequence     compressed MIDI (CSeq) decoder (from JFG Forge)
audio_render       song and sound-effect rendering (from JFG Forge)
audio_reverb       the game's effect buses and stereo reverb
audio_export       WAV/MP3 writing (from JFG Forge)
audio_names        song and sound-effect names
"""
