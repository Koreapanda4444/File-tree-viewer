# File Tree Viewer

File Tree Viewer는 실제 파일 시스템과 가상 작업공간을 트리 형태로 탐색하는 PySide6 데스크톱 앱입니다. 서버 없이 로컬에서 동작합니다.

## 주요 기능

- 폴더를 펼칠 때만 내용을 읽는 지연 로딩
- 표시 개수 제한 없는 파일·폴더 검색
- 파일과 폴더 생성, 이름 변경, 복사, 이동, 휴지통 삭제
- 가능한 작업에 대한 실행 취소
- 파일 정보와 텍스트 미리보기 및 편집
- 가상 파일 트리 생성, 저장, 불러오기, 실제 폴더로 내보내기
- 대규모 검색 결과를 위한 디스크 저장과 페이지 캐시

## 실행

Python 3.10 이상이 필요합니다.

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Windows 실행 파일 만들기

`build_windows.bat`을 실행하면 단일 실행 파일이 생성됩니다.

```text
release\FileTreeViewer.exe
```

GitHub의 `Actions` → `Build Windows` → `Run workflow`에서도 빌드할 수 있습니다. 완료된 실행 파일은 `FileTreeViewer-windows-x64` 아티팩트에서 받을 수 있습니다.

## 구조

```text
main.py                   앱 실행
ui/window.py              메인 화면과 사용자 동작
real/                     실제 파일 탐색, 검색, 작업, 미리보기
virtual/workspace.py      가상 작업공간 모델과 저장·내보내기
.github/workflows/        코드 검사와 Windows 빌드
```
