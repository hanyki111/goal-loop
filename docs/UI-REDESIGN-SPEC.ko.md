# Goal Loop 데스크톱 UI 재설계 명세

상태: **검토용 설계안** (2026-09-29 작성, R1–R5로 구현됨)  
> **2026-10-03 안내:** 이 문서의 화면 용어(방향 목표·실행 방향·실행 목표 등)와 일부 화면 구조는 2026-10 UI 폴리싱에서 바뀌었다.
> 현행 용어는 목표·접근안·과제·완료 기준·실행이며, 현행 화면과 흐름의 기준은 [`UI-MANUAL-QA-GUIDE.ko.md`](UI-MANUAL-QA-GUIDE.ko.md)다.
> 이 문서는 재설계 당시의 결정 근거로 보존한다.

작성일: 2026-09-29  
근거: `archive/UI-MANUAL-QA-RESULT-20260929.ko.md` S0~S2 수동 QA 결과

## 1. 결정과 목표

현재 UI는 Core lifecycle을 화면에 옮기는 데에는 성공했지만, 사용자가 다음 행동을 찾고 결과를
확인하는 데 실패했다. 이 문서는 개별 버튼·문구의 땜질이 아니라, 화면의 정보 구조와 상호작용
모델을 다시 정한다.

### 확정할 설계 결정

1. **상시 하단 입력 UI/Action dock을 제거한다.** 화면 아래에 독립적으로 떠 있는 입력 폼과 lifecycle 안내를 두지 않는다.
2. **데스크톱 Workspace는 크기 조절 가능한 좌·중·우 세 패널로 구성한다.** 패널 너비는 이 PC의 local preference이며 SQLite journal 사실이 아니다.
3. **오른쪽 상세 패널을 기본보다 넓게 사용한다.** 선택한 Aim/Goal의 정보 확인과 다음 기록 작성은 모두 이 맥락에서 수행한다.
4. **전역 생성과 선택 맥락 행동을 분리한다.** 어느 상태에서도 새 Aim을 만들 수 있어야 하며, Proposal·Goal·Attempt 등은 선택한 대상 안에서만 만든다.
5. **사용자에게 내부 명령명·ID·raw 상태를 기본 노출하지 않는다.** `CREATE_AIM`, `produce`, `aim-evaluate` 등은 사용자 문장과 한국어 선택지로 번역한다.
6. **두 번째 native 확인 팝업을 없앤다.** 입력 폼의 제출 자체가 기록 의사 표현이다. 실제 삭제와 복구하기 어려운 workspace 제거만 앱 스타일의 확인 dialog를 사용한다.
7. **앱별 화면 배율을 제공한다.** 운영체제 DPI 설정을 바꾸지 않고 Goal Loop의 글자·여백·아이콘·pane을 함께 확대/축소한다.

### 성공 기준

처음 사용하는 사람은 도움말을 열지 않고도 다음을 할 수 있어야 한다.

- 어느 화면에서든 새 방향 목표를 시작한다.
- 선택한 방향 목표에 실행 방향을 추가하고, 새 카드가 생긴 위치를 즉시 안다.
- 선택한 실행 방향을 실행 목표로 만들고, `만들기·조사하기·결정하기`의 차이를 이해한다.
- 실행 목표에서 현재 필요한 단 한 가지 다음 행동을 수행한다.
- Attempt 성공, Goal 검증, Aim 달성이 서로 다른 단계임을 화면 흐름으로 이해한다.

## 2. 유지하는 경계

이 재설계는 presentation과 command adapter만 바꾼다.

- SQLite append-only journal, Core phase, `next`, `admission`, review 순서, command allow-list는 바꾸지 않는다.
- Attempt `succeeded`, Goal `verified`, Aim `achieved`는 계속 독립된 사실이다.
- 달성된 Aim에는 Proposal/Goal을 추가하지 않는다. 새 일을 시작하려면 전역 **새 방향 목표**를 쓴다. 기존 Aim을 다시 열 수 있는 경우는 종결 근거가 invalidated 된 뒤의 명시적인 advanced lifecycle뿐이다.
- Proposal이나 기록된 사실을 삭제하지 않는다. 사용자는 open Proposal을 **새 버전으로 바꾸기** (`proposal-revise`) 하거나 이 PC 화면에서 **보관**할 수 있다. 보관은 삭제가 아니라 표시 설정이다.
- workspace 삭제만 SQLite 파일을 실제로 삭제한다.

