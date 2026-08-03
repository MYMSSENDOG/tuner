# Real-audio test fixtures

Source: University of Iowa Electronic Music Studios — Musical Instrument
Samples (https://theremin.music.uiowa.edu/MIS.html), freely available for
any projects. Individual notes are from the 2012/2014 collection; the
`violin_scale_G3B3` file is a chromatic scale from the original collection.

Preparation:
- individual-note files were trimmed to their main sustained stroke
  (inter-stroke resonance tails ring at other pitches — open-string
  sympathetics — and are not tuning ground truth)
- `*.ref.json` generated with `python -m tuner.tools.annotate <file>`
- `*.snr20.wav` noisy variants generated with
  `python -m tuner.tools.add_noise <file> --snr 20`; their `.ref.json` is
  copied from the clean file's annotation

`tests/test_real_audio.py` picks up any audio file dropped here
automatically (wav/flac/ogg/aif/aiff), annotating on the fly when no
`.ref.json` exists. Files with `.snr` in the name are graded with the
looser noisy criteria.
