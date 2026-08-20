# 개발 루프 도구

**언제 읽나**: 개발 인프라(측정 기록·전후 비교·현장 캡처)를 손볼 때. 새
항목을 시작하기 전에 순서와 "안 하기로 한 것"을 먼저 확인한다.

제품 기능이 아니라 **개발을 계속하기 위한 장치**의 계획이다. 판정 기준·
파라미터 자체를 다루는 규칙은 [회귀 봉인](process/regression.md)과
[주관적 품질 작업](process/subjective-ux.md)에 있다.

## 진단 — 무엇이 없나

- 스위트는 median/p90/p95/옥타브미스를 **이미 계산하고 `print` 로 버린다**
  (`tests/helpers.py` `assert_pipeline_agreement`). 런이 끝나면 남는 건
  통과/실패 1비트뿐이다.
- 게이트가 "실측 최악 바로 위"에 놓여 있어(p90 40.0 → 상한 45.0) **그 아래의
  드리프트는 보이지 않는다.** p95 가 8 → 11.9 로 밀려도 그린이다.
- 남은 결함은 대부분 **시간축 위의 표시 시퀀스**다(`review-followups.md` 10번:
  전환 구간에서 F6 가 13프레임). 스칼라 지표로는 안 잡히고, 두 리비전의
  표시를 나란히 놓고 볼 수단이 없다.
- 코퍼스는 유한·고정이다. 앞으로 발견될 결함은 실연주 중에 나오는데, 그
  순간의 오디오가 남지 않아 재현 테스트를 만들 수 없다.

## 순서

**1 → 2 → 3.** 트레이스가 2번의 기록 포맷과 3번의 캡처 산출물 양쪽에서 쓰이는
공용 자료구조라 먼저 정의한다. 그러면 도구가 셋이 아니라 하나가 된다.

### [x] 1. 표시 트레이스 + 트레이스 diff  — 2026-08-18 완료

프레임마다 `(t, raw_hz, confidence, 표시 Hz, 음이름, cent, state)` 를 남기고,
두 트레이스의 차이를 구간 단위로 보여준다.

- `python -m tuner.tools.trace <audio> [-o t.jsonl]` — 실제 엔진을 오프라인
  (Qt·오디오 장치 없이, 실시간보다 빠르게)으로 돌려 트레이스 생성 + 요약
- `python -m tuner.tools.trace <audio> --vs <git-rev>` — 그 리비전의 `src` 를
  sparse worktree 로 꺼내 같은 오디오를 돌리고 **전후 diff** 출력
- `python -m tuner.tools.trace --diff a.jsonl b.jsonl` — 저장된 두 트레이스 비교
- 표시 지표(세그먼트 수, 짧은 표시 개수)는 여기 한 곳에서 정의하고
  `tests/integration/test_display_stability.py` 가 그것을 쓴다 — 테스트와
  도구가 같은 것을 재는지 증명 가능해야 한다

**완료 기준**: 도구가 뽑은 세그먼트·짧은표시 수치가 기존 테스트의 실측값과
정확히 일치하고, `--vs HEAD~1` 이 한 명령으로 돈다.

**결과** (`src/tuner/tools/trace.py`, `tests/integration/test_trace_tool.py`):

- 실측 일치 확인 — 오보에 13세그먼트/0짧은표시, 플루트 15/1, 간섭 2/0 으로
  기존 테스트의 드라이버 주석 값과 그대로 맞았다.
- `test_display_stability.py` 가 자체 파이프라인 구동·지표 정의를 버리고
  트레이서를 쓴다. 도구와 스위트가 같은 것을 재는지 테스트로 증명된다
  (`test_trace_is_the_app_pipeline_not_a_copy`).
- `--vs <rev>` 는 그 리비전의 `src` 만 sparse worktree 로 꺼내고(픽스처
  59MB 는 안 꺼낸다) **현재 트레이서 파일을 복사해 넣어** 실행한다 —
  트레이서가 없던 리비전도 비교 대상이 된다. 전체 6977프레임 비교가 ~13초.
- 실전 확인 2건:
  - `--vs HEAD~1`(DTFT 4.9× 최적화) → 다른 프레임 0/6977. "비트 단위 동일"
    주장이 표시 레벨에서 독립 확인됐다.
  - `--vs b028ff6`(±50 규칙 도입 전) → 53/346 프레임, 6구간, 세그먼트
    3 → 15. `docs/note-latch-tuning.md` 가 표로 적어둔 변화가 한 명령으로
    재현되고, 바뀐 시각 구간(0.74s, 0.92s…)까지 나온다.