## 3. 앱 구조

### 3.1 App header: 앱 수준 기능만

높이 52px의 한 줄 header를 사용한다. 현재처럼 project 관리가 넓은 두 번째 행을 차지하지 않는다.

```
┌ Goal Loop ─ [현재 프로젝트 ▾] [···] ─────── 대시보드 | 작업함 | 검토함 | 실행 대기함 | 도움말 ─ [＋ 새 방향 목표] ┐
```

- **현재 프로젝트 ▾**: project/workspace 전환 메뉴다.
- **···**: 이름 변경, 새 프로젝트, 프로젝트 삭제를 담는 menu다. 삭제는 위험 행동으로 가장 아래에 둔다.
- **＋ 새 방향 목표**: 항상 보이는 전역 primary action이다. 선택한 Aim의 phase와 무관하다.
- `배치 초기화`, 새로고침은 graph toolbar로 옮긴다. 전역 header에 두지 않는다.
- **보기 ▾**: 화면 배율과 pane 표시를 조절하는 앱 수준 menu다. `80%`, `90%`, `100%`, `110%`, `125%`, `140%`와 **기본 배율로 되돌리기**를 제공한다.

### 3.2 Desktop Workspace: 세 개의 resizable pane

```
┌──────────────────┬──────────────────────────────────────────────┬──────────────────────────────────┐
│ 목표 목록         │ 목표 구조                                    │ 선택한 항목                       │
│ [＋ 새 방향 목표] │ [구조 보기] [목록 보기]    [배치 초기화]     │ Aim / Goal 제목 · 상태             │
│                  │                                              │ 무엇을 하는 항목인지 짧은 설명     │
│ 진행 중           │          선택한 Aim의 그래프                │ ──────────────────────────────── │
│  └ Aim            │          · Goal                              │ 지금 할 일                         │
│     └ Goal        │                                              │ [실행 방향 추가]                  │
│                  │                                              │                                    │
│ 완료됨 / 보관함   │                                              │ 기록 · 계약 · 관계 · 고급          │
└──────────────────┴──────────────────────────────────────────────┴──────────────────────────────────┘
      240–360px                    400px 이상                            360–640px
```

| 영역 | 기본 너비 | 최소/최대 | 책임 |
| --- | ---: | ---: | --- |
| 왼쪽 목표 목록 | 280px | 224–360px | Aim·Goal의 탐색, 상태 필터, 보관함 |
| 중앙 구조 | 남은 폭 | 400px 이상 | 선택과 관계를 위한 graph/list 보기 |
| 오른쪽 상세 | 460px | 360–640px | 선택한 항목의 설명, 기록, 다음 행동, 타임라인 |

- 좌/중, 중/우 경계에 키보드 접근 가능한 splitter를 둔다. 드래그·화살표로 조절하며 `localStorage`에 workspace별 저장한다.
- 중간 폭이 부족하면 graph가 가로 스크롤되는 대신, 패널이 서로 겹치거나 하단에 새 pane를 만들지 않는다.
- 좁은 화면에서는 좌/우 pane을 drawer로 접는다. 모바일 설계는 이 desktop interaction model을 재사용하되 별도 설계 단위로 다룬다.

### 3.3 앱별 화면 배율

화면 배율은 OS의 DPI 또는 Windows 배율을 바꾸는 기능이 아니다. Goal Loop 안에서만 글자 크기,
행 높이, 간격, icon, button, pane 최소 너비를 같은 비율로 조정하는 accessibility·가독성 설정이다.

- 기본값은 `100%`이며, `80%`, `90%`, `100%`, `110%`, `125%`, `140%` 중 선택한다.
- 이 값은 **작업 공간별이 아니라 이 PC의 Goal Loop 앱 전체**에 적용한다. 프로젝트를 바꿔도 가독성
  선호가 바뀌지 않아야 하며, SQLite journal에는 기록하지 않는다.
- `보기 ▾` menu와 `Ctrl+-`, `Ctrl+=`, `Ctrl+0`(기본값) keyboard shortcut으로 조절한다. 포커스가
  textarea/input 안에 있을 때는 입력 동작을 우선해 shortcut을 가로채지 않는다.
