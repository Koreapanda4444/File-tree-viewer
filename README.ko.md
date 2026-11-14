# File Tree Viewer

한국어 | [English](README.md)

File Tree Viewer는 실제 파일을 탐색하고 가상 폴더 트리를 편집하며 적용 전에 변경을
검토하는 Windows 데스크톱 프로그램입니다. Snapshot과 복구 가능한 Plan으로 변경 내용을
확인하면서 파일을 정리할 수 있습니다.

## 미리보기

![실제 파일 작업 화면](docs/images/real.png)

![변경 계획 화면](docs/images/plan.png)

## 주요 기능

- Real: 폴더 탭·즐겨찾기·이름/내용 검색·미리보기와 Undo를 지원하는 파일 작업
- Virtual: 트리·텍스트 편집, JSON 저장·불러오기와 실제 폴더 내보내기
- Plan: Snapshot 저장, 변경 미리보기·충돌 검토·적용·Rollback·복구
- 폴더 비교, 단방향·양방향 동기화, 디스크 사용량과 중복 분석
- 폴더 트리 내보내기, 작업·복구 기록과 보고서
- 한국어·영어 UI와 라이트·다크·시스템 테마

## 시작하기

저장소 폴더에서 Python 3.12 이상으로 실행합니다.

```sh
python -m pip install -r requirements.txt
python main.py
```

Windows에서는 `build_windows.bat`로
`dist/FileTreeViewer/FileTreeViewer.exe`를 만듭니다.
`_internal/`을 포함한 프로그램 폴더 전체를 함께 보관하세요. 빌드한 프로그램을
실행할 때는 Python이 필요하지 않습니다. 설정·업데이트·이동은 포터블 실행 안내를 참고하세요.

## 문서

- [데스크톱 사용법](docs/ko/usage.md)
- [포터블 실행과 데이터 위치](docs/ko/portable.md)

Plan 적용 전 Diff와 충돌을 검토하세요. Snapshot은 파일 내용 백업이 아닌 메타데이터를
저장합니다. 각 대상 루트의 `.file-tree-viewer` 복구 자료를 보존하고 중요한 파일은
별도로 백업하세요. 휴지통 삭제는 Real Undo 대상이 아닙니다.
