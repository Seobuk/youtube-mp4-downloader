# 유튜브 → MP4 다운로더

yt-dlp + ffmpeg 기반, PyQt6 GUI. 유튜브 링크를 붙여넣고 버튼 한 번으로 MP4 저장.

## 소스로 실행

```
pip install -r requirements.txt
python youtube_mp4.py
```

`ffmpeg`는 별도로 설치되어 PATH에 있어야 함 (bestvideo+bestaudio 병합에 필요).

## exe

Releases 탭에서 `YoutubeMP4.exe` 다운로드 후 실행. `ffmpeg`는 별도로 PATH에 설치 필요.

유튜브가 바뀌어 다운로드가 안 되면:

- **소스 실행 중이면** 창 하단 **yt-dlp 업데이트** 버튼 → 앱 재시작.
- **exe 실행 중이면** 자체 업데이트가 안 되므로 Releases에서 새 exe를 받아 교체.
  (구버전 exe에서 업데이트 버튼을 누르면 앱이 계속 다시 뜨며 멈추는 버그가 있었음 — 새 exe에서는 안내 메시지만 표시됨)
