"""유튜브 링크 -> MP4 다운로드 GUI (pywebview/HTML). yt-dlp + ffmpeg 래퍼.

실행: pip install yt-dlp pywebview  →  python youtube_mp4.py
(Windows 11은 Edge WebView2 내장, 추가 런타임 불필요)
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yt_dlp


def format_status(d):
    """진행률 훅 dict -> (퍼센트, 메시지)."""
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes", 0)
        pct = done / total * 100 if total else 0
        speed = d.get("speed") or 0
        return pct, f"다운로드 중... {pct:5.1f}%  ({speed / 1e6:.1f} MB/s)"
    if d["status"] == "finished":
        return 100, "병합 중... (ffmpeg)"
    return 0, d["status"]


def download(url, out_dir, report):
    """url을 out_dir에 mp4로 저장. report(pct, msg)로 진행 상황 통지."""
    opts = {
        # bestvideo+bestaudio mp4 우선, 없으면 최선의 단일 mp4, 그것도 없으면 아무거나
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(Path(out_dir) / "%(title)s.%(ext)s"),
        "progress_hooks": [lambda d: report(*format_status(d))],
        "noplaylist": True,  # 재생목록 링크여도 영상 1개만
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    report(100, f"완료: {info.get('title', '')}.mp4")


def update_ytdlp(report):
    """pip로 yt-dlp 최신화. 완료 후 앱 재시작 필요."""
    if getattr(sys, "frozen", False):
        # exe에서는 sys.executable이 이 앱 자신 → pip 대신 앱이 무한 재실행됨
        report(0, "exe 버전은 자체 업데이트 불가 — Releases에서 새 exe를 받아 교체하세요")
        return
    report(0, "yt-dlp 업데이트 중... (잠시 기다리세요)")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode == 0:
        report(100, "업데이트 완료 — 앱을 다시 실행하세요")
    else:
        tail = (r.stderr or r.stdout).strip().splitlines()
        report(0, f"업데이트 실패: {tail[-1] if tail else '알 수 없는 오류'}")


HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0; padding: 18px;
         background: #f4f5f7; color: #1c1e21; }
  h1 { font-size: 15px; margin: 0 0 4px; font-weight: 600; }
  label { display: block; font-size: 12px; opacity: .75; margin: 12px 0 4px; }
  input { width: 100%; padding: 8px 10px; font-size: 13px; border: 1px solid #ccc;
          border-radius: 6px; background: #fff; color: inherit; }
  .row { display: flex; gap: 8px; } .row input { flex: 1; }
  button { padding: 8px 14px; font-size: 13px; border: 0; border-radius: 6px;
           background: #e63946; color: #fff; cursor: pointer; }
  button.sec { background: #5f6368; }
  button:disabled { opacity: .5; cursor: default; }
  #go { width: 100%; margin-top: 14px; padding: 12px; font-size: 14px; font-weight: 600; }
  progress { width: 100%; height: 8px; margin-top: 14px; }
  #status { font-size: 12px; margin-top: 8px; min-height: 16px; opacity: .85; }
  footer { margin-top: 14px; font-size: 11px; opacity: .55; display: flex;
           justify-content: space-between; align-items: center; }
  @media (prefers-color-scheme: dark) {
    body { background: #202124; color: #e8eaed; }
    input { background: #2c2d30; color: #e8eaed; border-color: #444; }
  }
</style></head><body>
  <h1>유튜브 → MP4</h1>
  <label>유튜브 링크</label>
  <input id="url" placeholder="https://youtu.be/...">
  <label>저장 폴더</label>
  <div class="row">
    <input id="dir">
    <button class="sec" onclick="pickDir()">폴더</button>
  </div>
  <button id="go" onclick="start()">MP4 다운로드</button>
  <progress id="bar" max="100" value="0"></progress>
  <div id="status">대기 중</div>
  <footer>
    <span id="ver"></span>
    <button class="sec" id="upd" onclick="update()">yt-dlp 업데이트</button>
  </footer>
<script>
  function setStatus(pct, msg) {
    document.getElementById('bar').value = pct;
    document.getElementById('status').textContent = msg;
  }
  function busy(on) { for (const id of ['go', 'upd']) document.getElementById(id).disabled = on; }
  async function start() {
    const url = document.getElementById('url').value.trim();
    if (!url) { setStatus(0, '링크를 입력하세요'); return; }
    busy(true); setStatus(0, '시작 중...');
    await pywebview.api.download(url, document.getElementById('dir').value);
    busy(false);
  }
  async function pickDir() {
    const d = await pywebview.api.pick_dir();
    if (d) document.getElementById('dir').value = d;
  }
  async function update() { busy(true); await pywebview.api.update(); busy(false); }
  window.addEventListener('pywebviewready', async () => {
    document.getElementById('dir').value = await pywebview.api.default_dir();
    document.getElementById('ver').textContent = 'yt-dlp ' + await pywebview.api.version();
  });
</script></body></html>"""


class Api:
    """JS <-> Python 브릿지. pywebview가 각 메서드를 별도 스레드에서 호출."""
    def __init__(self):
        self.window = None
        self._last_pct = -1

    def version(self):
        return yt_dlp.version.__version__

    def default_dir(self):
        return str(Path.home() / "Downloads")

    def pick_dir(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None

    def download(self, url, out_dir):
        url = (url or "").strip()
        if not url:
            return self._status(0, "링크를 입력하세요")
        if not shutil.which("ffmpeg"):
            return self._status(0, "ffmpeg가 없습니다 (병합 불가)")
        self._last_pct = -1
        try:
            download(url, out_dir, self._status)
        except Exception as e:  # 네트워크/포맷 오류를 GUI에 그대로 표시
            self._status(0, f"오류: {e}")

    def update(self):
        self._last_pct = -1
        try:
            update_ytdlp(self._status)
        except Exception as e:
            self._status(0, f"오류: {e}")

    def _status(self, pct, msg):
        ip = int(pct)
        # ponytail: 다운로드 진행 틱을 1% 단위로 스로틀 (evaluate_js 폭주 방지)
        if msg.startswith("다운로드 중") and ip == self._last_pct:
            return
        self._last_pct = ip
        self.window.evaluate_js(f"setStatus({ip}, {json.dumps(msg)})")


def _selfcheck():
    p, m = format_status({"status": "downloading", "downloaded_bytes": 50,
                          "total_bytes": 100, "speed": 2e6})
    assert p == 50 and "50.0%" in m, m
    p, _ = format_status({"status": "downloading", "downloaded_bytes": 5})  # total 없음
    assert p == 0
    p, m = format_status({"status": "finished"})
    assert p == 100 and "병합" in m
    # exe(frozen)에서는 pip를 실행하지 않고 안내만 해야 함
    msgs = []
    sys.frozen = True
    try:
        update_ytdlp(lambda pct, msg: msgs.append(msg))
    finally:
        del sys.frozen
    assert msgs == ["exe 버전은 자체 업데이트 불가 — Releases에서 새 exe를 받아 교체하세요"], msgs
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        import webview  # GUI 실행 시에만 필요 (selfcheck는 의존성 없이 동작)
        api = Api()
        api.window = webview.create_window(
            "유튜브 → MP4", html=HTML, js_api=api, width=560, height=360,
        )
        webview.start()
