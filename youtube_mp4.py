"""유튜브 링크 -> MP4 다운로드 GUI (PyQt6). yt-dlp + ffmpeg 래퍼.

exe(PyInstaller) 배포: ffmpeg·deno 내장, 다운로드 실패 시 깃허브 릴리즈에서 자동 업데이트.
소스 실행: pip install -r requirements.txt  →  python youtube_mp4.py
(ffmpeg는 PATH에. deno가 PATH에 있으면 전체 화질, 없으면 유튜브가 360p로 제한될 수 있음)
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import yt_dlp

GITHUB_REPO = "Seobuk/youtube-mp4-downloader"
APP_VERSION = "dev"  # 릴리즈 빌드 시 워크플로우가 태그로 치환
EXE_NAME = "YoutubeMP4.exe" if os.name == "nt" else "YoutubeMP4-linux"  # 자동 업데이트가 찾는 릴리즈 에셋


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


def is_playlist(url):
    """재생목록 전체 다운로드 대상인지. watch?v=...&list=... 링크는 영상 1개로 취급."""
    return "/playlist" in url


def hook_status(d):
    """진행률 훅 -> (전체 퍼센트, 메시지). 재생목록이면 [n/M]과 전체 진행도로 환산."""
    pct, msg = format_status(d)
    info = d.get("info_dict") or {}
    idx, total = info.get("playlist_index"), info.get("n_entries")
    if idx and total:
        return ((idx - 1) + pct / 100) / total * 100, f"[{idx}/{total}] {msg}"
    return pct, msg


def find_tool(name):
    """exe에 번들된 실행 파일(ffmpeg/deno) 우선, 없으면 PATH에서 탐색. 없으면 None."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / (
            f"{name}.exe" if os.name == "nt" else name)
        if bundled.is_file():
            return str(bundled)
    return shutil.which(name)


def build_format(height=None):
    """화질 상한에 맞는 yt-dlp 포맷 문자열.
    H.264(avc1)+AAC 우선 — 파워포인트 등에서 충돌 없이 재생되는 코덱 조합.
    그런 조합이 없는 영상(분리 스트림만, m4a 오디오 없음 등)도 받도록 단계적 폴백."""
    h = f"[height<={height}]" if height else ""
    return (f"bestvideo{h}[vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo{h}[ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo{h}+bestaudio/"
            f"best{h}[ext=mp4]/best{h}/"
            "bestvideo+bestaudio/best")


def exe_dir():
    """exe(또는 스크립트)가 있는 폴더 — cookies.txt를 찾는 위치."""
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent


def needs_login_retry(err):
    """쿠키 인증이나 다른 재생 클라이언트로 재시도할 가치가 있는 오류인지.
    (비로그인 차단, 그리고 현재 클라이언트에 맞는 포맷이 없는 경우 포함)"""
    e = str(err).lower()
    return any(k in e for k in ("not available", "403", "forbidden",
                                "sign in", "age", "private video", "bot",
                                "requested format"))


def is_cookie_error(err):
    """브라우저 쿠키를 읽는 단계 자체가 실패한 오류인지 (영상 차단과 구분)."""
    e = str(err).lower()
    return any(k in e for k in ("cookie", "decrypt", "dpapi", "keyring"))


def write_debug_log(url, errors):
    """실패한 시도 내역을 exe 옆 YoutubeMP4.log에 기록 (안 되면 홈 폴더에). 진단용."""
    from datetime import datetime
    for base in (exe_dir(), Path.home()):
        try:
            with open(base / "YoutubeMP4.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {APP_VERSION} {url}\n")
                for label, err in errors:
                    f.write(f"  - {label}: {err}\n")
            return
        except OSError:
            continue


