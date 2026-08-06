# External fixtures (TinySOL subset)

Isolated notes from **TinySOL** (Cella, Ghisi, Lostanlen et al., IRCAM),
distributed under **CC-BY-4.0**: https://zenodo.org/records/3659365

These exist for one reason: their pitch labels were assigned by someone
else. Every other real-audio test in this repo grades against annotations
this codebase produced, so a shared mistake between the app and the
annotator could pass unnoticed. Here the label is the authority.

- `labels.json` — clip -> instrument, note name, MIDI number, dynamics, and
  the original path inside TinySOL
- 8 instruments x 12 notes spread across each instrument's range, `mf`,
  ordinario, mono FLAC trimmed to 2s
- clips that TinySOL marks as `Resampled` (a pitch that was never actually
  played, produced by transposing another recording) are excluded

Re-import a different subset:

```
python -m tuner.tools.import_tinysol <extracted TinySOL dir> \
    TinySOL_metadata.csv tests/fixtures/external --per-instrument 12
```

Consumed by `tests/dsp/test_external_dataset.py`.
