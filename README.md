# File Tree Viewer

File Tree Viewer는 실제 파일 시스템을 탐색하고, 변경 작업을 미리 계획한 뒤 검증해서 한 번에 적용할 수 있는 PySide6 데스크톱 앱입니다. 모든 기능은 별도 서버 없이 로컬에서 동작합니다.

핵심은 **Plan 작업공간**입니다. 파일 생성·이름 변경·이동·삭제 요청을 먼저 Plan에 쌓으므로 검토 단계에서는 실제 파일이 바뀌지 않습니다. 적용 전에 Diff 시뮬레이션으로 충돌과 외부 변경을 확인하고, 적용 중 문제가 생기면 완료된 작업을 자동으로 되돌립니다.

## 작업공간

| 탭 | 용도 |
| --- | --- |
| **Real File System** | 실제 폴더 탐색, 검색, 미리보기, 텍스트 편집과 개별 파일 작업 |
| **Virtual File System** | 메모리 안에서 가상 트리를 구성하고 저장·불러오기·실제 폴더 내보내기 |
| **Plan** | Snapshot을 기준으로 여러 변경을 준비하고, 분석·검증 후 일괄 적용 |

## Plan 사용 흐름

### 1. Snapshot 가져오기

`Plan` 탭에서 **Import Snapshot**을 누르고 작업할 폴더나 드라이브를 선택합니다. 앱은 경로, 종류, 크기, 수정 시각 등의 메타데이터를 임시 SQLite 데이터베이스에 기록합니다.

- 원본 파일 내용은 수정하지 않습니다.
- 대용량 폴더도 트리를 펼칠 때 필요한 항목만 읽습니다.
- 진행 중인 스캔은 **Cancel**로 중단할 수 있습니다.

### 2. 작업을 Plan에 추가하기

왼쪽 Snapshot 트리에서 항목을 선택하고 아래 작업 버튼을 사용합니다.

| 버튼 | Plan에 추가되는 작업 |
| --- | --- |
| **New File / New Folder** | 선택한 위치에 파일 또는 폴더 생성 |
| **Rename** | 선택한 항목 하나의 이름 변경 |
| **Move** | 선택한 항목들을 다른 폴더로 이동 |
| **Delete** | 선택한 항목과 하위 항목 삭제 |
| **Batch Rename** | 접두사·접미사·문자열 치환·대소문자·순번 규칙으로 일괄 이름 변경 |
| **Organize Files** | 이름, 확장자, 크기, 날짜 조건으로 파일을 분류하고 이동 |

트리 안에서 드래그해 이동 작업을 추가할 수도 있습니다. 오른쪽 Plan 표에는 작업 종류, 원본 경로, 대상 경로, 충돌 정책이 표시됩니다. **Remove Selected**와 **Clear Plan**으로 아직 적용하지 않은 작업을 수정할 수 있습니다.

### 3. 일괄 작업 미리보기

**Batch Rename**과 **Organize Files**는 결과를 표로 먼저 보여주며, **Add to Plan**을 눌러야 현재 Plan에 합쳐집니다.

정리 규칙은 다음 조건을 조합할 수 있습니다.

- 파일명 패턴과 확장자
- 최소·최대 파일 크기
- 수정 날짜 범위
- 선택 항목 또는 Snapshot 전체 범위
- 확장자, 연도, 연도/월 기준 하위 폴더 생성

`Downloads`, `Photos`, `Development` 기본 Preset을 사용할 수 있고, 사용자 규칙은 JSON 파일로 저장하거나 다시 불러올 수 있습니다.

### 4. 구조와 문제 분석

- **Analyze Structure**: 파일·폴더 수, 전체 크기, 확장자 분포, 큰 파일과 폴더, 가장 깊은 경로를 분석합니다.
- **Find Problems**: 중복 파일, 빈 폴더, 대소문자 이름 충돌, 긴 경로, 접근 불가 항목, 끊어진 링크, Snapshot 이후 변경·삭제된 항목을 찾습니다.
- 중복 파일은 크기로 후보를 줄인 뒤 SHA-256으로 확인합니다. 선택한 중복 파일은 즉시 지우지 않고 DELETE 작업으로 Plan에 추가됩니다.

분석은 백그라운드에서 실행되며 취소할 수 있습니다.

### 5. Plan 저장과 불러오기

**Save Plan**은 현재 작업 목록을 JSON 파일로 저장합니다. **Load Plan**은 저장된 작업을 현재 Snapshot 루트에 불러옵니다. 다른 루트에서 만든 Plan이라면 원래 루트를 안내하므로 경로가 맞는지 확인한 뒤 다시 시뮬레이션해야 합니다.

대규모 Plan은 스트리밍 방식으로 저장·불러오며, 작업 중에도 UI가 멈추지 않습니다.

### 6. Diff 시뮬레이션과 충돌 해결

**Diff / Simulate**는 실제 파일을 바꾸지 않고 전체 Plan을 검사합니다.

