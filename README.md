# 유튜브 → MP4 다운로더

yt-dlp + ffmpeg 기반, PyQt6 GUI. 유튜브 링크를 붙여넣고 버튼 한 번으로 MP4 저장.

- **화질 선택**: 최고 화질 / 1080p / 720p / 480p / 360p (낮출수록 용량 작음)
- **재생목록 지원**: `/playlist` 링크를 넣으면 재생목록 제목 폴더에 전체 다운로드 ([n/M] 진행 표시)
- **PPT 호환**: H.264+AAC 코덱을 우선 선택해 파워포인트에 바로 삽입 가능
- **완전 독립 exe**: ffmpeg 내장, 아무것도 설치할 필요 없음
- **자동 업데이트**: 다운로드 실패 시(유튜브 변경 등) 깃허브 릴리즈의 새 exe로 자동 교체
- **영상 편집**: 다운로드한(또는 아무) 영상을 구간 자르기(MP4) / **WMV 변환** / **GIF 만들기** — 시작·끝 시간(`90`, `1:30`, `0:01:30.5`)으로 구간 지정, GIF는 fps·가로 크기 조절. 결과는 원본 옆에 `이름_cut.mp4`·`이름_wmv.wmv`·`이름_gif.gif`로 저장
- **로그인 쿠키 재시도**: 연령 제한·403 등 비로그인 차단 영상은 브라우저(크롬/엣지/파이어폭스) 로그인 정보로 자동 재시도, 그래도 안 되면 유튜브의 모든 재생 클라이언트를 순서대로 시도

## 그래도 안 받아지는 영상이 있으면 (cookies.txt)

최신 크롬/엣지는 보안 강화로 외부 프로그램의 쿠키 읽기를 막는 경우가 있다. 이때는:

1. 크롬 확장 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) 설치
2. youtube.com에 로그인한 상태에서 확장 아이콘 → Export → `cookies.txt` 저장
3. 그 파일을 `YoutubeMP4.exe`와 **같은 폴더**에 두기

파일이 있으면 앱이 항상 그 쿠키로 인증한다 (자동 재시도보다 우선).

## exe (권장)

Releases 탭에서 `YoutubeMP4.exe` 다운로드 후 실행하면 끝.

업데이트는 창 하단 **업데이트 확인** 버튼 — 최신 릴리즈 exe를 받아 스스로 교체하고 재시작한다.
다운로드가 갑자기 안 될 때도 새 릴리즈가 있으면 자동으로 교체된다.

## 소스로 실행 (개발용)

```
pip install -r requirements.txt
python youtube_mp4.py
```

`ffmpeg`는 별도로 설치되어 PATH에 있어야 함. 창 하단 버튼은 pip로 yt-dlp만 업데이트.

## 릴리즈 만들기

Actions 탭 → **release** 워크플로우 → Run workflow에 태그(예: `v1.2.0`) 입력.
Windows에서 최신 yt-dlp + ffmpeg를 넣어 exe를 빌드하고 릴리즈에 자동 첨부한다.
