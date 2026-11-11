# 개발·빌드·검증

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

CI의 **Build Windows**도 같은 Python 스크립트로 빌드·패키징합니다. 검증된 ZIP·체크섬을 **FileTreeViewer-windows-x64** 아티팩트로 14일간 보관합니다. `main` Push·`v*` 태그·수동 실행은 빌드만 수행하며, 워크플로에는 릴리스 게시·태그 생성 작업과 쓰기 권한이 없습니다.

## 배포 준비 상태

버전·아이콘·실행 안내·릴리스 노트는 1.0.0 기준으로 준비합니다. 정식 Release와 `v1.0.0` 태그 생성은 사용자의 별도 게시 지시까지 보류합니다. 게시 시점에는 최종 `main` 커밋의 CI·Build Windows 성공과 해당 실행의 ZIP·SHA256SUMS를 다시 확인해야 합니다. [릴리스 노트 초안](RELEASE_NOTES.md)

## 범위와 제약

- 자동 검증은 임시 로컬 폴더를 사용합니다. 실제 네트워크 드라이브, USB 분리, 전원 차단, Windows Explorer 수동 드래그, 여러 DPI의 실제 모니터는 환경별 확인 대상입니다.
- Linux에서 화면 구성과 데이터 안전성을 확인할 수 있지만 Windows EXE 검증은 Windows에서 수행합니다.
- 단일 루트 Plan은 파일 시스템을 가로지르는 이동을 지원하지 않습니다. 다중 루트 동기화는 대상 루트에 복사본을 준비하고 각 루트의 로컬 트랜잭션을 조정합니다.
- Snapshot 비교는 메타데이터 기준이며 파일 내용 백업이 아닙니다. 중복과 동기화의 내용 검증은 SHA-256을 별도로 사용합니다.
- 실제 파일의 복구 자료는 프로그램의 `data`와 분리되어 대상 루트에 남습니다. OS·파일 시스템·저장장치 전체의 고장을 대신하는 백업 시스템은 아닙니다.