- 오보에 세그먼트 목록에 `D#4 F6 E4` 가 그대로 보인다 — `review-followups.md`
  10번의 글리치가 도구 출력 첫 줄에서 눈에 띈다.

### [x] 2. 지표 영속화 + 스코어보드  — 2026-08-18 완료

이미 계산 중인 값을 런마다 저장하고 비교한다. 새 측정 코드는 쓰지 않는다.

- `tests/metrics.py` 의 `record(...)` 를 기존 `print` 자리에 얹고, pytest 훅이
  런 종료 시 `metrics/runs/<utc>-<sha>.json` 으로 덤프
- `python -m tuner.tools.scoreboard --last 5` / `--vs <rev>` / `--check`
- 버전 축은 **git sha**로 잡는다. 태그·릴리스 의식은 만들지 않는다

**완료 기준**: 스무딩 파라미터를 일부러 바꾸면 스코어보드가 어떤 지표가
얼마나 나빠졌는지 표로 보여준다. 게이트는 늘리지 않는다 — 스코어보드는
**관찰용**이고, 임계값을 늘리면 유지보수가 개발을 잡아먹는다.

**결과** (`tests/metrics.py`, `tests/conftest.py`, `src/tuner/tools/scoreboard.py`,
`tests/unit/test_metrics.py`):

- 기록 지점 6곳, 지표 31개. 전부 **이미 계산해서 print 하던 값**이다 —
  새 측정 코드도, 새 임계값도 없다. 게이트는 그대로 유일한 실패 조건.
- xdist 대응: 컨트롤러가 런 id 를 만들고 `pytest_configure_node` 로 워커에
  내려준다. 워커마다 자기 파일을 같은 런 디렉터리에 쓰고, 읽는 쪽이 glob 으로
  합친다 — 컨트롤러로 되돌려 보내는 경로가 없어 경합이 없다.
- 런 id = `<UTC>-<sha[+dirty]>`. 태그·릴리스 없이 sha 가 버전 축이다.
- `metrics/` 는 gitignore — 로컬 관측 기록이고, 결론은 `docs/` 에 남긴다.
- 검증: 같은 코드로 두 번 돌리면 31개 전부 변화 없음(결정성 확인).
  `SMOOTHING_ENABLED = False` 로 한 번 돌리자 —

  | 지표 | 스무딩 on | off |
  |---|---:|---:|
  | `smoothness/jitter_p50_cents` | 0.096 | 0.204 (+112%) |
  | `smoothness/jitter_p95_cents` | 0.491 | 0.882 (+80%) |
  | `smoothness/vibrato_ratio` | 0.845 | 0.947 (+12%) |
  | 간섭 픽스처 `brief_flashes` | 0 | 1 |

  `--check` 가 exit 1 과 함께 악화 3개를 짚었다. `docs/smoothing-tuning.md`
  가 표로 적어둔 지터↔비브라토 트레이드오프가 그대로 재현된다.

### [x] 3. 앱 현장 캡처  — 2026-08-18 완료

새 정답 데이터의 유일한 출처. 사용자가 이상함을 느낀 순간을 픽스처로 만든다.

- 엔진에 마지막 N초 링버퍼(오디오 + 트레이스), 앱 단축키 하나로 덤프
- `~/.tuner/reports/<ts>/{audio.wav, trace.jsonl, meta.json}` — meta 에 코드
  sha·A4·detector·장치·샘플레이트
- `python -m tuner.tools.promote <report>` → 픽스처로 승격 + `annotate` 로
  `.ref.json` 생성

**완료 기준**: "가끔 깜빡여요" 제보가 클릭 한 번으로 재현 가능한 픽스처가 되고,
그 픽스처로 회귀 테스트를 쓸 수 있다.

**결과** (`src/tuner/app/capture.py`, `src/tuner/tools/promote.py`,
`tests/integration/test_capture.py`):

- 엔진은 두 줄만 늘었다 — 블록 하나를 링에 넣고, 리딩 하나를 트레이스에
  넣는다. `capture=None` 이 기본이라 안 쓰면 비용 0.
- 리포트 = `audio.wav`(float, PCM16 로 양자화하면 재생이 달라진다) +
  `trace.jsonl` + `meta.json`(sha·A4·detector·장치·샘플레이트).