def download(url, out_dir, report, height=None, playlist=None, cookies_from=None,
             alt_clients=False):
    """url을 out_dir에 mp4로 저장. report(pct, msg)로 진행 상황 통지.
    height를 주면 그 해상도 이하 중 최선으로 (용량 조절용).
    재생목록이면 재생목록 제목 폴더를 만들어 전체 다운로드.
    playlist=None이면 URL 형태로 자동 판별, True/False로 강제 지정 가능.
    cookies_from에 브라우저 이름을 주면 그 브라우저의 로그인 쿠키로 인증.
    (exe 옆에 cookies.txt가 있으면 그것을 최우선으로 사용.)
    alt_clients=True면 유튜브의 모든 재생 클라이언트를 순서대로 시도."""
    if playlist is None:
        playlist = is_playlist(url)
    name = ("%(playlist_title)s/%(playlist_index)02d %(title)s.%(ext)s"
            if playlist else "%(title)s.%(ext)s")
    opts = {
        "format": build_format(height),
        "merge_output_format": "mp4",
        "outtmpl": str(Path(out_dir) / name),
        "progress_hooks": [lambda d: report(*hook_status(d))],
        "noplaylist": not playlist,
        "ignoreerrors": playlist,  # 재생목록 중 막힌 영상(비공개 등)은 건너뛰고 계속
        "quiet": True,
        "noprogress": True,  # 진행률은 progress_hooks로만 (windowed exe엔 콘솔 없음)
        "no_warnings": True,
        "ffmpeg_location": find_tool("ffmpeg"),
    }
    deno = find_tool("deno")
    if deno:
        # 최신 유튜브는 JS 챌린지(EJS)를 풀어야 전체 화질 포맷을 줌 — deno로 해결.
        # 없으면 yt-dlp가 360p 폴백이나 '포맷 없음'으로 떨어짐.
        opts["js_runtimes"] = {"deno": {"path": deno}}
    if alt_clients:
        # 기본 클라이언트에서 'not available'인 영상도 다른 클라이언트엔 있을 수 있음
        opts["extractor_args"] = {"youtube": {"player_client": ["all"]}}
    cookie_txt = exe_dir() / "cookies.txt"
    if cookie_txt.is_file():
        opts["cookiefile"] = str(cookie_txt)  # 수동 쿠키 파일이 항상 최우선
    elif cookies_from:
        opts["cookiesfrombrowser"] = (cookies_from,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if playlist:
        done = [e for e in info.get("entries") or [] if e]
        report(100, f"완료: 재생목록 '{info.get('title', '')}' 영상 {len(done)}개")
        return None
    report(100, f"완료: {info.get('title', '')}.mp4")
    # 편집 탭에 바로 넘길 수 있게 저장된 파일 경로 반환
    return next((d.get("filepath") for d in info.get("requested_downloads") or []
                 if d.get("filepath")), None)


def update_ytdlp(report):
    """(소스 실행 전용) pip로 yt-dlp 최신화. 완료 후 앱 재시작 필요."""
    if getattr(sys, "frozen", False):
        # exe에서는 sys.executable이 이 앱 자신 → pip 대신 앱이 무한 재실행됨
        report(0, "exe 버전은 pip 업데이트 불가 — 업데이트 확인 버튼을 사용하세요")
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


def latest_release():
    """깃허브 최신 릴리즈 조회 -> (태그, exe 에셋 dict | None)."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers={"User-Agent": EXE_NAME})
    with urllib.request.urlopen(req, timeout=15) as r:
        rel = json.load(r)
    asset = next((a for a in rel.get("assets", []) if a["name"] == EXE_NAME), None)
    return rel.get("tag_name", ""), asset


def swap_exe(exe, new):
    """실행 중인 exe를 new로 교체. 기존 파일은 .old로 보관 (다음 실행 때 정리)."""
    old = exe.with_name(exe.stem + ".old.exe")
    old.unlink(missing_ok=True)
    exe.rename(old)  # 윈도우도 실행 중인 exe의 rename은 허용됨
    new.rename(exe)
    if os.name != "nt":
        exe.chmod(0o755)  # 리눅스: 새로 받은 파일엔 실행 비트가 없음


def cleanup_old_exe():
    """이전 자동 업데이트가 남긴 .old 파일 삭제 (아직 실행 중이면 다음 기회에)."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        try:
            exe.with_name(exe.stem + ".old.exe").unlink(missing_ok=True)
        except OSError:
            pass


def update_app(report, skip_same=False):
    """깃허브 최신 릴리즈 exe로 자기 자신을 교체. 교체했으면 True (앱 재시작 필요)."""
    try:
        return _update_app(report, skip_same)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # 비공개 저장소는 조회/다운로드 어느 단계든 익명 접근이 404
            report(0, "릴리즈 접근 실패(404) — 깃허브 저장소가 비공개면 공개로 전환해야 합니다")
            return False
        raise


def _update_app(report, skip_same):
    if not getattr(sys, "frozen", False):
        report(0, "자동 업데이트는 exe 버전에서만 동작합니다")
        return False
    report(0, "새 버전 확인 중...")
    tag, asset = latest_release()
    if tag == APP_VERSION:
        if not skip_same:
            report(100, f"이미 최신 버전입니다 ({APP_VERSION})")
        return False
    if not asset:
        report(0, f"릴리즈 {tag}에 {EXE_NAME}가 없습니다")
        return False
    exe = Path(sys.executable)
    new = exe.with_name(exe.stem + ".new.exe")
    req = urllib.request.Request(asset["browser_download_url"],
                                 headers={"User-Agent": EXE_NAME})
    total = asset.get("size") or 0
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(new, "wb") as f:
        while chunk := r.read(1 << 18):
            f.write(chunk)
            done += len(chunk)
            pct = done / total * 100 if total else 0
            report(pct, f"{tag} 받는 중... {pct:3.0f}%")
    swap_exe(exe, new)
    subprocess.Popen([str(exe)])
    report(100, f"{tag} 교체 완료 — 새 창이 열립니다")
    return True


# ---- 영상 편집 (자르기 / WMV 변환 / GIF) — 번들 ffmpeg 사용 ----

EDIT_MODES = {  # 모드 -> (확장자, 파일명 접미사)
    "mp4": ("mp4", "cut"),
    "wmv": ("wmv", "wmv"),
    "gif": ("gif", "gif"),
}


def parse_time(s):
    """'90', '1:30', '0:01:30.5' -> 초(float). 빈 문자열은 None. 잘못된 형식은 ValueError."""
    s = (s or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(f"시간 형식 오류: {s}")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    if sec < 0:
        raise ValueError(f"시간 형식 오류: {s}")
    return sec


def unique_path(p):
    """p가 이미 있으면 'name (2).ext' 식으로 비어 있는 이름을 찾음 (덮어쓰기 방지)."""
    p = Path(p)
    n = 2
    while p.exists():
        p = p.with_name(f"{p.stem.rsplit(' (', 1)[0]} ({n}){p.suffix}")
        n += 1
    return p


def edit_output_path(src, mode):
    """원본 옆에 '이름_cut.mp4' / '이름_wmv.wmv' / '이름_gif.gif'."""
    ext, suffix = EDIT_MODES[mode]
    src = Path(src)
    return unique_path(src.with_name(f"{src.stem}_{suffix}.{ext}"))


def build_edit_cmd(ffmpeg, src, dst, mode, start=None, end=None, gif_fps=12, gif_width=480):
    """ffmpeg 명령 리스트. start/end(초)로 구간 자르기 — 모든 모드에 적용.
    재인코딩으로 자르므로 키프레임과 무관하게 정확한 위치에서 잘림."""
    cmd = [ffmpeg, "-hide_banner", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]  # 입력 앞 -ss: 빠른 탐색 + (재인코딩 시) 정확
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    if mode == "mp4":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart"]
    elif mode == "wmv":
        # wmv2 + wmav2: 윈도우 기본 플레이어·구버전 파워포인트 호환
        cmd += ["-c:v", "wmv2", "-q:v", "2", "-c:a", "wmav2", "-b:a", "192k"]
    elif mode == "gif":
        # 팔레트 생성 후 적용 — 기본 256색 GIF보다 훨씬 깨끗함
        vf = (f"fps={gif_fps},scale={gif_width}:-1:flags=lanczos,"
              "split[a][b];[a]palettegen[p];[b][p]paletteuse")
        cmd += ["-vf", vf, "-an", "-loop", "0"]
    else:
        raise ValueError(mode)
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _no_window():
    """윈도우 windowed exe에서 ffmpeg 콘솔 창이 뜨지 않도록."""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW


def probe_duration(ffmpeg, src):
    """ffmpeg -i 출력의 'Duration: HH:MM:SS.xx'로 길이(초). 모르면 None."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", **_no_window())
    for line in r.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            try:
                return parse_time(line.split(",")[0].split(":", 1)[1])
            except ValueError:
                return None
    return None


def edit_video(src, mode, report, start=None, end=None, gif_fps=12, gif_width=480):
    """src 영상을 mode(mp4/wmv/gif)로 변환(+구간 자르기)해 원본 옆에 저장. 결과 경로 반환."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg가 없습니다")
    src = Path(src)
    if not src.is_file():
        raise RuntimeError(f"파일이 없습니다: {src}")
    total = probe_duration(ffmpeg, src)
    if end is not None and total:
        end = min(end, total)
    if start and total and start >= total:
        raise RuntimeError("시작 시간이 영상 길이보다 깁니다")
    if end is not None and end <= (start or 0):
        raise RuntimeError("끝 시간은 시작 시간보다 커야 합니다")
    span = (end if end is not None else total or 0) - (start or 0)
    dst = edit_output_path(src, mode)
    cmd = build_edit_cmd(ffmpeg, src, dst, mode, start, end, gif_fps, gif_width)
    label = {"mp4": "자르는 중", "wmv": "WMV 변환 중", "gif": "GIF 만드는 중"}[mode]
    report(0, f"{label}...")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", **_no_window())
    err_tail = []
    # stderr를 따로 비워주지 않으면 파이프가 차서 ffmpeg가 멈출 수 있음
    t = threading.Thread(target=lambda: err_tail.extend(p.stderr.read().splitlines()[-5:]),
                         daemon=True)
    t.start()
    for line in p.stdout:
        k, _, v = line.strip().partition("=")
        if k == "out_time_us" and span > 0 and v.isdigit():
            pct = min(int(v) / 1e6 / span * 100, 99.9)
            report(pct, f"{label}... {pct:5.1f}%")
    p.wait()
    t.join(timeout=5)
    if p.returncode != 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(err_tail[-1] if err_tail else f"ffmpeg 오류 ({p.returncode})")
    report(100, f"완료: {dst.name}")
    return dst


def fmt_time(sec, precise=True):
    """초 -> '1:02:03.45' / '02:03.45' (precise=False면 소수점 없이). parse_time과 왕복 가능."""
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    ss = f"{s:05.2f}" if precise else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{ss}" if h >= 1 else f"{int(m):02d}:{ss}"


def build_thumb_cmd(ffmpeg, src, out_dir, duration, count=24, height=72):
    """타임라인용 썸네일 count장을 영상 전체에 고르게 뽑는 ffmpeg 명령 (thumb_001.jpg ...)."""
    rate = count / max(duration, 0.1)
    return [ffmpeg, "-hide_banner", "-v", "error", "-y", "-i", str(src),
            "-vf", f"fps={rate:.6f},scale=-2:{height}", "-frames:v", str(count),
            "-q:v", "5", str(Path(out_dir) / "thumb_%03d.jpg")]


def tick_step(duration, width, min_px=70):
    """타임라인 눈금 간격(초): 눈금 사이가 min_px 이상 되는 가장 작은 '보기 좋은' 값."""
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
        if duration <= 0 or step / duration * width >= min_px:
            return step
    return 7200


_EDITOR_CLS = None


def open_editor(parent, src):
    """영상 편집기 창 열기: 미리보기 재생 + 썸네일 타임라인에서 시작/끝 핸들을 끌어 구간 지정,
    그 구간을 MP4 자르기 / WMV / GIF로 내보내기."""
    global _EDITOR_CLS
    if _EDITOR_CLS is None:
        _EDITOR_CLS = _make_editor_class()
    dlg = _EDITOR_CLS(parent, src)
    dlg.show()
    return dlg


def _make_editor_class():
    import tempfile
    from PyQt6.QtCore import QRect, QRectF, Qt, QUrl, pyqtSignal
    from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut
    from PyQt6.QtCore import QPointF
    from PyQt6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    from PyQt6.QtWidgets import (
        QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
        QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
    )

    MIN_SPAN = 100  # ms — 시작/끝 핸들 최소 간격

    class Timeline(QWidget):
        """썸네일 스트립 + 시간 눈금 + 구간 핸들 + 재생 헤드. 모든 시간은 ms."""
        seek = pyqtSignal(int)
        range_changed = pyqtSignal(int, int)

        RULER, PAD, HANDLE = 18, 10, 9

        def __init__(self):
            super().__init__()
            self.setMinimumHeight(96)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setMouseTracking(True)
            self.dur = self.pos_ms = self.a = self.b = 0
            self.thumbs = []
            self._drag = None  # 'a' | 'b' | 'seek'

        # 좌표 변환
        def _w(self):
            return max(1, self.width() - 2 * self.PAD)

        def x_of(self, ms):
            return self.PAD + (ms / self.dur * self._w() if self.dur else 0)

        def ms_of(self, x):
            return int(min(max((x - self.PAD) / self._w(), 0), 1) * self.dur)

        def set_duration(self, ms):
            self.dur, self.a, self.b = ms, 0, ms
            self.update()

        def set_position(self, ms):
            self.pos_ms = ms
            self.update()

        def set_range(self, a, b, emit=True):
            a = max(0, min(a, self.dur - MIN_SPAN))
            b = min(self.dur, max(b, a + MIN_SPAN))
            self.a, self.b = a, b
            self.update()
            if emit:
                self.range_changed.emit(a, b)

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            p.fillRect(self.rect(), QColor("#1e1e24"))
            strip = QRect(self.PAD, self.RULER, self._w(), h - self.RULER - 4)
            # 눈금
            p.setPen(QColor("#9a9aa5"))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            if self.dur:
                step = tick_step(self.dur / 1000, self._w())
                t = 0.0
                while t * 1000 <= self.dur:
                    x = int(self.x_of(t * 1000))
                    p.drawLine(x, self.RULER - 5, x, self.RULER - 1)
                    p.drawText(x + 3, self.RULER - 6, fmt_time(t, precise=step < 1))
                    t += step
            # 썸네일 (칸 비율에 맞춰 가운데 크롭)
            p.fillRect(strip, QColor("#2b2b33"))
            if self.thumbs:
                cw = strip.width() / len(self.thumbs)
                for i, pix in enumerate(self.thumbs):
                    cell = QRectF(strip.x() + i * cw, strip.y(), cw + 1, strip.height())
                    sw = min(pix.width(), pix.height() * cell.width() / cell.height())
                    srect = QRectF((pix.width() - sw) / 2, 0, sw, pix.height())
                    p.drawPixmap(cell, pix, srect)
            if not self.dur:
                return
            xa, xb = self.x_of(self.a), self.x_of(self.b)
            # 선택 밖은 어둡게
            dim = QColor(0, 0, 0, 170)
            p.fillRect(QRectF(strip.x(), strip.y(), xa - strip.x(), strip.height()), dim)
            p.fillRect(QRectF(xb, strip.y(), strip.right() - xb + 1, strip.height()), dim)
            # 선택 테두리 + 핸들
            yellow = QColor("#ffc233")
            p.setPen(QPen(yellow, 3))
            p.drawLine(QPointF(xa, strip.top() + 1), QPointF(xb, strip.top() + 1))
            p.drawLine(QPointF(xa, strip.bottom()), QPointF(xb, strip.bottom()))
            p.setPen(Qt.PenStyle.NoPen)
            for x, left in ((xa, True), (xb, False)):
                r = QRectF(x - self.HANDLE if left else x, strip.top(), self.HANDLE,
                           strip.height() + 1)
                p.setBrush(yellow)
                p.drawRoundedRect(r, 3, 3)
                p.setBrush(QColor("#6b4e00"))
                p.drawRect(QRectF(r.center().x() - 1, r.center().y() - 8, 2, 16))
            # 재생 헤드
            x = self.x_of(self.pos_ms)
            p.setPen(QPen(QColor("#ff4d4d"), 2))
            p.drawLine(QPointF(x, 2), QPointF(x, h - 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ff4d4d"))
            p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))

        def _hit(self, x):
            xa, xb = self.x_of(self.a), self.x_of(self.b)
            if xa - self.HANDLE - 2 <= x <= xa + 3:
                return "a"
            if xb - 3 <= x <= xb + self.HANDLE + 2:
                return "b"
            return None

        def mousePressEvent(self, e):
            if not self.dur:
                return
            x = e.position().x()
            self._drag = self._hit(x) or "seek"
            self.mouseMoveEvent(e)

        def mouseMoveEvent(self, e):
            x = e.position().x()
            if not self._drag:
                self.setCursor(Qt.CursorShape.SizeHorCursor if self._hit(x)
                               else Qt.CursorShape.PointingHandCursor)
                return
            ms = self.ms_of(x)
            if self._drag == "a":
                self.set_range(ms, self.b)
                ms = self.a
            elif self._drag == "b":
                self.set_range(self.a, ms)
                ms = self.b
            self.seek.emit(ms)  # 핸들을 끄는 동안에도 그 위치 화면을 보여줌

        def mouseReleaseEvent(self, _):
            self._drag = None

    class Editor(QDialog):
        report_signal = pyqtSignal(float, str)
        thumbs_signal = pyqtSignal(list)
        done_signal = pyqtSignal()

        def __init__(self, parent, src):
            super().__init__(parent)
            self.src = str(src)
            self.setWindowTitle(f"영상 편집 — {Path(self.src).name}")
            self.resize(960, 700)
            self._range_play = False
            self._frame_ms = 33
            self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

            self.video = QVideoWidget()
            self.video.setMinimumSize(480, 270)
            self.video.setStyleSheet("background:#000;")
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)

            def btn(text, slot, tip=""):
                b = QPushButton(text, clicked=slot, toolTip=tip)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space 등 단축키를 버튼이 먹지 않게
                return b

            self.play_btn = btn("▶ 재생", self.toggle_play, "재생/일시정지 (Space)")
            prev_f = btn("◀|", lambda: self.step(-1), "이전 프레임 (←) · Shift+← 1초")
            next_f = btn("|▶", lambda: self.step(1), "다음 프레임 (→) · Shift+→ 1초")
            set_a = btn("[ 시작점", self.mark_a, "현재 위치를 시작점으로 (I)")
            set_b = btn("끝점 ]", self.mark_b, "현재 위치를 끝점으로 (O)")
            play_sel = btn("▶ 구간 재생", self.play_range, "선택 구간만 재생 (P)")
            self.time_lbl = QLabel("00:00.00 / 00:00.00")
            self.time_lbl.setStyleSheet("font-family:monospace; font-size:13px;")

            self.timeline = Timeline()
            self.timeline.seek.connect(self.player.setPosition)
            self.timeline.range_changed.connect(self._on_range)

            self.a_edit, self.b_edit = QLineEdit(), QLineEdit()
            for e in (self.a_edit, self.b_edit):
                e.setFixedWidth(100)
                e.setToolTip("직접 입력도 가능: 90 / 1:30 / 0:01:30.5")
                e.editingFinished.connect(self._typed_range)
            self.len_lbl = QLabel()

            self.mode = QComboBox()
            for label, m in [("MP4 (구간 자르기)", "mp4"), ("WMV로 변환", "wmv"),
                             ("GIF 만들기", "gif")]:
                self.mode.addItem(label, m)
            self.fps = QSpinBox(minimum=1, maximum=30, value=12, suffix=" fps")
            self.gif_w = QSpinBox(minimum=120, maximum=1920, value=480, singleStep=40,
                                  suffix=" px")
            self.gif_opts = QWidget()
            g = QHBoxLayout(self.gif_opts)
            g.setContentsMargins(0, 0, 0, 0)
            g.addWidget(QLabel("GIF"))
            g.addWidget(self.fps)
            g.addWidget(QLabel("가로"))
            g.addWidget(self.gif_w)
            self.mode.currentIndexChanged.connect(
                lambda: self.gif_opts.setVisible(self.mode.currentData() == "gif"))
            self.gif_opts.setVisible(False)
            self.export_btn = QPushButton("내보내기", clicked=self.export)
            self.export_btn.setStyleSheet(
                "background:#457b9d; color:#fff; font-weight:600;"
                "padding:8px 20px; border:0; border-radius:6px;")
            self.bar = QProgressBar(textVisible=False, maximumHeight=8)
            self.status = QLabel("타임라인의 노란 핸들을 끌거나 I/O 키로 구간을 지정하세요")

            root = QVBoxLayout(self)
            root.addWidget(self.video, 1)
            ctl = QHBoxLayout()
            for w in (prev_f, self.play_btn, next_f):
                ctl.addWidget(w)
            ctl.addWidget(self.time_lbl)
            ctl.addStretch()
            for w in (set_a, set_b, play_sel):
                ctl.addWidget(w)
            root.addLayout(ctl)
            root.addWidget(self.timeline)
            rng = QHBoxLayout()
            rng.addWidget(QLabel("시작"))
            rng.addWidget(self.a_edit)
            rng.addWidget(QLabel("끝"))
            rng.addWidget(self.b_edit)
            rng.addWidget(self.len_lbl)
            rng.addStretch()
            rng.addWidget(self.gif_opts)
            rng.addWidget(self.mode)
            rng.addWidget(self.export_btn)
            root.addLayout(rng)
            root.addWidget(self.bar)
            root.addWidget(self.status)

            keys = [("Space", self.toggle_play), ("I", self.mark_a), ("O", self.mark_b),
                    ("P", self.play_range), ("Left", lambda: self.step(-1)),
                    ("Right", lambda: self.step(1)), ("Shift+Left", lambda: self.jump(-1000)),
                    ("Shift+Right", lambda: self.jump(1000)),
                    ("Home", lambda: self.player.setPosition(self.timeline.a)),
                    ("End", lambda: self.player.setPosition(self.timeline.b))]
            for k, fn in keys:
                QShortcut(QKeySequence(k), self, activated=fn)

            self.player.durationChanged.connect(self._on_duration)
            self.player.positionChanged.connect(self._on_position)
            self.player.playbackStateChanged.connect(self._on_state)
            self.player.errorOccurred.connect(
                lambda _e, msg: self.status.setText(f"미리보기 오류: {msg}"))
            self.player.mediaStatusChanged.connect(self._on_media_status)
            self.report_signal.connect(self._on_report)
            self.thumbs_signal.connect(self._on_thumbs)
            self.done_signal.connect(lambda: self.export_btn.setDisabled(False))
            self.player.setSource(QUrl.fromLocalFile(self.src))

        # ---- 재생 ----
        def _on_media_status(self, st):
            if st == QMediaPlayer.MediaStatus.LoadedMedia:
                fr = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
                if fr:
                    self._frame_ms = max(1, round(1000 / float(fr)))
                self.player.pause()  # 첫 프레임 표시

        def _on_duration(self, ms):
            self.timeline.set_duration(ms)
            self._on_range(0, ms)
            self._make_thumbs(ms / 1000)

        def _on_position(self, ms):
            self.timeline.set_position(ms)
            self.time_lbl.setText(f"{fmt_time(ms / 1000)} / "
                                  f"{fmt_time(self.timeline.dur / 1000)}")
            if self._range_play and ms >= self.timeline.b:
                self._range_play = False
                self.player.pause()
                self.player.setPosition(self.timeline.b)

        def _on_state(self, st):
            playing = st == QMediaPlayer.PlaybackState.PlayingState
            self.play_btn.setText("⏸ 정지" if playing else "▶ 재생")
            if not playing:
                self._range_play = False

        def toggle_play(self):
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                if self.player.position() >= self.timeline.dur - 50:
                    self.player.setPosition(0)
                self.player.play()

        def play_range(self):
            self.player.setPosition(self.timeline.a)
            self.player.play()
            self._range_play = True

        def step(self, frames):
            self.player.pause()
            self.jump(frames * self._frame_ms)

        def jump(self, ms):
            self.player.setPosition(max(0, min(self.player.position() + ms, self.timeline.dur)))

        # ---- 구간 ----
        def mark_a(self):
            self.timeline.set_range(self.player.position(), self.timeline.b)

        def mark_b(self):
            self.timeline.set_range(self.timeline.a, self.player.position())

        def _on_range(self, a, b):
            self.a_edit.setText(fmt_time(a / 1000))
            self.b_edit.setText(fmt_time(b / 1000))
            self.len_lbl.setText(f"길이 {fmt_time((b - a) / 1000)}")

        def _typed_range(self):
            try:
                a, b = parse_time(self.a_edit.text()), parse_time(self.b_edit.text())
            except ValueError as e:
                self.status.setText(str(e))
                return self._on_range(self.timeline.a, self.timeline.b)
            a = 0 if a is None else int(a * 1000)
            b = self.timeline.dur if b is None else int(b * 1000)
            self.timeline.set_range(a, b)
            self.player.setPosition(self.timeline.a)

        # ---- 썸네일 (백그라운드 ffmpeg) ----
        def _make_thumbs(self, dur):
            ff = find_tool("ffmpeg")
            if not ff or dur <= 0:
                return

            def work():
                try:
                    subprocess.run(build_thumb_cmd(ff, self.src, self._tmp.name, dur),
                                   capture_output=True, timeout=120, **_no_window())
                except Exception:
                    return
                self.thumbs_signal.emit(sorted(str(p) for p in
                                               Path(self._tmp.name).glob("thumb_*.jpg")))

            threading.Thread(target=work, daemon=True).start()

        def _on_thumbs(self, paths):
            self.timeline.thumbs = [QPixmap(p) for p in paths]
            self.timeline.update()

        # ---- 내보내기 ----
        def export(self):
            self.player.pause()
            tl = self.timeline
            start = tl.a / 1000 if tl.a > 0 else None
            end = tl.b / 1000 if tl.b < tl.dur else None
            mode, fps, width = self.mode.currentData(), self.fps.value(), self.gif_w.value()
            self.export_btn.setDisabled(True)

            def work():
                try:
                    edit_video(self.src, mode, self.report_signal.emit, start, end, fps, width)
                except Exception as e:
                    self.report_signal.emit(0, f"오류: {e}")
                finally:
                    self.done_signal.emit()

            threading.Thread(target=work, daemon=True).start()

        def _on_report(self, pct, msg):
            self.bar.setValue(int(pct))
            self.status.setText(msg)

        def closeEvent(self, e):
            self.player.stop()
            self.player.setSource(QUrl())
            self._tmp.cleanup()
            super().closeEvent(e)

    return Editor


