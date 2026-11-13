# 기여 안내

Goal Loop에 관심을 가져 주셔서 감사합니다. 버그 신고, 기능 제안, 변경 제안(Pull Request)을 모두 환영합니다.

## 개발 환경

확인한 환경은 Windows 11, Python 3.11 이상, Node.js(npm), Rust(rustup)입니다.

```powershell
git clone https://github.com/hanyki111/goal-loop
cd goal-loop
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
cd ui
npm ci
```

데스크톱 앱 개발 실행은 `cd ui; npm run tauri dev`입니다.

## 시험

변경을 제안하기 전에 다음이 통과하는지 확인해 주십시오.

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 저장소 루트
cd ui
npx vitest run                                   # 화면 시험
npm run build                                    # 타입 확인과 화면 빌드
cd src-tauri
cargo build                                      # 데스크톱 앱(실행 중인 앱이 있으면 닫고 빌드)
```

동작을 고쳤다면 그 동작을 확인하는 시험을 함께 넣어 주십시오. 고치기 전 코드에서는 그 시험이 실패해야 합니다.

## 변경 제안 방법

1. 큰 변경은 먼저 이슈로 무엇을, 왜 바꾸려는지 알려 주십시오.
2. 변경은 작게 나누고, Pull Request 설명에 바꾼 내용과 확인한 시험 결과를 적어 주십시오.
3. 기록 형식(SQLite journal)이나 `ui-projection` schema를 바꾸면 기존 기록을 그대로 읽을 수 있는지 확인하는 시험을 넣어 주십시오.

## 이 저장소에서 CLI 쓰기

README에서 옮긴 내용입니다(goal-161).

For this repository, install the command into the project virtual environment once:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

In this project, use `& .\.venv\Scripts\goal-loop.exe ...` unless its `Scripts` directory is
already on your PATH. The shorter `goal-loop ...` form is for a shell where the command resolves.

## 개념 검증용 메모리 MVP와 데모

README에서 옮긴 내용입니다(goal-161).

The in-memory core is a pre-product semantic prototype. It validates selected scenarios and
counterexamples for immutable Goal Contracts, Attempt/Verdict separation, Contract-version
safety, Evidence correction and review, execution admission, and explicit Aim Evaluation.
The normative boundary is in [docs/MVP-SPEC.md](docs/MVP-SPEC.md). The persistent SQLite journal,
the AI_ORCHESTRATION adapter and the Goal Tree were built on top of it later (see [docs/CLI-REFERENCE.md](docs/CLI-REFERENCE.md)).

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

한국어 단계 추적 데모입니다.

```powershell
.\.venv\Scripts\python.exe -m goal_loop_tool demo
.\.venv\Scripts\python.exe -m goal_loop_tool not-satisfied
.\.venv\Scripts\python.exe -m goal_loop_tool inconclusive
.\.venv\Scripts\python.exe -m goal_loop_tool contract-revision
.\.venv\Scripts\python.exe -m goal_loop_tool evidence-invalidation
.\.venv\Scripts\python.exe -m goal_loop_tool execution-admission
.\.venv\Scripts\python.exe -m goal_loop_tool aim-evaluation
```

`demo` optionally accepts `--output <path>` for its newly created file. The default writes a
new file under `artifacts/`.

## 이 저장소의 개발 방식

이 저장소는 Goal Loop 자체로 개발을 기록합니다(dogfooding). 개발자의 기록 폴더(`.goal-loop/`, `.memory/`)와 개인 운영 메모(`*.local.md`)는
`.gitignore`로 빠지며 공개 대상이 아닙니다. 이 체크아웃에서 쓰는 에이전트 지침(`CLAUDE.md`, `AGENTS.md`)도 같은 이유로 공개하지 않습니다.

## 라이선스

이 저장소는 [Apache License 2.0](LICENSE)으로 배포됩니다. 기여한 변경도 같은 라이선스로 제공되는 것으로 봅니다(Apache-2.0 제5절).

## 보안

취약점은 공개 이슈 대신 [SECURITY.md](SECURITY.md)의 방법으로 알려 주십시오.
