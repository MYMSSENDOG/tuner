# Tuner App 계획서

크로스 플랫폼(Windows/macOS) 데스크톱 크로마틱 튜너.

## 1. 기능 스펙

- **기본 튜닝**: 마이크 입력 → 실시간 피치 검출 → 가장 가까운 음이름 + cent 편차 표시
- **A4 기준 주파수 조절**: 440 / 442 등 (415~466 범위, 1Hz 단위)
- **항상 위에 표시** (always-on-top) 토글
- **입력 장비 선택**: 시스템 오디오 입력 장치 목록에서 선택, 핫스왑 가능
- **미터 UI** (`.venv/img.png` 참고):
  - -50 ~ +50 cent 아날로그 미터 (크로마틱 반음 = 100 cent, 미터는 반음 중심 ±50)
  - 검출 음이름(예: D), 옥타브 포함 표기(D6), 실측 주파수(Hz), cent 수치 실시간 표시
  - 색상: |cent| ≤ 8 → 초록, ≤ 15 → 주황, 그 외 → 빨강
  - 신호가 약하거나 잡음 판정이면 NOISY/무신호 상태 표시

## 2. 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.13 (기존 .venv) | DSP 프로토타이핑·테스트 작성이 압도적으로 빠름 |
| 오디오 입력 | `sounddevice` (PortAudio) | **Win/Mac 단일 구현** — 장치 열거/선택/스트리밍 모두 동일 API |
| DSP | `numpy` (+ `scipy` 필요 시) | 피치 검출 직접 구현 |
| UI | `PySide6` (Qt) | 크로스 플랫폼, `WindowStaysOnTopHint`로 always-on-top 단일 구현, 커스텀 미터 위젯 그리기 용이 |
| 테스트 | `pytest` | 합성 신호 fixture 기반 |
| 배포(후순위) | PyInstaller | Win/Mac 각각 번들 |

**플랫폼 분기 관련**: PortAudio와 Qt가 OS 차이를 전부 흡수하므로 **별도 Win/Mac 구현체는 원칙적으로 불필요**. 다만 만일에 대비해 오디오 입력을 `AudioInput` 인터페이스(Protocol) 뒤에 두어, 특정 OS에서 문제가 생기면 그 구현만 교체 가능하게 한다. OS별 코드가 실제로 필요해지기 전에는 platform 모듈을 만들지 않는다 (YAGNI).

## 3. 아키텍처

핵심 원칙: **DSP 코어는 순수 함수 — 오디오 장치/UI/스레드에 대한 의존 없음.** 테스트가 코어를 직접, 결정론적으로 두들길 수 있어야 한다.

```
tuner/
├── pyproject.toml
├── src/tuner/
│   ├── core/                  # 순수 DSP. numpy in → dataclass out. I/O 없음
│   │   ├── pitch.py           #   피치 검출기 (프레임 → Hz | None)
│   │   ├── tracker.py         #   시간축 스무딩/이상치 제거 (민감도 vs 안정성 정책)
│   │   └── notes.py           #   Hz ↔ (음이름, 옥타브, cent) 변환. A4 기준 파라미터화
│   ├── audio/
│   │   ├── input.py           #   AudioInput Protocol: 장치 목록, 스트림 열기, 프레임 콜백
│   │   └── sounddevice_input.py  # PortAudio 구현 (유일한 구현이길 기대)
│   ├── app/
│   │   ├── engine.py          #   audio → core 파이프라인 조립, UI로 TunerReading 발행
│   │   ├── main_window.py     #   메인 창, always-on-top, 설정(A4, 장치)
│   │   └── meter_widget.py    #   ±50 cent 미터 (커스텀 페인팅, 색상 규칙)
│   └── __main__.py
└── tests/
    ├── synth.py               # 테스트 신호 합성기 (아래 4절)
    ├── test_notes.py
    ├── test_pitch_accuracy.py
    ├── test_noise_robustness.py
    ├── test_responsiveness.py
    └── test_tracker.py
```

데이터 흐름: `sounddevice 콜백 → ring buffer → engine (worker) → pitch.detect → tracker.update → notes.to_reading → Qt signal → UI`

`TunerReading` = `{ freq_hz, note_name, octave, cents, confidence, state(OK|NOISY|SILENT) }`

### 피치 검출 알고리즘

검출기는 `PitchDetector` 인터페이스(Protocol) 뒤에 두고 UI에서 선택 가능. 각 구현이 자기 frame/hop 크기를 선언하고 엔진은 그에 맞춰 검출 주기를 잡는다.

- **YinDetector (기본)**: CMNDF + parabolic interpolation. 작은 프레임(2048)으로 가장 빠른 반응.
- **SpectralDetector (정밀)**: HPS + 배음별 연속 DTFT 탐색(오프라인 주석기와 동일 알고리즘의 실시간 설정). 안정 피치에서 더 정밀하지만 4096 프레임이라 반응이 느림.

**YIN 계열 (CMNDF + parabolic interpolation)** 을 1차 채택.

- 단순 FFT peak는 저음 해상도가 나쁘고 배음에 속기 쉬워 부적합
- YIN은 단선율 악기에서 정확도/노이즈 내성의 검증된 표준. parabolic interpolation으로 sub-Hz 정밀도 확보
- 창 크기 ~2048–4096 samples @ 44.1kHz + hop 256–512 → cent 단위 정밀도와 <50ms급 반응성 양립
- CMNDF threshold를 confidence로 사용 → NOISY 판정 근거