- **승격 전에 재현부터 묻는다**: `promote` 가 저장된 오디오를 같은
  파이프라인에 다시 통과시켜 현장 트레이스와 프레임 단위로 비교한다.
  같으면 코드 문제(픽스처가 붙든다), 다르면 타이밍·장치 의존(픽스처로는
  못 잡고 리포트가 증거다). 이 판정이 없으면 재현되지도 않는 파일을
  코퍼스에 넣게 된다.
- 트레이스 기록 포맷은 `analysis/trace.py` 로 옮겨 앱·도구·테스트가 한
  형식을 쓴다. 덕분에 `test_live_trace_equals_the_offline_one` 이 "현장에서
  본 것 == 오프라인 재생"을 테스트로 붙들 수 있다.
- 실측 확인: A4 를 442 로 돌린 캡처를 440 으로 재생하면 diff 가 전 구간
  7.85¢ 차이를 짚어낸다(= 1200·log2(442/440)). 재현 판정이 실제로 민감하다.

**보강 (2026-08-19)** — 링 캡처만으로는 부족한 게 드러났다. Ctrl+R 은
"예상 못 한 순간"용이고, **답을 알고 싶은 질문을 들고 작정하고 부는** 경우엔
전 구간 기록이 필요하다 (사용자 지적).

- `기록` 버튼 / Ctrl+L: 시작~정지 사이를 한 프레임도 안 버리고 남긴다.
  메모리에 들고 있다가 정지할 때 쓰므로(오디오 스레드에서 파일을 쓰면
  드롭아웃이 난다) 10분 상한, float32 로 분당 ~10.6MB.
  스트림이 재시작되면(장치 변경) 프레임 시계가 끊기므로 기록을 종료하고
  `interrupted` 로 표시한다. 창을 닫아도 진행 중이던 기록은 저장된다.
- `trace <trace.jsonl> --explain`: **세 숫자가 어긋난 순간**을 분류한다.
  `pinned`(숫자가 ±50 에 붙음) 는 raw 위치를 보고 *정직한 경계*와
  *래치 홀드+클램프*로 갈리고, `held`(화면 이름 ≠ 검출), `flash`(8프레임
  미만 표시)를 시각·지속과 함께 뽑는다.

실측 예 (`cello_scale_A2Gb3.aiff`):

| 구간 | 지속 | 화면 | raw 이탈 | 판정 |
|---|---|---|---|---|
| 32.70s | 197ms | `D3 -50c` | -51..-48c | 정직 — 음정이 진짜 경계에 있다 |
| 3.15s | 64ms | `G#3 -50c` | -1138..-1107c | 래치 홀드 + 클램프 |
| 68.09s | 64ms | `F#2 +50c` | +1196..+1206c | 래치 홀드 + 클램프 |

래치 홀드는 dwell(12프레임 ≈ 70ms) 상한에 정확히 걸려 있고, 오래 지속되는
±50 은 전부 정직한 경계였다.

## 지금 쓰는 법 (세 도구가 한 루프다)

```bash
# 1. 이상한 걸 봤다 -> 앱에서 Ctrl+R, 그리고
python -m tuner.tools.promote ~/.tuner/reports/<시각>            # 재현되나?
python -m tuner.tools.promote ~/.tuner/reports/<시각> --name X   # 픽스처로
# 2. 고치는 중 -> 표시가 어디서 달라졌나
python -m tuner.tools.trace tests/fixtures/audio/X.wav --vs HEAD
# 3. 고친 뒤 -> 게이트는 그린인데 딴 데가 나빠지지 않았나
pytest -m "not e2e and not crosscheck" && python -m tuner.tools.scoreboard --check
```

## 안 하기로 한 것

| 후보 | 왜 안 하나 |
|---|---|
| `compare --blind` (라벨 가린 A/B) | 스무딩 값은 이미 결정·문서화됐다. 다음 대규모 주관 튜닝이 실제로 생길 때 플래그 한 개로 붙인다 |
| 버전 태그 + CHANGELOG | 솔로 프로젝트에서 sha 가 이미 버전이다. 필요한 건 "제보에 코드 식별자가 붙는 것"뿐이고 그건 3번의 `meta.json` 이 준다 |
| 픽스처 커버리지 맵 도구 | 도구로 만들 일이 아니라 한 번 세면 끝나는 일회성 분석 |
| 런타임 자기진단(콜백 오버런 카운터) | `review-followups.md` 8번(워커 스레드)을 실제로 착수할 때 필요. 지금은 잴 대상이 없다 |
