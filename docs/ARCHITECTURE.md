# 프로젝트 구조

## 레이어

```
┌─────────────────────────────────────────────────┐
│ app/        UI·조립 (PySide6)                     │
│   main_window.py   창, 컨트롤(A4/장치/detector/핀) │
│   meter_widget.py  미터 렌더링 (Qt 페인팅)          │
│   meter_model.py   미터 계산 (색상·바늘각, 무-Qt)    │
│   engine.py        audio→core 파이프라인 조립(무-Qt) │
│   capture.py       직전 N초 링버퍼 + 리포트 저장      │
├──────────────────────┬──────────────────────────┤
│ audio/  입력 경계      │ analysis/  오프라인 분석    │
│   input.py Protocol  │   reference.py 주석기      │
│   sounddevice_input  │   trace.py 표시 트레이스 기록 │
├──────────────────────┴──────────────────────────┤
│ core/       순수 DSP — 상위 레이어를 모름            │
│   pitch.py     YIN 검출 (프레임 → Hz+confidence)   │
│   spectral.py  HPS+DTFT 검출 (제2의 독립 추정기)    │
│   detector.py  PitchDetector 인터페이스 + 구현 선택  │
│   tracker.py   시간축 표시 정책 (스무딩·이상치)       │
│   notes.py     Hz ↔ 음이름/cent (A4 파라미터화)     │
└─────────────────────────────────────────────────┘
tools/   개발용 CLI — 아래 레이어 전부를 조립해 쓰는 최상위
```

**의존 규칙**: 화살표는 항상 아래로만. `core`는 아무것도 import하지 않고,
`app`은 `core`+`audio`+`analysis`(트레이스 **기록 포맷**만 — 앱과 도구가
같은 파일 형식을 써야 현장 리포트를 오프라인 재생과 비교할 수 있다)를,
`analysis`는 `core`만 쓴다. `tools`는 그 위에
앉아 필요한 것을 아무거나 조립한다 — `annotate`/`build_note_bank`는
`analysis`를, `playback`은 `audio`를, `demo`/`compare`는 `app`(창을 띄우므로)
과 `core`를, `trace`는 `app`의 엔진을 Qt 없이 오프라인으로 돌리고,
`promote`/`scoreboard`는 그 위에서 리포트·측정 기록을 다룬다. 순환·역류 금지가 이 코드베이스의 제1원칙.

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
├── sequence_bank.py 단음 뱅크에서 스케일·아르페지오·엑섭을 즉석 합성
├── unit/            순수 로직: notes, 음이름 래치, tracker, 합성기 자체검증
├── dsp/             DSP 품질: 정확도(±2¢), 노이즈 내성, 반응성(≤100ms),
│                    detector 계약, 주석기 정밀도, 외부 교차검증
├── integration/     조립: engine 파이프라인, UI offscreen(장치 전환 포함),
│                    실오디오 vs 주석 비교, 표시 안정성(음이름 깜빡임)
├── e2e/             실기기 전용(없으면 자체 skip): 실제 장치 열거/캡처/핫스왑,
│                    always-on-top 실제 스태킹, 음향 루프백(스피커→마이크)
├── fixtures/audio/  Iowa MIS 실악기 샘플 + .ref.json + 노이즈/간섭 변형
└── fixtures/notes/  악기별 크로마틱 단음 뱅크 + bank.json(창 단위 피치)
```

표시 지표(세그먼트 수, 짧은 표시 개수)와 "녹음 전체를 앱 파이프라인에
통과시키기"의 정의는 `tools/trace.py` **한 곳**에 있고 테스트가 그것을
import 한다 — 개발 도구와 스위트가 다른 것을 재기 시작하면 둘 다 못 믿는다
(`docs/dev-loop.md`).

Win/Mac 테스트는 분리하지 않는다 — 코드가 플랫폼 공용이므로 같은 스위트를
CI 매트릭스(macos/windows, `.github/workflows/test.yml`)에서 실행하고,
하드웨어가 필요한 e2e는 환경 기준으로 자체 skip한다. OS 특이 케이스가
생기면 그때 `skipif(sys.platform...)` 마커로 처리.

## 구조 변경 시 규칙

새 모듈·구현체를 추가하거나 추상화 도입을 판단할 때:

```
while True:
    audio = listen()          # ← interface-impl 로
    tuned = find_tuned(audio) # ← interface-impl 로
    show(tuned)               # ← interface-impl 로
```

- **루프(조립부) 자체는 단순하게** 둔다. 상태 머신·추상화를 얹지 않는다.
- 교체·비교·모킹이 필요한 **핵심 구현체만** 인터페이스 뒤에 둔다.
  기준선: `AudioInput`(장치↔파일↔페이크), `PitchDetector`(YIN↔Spectral)
  — 이 둘 덕에 파일 재생 데모, 페이크 주입 테스트, 검출기 실시간
  교체가 전부 공짜로 나왔다.
- **미리 만들지 않는다**: "필요할지도 모르는" 구현(OS 별 백엔드 등)은
  교체 지점(인터페이스)만 남기고 실제 필요가 생길 때 만든다.
- 의존 방향(위 "의존 규칙")을 지켰는지가 리뷰의 첫 질문이다.

## 향후 방향 (설계 제약)

이 프로젝트는 장기적으로 **악보 관리 앱**으로 확장되고, 튜너는 그 안에
쉽게 붙는 한 기능이 될 예정이다. 이를 위해 지금부터 지키는 규칙:

- 의존 방향은 언제나 "호스트 앱 → 튜너". 튜너 쪽 코드(core/audio/
  engine/meter)에 악보 도메인 개념을 넣지 않는다.
- 튜너 임베드 단위는 `TunerEngine` + `MeterWidget` 조합이다. 악보 앱
  셸이 실제로 생길 때 이 조합을 위젯 하나로 추출한다 (미리 하지 않음).
- 모바일 확장 시 Python 코어는 이식 대상이다. 그때의 자산은 코드가
  아니라 **알고리즘 명세와 골든 테스트 데이터**(합성 기준, 실악기
  픽스처 + ref.json, 노트 뱅크 + 시퀀스) — 언어 무관이므로 어떤
  재구현이든 같은 수치 기준으로 검증한다. 새 DSP 로직과 테스트를 만들
  때 이 "이식 계약" 역할을 염두에 둘 것.

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
- **사용자가 겪은 결함을 데이터로**: 앱에서 Ctrl+R(직전 10초) 또는 기록
  버튼/Ctrl+L(시작~정지 전 구간) → `~/.tuner/reports/<utc>/` (오디오 + 표시
  트레이스 + 코드 sha). `python -m tuner.tools.promote <report>` 가 **재현
  여부부터** 판정하고(`--name` 을 주면 픽스처로 승격 + 주석 생성),
  `trace <report>/trace.jsonl --explain` 이 검출·표시·이름이 어긋난 순간을
  분류한다. 개발 루프 전체는 `docs/dev-loop.md`.
- **표시 동작을 바꿀 때**: `python -m tuner.tools.trace <audio> --vs <rev>` 로
  어느 구간이 달라졌는지 보고, `python -m tuner.tools.scoreboard --check` 로
  게이트 아래 드리프트를 확인한다.