- Snapshot에 있던 원본이 사라졌거나 변경되었는지 확인
- 같은 원본 또는 대상 경로가 여러 작업에 사용되는지 확인
- 대상이 이미 존재하는지 확인
- 상위 폴더가 없거나 사용할 수 없는지 확인
- 폴더를 자기 하위로 이동하는 등의 잘못된 작업 차단

충돌이 있으면 작업별로 가능한 경우 **Overwrite**, **Auto Rename**, **Skip** 중 하나를 선택할 수 있습니다. 해결된 Plan은 다시 시뮬레이션해 새로운 충돌이 없는지 확인합니다.

### 7. 적용과 Undo

**Apply All**을 누르면 최신 시뮬레이션 결과를 확인한 뒤 실제 파일 변경을 시작합니다.

- 적용 전에 작업 수, 삭제 수, 덮어쓰기 수와 대상 루트를 다시 표시합니다.
- 적용 중 취소되거나 오류가 발생하면 이번 실행에서 완료된 변경을 자동으로 되돌립니다.
- 복구가 완전히 끝나지 못한 경우에는 작업을 중단하고 수동 확인이 필요한 복구 기록 위치를 안내합니다.
- 성공한 뒤 **Undo Last Apply**로 현재 실행에서 마지막으로 적용한 Plan 전체를 되돌릴 수 있습니다. 파일이 외부에서 다시 변경되어 충돌이 생기면 Undo를 중단합니다.

> **Apply All부터 실제 파일이 변경됩니다.** 중요한 폴더는 별도 백업을 준비하고, Diff 결과와 대상 루트를 확인한 뒤 적용하세요.

## 다른 주요 기능

### Real File System

- 폴더를 펼칠 때만 읽는 지연 로딩
- 결과 개수 제한 없는 백그라운드 검색과 페이지 캐시
- 파일·폴더 생성, 이름 변경, 복사, 이동, 휴지통 삭제
- 지원되는 개별 작업 Undo
- 파일 정보, 텍스트 미리보기와 원자적 저장
- 기본 프로그램으로 파일 열기

### Virtual File System

- 실제 파일을 건드리지 않는 가상 파일·폴더 트리
- 생성, 이름 변경, 복사, 이동, 삭제와 Undo
- 가상 작업공간 저장·불러오기
- 선택한 실제 폴더로 내보내기

## 대규모 폴더 처리

- Snapshot과 검색 결과는 디스크 기반 SQLite 저장소를 사용합니다.
- Snapshot 트리와 Plan 표는 보이는 데이터만 요청하는 모델 기반 UI입니다.
- 스캔, 검색, 미리보기, 분석, 문제 탐지, Plan 입출력, Diff, 적용 작업은 UI 스레드 밖에서 실행됩니다.
- Plan 작업 조회 결과와 Diff 집계를 캐시하고, Snapshot 경로는 묶어서 조회합니다.
- 실행 중인 장기 작업은 종료 요청 시 먼저 안전하게 취소하거나 완료를 기다립니다.

## 실행

Python 3.10 이상이 필요합니다.

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Windows 실행 파일 만들기

프로젝트 루트에서 `build_windows.bat`을 실행합니다. 스크립트가 임시 가상환경을 만들고 PyInstaller 단일 실행 파일을 생성합니다.

```powershell
build_windows.bat
```

결과 파일:

```text
release\FileTreeViewer.exe
```

GitHub의 `Actions` → `Build Windows` → `Run workflow`에서도 빌드할 수 있습니다. 완료된 실행 파일은 `FileTreeViewer-windows-x64` 아티팩트에서 받을 수 있습니다.

## 품질 검사

```powershell
ruff format --check main.py planning.py snapshot.py organization.py persistence.py plan_diff.py conflicts.py plan_apply.py analysis.py diagnostics.py ui real virtual
ruff check main.py planning.py snapshot.py organization.py persistence.py plan_diff.py conflicts.py plan_apply.py analysis.py diagnostics.py ui real virtual
python -m compileall -q main.py planning.py snapshot.py organization.py persistence.py plan_diff.py conflicts.py plan_apply.py analysis.py diagnostics.py ui real virtual
```

GitHub Actions의 `CI` workflow는 Windows와 Python 3.12에서 같은 검사를 실행합니다.

## 프로젝트 구조

```text
main.py                   앱 진입점
planning.py               Plan 작업 모델과 경로 검증
snapshot.py               SQLite 기반 파일 시스템 Snapshot
plan_diff.py              Plan Diff 시뮬레이션과 충돌 탐지
conflicts.py              충돌 해결 정책 적용
plan_apply.py             트랜잭션 적용, Rollback과 전체 Undo
organization.py           일괄 이름 변경과 정리 규칙
persistence.py            Plan·정리 규칙 JSON 저장과 불러오기
analysis.py               폴더 구조 통계
diagnostics.py            중복 파일과 문제 항목 탐지
ui/                       PySide6 화면, 대화상자와 백그라운드 Worker
real/                     실제 파일 탐색, 검색, 작업과 미리보기
virtual/workspace.py      가상 작업공간 모델과 저장·내보내기
.github/workflows/        코드 검사와 Windows 빌드
```