def run_gui(exec_=True):
    """GUI 실행. exec_=False면 이벤트 루프를 돌리지 않고 (app, win) 반환 (테스트용)."""
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import (
        QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
        QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
    )

    cleanup_old_exe()

    class MainWindow(QWidget):
        # 워커 스레드 -> UI 스레드 전달용 (Qt 시그널은 스레드 안전)
        status_signal = pyqtSignal(float, str)
        done_signal = pyqtSignal()
        restart_signal = pyqtSignal()  # 자동 업데이트 후 앱 종료 (새 exe가 이미 실행됨)
        file_signal = pyqtSignal(str)  # 다운로드 완료 파일 -> 편집 원본 칸

        def __init__(self):
            super().__init__()
            self.setWindowTitle("유튜브 → MP4")
            self.setFixedWidth(520)
            self._last_pct = -1

            self.url = QLineEdit(placeholderText="https://youtu.be/... 또는 재생목록 링크")
            self.dir = QLineEdit(str(Path.home() / "Downloads"))
            pick = QPushButton("폴더", clicked=self.pick_dir)
            self.quality = QComboBox()
            for label, h in [("최고 화질", None), ("1080p", 1080), ("720p", 720),
                             ("480p", 480), ("360p", 360)]:
                self.quality.addItem(label, h)
            self.go = QPushButton("MP4 다운로드", clicked=self.start_download)
            self.go.setStyleSheet(
                "background:#e63946; color:#fff; font-weight:600;"
                "padding:10px; border:0; border-radius:6px;"
            )
            self.bar = QProgressBar(textVisible=False)
            self.bar.setRange(0, 100)
            self.bar.setFixedHeight(8)
            self.status = QLabel("대기 중")
            ver = QLabel(f"{APP_VERSION} · yt-dlp {yt_dlp.version.__version__}")
            ver.setStyleSheet("color:gray; font-size:11px;")
            frozen = getattr(sys, "frozen", False)
            self.upd = QPushButton("업데이트 확인" if frozen else "yt-dlp 업데이트",
                                   clicked=self.start_update)

            root = QVBoxLayout(self)
            root.addWidget(QLabel("유튜브 링크"))
            root.addWidget(self.url)
            root.addWidget(QLabel("저장 폴더"))
            row = QHBoxLayout()
            row.addWidget(self.dir)
            row.addWidget(pick)
            root.addLayout(row)
            root.addWidget(QLabel("화질 (낮출수록 용량 작음 · mp4/H.264라 PPT 삽입 가능)"))
            root.addWidget(self.quality)
            root.addWidget(self.go)
            root.addWidget(self._build_edit_box())
            root.addWidget(self.bar)
            root.addWidget(self.status)
            foot = QHBoxLayout()
            foot.addWidget(ver)
            foot.addStretch()
            foot.addWidget(self.upd)
            root.addLayout(foot)

            self.status_signal.connect(self._on_status)
            self.done_signal.connect(lambda: self._busy(False))
            self.restart_signal.connect(QApplication.instance().quit)
            self.file_signal.connect(self.src.setText)

        def _build_edit_box(self):
            """영상 편집: 편집기 창에서 화면을 보며 구간 지정 → 자르기 / WMV / GIF."""
            box = QGroupBox("영상 편집 (자르기 · WMV · GIF)")
            self.src = QLineEdit(placeholderText="편집할 영상 파일 (다운로드하면 자동 입력)")
            src_pick = QPushButton("파일", clicked=self.pick_src)
            self.edit_go = QPushButton("편집기 열기", clicked=self.start_edit)
            self.edit_go.setStyleSheet(
                "background:#457b9d; color:#fff; font-weight:600;"
                "padding:8px; border:0; border-radius:6px;")
            lay = QVBoxLayout(box)
            r1 = QHBoxLayout()
            r1.addWidget(self.src)
            r1.addWidget(src_pick)
            lay.addLayout(r1)
            lay.addWidget(self.edit_go)
            return box

        def pick_src(self):
            f, _ = QFileDialog.getOpenFileName(
                self, "편집할 영상 선택", self.src.text() or self.dir.text(),
                "영상 (*.mp4 *.mkv *.webm *.mov *.avi *.wmv *.m4v);;모든 파일 (*)")
            if f:
                self.src.setText(f)
                self.start_edit()

        def start_edit(self):
            src = self.src.text().strip()
            if not src or not Path(src).is_file():
                return self._on_status(0, "편집할 영상 파일을 선택하세요")
            if not find_tool("ffmpeg"):
                return self._on_status(0, "ffmpeg가 없습니다 (편집 불가)")
            self._editors = [e for e in getattr(self, "_editors", []) if e.isVisible()]
            self._editors.append(open_editor(self, src))

        def pick_dir(self):
            d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.dir.text())
            if d:
                self.dir.setText(d)

        def _ask_playlist_scope(self):
            """영상 링크에 재생목록이 붙어 있을 때: True=전체, False=이 영상만, None=취소."""
            box = QMessageBox(self)
            box.setWindowTitle("재생목록 감지")
            box.setText("이 영상은 재생목록에 포함되어 있습니다.\n어떻게 받을까요?")
            one = box.addButton("이 영상만", QMessageBox.ButtonRole.NoRole)
            all_ = box.addButton("재생목록 전체", QMessageBox.ButtonRole.YesRole)
            box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is one:
                return False
            if box.clickedButton() is all_:
                return True
            return None

        def start_download(self):
            url = self.url.text().strip()
            if not url:
                return self._on_status(0, "링크를 입력하세요")
            if not find_tool("ffmpeg"):
                return self._on_status(0, "ffmpeg가 없습니다 (병합 불가)")
            playlist = None  # URL 형태로 자동 판별
            if "list=" in url and not is_playlist(url):
                playlist = self._ask_playlist_scope()
                if playlist is None:
                    return  # 취소
            out_dir = self.dir.text()
            height = self.quality.currentData()

            def work(report):
                errors = []

                def attempt(label, **kw):
                    if label:
                        report(0, label)
                    try:
                        path = download(url, out_dir, report, height, playlist, **kw)
                        if path:
                            self.file_signal.emit(path)
                        return True
                    except Exception as e:
                        errors.append((label or "기본 시도", str(e)))
                        return False

                if attempt(None):
                    return
                first_err = errors[0][1]
                # 유튜브가 비로그인 클라이언트만 막는 오류(연령 제한, 403 등)면 재시도.
                # 'not available'류는 로그인 문제가 아닌 경우가 대부분이라
                # 빠른 전체 클라이언트 시도를 먼저, 브라우저 쿠키는 그다음.
                if needs_login_retry(first_err):
                    if attempt("다른 재생 클라이언트로 재시도 중... (시간이 걸릴 수 있음)",
                               alt_clients=True):
                        return
                    good_browser = None
                    for browser in ("chrome", "edge", "firefox"):
                        if attempt(f"{browser} 로그인 정보로 재시도 중...",
                                   cookies_from=browser):
                            return
                        if not is_cookie_error(errors[-1][1]):
                            # 쿠키는 읽었는데도 차단 → 다른 브라우저도 같은 계정
                            good_browser = browser
                            break
                    if good_browser and attempt(
                            "쿠키 + 전체 클라이언트로 재시도 중...",
                            cookies_from=good_browser, alt_clients=True):
                        return
                write_debug_log(url, errors)  # 진단용: 시도 내역을 YoutubeMP4.log에
                # exe에서 실패하면 yt-dlp가 낡았을 수 있음 → 새 릴리즈 있으면 자동 교체
                if getattr(sys, "frozen", False):
                    try:
                        report(0, f"오류: {first_err} — 새 버전 확인 중...")
                        if update_app(report, skip_same=True):
                            return True
                    except Exception:
                        pass  # 업데이트 확인 실패 시 원래 오류 표시
                report(0, f"오류: {first_err}")

            self._run(work)

        def start_update(self):
            if getattr(sys, "frozen", False):
                self._run(update_app)
            else:
                self._run(update_ytdlp)

        def _run(self, fn):
            """fn(report)를 워커 스레드에서 실행. 완료까지 버튼 잠금.
            fn이 참을 반환하면 앱 재시작 필요 (자동 업데이트로 교체됨)."""
            self._busy(True)
            self._last_pct = -1

            def work():
                restart = False
                try:
                    restart = bool(fn(self._report))
                except Exception as e:
                    self._report(0, f"오류: {e}")
                finally:
                    (self.restart_signal if restart else self.done_signal).emit()

            threading.Thread(target=work, daemon=True).start()

        def _busy(self, on):
            self.go.setDisabled(on)  # 편집기는 다운로드 중에도 열 수 있음
            self.upd.setDisabled(on)

        def _report(self, pct, msg):
            # 다운로드 진행 틱을 1% 단위로 스로틀 (시그널 폭주 방지)
            ip = int(pct)
            if ("다운로드 중" in msg or msg.endswith("%")) and ip == self._last_pct:
                return
            self._last_pct = ip
            self.status_signal.emit(pct, msg)

        def _on_status(self, pct, msg):
            self.bar.setValue(int(pct))
            self.status.setText(msg)

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if not exec_:
        return app, win
    sys.exit(app.exec())