- 선택값은 `localStorage`에 저장하고 앱 재시작 뒤 복원한다. `기본 배율로 되돌리기`는 `100%`로 즉시 복귀한다.
- CSS custom property 기반 token을 사용한다. 브라우저의 단순 `zoom` 또는 graph canvas만 확대하는 방식은
  사용하지 않는다. 따라서 focus ring, hit target, pane minimum, dialog, toast도 함께 커진다.
- 중앙 graph의 노드 확대/축소는 **그래프 보기 배율**로 별도 제공할 수 있다. 이는 전체 앱 화면 배율과
  다른 기능이며, graph toolbar에만 둔다.

## 4. 정보 위계와 문구

### 4.1 한 화면에는 한 안내만

빈 workspace에는 중앙에 하나의 환영 화면만 보인다. 왼쪽 목록, 중앙 graph, 하단 dock에 같은 lifecycle 설명을 반복하지 않는다.

```
방향 목표부터 시작하세요
이루고 싶은 변화를 한 문장으로 기록한 뒤, 실행 방향과 실행 목표를 차례로 구체화합니다.
[＋ 첫 방향 목표 만들기]   용어 알아보기
```

입력 폼은 버튼을 눌렀을 때 오른쪽 detail pane에 열린다. 빈 화면에서 textarea가 아래로 밀려 스크롤되어 버튼이 보이지 않는 문제를 없앤다.

### 4.2 사용자 언어

| 기술/내부 표현 | 기본 화면 표현 | 설명이 필요한 경우 |
| --- | --- | --- |
| `CREATE_AIM`, `create_aim` | 새 방향 목표 | 표시하지 않음 |
| `Proposal` | 실행 방향 | “목표에 다가가기 위한 후보 경로” |
| `Goal Contract` | 실행 목표와 기준 | “무엇을, 어디까지, 무엇으로 확인할지” |
| `produce` | 만들기 | “무언가를 만들거나 결과물을 준비하는 실행” |
| `research` | 조사하기 | “근거를 모아 판단을 돕는 실행” |
| `decide` | 결정하기 | “선택지를 비교해 명시적인 결론을 내리는 실행” |
| `Verdict` | 검증 판단 | “관찰 근거를 바탕으로 기준 충족 여부를 판단” |
| `aim-evaluate` | 방향 목표 평가 | “검증된 결과가 큰 방향을 뒷받침하는지 별도 평가” |

ID, contract version, raw phase는 detail pane의 **기술 정보** 접기 영역에서만 보여 준다.

### 4.3 시각 규칙

- 기본 본문은 14px 이상, pane 제목은 16–18px 이상으로 한다. 회색 설명도 읽을 수 있는 대비와 크기를 유지한다.
- 8px 간격 단위와 16px/24px pane padding을 공통 token으로 쓴다.
- 모든 크기 token은 앱별 화면 배율을 기준으로 계산한다. `125%`에서도 button의 텍스트가 잘리거나 pane이
  서로 겹치지 않아야 한다.
- border·card·badge는 정보를 묶는 경우에만 쓴다. 한 action의 설명, 상태, 폼, 중복 stepper를 각각 카드로 겹치지 않는다.
- primary는 한 맥락에 하나만 쓴다. Archive/삭제는 overflow menu 또는 detail 하단의 “관리” 영역으로 보낸다.
- 성공·경고·차단 상태는 텍스트 label과 icon을 같이 제공한다. 색만으로 상태를 전달하지 않는다.

## 5. 선택한 항목과 행동

### 5.1 오른쪽 detail pane의 공통 구조

```
상태 badge · 항목 종류
제목
한 문장 설명

지금 할 일
왜 지금 이 행동이 필요한지
[주 행동]   [다른 선택 ▾]

내용 / 타임라인 / 계약 / 관계 / 관리
```

- `next`는 행동의 validity를 정하는 read model로 계속 사용한다.
- UI는 raw `next.kind`를 표시하지 않고, 선택 대상에 맞는 한국어 주 행동 하나로 mapping한다.
- 관련 없는 Aim/Goal의 행동은 detail pane에 섞지 않는다. 그것들은 상단 **작업함**에서 대상 이름과 함께 보인다.
- 기록에 성공하면 toast `실행 방향을 기록했습니다`를 보이고, 만든/변경한 카드를 잠시 강조하며, 그 항목을 선택 상태로 만든다.

### 5.2 Aim detail

