# File Tree Viewer

File Tree Viewer는 실제 파일 탐색, 가상 트리 편집, Snapshot 기반의 복구 가능한 변경
계획을 다루는 Python·PySide6 데스크톱 도구입니다.

한국어 | [English](README.md)

## 주요 기능

- Real: 폴더 탭·즐겨찾기·이름/내용 검색·파일 작업과 세션 Undo
- Virtual: 트리·텍스트 편집, JSON 저장·불러오기와 실제 폴더 내보내기
- Plan: Snapshot 저장·비교, 변경 준비, Diff·적용·Rollback·복구
- 충돌 검토를 포함한 폴더 비교와 단방향·양방향 동기화
- 텍스트·이미지·바이너리 미리보기, 파일 비교와 메타데이터 확인
- 디스크 사용량·중복 분석, 작업·복구 기록과 보고서 내보내기
- 폴더 트리 미리보기, 클립보드 복사와 TXT·Markdown·JSON 내보내기
- 한국어·영어와 라이트·다크·시스템 테마

## 설치와 실행

저장소 폴더에서 Python 3.12 이상을 사용합니다.

```sh
python -m pip install -r requirements.txt
python main.py
```

Windows x64 포터블 빌드는 성공한
[Build Windows 실행](https://github.com/Koreapanda4444/File-tree-viewer/actions/workflows/build-windows.yml)의
`FileTreeViewer-windows-x64` 산출물에서 받을 수 있습니다.
산출물과 내부 프로그램 ZIP 전체를 풀고 `FileTreeViewer/FileTreeViewer.exe`를 실행합니다.
`_internal/`을 EXE와 함께 보관하세요. 데이터 위치·업데이트·이전은 포터블 실행 안내를 참고하세요.

## 문서

- [포터블 실행·업데이트·데이터 위치](docs/ko/portable.md)
- [데스크톱 사용법과 화면](docs/ko/usage.md)
- [개발·검증·빌드](docs/ko/development.md)

변경 적용 전 Diff와 충돌을 검토하세요. Snapshot은 파일 내용 백업이 아닌 메타데이터를
저장합니다. 대상 루트의 `.file-tree-viewer` 복구 자료를 보존하고 중요한 파일은 별도로
백업하세요. 휴지통 삭제는 Real Undo 대상이 아닙니다.

## 개발

```sh
python -m pip install ruff==0.16.6
python -m ruff format --check .
python -m ruff check .
python scripts/verify_core.py
python scripts/verify_gui.py
python scripts/verify_portable.py
python main.py --smoke-test
```

기존 검증은 임시 데이터를 사용합니다. Quality와 Build Windows는 Windows의 Python
3.12에서 EXE 시작·포터블 이동·ZIP 압축 해제 실행까지 검사합니다.
빌드 명령과 플랫폼 제약은 개발 안내를 참고하세요.