def _selfcheck():
    import tempfile

    p, m = format_status({"status": "downloading", "downloaded_bytes": 50,
                          "total_bytes": 100, "speed": 2e6})
    assert p == 50 and "50.0%" in m, m
    p, _ = format_status({"status": "downloading", "downloaded_bytes": 5})  # total 없음
    assert p == 0
    p, m = format_status({"status": "finished"})
    assert p == 100 and "병합" in m
    # 쿠키 재시도 판별: 실제 관측된 두 오류 유형은 참, 일반 오류는 거짓
    assert needs_login_retry("ERROR: [youtube] q1DinydBRNE: This video is not available")
    assert needs_login_retry("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert needs_login_retry("Sign in to confirm your age")
    assert needs_login_retry("Requested format is not available. Use --list-formats")
    assert not needs_login_retry("HTTP Error 404: Not Found")
    # 쿠키 읽기 실패 판별 (영상 차단과 구분): 크롬 암호화 오류들 포함
    assert is_cookie_error("could not find chrome cookies database")
    assert is_cookie_error("Failed to decrypt with DPAPI")
    assert not is_cookie_error("This video is not available")
    # cookies_from 지정 시 yt-dlp 옵션에 반영되는지 (가짜 YoutubeDL로 캡처)
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return {"title": "t"}

    orig_ydl = yt_dlp.YoutubeDL
    yt_dlp.YoutubeDL = FakeYDL
    try:
        download("https://youtu.be/x", ".", lambda p, m: None, cookies_from="edge")
        assert captured["cookiesfrombrowser"] == ("edge",), captured
        captured.clear()
        download("https://youtu.be/x", ".", lambda p, m: None)
        assert "cookiesfrombrowser" not in captured
        # deno가 있으면 js_runtimes로 전달 (전체 화질 포맷의 핵심), 없으면 미설정
        orig_which = shutil.which
        shutil.which = lambda n: r"C:\x\deno.exe" if n == "deno" else orig_which(n)
        try:
            captured.clear()
            download("https://youtu.be/x", ".", lambda p, m: None)
            assert captured["js_runtimes"] == {"deno": {"path": r"C:\x\deno.exe"}}, captured
        finally:
            shutil.which = orig_which
        shutil.which = lambda n: None if n == "deno" else orig_which(n)
        try:
            captured.clear()
            download("https://youtu.be/x", ".", lambda p, m: None)
            assert "js_runtimes" not in captured
        finally:
            shutil.which = orig_which
        # alt_clients=True면 모든 재생 클라이언트 시도 옵션이 켜져야 함
        captured.clear()
        download("https://youtu.be/x", ".", lambda p, m: None, alt_clients=True)
        assert captured["extractor_args"] == {"youtube": {"player_client": ["all"]}}
        # exe 옆 cookies.txt가 있으면 브라우저 쿠키보다 우선
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
            g = globals()
            orig_exe_dir = g["exe_dir"]
            g["exe_dir"] = lambda: Path(td)
            try:
                captured.clear()
                download("https://youtu.be/x", ".", lambda p, m: None, cookies_from="edge")
                assert captured["cookiefile"].endswith("cookies.txt"), captured
                assert "cookiesfrombrowser" not in captured
            finally:
                g["exe_dir"] = orig_exe_dir
    finally:
        yt_dlp.YoutubeDL = orig_ydl
    # 재생목록 판별: /playlist 링크만 전체 다운로드, watch+list는 영상 1개
    assert is_playlist("https://www.youtube.com/playlist?list=PLYU_yAU_QLzY")
    assert not is_playlist("https://www.youtube.com/watch?v=abc&list=PLYU&index=3")
    assert not is_playlist("https://youtu.be/abc")
    # 재생목록 진행률: 10개 중 3번째가 50%면 전체 25%, [3/10] 표기
    p, m = hook_status({"status": "downloading", "downloaded_bytes": 50,
                        "total_bytes": 100, "speed": 2e6,
                        "info_dict": {"playlist_index": 3, "n_entries": 10}})
    assert p == 25 and m.startswith("[3/10] 다운로드 중"), (p, m)
    p, m = hook_status({"status": "downloading", "downloaded_bytes": 50,
                        "total_bytes": 100, "speed": 2e6})  # 단일 영상은 그대로
    assert p == 50 and m.startswith("다운로드 중"), (p, m)
    # 포맷 문자열: H.264(avc1) 우선, 화질 상한 반영, 코덱 불문 병합 폴백, 최후엔 무조건 best
    f = build_format()
    assert f.startswith("bestvideo[vcodec^=avc1]") and f.endswith("/best"), f
    assert "bestvideo+bestaudio/" in f, f
    f = build_format(720)
    assert "bestvideo[height<=720][vcodec^=avc1]" in f and "best[height<=720]/" in f, f
    assert "bestvideo[height<=720]+bestaudio/" in f, f
    assert f.endswith("bestvideo+bestaudio/best"), f  # 최후 폴백은 화질 제한도 해제
    # exe(frozen)에서는 pip를 실행하지 않고 안내만 해야 함
    msgs = []
    sys.frozen = True
    try:
        update_ytdlp(lambda pct, msg: msgs.append(msg))
    finally:
        del sys.frozen
    assert msgs == ["exe 버전은 pip 업데이트 불가 — 업데이트 확인 버튼을 사용하세요"], msgs
    # frozen + _MEIPASS에 번들 실행 파일이 있으면 PATH보다 번들을 우선해야 함
    with tempfile.TemporaryDirectory() as td:
        for tool in ("ffmpeg", "deno"):
            bundled = Path(td) / (f"{tool}.exe" if os.name == "nt" else tool)
            bundled.touch()
            sys.frozen, sys._MEIPASS = True, td
            try:
                assert find_tool(tool) == str(bundled)
            finally:
                del sys.frozen, sys._MEIPASS
    # swap_exe: 새 파일로 교체, 기존은 .old로 보관
    with tempfile.TemporaryDirectory() as td:
        exe, new = Path(td) / "app.exe", Path(td) / "app.new.exe"
        exe.write_text("old"), new.write_text("new")
        swap_exe(exe, new)
        assert exe.read_text() == "new"
        assert (Path(td) / "app.old.exe").read_text() == "old"
        assert not new.exists()
    # 편집: 시간 파싱
    assert parse_time("") is None and parse_time("90") == 90
    assert parse_time("1:30") == 90 and parse_time("0:01:30.5") == 90.5
    for bad in ("a", "1:2:3:4", "-5"):
        try:
            parse_time(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # 편집: 명령 구성 — 구간은 -ss/-t, 모드별 코덱
    c = build_edit_cmd("ff", "a.mp4", "b.mp4", "mp4", 10, 25)
    assert c[c.index("-ss") + 1] == "10.000" and c[c.index("-t") + 1] == "15.000", c
    assert c.index("-ss") < c.index("-i") and "libx264" in c, c
    c = build_edit_cmd("ff", "a.mp4", "b.wmv", "wmv")
    assert "-ss" not in c and "-t" not in c and "wmv2" in c and "wmav2" in c, c
    c = build_edit_cmd("ff", "a.mp4", "b.gif", "gif", gif_fps=10, gif_width=320)
    vf = c[c.index("-vf") + 1]
    assert "fps=10" in vf and "scale=320" in vf and "paletteuse" in vf and "-an" in c, c
    # 편집기: 시간 표시 (parse_time과 왕복), 눈금 간격, 썸네일 명령
    assert fmt_time(83.456) == "01:23.46" and fmt_time(3723.5) == "1:02:03.50"
    assert fmt_time(83.9, precise=False) == "01:23" and fmt_time(-1) == "00:00.00"
    for t in (0, 1.5, 83.46, 3723.5):
        assert abs(parse_time(fmt_time(t)) - t) < 0.01, t
    assert tick_step(10, 800) == 1 and tick_step(3600, 800) == 600 and tick_step(0, 800) == 0.5
    c = build_thumb_cmd("ff", "a.mp4", "/tmp/x", 120, count=24)
    assert "fps=0.200000,scale=-2:72" in c and c[c.index("-frames:v") + 1] == "24", c
    # 편집: 출력 이름은 덮어쓰지 않음
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "clip.mp4"
        assert edit_output_path(src, "gif").name == "clip_gif.gif"
        (Path(td) / "clip_gif.gif").touch()
        assert edit_output_path(src, "gif").name == "clip_gif (2).gif"
        (Path(td) / "clip_gif (2).gif").touch()
        assert edit_output_path(src, "gif").name == "clip_gif (3).gif"
    # 편집: ffmpeg가 있으면 실제 변환까지 (3초 테스트 영상 -> 자르기/WMV/GIF)
    ff = find_tool("ffmpeg")
    if ff:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t.mp4"
            subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                            "-f", "lavfi", "-i", "sine", "-t", "3", "-pix_fmt", "yuv420p",
                            str(src)], check=True, **_no_window())
            assert abs(probe_duration(ff, src) - 3) < 0.2
            pcts = []
            out = edit_video(src, "mp4", lambda p, m: pcts.append(p), start=1, end=2)
            assert out.name == "t_cut.mp4" and abs(probe_duration(ff, out) - 1) < 0.2
            assert pcts[-1] == 100
            out = edit_video(src, "wmv", lambda p, m: None)
            assert out.suffix == ".wmv" and out.stat().st_size > 0
            out = edit_video(src, "gif", lambda p, m: None, end=1, gif_width=160)
            assert out.read_bytes()[:3] == b"GIF"
            try:
                edit_video(src, "gif", lambda p, m: None, start=2, end=1)
                raise AssertionError("역순 구간 허용됨")
            except RuntimeError:
                pass
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        run_gui()  # PyQt6는 GUI 실행 시에만 필요 (selfcheck는 의존성 없이 동작)
