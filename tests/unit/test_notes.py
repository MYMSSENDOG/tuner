import math

import pytest

from tuner.app.meter_model import needle_angle_deg, zone_for_cents
from tuner.core.notes import freq_to_note, note_to_freq


class TestNoteToFreq:
    def test_a4_reference(self):
        assert note_to_freq("A", 4) == pytest.approx(440.0)
        assert note_to_freq("A", 4, a4_hz=442.0) == pytest.approx(442.0)

    def test_known_pitches_440(self):
        assert note_to_freq("C", 4) == pytest.approx(261.6256, abs=1e-3)
        assert note_to_freq("E", 5) == pytest.approx(659.2551, abs=1e-3)
        assert note_to_freq("G", 3) == pytest.approx(195.9977, abs=1e-3)  # violin G string
        assert note_to_freq("D", 6) == pytest.approx(1174.659, abs=1e-2)

    def test_octave_doubles(self):
        assert note_to_freq("D", 5) == pytest.approx(2 * note_to_freq("D", 4))

    def test_a4_scaling(self):
        # every pitch scales linearly with the A4 reference
        assert note_to_freq("C", 4, a4_hz=442.0) == pytest.approx(
            note_to_freq("C", 4) * 442.0 / 440.0
        )


class TestFreqToNote:
    def test_exact_pitch(self):
        note = freq_to_note(440.0)
        assert (note.name, note.octave) == ("A", 4)
        assert note.cents == pytest.approx(0.0, abs=1e-9)

    def test_roundtrip_all_notes(self):
        for a4 in (440.0, 442.0, 415.0):
            for octave in range(2, 8):
                for name in ("C", "D#", "F#", "A", "B"):
                    freq = note_to_freq(name, octave, a4_hz=a4)
                    note = freq_to_note(freq, a4_hz=a4)
                    assert (note.name, note.octave) == (name, octave)
                    assert note.cents == pytest.approx(0.0, abs=1e-6)

    def test_cents_deviation(self):
        # 10 cents sharp of A4
        freq = 440.0 * 2 ** (10 / 1200)
        note = freq_to_note(freq)
        assert note.name == "A"
        assert note.cents == pytest.approx(10.0, abs=1e-9)

    def test_boundary_between_notes(self):
        # slightly beyond +50 cents from A4 -> classified as A#4, slightly flat
        freq = 440.0 * 2 ** (50.5 / 1200)
        note = freq_to_note(freq)
        assert (note.name, note.octave) == ("A#", 4)
        assert note.cents == pytest.approx(-49.5, abs=1e-9)

    def test_a4_reference_changes_classification(self):
        # 441 Hz: nearly exact A under a4=442, ~+4 cents under a4=440
        under_440 = freq_to_note(441.0, a4_hz=440.0)
        under_442 = freq_to_note(441.0, a4_hz=442.0)
        assert under_440.cents == pytest.approx(1200 * math.log2(441 / 440), abs=1e-9)
        assert under_442.cents == pytest.approx(1200 * math.log2(441 / 442), abs=1e-9)

    def test_octave_naming(self):
        assert freq_to_note(261.6256).label == "C4"
        assert freq_to_note(1174.659).label == "D6"

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            freq_to_note(0.0)


class TestMeterModel:
    def test_zone_boundaries(self):
        assert zone_for_cents(0.0) == "green"
        assert zone_for_cents(8.0) == "green"
        assert zone_for_cents(-8.0) == "green"
        assert zone_for_cents(8.01) == "orange"
        assert zone_for_cents(15.0) == "orange"
        assert zone_for_cents(-15.0) == "orange"
        assert zone_for_cents(15.01) == "red"
        assert zone_for_cents(-49.0) == "red"

    def test_needle_angle(self):
        assert needle_angle_deg(0.0) == 0.0
        assert needle_angle_deg(50.0) == 50.0
        assert needle_angle_deg(-50.0) == -50.0
        assert needle_angle_deg(25.0) == pytest.approx(25.0)
        # clamped beyond range
        assert needle_angle_deg(80.0) == 50.0
        assert needle_angle_deg(-80.0) == -50.0
