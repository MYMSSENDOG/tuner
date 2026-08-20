# Tuner

크로스 플랫폼(Windows/macOS) 크로마틱 튜너. 실시간 피치 검출(YIN),
±50 cent 아날로그 미터, A4 기준 조절(415~466Hz), 입력 장치 선택,
always-on-top.

## 실행

```bash
pip install -e '.[dev]'
python -m tuner
```

첫 실행 시 마이크 권한을 허용해야 한다.

리포트 두 가지 (둘 다 `~/.tuner/reports/<시각>/` 에 오디오 + 그때 화면에
표시된 값이 함께 저장된다):

- **Ctrl+R** — 방금 이상했다면 누른다. **직전 10초**가 소급 저장된다.
- **● 기록 버튼 (Ctrl+L)** — 작정하고 실험할 때. 누른 순간부터 다시 누를
  때까지 **한 프레임도 빠짐없이** 기록된다 (최대 10분).

저장된 리포트는:

```bash
python -m tuner.tools.trace <리포트>/trace.jsonl --explain  # 왜 그렇게 표시됐나
python -m tuner.tools.promote <리포트>                      # 재현되나 / 픽스처로
```

## 테스트

```bash
pytest                                    # 기본 (e2e는 하드웨어 없으면 자체 skip)
pytest -m perf -n0                        # 타이밍 게이트 (직렬로만 유효, ~8s)
pytest -m "not e2e and not crosscheck"    # CI와 동일: 헤드리스 스위트만
pytest -m crosscheck                      # 느린 정답 감사 (pip install -e '.[crosscheck]')
```

기본으로 `-n auto`(pytest-xdist) 로 병렬 실행한다 — 스위트가 녹음 전체를
돌리는 통합 테스트 위주라 12코어에서 12분+ → ~90초. 실시간 예산을 재는
`perf` 테스트만은 워커끼리 CPU 를 나눠 쓰면 측정이 무의미해지므로 병렬
실행 시 **자체 skip** 하고(`tests/conftest.py`), 위 직렬 명령으로 따로 돈다.

테스트 스위트는 합성 신호(정확도 ±2 cent, SNR 10dB 내성, 반응 ≤100ms,
글리산도 추종)와 실악기 녹음(Iowa MIS 샘플을 오프라인 고정밀 주석기로
라벨링) 양쪽으로 파이프라인을 검증한다.

스위트가 재는 값(정확도 분위수, 지터, 반응 ms, 표시 세그먼트 …)은 런마다
`metrics/runs/` 에 기록된다 — 게이트는 통과하는데 값만 나빠지는 드리프트를
`python -m tuner.tools.scoreboard` 로 본다.

## 도구

```bash
python -m tuner.tools.demo 오디오.wav --loop       # 파일 들으며 튜너 동작 관찰
python -m tuner.tools.compare 오디오.wav --loop    # 같은 음원, 파라미터 변형 8개(4x2) 나란히 비교
python -m tuner.tools.trace 오디오.wav              # 표시 트레이스 + 요약(세그먼트/짧은 표시)
python -m tuner.tools.trace 오디오.wav --vs HEAD~1  # 그 리비전과 표시가 어디서 달라졌는지
python -m tuner.tools.trace 트레이스.jsonl --explain # 검출/표시/이름이 어긋난 순간 분류
python -m tuner.tools.scoreboard                   # 스위트가 잰 값, 런끼리 비교 (--vs/--check)
python -m tuner.tools.promote 리포트/                # 현장 리포트: 재현 확인 후 픽스처로 승격
python -m tuner.tools.annotate 녹음.wav -w 0.05    # 주파수 주석(.ref.json) 생성
python -m tuner.tools.add_noise 녹음.wav --snr 20  # 노이즈/간섭 변형 픽스처 생성
python tests/render_sequence.py oboe tchaik4 t.wav # 뱅크에서 시퀀스 wav 렌더
```

## 문서 안내

**하려는 작업을 시작하기 전에** 해당 문서를 먼저 읽는다:

| 하려는 것 | 먼저 읽기 |
|---|---|
| 새 알고리즘/기능 영역 진입, 새 테스트 데이터 확보 | [정답 데이터 먼저](docs/process/ground-truth.md) |
| 피처 개발 시작/마무리, 버그 수정, 판정 기준·파라미터 조정 | [회귀 봉인](docs/process/regression.md) |
| 체감 품질(부드러움·반응·깜빡임 등) 조정 | [주관적 품질 작업](docs/process/subjective-ux.md) |
| 개발 인프라(측정 기록·전후 비교·현장 캡처) 손보기 | [개발 루프 도구](docs/dev-loop.md) |
| 모듈·구현체 추가, 구조 변경, 추상화 도입 판단 | [프로젝트 구조 + 구조 규칙](docs/ARCHITECTURE.md) |
| 설계·알고리즘 접근 선택 (과거 기각된 시도 확인) | [기각·결정 인덱스](docs/decisions/INDEX.md) — 관련 항목만 열기 |
| 검출 로직 이해·수정 (게이트/YIN/옥타브/스무딩/래치) | [음정 측정 파이프라인](docs/pitch-pipeline.md) |
| 테스트 정답의 출처·신뢰 근거 확인 | [pitch-pipeline "정답의 신뢰 구조"](docs/pitch-pipeline.md) |
| 스무딩 값의 결정 근거 확인·재튜닝 | [스무딩 튜닝 기록](docs/smoothing-tuning.md) |
| 음이름 래치(유지·cent 상한) 값의 결정 근거 확인·재튜닝 | [음이름 래치 튜닝 기록](docs/note-latch-tuning.md) |
| 실악기 픽스처 추가 | [fixtures/audio](tests/fixtures/audio/README.md), [notes 뱅크](tests/fixtures/notes/README.md), [외부 라벨](tests/fixtures/external/README.md), [셈여림](tests/fixtures/dynamics/README.md) |
