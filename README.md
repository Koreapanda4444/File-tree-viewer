# File Tree Viewer

Python과 PySide6로 만든 로컬 파일 탐색·분석·작업 계획 앱입니다. 기존 Real·Virtual·Plan 작업공간을 유지하며, Snapshot을 기준으로 변경을 검토하고 실제 파일에 적용합니다.

## 실행

Python 3.10 이상과 `requirements.txt`의 PySide6·Send2Trash·regex가 필요합니다. Windows CI는 Python 3.12에서 실행됩니다.

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 작업공간과 탐색

| 작업공간 | 기능 |
| --- | --- |
| Real File System | 실제 폴더 탐색·검색, 파일 작업과 Undo, 텍스트 편집, 이미지·바이너리 미리보기, 파일 비교·메타데이터 |
| Virtual File System | 메모리 기반 가상 트리 편집·Undo, JSON 저장·불러오기, 실제 폴더로 내보내기 |
| Plan | Snapshot 저장·비교, 구조·중복 분석, 변경 미리보기, 작업 편집·의존성 검사, 트랜잭션 적용·복구·Undo |

`Workspace` 메뉴와 **Ctrl+1 / Ctrl+2 / Ctrl+3**으로 전환합니다. **Import Real Root into Plan**은 Real 루트의 Snapshot을 만들고, **Open Snapshot Root in Real**은 현재 Snapshot의 실제 폴더를 엽니다. 좁은 창에서는 작업 버튼이 다음 줄로 배치됩니다.

`Settings → Preferences`에서 숨김 파일 표시, 미리보기 줄바꿈·글꼴 크기, 편집 가능한 파일 크기(1~8 MiB), 큰 파일의 미리보기 범위(64~2048 KiB), 새 수동 Plan 작업의 기본 충돌 정책(중단·건너뛰기)을 설정합니다. 창 크기·위치, 패널 비율, 선택 탭도 복원합니다. 기존 Plan의 명시적 충돌 정책은 유지합니다.

## 검색과 작업공간 연결

Real 검색은 입력 후 350 ms 대기해 실행하며 이전 검색을 취소하고 오래된 결과를 제외합니다. **Filters**에서 확장자·유형·크기·생성일·수정일·검색 범위를 조합하고 일반 문자열·와일드카드·정규식·대소문자 구분을 선택합니다. 정규식에는 길이·반복 횟수·실행 시간 제한이 있습니다. 생성일 필터는 파일 시스템이 실제 생성 시각을 제공하는 항목에만 적용합니다.

내용 검색은 Filters의 **File contents contain**에서 설정합니다. UTF-8·BOM UTF-16/32 자동 감지와 명시적 UTF-8·UTF-16·CP949·CP1252를 지원합니다. 결과에 줄·열·일치 문장을 표시합니다. 파일당 64 MiB, 줄당 128 KiB, 파일당 20개 일치 줄로 제한하며 바이너리·접근 불가·지원하지 않는 인코딩은 건너뜁니다. **Search Tools**에서 최근 검색·조건 Preset, 결과 위치 이동, JSON·CSV 내보내기를 사용합니다. Preset은 현재 탐색 폴더에 적용하며 최근 검색은 당시 폴더를 기억합니다. 내보내기는 새 파일명으로 저장합니다.

Real의 우클릭 **Add to Plan** 또는 Plan 화면으로 드래그해 복사·이동·이름 변경·삭제를 준비합니다. 필요한 Snapshot은 비동기로 만들고 경계와 원본 상태를 검사합니다. **Workspace → Add Virtual Workspace to Plan**은 기존 내보내기를 이용해 Virtual 구조와 텍스트 내용을 준비합니다. Plan Snapshot을 먼저 선택해야 하며 가상 텍스트는 파일당 1 MiB·전체 64 MiB까지 준비합니다. 더 큰 가상 작업공간은 기존 Export를 사용합니다. COPY와 텍스트 내용은 기존 Plan JSON 형식의 호환 가능한 확장 필드로 저장합니다.

## 폴더 비교와 동기화

