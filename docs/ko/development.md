# 개발·검증·빌드

한국어 | [English](../development.md)

## 소스 실행

Python 3.12 이상을 사용합니다. Windows CI와 배포 빌드는 64비트 Python 3.12에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
python main.py
```

소스 실행에서도 저장소 옆 `data/`에 일반 설정과 기록을 저장합니다. 이 폴더와 빌드 산출물은 Git에서 제외됩니다. `application_paths.py`는 실행 파일 위치, PyInstaller 리소스 위치, 현재 작업 디렉터리를 구분합니다.

## 검증

```powershell
python -m pip install ruff==0.16.6
ruff format --check .
ruff check .
python -m compileall -q -x '(^|[/\\])(\.git|\.venv|build|dist|release)([/\\]|$)' .
python scripts/verify_core.py
python scripts/verify_gui.py
python scripts/verify_portable.py
python main.py --smoke-test
```

| 진입점 | 확인 내용 |
| --- | --- |
| `verify_core.py` | 임시 폴더의 파일 생성·이름 변경·복사·이동·취소·충돌·외부 변경 보호, Snapshot 저장, Plan 적용·Undo·Rollback·잠금, 동기화·충돌, 트리 출력 |
| `verify_gui.py` | 백그라운드 전체 정렬·페이지 표시·오래된 작업 취소, 외부 생성·이름 변경 감지, 새로고침 선택 복원, 검색, 트리 미리보기·복사 |
| `verify_portable.py` | 실행·리소스 경로 분리, 포터블 설정·기록, 기존 데이터 보존·중복 이전 방지, 읽기 전용 오류(Linux) |
| `main.py --smoke-test` | 임시 데이터로 영어·한국어의 Real·Virtual·Plan 시작·종료, 아이콘 로딩 |
| `verify_windows.py` | 실제 x64 EXE, 한글·공백 경로, 다른 CWD, 폴더 이동 후 임의 설정 토큰·테마·기존 기록 유지, ZIP 체크섬·구조·압축 해제 실행 |

검증용 파일은 임시 폴더에서 만들고 정리합니다. `tests/`나 사용자 파일을 사용하지 않습니다. 배포 ZIP에는 검증 스크립트·샘플 데이터·개발 환경·사용자 데이터가 들어가지 않습니다.

## Windows 포터블 빌드

```powershell
.\build_windows.bat
```

전용 임시 가상환경에서 검증·PyInstaller `--onedir` 빌드·EXE 실행·포터블 이동·ZIP 재실행까지 수행한 후 중간 환경을 정리합니다.

| 경로 | 내용 |
| --- | --- |
| `dist/FileTreeViewer/` | EXE와 `_internal` 런타임 |
| `release/FileTreeViewer-1.0.0-windows-x64.zip` | 프로그램 폴더와 한·영 실행 안내 |
| `release/SHA256SUMS` | ZIP의 SHA-256 |

CI의 **Build Windows**도 같은 Python 스크립트로 빌드·패키징합니다. 검증된 ZIP·체크섬을 **FileTreeViewer-windows-x64** 아티팩트로 14일간 보관합니다. 품질 검사는 `quality.yml`, 포터블 빌드는 `build-windows.yml`에서 관리합니다. 기존 Windows 빌드 트리거와 산출물 보관 방식은 유지합니다.

## 범위와 제약

- 자동 검증은 임시 로컬 폴더를 사용합니다. 실제 네트워크 드라이브, USB 분리, 전원 차단, Windows Explorer 수동 드래그, 여러 DPI의 실제 모니터는 환경별 확인 대상입니다.
- Linux에서 화면 구성과 데이터 안전성을 확인할 수 있지만 Windows EXE 검증은 Windows에서 수행합니다.
- 단일 루트 Plan은 파일 시스템을 가로지르는 이동을 지원하지 않습니다. 다중 루트 동기화는 대상 루트에 복사본을 준비하고 각 루트의 로컬 트랜잭션을 조정합니다.
- Snapshot 비교는 메타데이터 기준이며 파일 내용 백업이 아닙니다. 중복과 동기화의 내용 검증은 SHA-256을 별도로 사용합니다.
- 실제 파일의 복구 자료는 프로그램의 `data`와 분리되어 대상 루트에 남습니다. OS·파일 시스템·저장장치 전체의 고장을 대신하는 백업 시스템은 아닙니다.

## 소스 구조


Snapshot·검색·Snapshot 비교는 SQLite와 페이지 캐시를 사용합니다. 트리는 필요한 하위 항목만 읽고, 대량 경로 조회는 배치로 처리합니다. 스캔·검색·미리보기·파일 비교·분석·저장·Diff·적용은 Worker에서 실행되며 안전한 종료를 기다립니다.

| 주요 모듈 | 역할 |
| --- | --- |
| `fs_paths.py`, `fs_ops.py`, `file_operations.py` | 공통 경로 검증과 파일 작업 |
| `snapshot.py`, `snapshot_store.py`, `snapshot_compare.py` | Snapshot 스캔·영구 저장·비교 |
| `planning.py`, `plan_dependencies.py`, `plan_diff.py`, `plan_preview.py` | Plan 모델·의존성·시뮬레이션·구조 미리보기 |
| `plan_apply.py`, `journal.py`, `recovery.py` | 트랜잭션·Rollback·Undo·복구 |
| `file_content.py`, `file_preview.py`, `file_comparison.py`, `file_metadata.py` | 안전한 파일 읽기·미리보기·비교·속성 |
| `analysis.py`, `diagnostics.py`, `duplicate_management.py`, `reports.py` | 구조·문제·중복 분석과 보고서 |
| `disk_usage.py`, `operation_history.py`, `recovery_history.py` | 디스크 집계·영구 감사 기록·기존 복구 자료 검토 |
| `folder_compare.py`, `sync_plan.py`, `sync_apply.py`, `sync_baseline.py`, `sync_preview.py` | 폴더 비교·다중 루트 동기화·이력·전후 트리 |
| `ui/`, `real/`, `virtual/` | 화면·Worker·탐색 모델·가상 작업공간 |

루트 모듈은 파일 시스템·Snapshot·Plan·동기화의 공통 로직입니다. `real/`은 실제
탐색 모델과 Qt Worker, `virtual/`은 가상 데이터 저장·모델·작업공간을 담당합니다.
기존 절대 Import와 PyInstaller 진입점을 유지하기 위해 전체 소스를 패키지로 옮기지 않습니다.
`ui/window.py`는 공통 탐색 화면과 Real·Virtual 수명 주기를 조정하며,
`ui/plan.py`는 Plan·작업 취소·복구 화면을 조정합니다. 표시 모델과 실행 로직을 구분하되
상태가 연결된 제어 코드를 파일 크기만으로 나누지 않습니다.

`assets/app-icon-source.png`는 편집 원본, `app-icon.png`는 앱·창 아이콘,
`app-icon.ico`는 Windows EXE 아이콘입니다. 원본은 포터블 ZIP에 포함하지 않습니다.
번역은 `ui/translations/ko.json`에 유지합니다. 로컬 환경·캐시·빌드 산출물과 `data/`,
대상 루트의 `.file-tree-viewer/`는 Git에서 제외합니다.

`ui/plan_models.py`는 페이지 단위 Snapshot 트리와 Plan 표 모델을 담당합니다.
Plan 제어 코드와 동기화 미리보기가 이 모듈을 직접 사용하여 미리보기가 전체 제어 코드에
의존하지 않도록 합니다. 기존 `ui.plan`의 모델 Import 경로도 유지합니다.
