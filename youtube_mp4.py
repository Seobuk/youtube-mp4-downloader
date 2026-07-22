"""유튜브 링크 -> MP4 다운로드 GUI (PyQt6). yt-dlp + ffmpeg 래퍼.

exe(PyInstaller) 배포: ffmpeg 내장, 다운로드 실패 시 깃허브 릴리즈에서 자동 업데이트.
소스 실행: pip install yt-dlp PyQt6  →  python youtube_mp4.py (ffmpeg는 PATH에)
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
EXE_NAME = "YoutubeMP4.exe"


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


def find_ffmpeg():
    """exe에 번들된 ffmpeg 우선, 없으면 PATH에서 탐색. 없으면 None."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / (
            "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if bundled.is_file():
            return str(bundled)
    return shutil.which("ffmpeg")


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
        "ffmpeg_location": find_ffmpeg(),
    }
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
    else:
        report(100, f"완료: {info.get('title', '')}.mp4")


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


def run_gui(exec_=True):
    """GUI 실행. exec_=False면 이벤트 루프를 돌리지 않고 (app, win) 반환 (테스트용)."""
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import (
        QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
        QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
    )

    cleanup_old_exe()

    class MainWindow(QWidget):
        # 워커 스레드 -> UI 스레드 전달용 (Qt 시그널은 스레드 안전)
        status_signal = pyqtSignal(float, str)
        done_signal = pyqtSignal()
        restart_signal = pyqtSignal()  # 자동 업데이트 후 앱 종료 (새 exe가 이미 실행됨)

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
            if not find_ffmpeg():
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
                        download(url, out_dir, report, height, playlist, **kw)
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
            self.go.setDisabled(on)
            self.upd.setDisabled(on)

        def _report(self, pct, msg):
            # 다운로드 진행 틱을 1% 단위로 스로틀 (시그널 폭주 방지)
            ip = int(pct)
            if "다운로드 중" in msg and ip == self._last_pct:
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
    # frozen + _MEIPASS에 ffmpeg가 있으면 PATH보다 번들을 우선해야 함
    with tempfile.TemporaryDirectory() as td:
        bundled = Path(td) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        bundled.touch()
        sys.frozen, sys._MEIPASS = True, td
        try:
            assert find_ffmpeg() == str(bundled)
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
    print("selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        run_gui()  # PyQt6는 GUI 실행 시에만 필요 (selfcheck는 의존성 없이 동작)
