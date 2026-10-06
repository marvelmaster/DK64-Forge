"""Regression checks: python -m unittest tools.test_sound_names"""
import hashlib
import io
import json
from pathlib import Path
import unittest
import wave
import zipfile

from dk64_forge.core import audio_names
from tools.build_sound_names import display_name, REJECTED

ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "local/roms/dk64_us.n64"
ARCHIVE = ROOT / "local_output/sound-research/community.zip"
CATALOG = json.loads((ROOT / "dk64_forge/data/sound_names.json").read_text(encoding="utf-8"))


class CatalogTests(unittest.TestCase):
    def test_ids_provenance_and_exclusions(self):
        entries = CATALOG["entries"]
        self.assertEqual(len(entries), 912)
        self.assertEqual(len({e["sound_id"] for e in entries}), len(entries))
        self.assertEqual(len({e["pcm_sha256"] for e in entries}), 809)
        rejected_ids = {i for group in CATALOG["rejected"] for i in group["sound_ids"]}
        for entry in entries:
            self.assertNotIn(entry["sound_id"], rejected_ids)
            self.assertEqual(entry["bank_index"], entry["sound_id"] - 1)
            self.assertTrue(1 <= entry["sound_id"] <= 1126)
            self.assertFalse(entry["source_label"].startswith(REJECTED))
            self.assertEqual(entry["label"], display_name(entry["source_label"]))
            self.assertIn("not independently verified", audio_names.sfx_note(entry["sound_id"]))

    def test_known_anchor_names_and_unknowns(self):
        self.assertEqual(audio_names.sfx_label(0), "Silence")
        self.assertEqual(audio_names.sfx_label(572), "Okay")
        self.assertEqual(audio_names.SFX_ARCHIVE_LABELS[572]["source_label"], "dkokay")
        self.assertEqual(audio_names.SFX_ARCHIVE_LABELS[418]["source_label"], "aztecgetout")
        self.assertEqual(sum(bool(audio_names.sfx_label(i)) for i in range(1, 1127)), 920)
        self.assertEqual(audio_names.sfx_label(2000), "")

    @unittest.skipUnless(ARCHIVE.is_file(), "local source archive unavailable")
    def test_archive_pcm_fingerprints(self):
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), CATALOG["archive_sha256"])
        with zipfile.ZipFile(ARCHIVE) as archive:
            for entry in CATALOG["entries"]:
                with wave.open(io.BytesIO(archive.read(entry["member"]))) as handle:
                    pcm = handle.readframes(handle.getnframes())
                    self.assertEqual(handle.getnframes(), entry["sample_count"])
                    self.assertEqual(hashlib.sha256(pcm).hexdigest(), entry["pcm_sha256"])


@unittest.skipUnless(ROM.is_file(), "local supported ROM unavailable")
class RomIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dk64_forge.core.audio_rom import load_audio
        from dk64_forge.core.rom_model import identify_rom
        rom, _ = identify_rom(ROM)
        cls.rom_hash = hashlib.sha256(rom).hexdigest()
        cls.audio = load_audio(rom)

    def test_every_catalog_entry_is_bit_exact(self):
        from dk64_forge.core.audio_rom import decode_adpcm
        self.assertEqual(self.rom_hash, CATALOG["rom_sha256"])
        sounds = self.audio.sfx_bank.instruments[0].sounds
        for entry in CATALOG["entries"]:
            pcm = decode_adpcm(self.audio.sfx_bank.samples, sounds[entry["bank_index"]].wave)
            self.assertEqual(len(pcm), entry["sample_count"])
            self.assertEqual(hashlib.sha256(pcm.astype("<i2").tobytes()).hexdigest(), entry["pcm_sha256"])

    def test_game_ids_resolve_first_last_and_anchor_samples(self):
        from dk64_forge.core.audio_render import AudioEngine
        engine = AudioEngine(self.audio)
        sounds = self.audio.sfx_bank.instruments[0].sounds
        self.assertEqual(len(self.audio.effects), 1127)
        self.assertIsNone(engine.render_effect(0))
        for sound_id in (1, 65, 418, 572, 1126):
            effect = self.audio.effects[sound_id]
            self.assertEqual(effect.sound_id, sound_id)
            self.assertIs(engine.effect_sound(effect), sounds[sound_id - 1])
            self.assertIsNotNone(engine.render_effect(sound_id))


if __name__ == "__main__":
    unittest.main()
