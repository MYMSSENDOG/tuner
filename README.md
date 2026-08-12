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

## 테스트

```bash
pytest                                    # 기본 (e2e는 하드웨어 없으면 자체 skip)
pytest -m "not e2e and not crosscheck"    # CI와 동일: 헤드리스 스위트만
pytest -m crosscheck                      # 느린 정답 감사 (pip install -e '.[crosscheck]')
```

테스트 스위트는 합성 신호(정확도 ±2 cent, SNR 10dB 내성, 반응 ≤100ms,
글리산도 추종)와 실악기 녹음(Iowa MIS 샘플을 오프라인 고정밀 주석기로
라벨링) 양쪽으로 파이프라인을 검증한다.

## 도구

```bash
python -m tuner.tools.demo 오디오.wav --loop       # 파일 들으며 튜너 동작 관찰
python -m tuner.tools.compare 오디오.wav --loop    # 같은 음원, 파라미터 변형 4개 나란히 비교
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
| 모듈·구현체 추가, 구조 변경, 추상화 도입 판단 | [프로젝트 구조 + 구조 규칙](docs/ARCHITECTURE.md) |
| 검출 로직 이해·수정 (게이트/YIN/옥타브/스무딩/래치) | [음정 측정 파이프라인](docs/pitch-pipeline.md) |
| 테스트 정답의 출처·신뢰 근거 확인 | [pitch-pipeline "정답의 신뢰 구조"](docs/pitch-pipeline.md) |
| 스무딩 값의 결정 근거 확인·재튜닝 | [스무딩 튜닝 기록](docs/smoothing-tuning.md) |
| 실악기 픽스처 추가 | [fixtures/audio](tests/fixtures/audio/README.md), [notes 뱅크](tests/fixtures/notes/README.md), [외부 라벨](tests/fixtures/external/README.md) |
