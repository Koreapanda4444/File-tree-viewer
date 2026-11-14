# 포터블 실행 안내

한국어 | [English](../portable.md)

## 실행

1. 빌드한 `dist/FileTreeViewer/` 또는 전달받은 프로그램 폴더 전체를 준비합니다.
2. `FileTreeViewer` 폴더의 `FileTreeViewer.exe`를 실행합니다.
3. `_internal` 폴더를 EXE와 함께 보관합니다. 별도 Python 설치는 필요하지 않습니다.

Windows x64용입니다. 프로그램과 `data`에 쓸 수 있는 일반 사용자 폴더에서 실행하세요. 프로그램 폴더가 읽기 전용이면 다른 위치로 전체를 옮깁니다. 압축 파일 안에서 바로 실행하거나 EXE만 복사하지 마세요.

## 설정과 언어

**Settings → Preferences**에서 한국어·영어와 Light·Dark·System 테마를 선택합니다. 한국어 화면에서는 **설정 → 환경설정**입니다. 언어 변경 후에는 앱을 재시작하세요.

- `data/settings/preferences.ini`: 설정, 창·패널 배치, 즐겨찾기, 최근 위치 등
- `data/history/operation-history.sqlite3`: 작업 기록 색인
- 저장한 Virtual·Snapshot·Plan·보고서: 직접 선택한 위치

필요한 폴더만 실행 중 생성합니다. 기록 색인은 실제 복구용 백업과 다릅니다. Snapshot은 메타데이터를 기록하며 파일 내용 자체를 백업하지 않습니다.

## 업데이트와 다른 PC로 이동

앱과 작업을 모두 종료한 뒤 진행하세요.

- 이동: `data`와 `_internal`을 포함한 `FileTreeViewer` 폴더 전체를 복사합니다.
- 업데이트: 새 프로그램 폴더를 별도로 준비하고 기존 `data` 폴더를 새 프로그램 폴더로 복사합니다. 새 버전에서 확인하기 전까지 이전 폴더를 보관합니다. 실행 중인 DB 파일을 복사하지 마세요.
- 새 PC에서 예전 경로·드라이브 문자가 유효한지 확인합니다. 이동은 외부 파일·Snapshot·Plan의 경로를 자동 변환하지 않습니다.
- 대상 루트의 `.file-tree-viewer/transactions` 등 복구 자료는 이동·삭제하지 않습니다. 여러 루트 동기화의 복구에는 모든 참여 드라이브가 필요합니다.

## 이전 버전 데이터 가져오기

이전 설정이나 기록이 발견되면 첫 실행 시 복사를 제안합니다. 나중에는 **설정 → 이전 데이터 가져오기**를 사용합니다.

원본을 삭제하지 않고, 현재 포터블 설정에 없는 값만 복사합니다. 포터블 기록 DB가 이미 있으면 덮어쓰거나 병합하지 않습니다. 가져오기가 끝나면 재시작하세요. 실패하면 원본은 보존되므로 저장 권한을 확인한 뒤 다시 시도할 수 있습니다.

## 파일 변경과 복구

Real은 실제 파일을 관리합니다. Virtual은 내보내기 전까지 가상 데이터만 바꿉니다. Plan은 먼저 Snapshot과 변경 계획을 만든 뒤 Diff·충돌·미리보기를 검토하고 적용합니다. 중요한 파일은 따로 백업하세요.

Plan·동기화의 Journal·Mutation 로그·백업·잠금은 각 대상 루트의 `.file-tree-viewer`에 남습니다. 이 자료를 지우면 Undo·중단 복구가 불가능해질 수 있습니다. 외부에서 파일이 바뀌거나 드라이브가 분리되면 복구를 차단하고 자료를 보존합니다. Real의 휴지통 삭제는 Real Undo 대상이 아닙니다.

## Windows 실행 파일 만들기

Windows에서 64비트 Python 3.12 이상을 `python` 명령으로 사용할 수 있어야 합니다.
저장소의 BAT를 실행하세요.

```powershell
.\build_windows.bat
```

현재 Python 환경에 필요한 의존성을 설치하고 `dist/FileTreeViewer/FileTreeViewer.exe`를
만듭니다. `_internal/`을 포함한 폴더 전체가 결과물입니다. 설치 프로그램·ZIP·체크섬은 만들지 않습니다.
