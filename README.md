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
pytest                # 전체 (e2e는 하드웨어 없으면 자체 skip)
pytest -m "not e2e"   # CI와 동일: 헤드리스 스위트만
```

테스트 스위트는 합성 신호(정확도 ±2 cent, SNR 10dB 내성, 반응 ≤100ms,
글리산도 추종)와 실악기 녹음(Iowa MIS 샘플을 오프라인 고정밀 주석기로
라벨링) 양쪽으로 파이프라인을 검증한다.

## 도구

```bash
python -m tuner.tools.annotate 녹음.wav -w 0.05    # 주파수 주석(.ref.json) 생성
python -m tuner.tools.add_noise 녹음.wav --snr 20  # 노이즈 변형 픽스처 생성
```

## 문서

- [음정 측정 파이프라인 상세](docs/pitch-pipeline.md) — 마이크부터 바늘까지 전 과정, 단계별 수치 근거
- [프로젝트 구조](docs/ARCHITECTURE.md) — 레이어, 의존 규칙, 설계 결정, 확장 포인트
- [스무딩 파라미터 튜닝 기록](docs/smoothing-tuning.md) — 그리드 스윕 실측과 채택 근거
- [계획서](PLAN.md) — 최초 스펙과 테스트 전략
