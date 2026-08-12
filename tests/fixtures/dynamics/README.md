# Dynamics fixtures (TinySOL pp/mf/ff trios)

15 notes x 3 dynamics from **TinySOL** (IRCAM, CC-BY-4.0,
https://zenodo.org/records/3659365) — flute, oboe, violin.

The point of this set is the *relative level*: each trio is scaled by one
shared gain (its loudest take), so pp stays genuinely quiet (measured
-26..-41dBFS, brushing the input gate) instead of being normalized into a
loud clip. Tests assert quiet playing is readable and lands on the labelled
note; the pp-vs-ff pitch spread itself is instrument physics (flutes go
flat when soft) and is reported, not asserted.

Regenerate:

```
python -m tuner.tools.import_tinysol <extracted> TinySOL_metadata.csv \
    tests/fixtures/dynamics --dynamics-sets 5
```

Consumed by `tests/dsp/test_dynamics_robustness.py`.
