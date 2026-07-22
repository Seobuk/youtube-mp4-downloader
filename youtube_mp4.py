"""유튜브 링크 -> MP4 다운로드 GUI (PyQt6). yt-dlp + ffmpeg 래퍼.

실행: pip install yt-dlp PyQt6  →  python youtube_mp4.py
"""
import shutil
import subprocess
import sys
import threading
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
        "noprogress": True,  # 진행률은 progress_hooks로만 (windowed exe엔 콘솔 없음)
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


def run_gui(exec_=True):
    """GUI 실행. exec_=False면 이벤트 루프를 돌리지 않고 (app, win) 반환 (테스트용)."""
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import (
        QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
        QProgressBar, QPushButton, QVBoxLayout, QWidget,
    )

    class MainWindow(QWidget):
        # 워커 스레드 -> UI 스레드 전달용 (Qt 시그널은 스레드 안전)
        status_signal = pyqtSignal(float, str)
        done_signal = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.setWindowTitle("유튜브 → MP4")
            self.setFixedWidth(520)
            self._last_pct = -1

            self.url = QLineEdit(placeholderText="https://youtu.be/...")
            self.dir = QLineEdit(str(Path.home() / "Downloads"))
            pick = QPushButton("폴더", clicked=self.pick_dir)
            self.go = QPushButton("MP4 다운로드", clicked=self.start_download)
            self.go.setStyleSheet(
                "background:#e63946; color:#fff; font-weight:600;"
                "padding:10px; border:0; border-radius:6px;"
            )
            self.bar = QProgressBar(textVisible=False)
            self.bar.setRange(0, 100)
            self.bar.setFixedHeight(8)
            self.status = QLabel("대기 중")
            ver = QLabel(f"yt-dlp {yt_dlp.version.__version__}")
            ver.setStyleSheet("color:gray; font-size:11px;")
            self.upd = QPushButton("yt-dlp 업데이트", clicked=self.start_update)

            root = QVBoxLayout(self)
            root.addWidget(QLabel("유튜브 링크"))
            root.addWidget(self.url)
            root.addWidget(QLabel("저장 폴더"))
            row = QHBoxLayout()
            row.addWidget(self.dir)
            row.addWidget(pick)
            root.addLayout(row)
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

        def pick_dir(self):
            d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.dir.text())
            if d:
                self.dir.setText(d)

        def start_download(self):
            url = self.url.text().strip()
            if not url:
                return self._on_status(0, "링크를 입력하세요")
            if not shutil.which("ffmpeg"):
                return self._on_status(0, "ffmpeg가 없습니다 (병합 불가)")
            out_dir = self.dir.text()
            self._run(lambda report: download(url, out_dir, report))

        def start_update(self):
            self._run(update_ytdlp)

        def _run(self, fn):
            """fn(report)를 워커 스레드에서 실행. 완료까지 버튼 잠금."""
            self._busy(True)
            self._last_pct = -1

            def work():
                try:
                    fn(self._report)
                except Exception as e:  # 네트워크/포맷 오류를 GUI에 그대로 표시
                    self._report(0, f"오류: {e}")
                finally:
                    self.done_signal.emit()

            threading.Thread(target=work, daemon=True).start()

        def _busy(self, on):
            self.go.setDisabled(on)
            self.upd.setDisabled(on)

        def _report(self, pct, msg):
            # 다운로드 진행 틱을 1% 단위로 스로틀 (시그널 폭주 방지)
            ip = int(pct)
            if msg.startswith("다운로드 중") and ip == self._last_pct:
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
        run_gui()  # PyQt6는 GUI 실행 시에만 필요 (selfcheck는 의존성 없이 동작)
