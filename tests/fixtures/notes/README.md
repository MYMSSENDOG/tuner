# Note bank (stitched-sequence source material)

One trimmed clip per chromatic note per instrument (violin G3-A5,
cello C2-A3, flute C4-C6, trumpet G3-C5, oboe F4-F5 for the Tchaikovsky
excerpt), from the University of Iowa MIS collection. `bank.json` carries each clip's per-window pitch timeline
(offline annotator), so sequences stitched from these clips inherit exact,
vibrato-aware ground truth with no annotation cost at test time.

- Build/rebuild: download raw notes (see tuner.tools.build_note_bank
  docstring for layout), then
  `python -m tuner.tools.build_note_bank <raw_dir> tests/fixtures/notes`
- Consume: `tests/sequence_bank.py` (loader, stitcher, scale/arpeggio/
  chromatic/melody patterns); `tests/integration/test_sequences.py` grades
  the app against every pattern per instrument.
- Each note's first 0.3s is unlabeled in ground truth: real attacks can
  transiently sound another pitch (flute register transitions pass through
  the octave below), so no single frequency is honest there.