1. **Workspace → Compare Folders**에서 좌우 폴더를 선택합니다. 다른 드라이브도 비교할 수 있습니다. 상태별 필터, 좌우 크기·시간·세부 정보와 기존 텍스트 파일 비교를 제공합니다.
2. 동기화를 준비하려면 **Verify all file contents with SHA-256**을 켜고 비교합니다. 메타데이터가 같은 다른 내용도 찾아내며 시각만 다른 동일 내용은 구별합니다.
3. **Plan Left → Right / Right → Left**로 복사·갱신 계획을 만듭니다. 대상에만 있는 항목은 기본적으로 보존하며 삭제 체크박스를 선택하면 백업 삭제를 계획합니다.
4. **Plan Bidirectional**은 마지막 성공 동기화의 이력을 비교합니다. 양쪽 변경은 기본 차단하며 Prefer Left / Right로 명시적으로 해결할 수 있습니다. 이력이 없으면 생성·삭제를 추측하지 않습니다. **Initialize**는 한쪽에만 있는 항목을 복사하는 초기 병합을 명시적으로 선택하며 삭제를 추론하지 않습니다. 폴더 삭제와 내부 변경이 겹치는 복합 충돌은 수동 해결이 필요합니다.
5. 별도 다중 루트 계획 화면에서 전체 생성·덮어쓰기·삭제 목록과 두 루트를 검토합니다. 검토 체크박스와 실행 확인을 거쳐야 실제로 변경됩니다. 실행 직전 전체 경로·파일 식별자·SHA-256을 다시 확인합니다.

동기화는 기존 단일 루트 Plan과 구분한 조정 계층을 사용합니다. 복사 내용을 대상 루트 내부에 먼저 준비하고, 각 루트에 기존 Journal·Mutation 로그와 백업을 유지합니다. 드라이브 간에는 원본을 읽어 복사하며 실제 변경·복원은 각 루트 안에서 수행합니다. 양쪽 루트에 쓰기 권한과 준비 복사본·백업을 보관할 여유 공간이 필요합니다.

**Cancel after current step**은 현재 단계가 끝난 뒤 양쪽 변경을 복원합니다. **Undo Both Roots** 또는 Plan의 **Undo Last Apply**는 참여 루트를 함께 복원합니다. 중단된 동기화는 어느 참여 루트의 Snapshot에서든 **Recover Interrupted Plan**으로 함께 복구합니다. 다른 드라이브가 분리되거나 기록이 손상되었거나 파일이 외부에서 바뀌면 복구를 차단하고 데이터를 보존합니다. 모든 참여 드라이브를 다시 연결해야 합니다. 복구·Undo 후에는 모호한 과거 이력을 제거하므로 다음 양방향 계획에서 초기 상태를 다시 검토할 수 있습니다.

링크·Reparse Point, 중첩된 루트, 유형·대소문자 충돌, 스캔 오류, 중첩된 복구 자료는 동기화 적용을 차단합니다. 원격 파일 시스템·정전 내구성·네이티브 Windows 탐색기와의 수동 드래그 조작은 환경별 추가 검증이 필요합니다. 트랜잭션 백업은 자동 삭제하지 않습니다.

## Snapshot 저장과 비교

1. Plan의 **Import Snapshot**으로 실제 폴더를 스캔합니다. 스캔은 취소할 수 있으며 원본 내용을 변경하지 않습니다.
2. **Save Snapshot**에서 이름을 지정해 `.ftvsnapshot` 파일로 보관합니다. **Open Snapshot**은 검증한 저장 파일을 전용 임시 데이터베이스로 복사해 읽습니다. 기존 임시 Snapshot 기능도 유지됩니다.
3. **Compare Snapshot**에서 비교할 이전 Snapshot을 선택합니다. 생성·삭제·수정·이동/이름 변경 후보를 유형별로 필터링하고 전후 메타데이터를 확인합니다.

Snapshot은 경로·크기·시각·파일 식별자 등의 **메타데이터**를 저장하며 파일 내용을 백업하지 않습니다. 수정 비교는 저장된 메타데이터 기준입니다. 이동/이름 변경은 같은 루트의 고유 파일 식별자 등에 기반한 후보이므로 확정 판정이 아닙니다. 파일 내용과 메타데이터를 모두 동일하게 조작한 변경은 이 비교만으로 찾을 수 없습니다.

원래 폴더가 없어도 저장 Snapshot을 열어 탐색·분석·비교할 수 있습니다. 실제 Plan 적용에는 접근 가능한 원본 루트가 필요합니다.

## Plan 만들기와 검토

Snapshot 트리에서 **New File / New Folder / Rename / Move / Delete**를 사용하거나 드래그로 이동을 계획합니다. 이 단계에서는 실제 파일이 바뀌지 않습니다.