Open Aim에서는 상단에 “방향 목표는 이루고 싶은 변화입니다”라는 한 문장 설명을 보이고, 주 행동은 **실행 방향 추가**다. 실행 방향은 즉시 보이는 카드 목록으로 나타난다. 새 카드는 목록 맨 위에 강조되어 나타난다. 각 Proposal 카드에서 **실행 목표로 만들기**, **새 버전으로 바꾸기**, **보관**을 제공한다.

Achieved Aim에는 “이 방향은 별도 평가로 마무리되었습니다”와 근거 요약을 보인다. Proposal 추가 버튼을 숨기는 대신 전역 CTA와 동일한 **새 방향 목표 만들기**를 제시한다. advanced menu에만 pause/resume, closure invalidation 등 Core가 허용한 복구 경로를 둔다.

### 5.3 Goal detail

Goal을 선택하면 우측 pane의 주 행동은 phase에 따라 하나다.

| 상태 | 주 행동 | 보조 정보 |
| --- | --- | --- |
| accepted | 실행 시도 시작 | 계약의 결과·기준 요약 |
| active | 실행 결과 기록 | 실행자와 시작 시각 |
| completed | 관찰 근거 기록 | 성공/실패 결과와 Evidence 수 |
| verifying | 검증 판단 기록 | 필요한 독립 검증자와 인용할 Evidence |
| verified | 방향 목표에서 결과 확인 | Goal 검증과 Aim 달성은 다르다는 안내 |
| held/dependency blocked | 보류 원인 확인 | 해제/선행 Goal으로 가는 명확한 link |

Attempt, Evidence, Verdict는 Goal detail 안에서 시간순 타임라인으로 펼친다. “완료한 Attempt를 따로 선택해야만 다음 행동이 보인다”는 흐름을 없애고, 현재 Goal의 필요한 Attempt를 자동으로 focus한다.

## 6. 작성과 확인 흐름

### 6.1 inline 작성과 detail editor

짧은 기록(Proposal, Attempt 결과, Evidence)은 detail pane의 `지금 할 일` 아래 inline editor로 연다. 긴 기록(Goal contract, Verdict, Aim evaluation, contract revision)은 detail pane 전체를 차지하는 editor mode로 연다. breadcrumb와 취소 버튼을 제공해 사용자가 맥락을 잃지 않게 한다.

Goal contract editor는 다음 세 단계로 구성한다.

1. **무엇을 만들거나 확인할까요?** — 실행 목표, 실행 종류(만들기/조사하기/결정하기)
2. **어디까지 하면 되나요?** — 실행 범위, 성공 기준
3. **어떻게 확인하나요?** — 검증 방법, 요약 확인

각 선택지는 설명과 예시를 같이 보여 준다. 자유 텍스트만 있는 `책임 성격: produce` 입력은 사용하지 않는다.

### 6.2 확인 규칙

- 일반 append-only 기록: 폼의 명확한 `실행 방향 기록`, `성공으로 기록`, `검증 판단 기록` 버튼 한 번으로 완료한다. native `window.confirm`을 추가로 띄우지 않는다.
- 중요한 판단(Verdict, Aim evaluation, review resolution): 제출 버튼 바로 위에 “이 기록이 만드는 사실” 요약과 Evidence/결론을 재검토하는 checkbox를 둔다. 이는 별도 popup이 아니라 폼의 일부다.
- 위험한 관리: workspace 삭제는 앱 디자인과 일치하는 modal, 대상 이름 재확인, 취소 기본 focus를 사용한다.
- 성공·실패 후에는 toast, 항목 강조, screen-reader live message를 제공한다.

## 7. Proposal과 완료된 Aim의 회복성

| 사용자 상황 | 화면이 제공할 해결 | Core 사실 |
| --- | --- | --- |
| 방금 실행 방향을 기록함 | 새 Proposal 카드를 자동 선택·강조 | `proposal-create` |
| 문구를 잘못 입력함 | **새 버전으로 바꾸기**로 successor 작성 | `proposal-revise`; 과거 문구 보존 |
| 더 이상 목록에서 보고 싶지 않음 | 이 PC에서 **보관** | local display preference |
| 달성된 Aim 뒤 새 일을 하고 싶음 | 항상 보이는 **새 방향 목표** | 새 `aim-create` |
| 실제 종결 근거가 무효화됨 | Advanced의 explicit recovery | 기존 Core lifecycle |

이 정책은 append-only를 사용자에게 부담으로 보이지 않게 번역한다. “삭제”라고 약속하지 않고, 무엇이 기록으로 남고 무엇이 내 화면에서만 숨겨지는지 각 메뉴에 명시한다.