### 민감도 vs 노이즈 내성 (핵심 트레이드오프)

검출(pitch.py)과 표시 정책(tracker.py)을 분리해서 해결:

- 검출 자체는 프레임마다 raw로 출력 (민감도 최대)
- tracker가 정책 적용: confidence 낮은 프레임 무시, 짧은 이상치(1–2프레임 옥타브 점프) 제거, **작은 변화는 즉시 반영 + 큰 변화는 2~3프레임 연속 확인 후 점프** (글리산도 추종과 노이즈 억제 양립)
- 이 정책이 전부 순수 코드라 4절의 반응성 테스트로 수치 검증 가능

## 4. 테스트 전략 (최우선 순위)

### 4.1 신호 합성기 (`tests/synth.py`)

실녹음 대신 **합성 신호**를 사용 — ground truth 주파수를 정확히 알 수 있어 cent 단위 검증이 가능하다.

- `tone(freq, harmonics_profile, vibrato?, duration)`: 배음 구조로 악기 음색 모사
  - 악기 프로파일: violin(풍부한 배음+비브라토), cello(저역+강한 배음), flute(기음 위주), guitar(감쇠 envelope), voice(포르만트 근사)
- `add_noise(signal, snr_db)`: 화이트/핑크 노이즈 믹스
- `glissando(f_start, f_end, duration)`: 연속 주파수 상승
- `sequence(freqs, note_duration)`: 스케일/아르페지오 (음 사이 미세한 어택/갭 포함)
- 바이올린 스케일 fixture: G3~E7 크로마틱 + 주요 스케일, A4 기준별(440/442) 정밀 주파수 세트

### 4.2 테스트 스위트

| 테스트 | 내용 | 합격 기준(초안) |
|---|---|---|
| **정확도** | 악기 프로파일 × 음역대별 순음/합성음 스케일 | 정상 신호에서 오차 ≤ ±2 cent |
| **노이즈 내성** | SNR 20/10/5dB에서 같은 스케일 | SNR 10dB에서 오차 ≤ ±5 cent, 옥타브 오류 0 |
| **반응성** | 반음 스텝 변화 후 새 음정 수렴까지 시간 | ≤ 100ms (프레임 수로 환산해 검증) |
| **글리산도 추종** | 연속 상승 신호에서 검출값 vs ground truth 곡선 | 추종 지연 ≤ 100ms, 계단 현상 없이 단조 추종 |
| **스케일/아르페지오** | 음 전환 시 각 음을 올바르게, 옥타브 착오 없이 검출 | 음별 판정 정확도 100%, 전환 과도구간 제외 |
| **무신호/순수 노이즈** | 침묵, 노이즈만 | Hz 출력 없이 SILENT/NOISY 판정 |
| **notes 단위** | Hz↔note↔cent 변환, A4=440/442, 경계값(±50 cent) | 수학적 정확성 |
| **색상 규칙** | cent → 색 매핑 경계 (8, 15) | 명세 일치 |

- 정확도/내성/반응성 결과를 pytest에서 **수치 리포트**로 출력해 알고리즘 튜닝 시 회귀 추적
- UI는 로직(색상 매핑, 바늘 각도 계산)만 단위 테스트, 렌더링은 수동 확인

### 4.3 실오디오 파이프라인 (source of truth 생성)

실녹음에는 라벨이 없으므로, **오프라인 고정밀 주석기**가 x초 창 단위로 주파수를 뽑아 source of truth를 만든다.

- 주석기(`analysis/reference.py`)는 앱 기본 경로(YIN)와 **독립된 알고리즘**(HPS + 배음 가중 DTFT 최우추정, 비인과 장구간 창) → 자기 검증 순환 없음. 합성 신호 기준 안정 피치 오차 **0.0001 cent** 수준으로 자체 검증됨(`test_reference.py`).
- CLI: `python -m tuner.tools.annotate 녹음.wav -w 0.05` → `녹음.ref.json`
- `tests/fixtures/audio/` 에 오디오 파일을 넣으면 `test_real_audio.py` 가 자동 수집해 앱 파이프라인과 비교 (`.ref.json` 있으면 사용, 없으면 즉석 주석).
- **시간축 정렬**: 앱 리딩은 프레임 끝에 나오지만 내용은 프레임 중심의 피치이므로, 프레임 중심 시각이 속한 주석 창과 비교. 음 전환 구간(이웃 창 불일치)은 판정 제외. 합격: p95 ≤ 10 cent, 옥타브 오류 0.

## 5. 구현 순서

1. 프로젝트 스캐폴딩 (pyproject, 패키지 구조, pytest 설정)
2. `core/notes.py` + 테스트 — 가장 단순, 즉시 완결
3. `tests/synth.py` 신호 합성기 + 자체 검증 테스트
4. `core/pitch.py` (YIN) → 정확도/노이즈 테스트로 튜닝
5. `core/tracker.py` → 반응성/글리산도/아르페지오 테스트로 튜닝
6. `audio/` sounddevice 입력 + 장치 열거
7. `app/` UI: 미터 위젯 → 메인 창 → A4/장치 설정 → always-on-top
8. 실기기 수동 검증 (macOS 먼저, Windows는 접근 가능할 때)