- **Batch Rename**: 접두사·접미사·문자열 치환·대소문자·순번으로 이름 변경을 준비합니다.
- **Organize Files**: 이름 패턴, 확장자, 크기, 수정 날짜 조건으로 이동·하위 폴더 생성 작업을 준비합니다. Downloads·Photos·Development Preset과 사용자 규칙 JSON 저장·불러오기를 지원합니다.
- **Preview Structure**: 원본 파일을 변경하지 않고 예상 적용 후 트리를 생성해 전후 구조를 나란히 표시합니다.
- 오른쪽 작업 표의 **Edit / Move Up / Move Down / Remove Selected / Clear Plan**으로 요청을 관리합니다. **Set Policy**는 선택 작업들의 정책을 일괄 변경합니다.
- **Review Dependencies**는 경로 의존성, 순환 관계, 안전한 실행 순서와 문제를 보여줍니다. 안전하게 임시 보관 후 교환할 수 있는 순환 이동과 실행 불가능한 복합 충돌을 구분합니다.
- **Save Plan / Load Plan**은 기존 JSON 형식을 유지합니다. 다른 루트에서 불러오면 원래 루트를 안내하고 현재 루트 기준으로 다시 검증합니다.

**Diff / Simulate**는 외부 파일 변경, 원본·대상 중복, 대상 존재, 상위 폴더, 경로 의존성 등의 충돌을 검사합니다. 가능한 충돌은 Overwrite·Auto Rename·Skip으로 해결한 뒤 다시 검증합니다.

이동할 폴더 내부의 다른 작업처럼 현재 실행 단계로 안전한 순서를 보장할 수 없는 조합은 차단합니다. 해당 변경은 Plan을 나누어 적용해야 합니다. Plan은 루트 밖 경로와 링크를 통한 경로 이탈을 거부하며, 서로 다른 파일 시스템 사이의 이동은 지원하지 않습니다.

## 적용·복구·Undo

**Apply All**은 적용 수·삭제 수·덮어쓰기 수와 루트를 확인한 뒤 실제 파일을 변경합니다. 중요한 데이터는 별도 백업하고 Diff를 확인한 후 적용하세요.

- 작업 직전 경로·파일 상태를 재검증하며, 기존 파일을 예기치 않게 덮어쓰지 않습니다.
- 오류·취소 시 완료된 변경을 Rollback합니다. 상태 표시줄에서 진행률과 취소 대기를 확인하고 실행 중인 작업으로 이동할 수 있습니다.
- `.file-tree-viewer/transactions`에 Journal과 순서·체크섬이 있는 Mutation 로그를 기록합니다. 기존 v2 기록과 호환되며 새 기록은 v3입니다.
- 앱 시작 시 마지막 작업 루트에서 미완료·손상 기록과 트랜잭션 잠금을 찾습니다. 다른 루트는 Snapshot을 가져올 때 확인합니다. 복구가 필요하면 Apply를 차단하고 **Recover Interrupted Plan**을 안내합니다.
- 복구는 중단된 변경을 되돌립니다. 반복 복구와 이미 복원된 항목을 고려하며, 외부 변경이나 모호한 기록은 사용자 데이터를 보존하고 수동 확인을 요청합니다.
- **Undo Last Apply**는 마지막으로 완료된 Plan 전체를 복원합니다. 외부 변경 충돌이나 부분 실패가 있으면 기록을 남기고 추가 확인을 요구합니다. Undo·복구 도중에는 종료를 기다립니다.

복구 자료는 Undo에 필요하므로 임의로 삭제하지 마세요. 저장장치 자체의 고장이나 모든 파일 시스템의 전원 손실 내구성까지 보장하는 백업 시스템은 아닙니다.

## 파일 분석과 보고서

**Analyze Structure**는 파일·폴더 수, 전체 크기, 확장자 분포, 큰 파일·폴더, 깊은 경로를 분석합니다. **Find Problems**는 중복 파일, 빈 폴더, 대소문자 충돌, 긴 경로, 접근 오류, 끊어진 링크, Snapshot 이후 변경·삭제 등을 확인합니다.

중복은 크기로 후보를 좁힌 후 SHA-256으로 확인합니다. 그룹을 크기·절감 가능 용량 등으로 정렬하고 후보를 검토할 수 있습니다. 하드 링크는 물리적 복사본 수를 고려합니다. 삭제 후보를 Plan에 넣기 전에 내용과 남길 복사본을 다시 검증하며, 모든 복사본 삭제와 이미 예정된 삭제·변경 충돌을 차단합니다. 검토 단계에서는 실제로 삭제하지 않습니다.

**Export Report**는 JSON·CSV·HTML로 Snapshot 항목과 구조 분석을 출력하고, 현재 Snapshot에서 실행한 최근 문제 분석 결과가 있으면 함께 포함합니다. 중복 분석을 자동으로 다시 실행하지는 않으며 보고서에 포함 여부를 표시합니다. 큰 결과는 스트리밍으로 기록합니다. HTML 특수문자와 CSV 수식 문자를 처리하고 실패·취소 시 기존 출력 파일을 보존합니다.