## 8. 참고 UI와 적용 범위

사용자가 제공한 `http://localhost:5173/projects/ai-orchestration/sessions/run-1789660821076`는 이 환경에서 Vite 앱과 source 구조까지 접근 가능했다. 해당 앱의 routing shell, theme token, toaster 같은 패턴은 참고할 수 있다. Goal Loop은 app shell과 content workspace 분리, 공통 색·spacing·radius·focus token, toast feedback 원칙만 채택한다.

그 앱의 페이지 구조나 domain model을 Goal Loop에 복사하지 않는다. Goal Loop은 local SQLite와 append-only lifecycle이라는 다른 목적을 유지한다.

## 9. 구현 순서와 acceptance 기준

### R1. Shell·pane·design token

- 상시 하단 Action dock 제거
- header/menu 재배치, 전역 새 Aim 진입점
- three-pane splitter, workspace별 local width 저장
- `보기` menu의 앱별 화면 배율, keyboard shortcut, app-wide local persistence
- 빈 상태를 중앙의 단일 CTA로 교체

완료 기준: `image_001.png`처럼 입력 폼이 화면 하단으로 밀려 보이지 않거나 header에 project controls가 어긋나는 일이 없다. 빈 workspace에서 한 화면 안에 하나의 시작 CTA만 보인다. `80%`, `100%`, `125%`, `140%`에서 panel·button·입력 칸이 겹치지 않고, 재시작 뒤 선택 배율이 유지된다.

### R2. Aim/Proposal의 발견 가능성

- Aim detail의 contextual primary action과 Proposal card
- 생성 후 선택/강조/toast
- Proposal revise/archive UI
- 완료된 Aim의 “새 Aim 만들기” 경로

완료 기준: 새 사용자에게 “Proposal이 어디에 생겼는가”, “Proposal을 선택할 수 있는가”, “달성된 Aim 뒤 어떻게 다음 일을 시작하는가”를 묻지 않는다.

### R3. Goal·Attempt·Evidence·Verdict flow

- Goal contract editor와 한국어 responsibility choice
- Goal detail의 phase별 주 행동·Attempt focus
- Evidence/Verdict/Aim evaluation editor의 문맥·근거 안내
- native confirmation 제거 및 결과 feedback

완료 기준: QA S1의 버튼 위치·입력 순서와 실제 UI가 일치하며, `produce`나 raw lifecycle key를 알지 못해도 성공 흐름을 마칠 수 있다.

### R4. Advanced lifecycle과 관리

- retry, Hold, parent/dependency, correction/review를 기본 흐름과 분리한 Advanced section
- workspace menu·rename·delete dialog
- Work/Review/Runtime inbox의 대상 식별과 action routing

완료 기준: 고급 기능은 찾을 수 있지만 빈 workspace와 정상 lifecycle을 방해하지 않는다.

### R5. 검증과 문서

- component test: action mapping, pane state, creating feedback, closed Aim route, archive/revise semantics
- Tauri E2E: Korean IME, splitter persistence, app-scale persistence/keyboard/reset, full happy path, retry, resize, keyboard, delete cancel
- 실제 최종 label·클릭 순서로 새 `UI-MANUAL-QA-GUIDE.ko.md` 작성

완료 기준: 사람이 같은 QA를 다시 수행했을 때 “설명과 구현이 다름”을 발견하지 않는다.

## 10. 이 설계안에서 논의가 필요한 항목

구현을 시작하기 전에 다음만 확인하면 된다.

1. **세 pane의 기본 비율**: 이 문서는 왼쪽 280px / 오른쪽 460px를 제안한다. 오른쪽 detail을 더 넓게 하려는 사용자 의견을 반영한 값이며, 실제 사용 후 조정 가능하다.
2. **중앙의 기본 보기**: graph를 기본으로 유지하되 `목록 보기` 전환을 함께 제공한다. graph를 보조 보기로 낮추고 목록을 기본으로 할지 결정이 필요하면 R1 전에 변경한다.
3. **Proposal 보관 범위**: Aim/Goal Archive와 같이 이 PC 표시 설정으로 처리하는 것이 Core 변경 없이 안전하다. 전역적으로 숨기는 기능이 필요하면 새로운 domain 의미론 여부를 별도로 논의해야 한다.

그 외 항목은 기존 Core contract와 이번 수동 QA 결과로 결정할 수 있다.
