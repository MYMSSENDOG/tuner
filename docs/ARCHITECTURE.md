# 프로젝트 구조

## 레이어

```
┌─────────────────────────────────────────────────┐
│ app/        UI·조립 (PySide6)                     │
│   main_window.py   창, 컨트롤(A4/장치/detector/핀) │
│   meter_widget.py  미터 렌더링 (Qt 페인팅)          │
│   meter_model.py   미터 계산 (색상·바늘각, 무-Qt)    │
│   engine.py        audio→core 파이프라인 조립(무-Qt) │
├──────────────────────┬──────────────────────────┤
│ audio/  입력 경계      │ analysis/  오프라인 분석    │
│   input.py Protocol  │   reference.py 주석기      │
│   sounddevice_input  │       (테스트 전용 소비)     │
├──────────────────────┴──────────────────────────┤
│ core/       순수 DSP — 상위 레이어를 모름            │
│   pitch.py     YIN 검출 (프레임 → Hz+confidence)   │
│   spectral.py  HPS+DTFT 검출 (제2의 독립 추정기)    │
│   detector.py  PitchDetector 인터페이스 + 구현 선택  │
│   tracker.py   시간축 표시 정책 (스무딩·이상치)       │
│   notes.py     Hz ↔ 음이름/cent (A4 파라미터화)     │
└─────────────────────────────────────────────────┘
tools/   개발용 CLI (annotate, add_noise) — analysis 소비
```

**의존 규칙**: 화살표는 항상 아래로만. `core`는 아무것도 import하지 않고,
`app`은 `core`+`audio`를, `analysis`는 `core`만, `tools`는 `analysis`를 쓴다.
순환·역류 금지가 이 코드베이스의 제1원칙.

## 데이터 흐름 (실시간)

```
마이크 → sounddevice 콜백(256샘플 블록, 오디오 스레드)
      → engine: 링버퍼 축적, detector.hop_size 마다
      → detector.detect(frame) → PitchResult(Hz, confidence)
      → tracker.update() → TrackedPitch(표시값, OK|NOISY|SILENT)
      → notes.freq_to_note(A4 기준) → TunerReading
      → Qt signal (queued) → GUI 스레드 → meter_widget
```

## 핵심 설계 결정

- **검출 vs 표시 정책 분리**: `pitch.py`는 프레임마다 raw 출력(민감도 최대),
  `tracker.py`가 정책 담당 — 작은 변화 즉시 통과(글리산도), 큰 점프는 2프레임
  확인(옥타브 글리치 차단), 짧은 dropout hold. 민감도/안정성 트레이드오프가
  전부 순수 코드라 수치 테스트 가능.
- **detector 인터페이스**: 각 구현이 `frame_size`/`hop_size`를 스스로 선언,
  엔진은 그에 맞춰 검출 주기만 조절. YIN(기본, 최속 반응) / Spectral(정밀,
  느린 반응) 을 UI에서 실행 중 교체 가능.
- **플랫폼 분기 없음**: PortAudio(sounddevice)와 Qt가 Win/Mac 차이를 전부
  흡수. `AudioInput` Protocol은 만일의 교체 지점일 뿐, OS별 구현체를 미리
  만들지 않는다.
- **주석기의 독립성**: `analysis/reference.py`는 앱 기본 경로(YIN)와 다른
  알고리즘(HPS + 배음별 연속 DTFT 최우추정)을 비인과 장구간 창으로 돌린다.
  실오디오 테스트가 자기 검증 순환이 아닌 두 독립 추정기의 교차 검증이 되는
  이유. 합성 신호 기준 안정 피치 오차 0.0001 cent 수준으로 자체 검증됨.

## 테스트 구조

```
tests/
├── conftest.py      Qt offscreen 기본값, 공용 qapp fixture
├── synth.py         신호 합성기 — ground truth를 정확히 아는 테스트 신호
├── helpers.py       측정 헬퍼 (cent 오차, track_signal 등)
├── fakes.py         FakeAudioInput (호출 기록 + 블록 주입)
├── unit/            순수 로직: notes, tracker, 합성기 자체검증
├── dsp/             DSP 품질: 정확도(±2¢), 노이즈 내성, 반응성(≤100ms),
│                    detector 계약, 주석기 정밀도(sub-cent)
├── integration/     조립: engine 파이프라인, UI offscreen(장치 전환 포함),
│                    실오디오 vs 주석 비교
├── e2e/             실기기 전용(없으면 자체 skip): 실제 장치 열거/캡처/핫스왑,
│                    always-on-top 실제 스태킹(서브프로세스 프로브)
└── fixtures/audio/  Iowa MIS 실악기 샘플 + .ref.json + 노이즈 변형
```

Win/Mac 테스트는 분리하지 않는다 — 코드가 플랫폼 공용이므로 같은 스위트를
CI 매트릭스(macos/windows, `.github/workflows/test.yml`)에서 실행하고,
하드웨어가 필요한 e2e는 환경 기준으로 자체 skip한다. OS 특이 케이스가
생기면 그때 `skipif(sys.platform...)` 마커로 처리.

## 확장 포인트

- **새 검출 알고리즘**: `core/`에 구현 + `detector.py`에 `PitchDetector`
  충족 클래스 추가(`name`, `frame_size`, `hop_size`, `detect`) 후 `DETECTORS`
  에 등록 — UI 콤보에 자동 노출. `tests/dsp/test_detector.py`가 계약
  (정확도·실시간 예산)을 자동 검증.
- **새 실오디오 테스트**: 오디오 파일을 `tests/fixtures/audio/`에 넣으면
  자동 수집. `python -m tuner.tools.annotate <file>`로 주석 생성(없으면 즉석
  생성), `python -m tuner.tools.add_noise <file> --snr 20`으로 노이즈 변형.
  파일명에 `.snr`이 들어가면 완화된 노이즈 기준으로 채점.
- **다른 OS 오디오 백엔드가 필요해지면**: `audio/input.py`의 `AudioInput`
  Protocol을 충족하는 구현을 추가하고 `__main__.py`에서 선택.