## Real 파일 미리보기와 비교

- 텍스트: 인코딩·줄바꿈을 고려해 표시하고 임시 파일을 통한 원자적 저장을 지원합니다. 외부 변경을 재확인합니다.
- 이미지: 지원되는 PNG·JPEG·GIF·BMP·TIFF·WebP 등을 제한된 크기로 디코딩해 표시합니다. 디코딩 불가·크기 초과 이미지는 안내와 바이너리 보기를 제공합니다.
- 바이너리: 최대 4 KiB를 16진수로 표시합니다. 큰 파일은 설정된 범위만 읽으며, 링크·잘린 미리보기는 읽기 전용입니다.
- **Compare Files**: 선택한 두 텍스트 파일을 나란히 표시하고 추가·삭제·변경 구간과 행 번호를 보여줍니다. 각 파일 최대 2 MiB·10,000행까지 비교하며 초과 시 안내합니다. 원본은 수정하지 않습니다.
- **Inspect Metadata**: 크기·할당량, 나노초 시각, 모드 권한, 파일 식별자, 하드 링크 수, Windows 속성, 링크 정보를 표시하고 JSON으로 복사합니다. 플랫폼에서 제공하지 않는 항목은 Unavailable로 표시하며 전체 ACL은 평가하지 않습니다.

Real의 개별 파일 작업은 생성·이름 변경·복사·이동·휴지통 삭제를 지원합니다. Undo 가능 여부는 작업 종류와 외부 변경 상태에 따라 달라집니다. 휴지통 삭제는 Real Undo 대상이 아닙니다.

## 성능과 구조

Snapshot·검색·Snapshot 비교는 SQLite와 페이지 캐시를 사용합니다. 트리는 필요한 하위 항목만 읽고, 대량 경로 조회는 배치로 처리합니다. 스캔·검색·미리보기·파일 비교·분석·저장·Diff·적용은 Worker에서 실행되며 안전한 종료를 기다립니다.

| 주요 모듈 | 역할 |
| --- | --- |
| `fs_paths.py`, `fs_ops.py`, `file_operations.py` | 공통 경로 검증과 파일 작업 |
| `snapshot.py`, `snapshot_store.py`, `snapshot_compare.py` | Snapshot 스캔·영구 저장·비교 |
| `planning.py`, `plan_dependencies.py`, `plan_diff.py`, `plan_preview.py` | Plan 모델·의존성·시뮬레이션·구조 미리보기 |
| `plan_apply.py`, `journal.py`, `recovery.py` | 트랜잭션·Rollback·Undo·복구 |
| `file_content.py`, `file_preview.py`, `file_comparison.py`, `file_metadata.py` | 안전한 파일 읽기·미리보기·비교·속성 |
| `analysis.py`, `diagnostics.py`, `duplicate_management.py`, `reports.py` | 구조·문제·중복 분석과 보고서 |
| `ui/`, `real/`, `virtual/` | 화면·Worker·탐색 모델·가상 작업공간 |

## Windows 실행 파일

`build_windows.bat`은 자신의 위치에서 실행되며 전용 임시 가상환경과 빌드 디렉터리를 사용합니다. 시작 진단, PyInstaller 빌드, 실행 파일 시작·종료 검사를 수행한 뒤 중간 파일을 정리합니다.

```powershell
build_windows.bat
```

출력은 `release\FileTreeViewer.exe`입니다. GitHub Actions의 **Build Windows**는 main Push·`v*` 태그·수동 실행에서 빌드하고 실행 진단 후 `FileTreeViewer-windows-x64` 아티팩트에 EXE와 `SHA256.txt`를 업로드합니다. 아티팩트 보관 기간은 14일입니다.

## 품질 확인

```powershell
python -m pip install ruff==0.16.6
ruff format --check .
ruff check .
python -m compileall -q main.py ui real virtual
python main.py --smoke-test
```

`--smoke-test`는 임시 설정으로 세 작업공간을 열고 닫아 시작 오류를 확인합니다. 사용자 파일 작업이나 기존 설정 변경을 수행하지 않습니다. CI는 전체 소스 구문·가져오기 검사와 이 진단을 Windows에서 실행하고, Windows 빌드는 패키징된 EXE도 같은 방식으로 실행합니다. 기능·삭제·이동·복구 검증용 코드는 저장소 외부 임시 디렉터리에서 실행하며 테스트 파일과 결과는 커밋하지 않습니다.
