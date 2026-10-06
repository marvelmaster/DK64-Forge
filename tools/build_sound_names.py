"""Match a user-supplied community WAV archive to the supported ROM, without extraction.

Run from the repository root: python -m tools.build_sound_names ROM ARCHIVE OUTPUT
Only PCM-identical, unambiguously named samples enter the catalog. No audio is saved.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
import wave
import zipfile

from dk64_forge.core.audio_rom import decode_adpcm, load_audio
from dk64_forge.core.rom_model import identify_rom

SOURCE_PAGE = "https://sounds.spriters-resource.com/nintendo_64/donkeykong64/asset/402582/"
ARCHIVE_URL = "https://files.catbox.moe/dobjw9.zip"
# These source filenames are jokes, an empty description, or an unsupported actor-credit claim.
REJECTED = ("cumsplat", "succ", "nothing", "kroolfuckyou", "arcadedkfuckingdies",
            "dkbutactuallygrantkirkhope")
PREFIXES = {
    "narrator": "Narrator", "kremling": "Kremling", "chunky": "Chunky",
    "diddy": "Diddy", "lanky": "Lanky", "tiny": "Tiny", "krool": "K. Rool",
    "klumsy": "K. Lumsy", "dk": "DK", "cranky": "Cranky", "funky": "Funky",
    "wrinkly": "Wrinkly", "madjack": "Mad Jack", "kutout": "Kut Out",
    "ladybug": "Ladybug", "rambi": "Rambi", "rabbit": "Rabbit",
    "cartoon": "Cartoon", "machine": "Machine", "wood": "Wood", "metal": "Metal",
    "water": "Water", "gun": "Gun", "door": "Door", "stock": "Stock",
    "beast": "Beast", "demon": "Demon", "squaks": "Squaks", "helmet": "Helmet",
    "scifi": "Sci-fi", "air": "Air", "arcade": "Arcade", "crowd": "Crowd",
    "aztec": "Aztec", "faerie": "Faerie", "bat": "Bat", "mouse": "Mouse",
}
WORDS = {"welcometobonusstage": "Welcome to bonus stage", "welldone": "Well done",
         "intheredcorner": "In the red corner", "inthebluecorner": "In the blue corner",
         "semifinalbattle": "Semifinal battle", "getout": "Get out", "feedme": "Feed me"}


def display_name(stem: str) -> str:
    """Only format source wording; do not infer an action, speaker, or title."""
    prefix = next((p for p in sorted(PREFIXES, key=len, reverse=True) if stem.startswith(p)), None)
    rest = stem[len(prefix):] if prefix else stem
    rest = WORDS.get(rest, re.sub(r"(\D)(\d+)$", r"\1 \2", rest))
    return f"{PREFIXES[prefix]} · {rest}" if prefix else rest[:1].upper() + rest[1:]


def build(rom_path: Path, archive_path: Path) -> dict:
    rom, _ = identify_rom(rom_path)
    audio = load_audio(rom)
    samples = defaultdict(list)
    for index, sound in enumerate(audio.sfx_bank.instruments[0].sounds):
        pcm = decode_adpcm(audio.sfx_bank.samples, sound.wave).astype("<i2").tobytes()
        samples[hashlib.sha256(pcm).hexdigest()].append(index)
    matches = defaultdict(list)
    unmatched = []
    with zipfile.ZipFile(archive_path) as archive:
        for member in sorted(archive.namelist()):
            if not member.lower().endswith(".wav"):
                continue
            with wave.open(io.BytesIO(archive.read(member))) as handle:
                if (handle.getnchannels(), handle.getsampwidth(), handle.getcomptype()) != (1, 2, "NONE"):
                    raise ValueError(f"Unsupported WAV encoding: {member}")
                pcm = handle.readframes(handle.getnframes())
                item = {"member": member, "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                        "sample_count": handle.getnframes(), "wav_rate": handle.getframerate()}
            if item["pcm_sha256"] not in samples:
                unmatched.append(member)
            else:
                matches[item["pcm_sha256"]].append(item)
    entries, rejected = [], []
    for fingerprint, members in sorted(matches.items()):
        ids = [index + 1 for index in samples[fingerprint]]
        if len(members) != 1:
            rejected.append({"sound_ids": ids, "reason": "multiple archive names for identical PCM",
                             "members": [m["member"] for m in members]})
            continue
        item = members[0]
        stem = Path(item["member"]).stem
        if stem.startswith(REJECTED):
            rejected.append({"sound_ids": ids, "reason": "unreliable source wording",
                             "members": [item["member"]]})
            continue
        for sound_id in ids:
            entries.append({"sound_id": sound_id, "bank_index": sound_id - 1,
                            "label": display_name(stem), "source_label": stem, **item})
    return {"schema": 1, "source_page": SOURCE_PAGE, "archive_url": ARCHIVE_URL,
            "source_comment_date": "2024-10-17", "confidence": "community description; PCM identity verified",
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "rom_sha256": hashlib.sha256(rom).hexdigest(),
            "bank_entries": 1126, "matched_wavs": sum(map(len, matches.values())),
            "unique_named_samples": len({e["pcm_sha256"] for e in entries}),
            "entries": sorted(entries, key=lambda e: e["sound_id"]),
            "rejected": rejected, "unmatched": unmatched}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = build(args.rom, args.archive)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(result['entries'])} named IDs; {result['unique_named_samples']} unique named samples; "
          f"{result['matched_wavs']} matched WAVs; {len(result['unmatched'])} unmatched")
