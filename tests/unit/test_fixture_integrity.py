"""Committed fixture data must be internally consistent.

A ref.json in this repo was once corrupted by a stray character and sat
undetected until an unrelated test happened to parse it. These checks are
cheap (no audio decoding) and make any corruption or drift — annotations
without audio, bank entries without clips, malformed JSON — fail loudly and
name the file.
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"
AUDIO_SUFFIXES = (".wav", ".flac", ".ogg", ".aif", ".aiff")


def audio_files(directory: Path) -> set[Path]:
    return {p for p in directory.glob("*") if p.suffix.lower() in AUDIO_SUFFIXES}


def test_every_ref_json_is_wellformed_and_has_audio():
    audio_dir = FIXTURES / "audio"
    refs = sorted(audio_dir.glob("*.ref.json"))
    assert refs, "no annotations found — wrong directory?"
    stems_with_audio = {p.name[: -len(p.suffix)] for p in audio_files(audio_dir)}
    for ref in refs:
        data = json.loads(ref.read_text())  # malformed JSON fails right here
        assert data["window_s"] > 0, ref.name
        assert data["windows"], f"{ref.name}: empty annotation"
        for window in data["windows"]:
            freq = window["freq_hz"]
            assert freq is None or 20.0 < freq < 5000.0, f"{ref.name}: freq {freq}"
        stem = ref.name[: -len(".ref.json")]
        assert stem in stems_with_audio, f"{ref.name}: annotation without its audio file"


def test_note_bank_manifest_matches_files():
    bank = FIXTURES / "notes"
    manifest_path = bank / "bank.json"
    if not manifest_path.exists():
        pytest.skip("note bank not built")
    manifest = json.loads(manifest_path.read_text())
    on_disk = {f"{p.parent.name}/{p.stem}" for p in bank.glob("*/*.flac")}
    in_manifest = {f"{i}/{n}" for i, notes in manifest.items() for n in notes}
    assert in_manifest == on_disk, (
        f"manifest-only: {sorted(in_manifest - on_disk)}, "
        f"disk-only: {sorted(on_disk - in_manifest)}"
    )
    for instrument, notes in manifest.items():
        for note, info in notes.items():
            assert 20.0 < info["freq_hz"] < 5000.0, f"{instrument}/{note}"
            assert info["windows"], f"{instrument}/{note}: no window timeline"


def test_external_labels_match_files():
    external = FIXTURES / "external"
    labels_path = external / "labels.json"
    if not labels_path.exists():
        pytest.skip("external fixtures not imported")
    labels = json.loads(labels_path.read_text())
    on_disk = {p.name for p in audio_files(external)}
    assert set(labels) == on_disk, (
        f"labelled-only: {sorted(set(labels) - on_disk)}, "
        f"unlabelled: {sorted(on_disk - set(labels))}"
    )
    for clip, label in labels.items():
        assert 0 <= label["midi"] <= 127, clip
        assert label["pitch"], clip
