"""PageSentinel - monitor HTML, JSON, text and binary resources (Python 3.10+)."""

import difflib
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QTextCharFormat, QSyntaxHighlighter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QPlainTextEdit, QVBoxLayout, QWidget, QMenu, QAbstractItemView,
)

APP_NAME = "PageSentinel"
CONFIDENCE_LEVELS = {
    "Low (1 check)": 1,
    "Medium (2 checks)": 2,
    "High (3 checks)": 3,
}
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
FREQUENCIES = {
    "10 seconds": 10,
    "30 seconds": 30,
    "10 minutes": 600,
    "30 minutes": 1800,
    "1 hour": 3600,
}
MAX_PAGE_SIZE_MB = 200
MAX_BYTES = MAX_PAGE_SIZE_MB * 1024 * 1024
IGNORED_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "nav", "footer", "header", "aside")


def storage_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def normalize_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        raise ValueError("Enter a URL.")
    if "://" not in url:
        url = "https://" + url
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Invalid HTTP/HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials embedded in URLs are not supported.")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid port.") from exc
    return url


def file_prefix(url: str) -> str:
    parsed = urlsplit(url)
    human = f"{parsed.hostname}_{parsed.path.strip('/').replace('/', '_')}".strip("_")
    human = re.sub(r"[^a-zA-Z0-9_-]+", "_", human)[:65].strip("_") or "page"
    return f"{human}_{hashlib.sha256(url.encode('utf-8')).hexdigest()[:10]}"


def atomic_write(path: Path, content: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def clean_html(payload: bytes, mode: str, selector: str, patterns: str) -> str:
    """Build a comparable representation without changing the stored HTML."""
    html = payload.decode("utf-8", errors="replace")
    if mode == "Strict HTML":
        text = html
    else:
        soup = BeautifulSoup(html, "html.parser")
        if selector:
            try:
                matches = soup.select(selector)
            except Exception as exc:
                raise ValueError(f"Invalid CSS selector: {exc}") from exc
            if not matches:
                raise ValueError("The CSS selector did not match any elements on the page.")
            roots = matches
        else:
            roots = [soup.main or soup.body or soup]
        contents = []
        for root in roots:
            # Reparse the fragment to avoid modifying the shared tree.
            fragment = BeautifulSoup(str(root), "html.parser")
            for tag in fragment.find_all(IGNORED_TAGS):
                tag.decompose()
            for tag in fragment.find_all(True):
                if tag.parent is None:
                    continue  # Already removed with its parent.
                if tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).lower() == "true":
                    tag.decompose()
                    continue
                style = re.sub(r"\s+", "", str(tag.get("style", "")).lower())
                if "display:none" in style or "visibility:hidden" in style:
                    tag.decompose()
            contents.append(fragment.get_text(separator="\n", strip=True))
        text = "\n".join(contents)
        text = "\n".join(
            re.sub(r"\s+", " ", line).strip()
            for line in text.splitlines()
            if line.strip()
        )
    for pattern in patterns.splitlines():
        pattern = pattern.strip()
        if pattern:
            try:
                text = re.sub(pattern, "", text, flags=re.MULTILINE)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression ({pattern}): {exc}") from exc
    if not text.strip():
        raise ValueError("No comparable content found: reference not updated.")
    return text



# Compare structured/text content semantically; all other formats by exact bytes.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".xml", ".yaml", ".yml", ".log",
    ".css", ".js", ".ts", ".py", ".sh", ".ini", ".conf",
    ".toml", ".svg", ".rss", ".atom", ".sql", ".jsonl", ".ndjson",
}
JSON_EXTENSIONS = {".json", ".jsonld", ".geojson"}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}


def resource_details(url: str, content_type: str) -> tuple[str, str]:
    """Return resource comparison kind and a safe snapshot extension."""
    mime = content_type.split(";", 1)[0].strip().lower()
    suffix = Path(urlsplit(url).path).suffix.lower()
    valid_suffix = bool(re.fullmatch(r"\.[a-z0-9]{1,12}", suffix))

    if suffix in JSON_EXTENSIONS or mime in ("application/json", "text/json") or mime.endswith("+json"):
        kind = "json"
    elif suffix in HTML_EXTENSIONS or mime in ("text/html", "application/xhtml+xml"):
        kind = "html"
    elif mime.startswith("text/") or mime.endswith("+xml") or mime in (
        "application/xml", "application/javascript", "application/x-javascript"
    ):
        kind = "text"
    elif suffix in JSON_EXTENSIONS:
        kind = "json"
    elif suffix in HTML_EXTENSIONS:
        kind = "html"
    elif suffix in TEXT_EXTENSIONS:
        kind = "text"
    elif mime:
        kind = "binary"
    else:
        # Old servers often omit Content-Type. Preserve original HTML default.
        kind = "html" if not valid_suffix else "binary"

    if valid_suffix:
        ext = suffix
    elif kind == "html":
        ext = ".html"
    elif kind == "json":
        ext = ".json"
    elif kind == "text":
        ext = mimetypes.guess_extension(mime) or ".txt"
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", ext):
            ext = ".txt"
    else:
        ext = ".bin"
    return kind, ext


def comparable_content(payload: bytes, kind: str, mode: str,
                       selector: str, patterns: str) -> str:
    """Generate comparison text, keeping downloaded files byte-for-byte intact."""
    if kind == "html":
        return clean_html(payload, mode, selector, patterns)

    if kind == "binary":
        # No lossy decoding for arbitrary PDFs, archives, images, executables, etc.
        if selector or patterns:
            raise ValueError("CSS/regex filters cannot be used on binary resources.")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Text/JSON resource is not UTF-8; cannot compare safely.") from exc

    if kind == "json":
        if selector:
            raise ValueError("CSS selectors only apply to HTML resources.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON returned by server: {exc}") from exc
        # Formatting / key order should not generate false positives.
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2)
    elif selector:
        raise ValueError("CSS selectors only apply to HTML resources.")

    for pattern in patterns.splitlines():
        pattern = pattern.strip()
        if pattern:
            try:
                text = re.sub(pattern, "", text, flags=re.MULTILINE)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression ({pattern}): {exc}") from exc
    if not text.strip():
        raise ValueError("No comparable content found: reference not updated.")
    return text


def diff_preview(before: str, after: str) -> list[str]:
    output = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    return [line[:250] for line in output if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))][:8]


def curl_binary() -> str | None:
    """Prefer the Windows built-in cURL rather than an arbitrary PATH executable."""
    if sys.platform == "win32":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        built_in = system_root / "System32" / "curl.exe"
        if built_in.is_file():
            return str(built_in)
    return shutil.which("curl.exe" if sys.platform == "win32" else "curl")


class FetchWorker(QObject):
    success = Signal(bytes, str, int, str, str)
    failure = Signal(str, int)
    finished = Signal()

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def fetch_with_curl(self) -> tuple[bytes, str, int, str]:
        """Single, bounded cURL fallback after an HTTPX 403 response."""
        executable = curl_binary()
        if not executable:
            raise RuntimeError("cURL is unavailable on this computer.")

        # Save to a temporary file: never buffer a large response on stdout.
        # curl --max-filesize caps the response even when Content-Length is absent.
        with tempfile.TemporaryDirectory(prefix="pagesentinel_") as directory:
            output = Path(directory) / "response.data"
            command = [
                executable,
                "--silent", "--show-error", "--location",
                "--max-redirs", "5",
                "--connect-timeout", "10", "--max-time", "30",
                "--max-filesize", str(MAX_BYTES),
                "--user-agent", FIREFOX_UA,
                "--header", "Accept: */*",
                "--output", str(output),
                "--write-out", "%{http_code}\n%{url_effective}\n%{content_type}",
                "--", self.url,
            ]
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=35,
                    check=False, creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("cURL timed out after 35 seconds.") from exc

            details = result.stdout.strip().splitlines()
            status = int(details[0]) if details and details[0].isdigit() else 0
            final_url = details[1] if len(details) > 1 else self.url
            content_type = details[2].lower() if len(details) > 2 else ""

            if status >= 400:
                raise CurlHTTPError(status, f"cURL HTTP {status}: {final_url}")
            if result.returncode != 0:
                raise RuntimeError(
                    f"cURL failed ({result.returncode}): {result.stderr.strip()[:350]}"
                )
            if status != 200:
                raise RuntimeError(f"Unexpected cURL HTTP {status}: {final_url}")
            if not output.exists():
                raise RuntimeError("cURL returned no page data.")
            if output.stat().st_size > MAX_BYTES:
                raise ValueError(f"Page too large (maximum {MAX_PAGE_SIZE_MB} MB).")
            payload = output.read_bytes()
            return payload, final_url, status, content_type

    @Slot()
    def run(self):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=True,
                max_redirects=5,
                headers={
                    "User-Agent": FIREFOX_UA,
                    "Accept": "*/*",
                },
            ) as client:
                with client.stream("GET", self.url) as response:
                    status = response.status_code
                    if status == 403:
                        # A single fallback per scheduled check, not a retry loop.
                        try:
                            payload, final_url, curl_status, content_type = self.fetch_with_curl()
                            self.success.emit(payload, final_url, curl_status, "cURL fallback", content_type)
                        except CurlHTTPError as exc:
                            self.failure.emit(
                                f"HTTPX 403; {exc} (fallback unsuccessful)", exc.status
                            )
                        except Exception as exc:
                            self.failure.emit(
                                f"HTTPX 403; cURL fallback failed: {exc}", 403
                            )
                        return

                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        if len(chunks) + len(chunk) > MAX_BYTES:
                            raise ValueError(f"Page too large (maximum {MAX_PAGE_SIZE_MB} MB).")
                        chunks.extend(chunk)
                    self.success.emit(bytes(chunks), str(response.url), status, "HTTPX", content_type)
        except httpx.HTTPStatusError as exc:
            self.failure.emit(f"HTTP {exc.response.status_code} : {exc.request.url}", exc.response.status_code)
        except Exception as exc:
            self.failure.emit(str(exc), 0)
        finally:
            self.finished.emit()


class CurlHTTPError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


@dataclass
class Monitor:
    url: str
    seconds: int
    mode: str = "Visible content"
    selector: str = ""
    patterns: str = ""
    confirmations: int = 2
    prefix: str = ""
    active: bool = False
    busy: bool = False
    timer: QTimer | None = None
    thread: QThread | None = None
    worker: FetchWorker | None = None
    checks: int = 0
    changes: int = 0
    errors: int = 0
    pending_hash: str = ""
    pending_count: int = 0
    next_at: datetime | None = None
    status: str = "Stopped"
    generation: int = 0

    def __post_init__(self):
        self.prefix = file_prefix(self.url)

    def config(self) -> dict:
        return {
            "url": self.url, "seconds": self.seconds, "mode": self.mode,
            "selector": self.selector, "patterns": self.patterns,
            "confirmations": self.confirmations,
        }


class ResultRelay(QObject):
    """Receives HTTP signals on the GUI thread, never on the HTTP worker thread."""

    def __init__(self, window, monitor, generation):
        super().__init__(window)
        self.window = window
        self.monitor = monitor
        self.generation = generation

    @Slot(bytes, str, int, str, str)
    def on_success(self, payload, final_url, status_code, client_name, content_type):
        self.window.handle_content(self.monitor, self.generation, payload, final_url, status_code, client_name, content_type)

    @Slot(str, int)
    def on_failure(self, message, status_code):
        self.window.handle_error(self.monitor, self.generation, message, status_code)

    @Slot()
    def on_finished(self):
        self.window.check_finished(self.monitor, self)


class AlertHighlighter(QSyntaxHighlighter):
    """Highlight alert blocks in QPlainTextEdit, without HTML or emojis."""

    def __init__(self, document):
        super().__init__(document)
        self.alert_format = QTextCharFormat()
        self.alert_format.setForeground(QColor("#e53935"))
        self.alert_format.setFontWeight(QFont.Weight.Bold)
        self.diff_added = QTextCharFormat()
        self.diff_added.setForeground(QColor("#2e9d59"))
        self.diff_removed = QTextCharFormat()
        self.diff_removed.setForeground(QColor("#d9534f"))

    def highlightBlock(self, text):
        if "[!!! CHANGE DETECTED !!!]" in text or text.startswith("    URL:") or text.startswith("    Reference updated:") or text.startswith("    Detected at:") or text.startswith("=" * 25):
            self.setFormat(0, len(text), self.alert_format)
        elif "DIFF : +" in text:
            self.setFormat(0, len(text), self.diff_added)
        elif "DIFF : -" in text:
            self.setFormat(0, len(text), self.diff_removed)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PageSentinel v2.4")
        self.resize(1030, 700)
        self.monitors: list[Monitor] = []
        self.closing = False
        self._relays = {}  # Retain relays until their threads have finished.

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://your.domain/")
        self.frequency = QComboBox()
        self.frequency.addItems(FREQUENCIES)
        self.frequency.setCurrentText("10 minutes")
        self.mode = QComboBox()
        self.mode.addItems(["Visible content", "Strict HTML"])
        self.selector = QLineEdit()
        self.selector.setPlaceholderText("Optional: article, #content, .post...")
        self.patterns = QLineEdit()
        self.patterns.setPlaceholderText("Optional: regex to ignore (one expression)")
        self.confidence = QComboBox()
        self.confidence.addItems(CONFIDENCE_LEVELS)
        self.confidence.setCurrentText("Medium (2 checks)")
        self.confidence.setToolTip("How many consecutive checks must observe the same new content")

        self.add_button = QPushButton("Add URL")
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.remove_button = QPushButton("Remove selected URL")
        self.stop_button.setEnabled(False)

        self.status = QLabel("Ready to monitor a web page.")
        self.status.setWordWrap(True)
        self.stats = QLabel("Checks: 0 | Changes: 0 | Errors: 0")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["URL", "Frequency", "Mode", "Status", "Next check"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.alert_highlighter = AlertHighlighter(self.log.document())
        self._latest_alert = None

        form = QFormLayout()
        form.addRow("URL :", self.url_input)
        form.addRow("Frequency:", self.frequency)
        form.addRow("Mode :", self.mode)
        form.addRow("CSS selector:", self.selector)
        form.addRow("Ignore (regex):", self.patterns)
        form.addRow("Confidence:", self.confidence)
        buttons = QHBoxLayout()
        for button in (self.add_button, self.remove_button, self.start_button, self.stop_button):
            buttons.addWidget(button)
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(self.stats)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Check history:"))
        layout.addWidget(self.log)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.add_button.clicked.connect(self.add_monitor)
        self.remove_button.clicked.connect(self.remove_monitor)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.heartbeat = QTimer(self)
        self.heartbeat.timeout.connect(self.refresh_table)
        self.heartbeat.start(1000)

        self.tray = None
        if sys.platform == "win32" and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self)
            self.tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_BrowserReload))
            tray_menu = QMenu()
            show_action = QAction("Show PageSentinel", self)
            show_action.triggered.connect(self.restore)
            quit_action = QAction("Quit", self)
            quit_action.triggered.connect(self.quit_application)
            tray_menu.addAction(show_action)
            tray_menu.addAction(quit_action)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self.tray.show()

        self.load_config()
        self.refresh_table()

    def record(self, message: str, alert: bool = False):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if alert:
            self.log.appendPlainText("=" * 60)
            self.log.appendPlainText(f"[{stamp}] [!!! CHANGE DETECTED !!!]")
            self.log.appendPlainText(f"    {message}")
            self.log.appendPlainText("=" * 60)
        else:
            self.log.appendPlainText(f"[{stamp}] {message}")

    def current_path(self, monitor: Monitor, extension: str = ".html") -> Path:
        return storage_dir() / f"{monitor.prefix}_current{extension}"

    def add_monitor(self):
        try:
            url = normalize_url(self.url_input.text())
            if any(m.url == url for m in self.monitors):
                raise ValueError("This URL is already being monitored.")
            pattern = self.patterns.text().strip()
            if pattern:
                re.compile(pattern)
            selector = self.selector.text().strip()
            if selector:
                BeautifulSoup("<html></html>", "html.parser").select(selector)
            monitor = Monitor(
                url=url, seconds=FREQUENCIES[self.frequency.currentText()],
                mode=self.mode.currentText(), selector=selector,
                patterns=pattern, confirmations=CONFIDENCE_LEVELS[self.confidence.currentText()],
            )
        except Exception as exc:
            QMessageBox.warning(self, "Cannot add URL", str(exc))
            return
        self.monitors.append(monitor)
        self.save_config()
        self.record(f"URL added: {url}")
        self.refresh_table()
        if self.stop_button.isEnabled():
            self.activate(monitor)

    def remove_monitor(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.monitors):
            return
        monitor = self.monitors[row]
        if monitor.busy:
            QMessageBox.information(self, "Check in progress", "Please retry after the HTTP check completes.")
            return
        self.deactivate(monitor)
        self.monitors.pop(row)
        self.save_config()
        self.refresh_table()
        self.record(f"URL removed from monitoring: {monitor.url} (files preserved).")

    def save_config(self):
        data = {"version": 2, "monitors": [m.config() for m in self.monitors]}
        try:
            atomic_write(storage_dir() / "pagesentinel.json", json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        except OSError as exc:
            self.record(f"Configuration save error: {exc}")

    def load_config(self):
        path = storage_dir() / "pagesentinel.json"
        if not path.exists():
            return
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            for item in config.get("monitors", []):
                url = normalize_url(item["url"])
                if any(m.url == url for m in self.monitors):
                    continue
                seconds = int(item.get("seconds", 600))
                mode = item.get("mode", "Visible content")
                mode = {
                    "Contenu visible": "Visible content",
                    "HTML strict": "Strict HTML",
                }.get(mode, mode)
                if seconds not in FREQUENCIES.values() or mode not in ("Visible content", "Strict HTML"):
                    continue
                self.monitors.append(Monitor(
                    url=url, seconds=seconds, mode=mode,
                    selector=str(item.get("selector", "")),
                    patterns=str(item.get("patterns", "")),
                    confirmations=max(1, min(5, int(item.get("confirmations", 2)))),
                ))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.record(f"Configuration load error: {exc}")

    def start(self):
        if not self.monitors:
            self.add_monitor()
        if not self.monitors:
            return
        try:
            test_file = storage_dir() / ".pagesentinel_write_test"
            atomic_write(test_file, b"ok")
            test_file.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Storage unavailable", str(exc))
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("Monitoring active.")
        for monitor in self.monitors:
            self.activate(monitor)
        self.record("Monitoring started.")

    def stop(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        for monitor in self.monitors:
            self.deactivate(monitor)
        self.status.setText("Monitoring stopped.")
        self.record("Monitoring stopped.")
        self.refresh_table()

    def activate(self, monitor: Monitor):
        if monitor.active:
            return
        monitor.active = True
        monitor.generation += 1
        monitor.pending_hash = ""
        monitor.pending_count = 0
        monitor.timer = QTimer(self)
        monitor.timer.setSingleShot(True)
        monitor.timer.timeout.connect(lambda m=monitor: self.check(m))
        if not monitor.busy:
            self.check(monitor)
        else:
            monitor.status = "Waiting for previous check..."

    def deactivate(self, monitor: Monitor):
        monitor.active = False
        monitor.generation += 1
        monitor.next_at = None
        if monitor.timer is not None:
            monitor.timer.stop()
            monitor.timer.deleteLater()
            monitor.timer = None
        monitor.pending_hash = ""
        monitor.pending_count = 0
        monitor.status = "Stopped"

    def check(self, monitor: Monitor):
        if not monitor.active or monitor.busy or self.closing:
            return
        monitor.busy = True
        monitor.next_at = None
        monitor.status = "Downloading..."
        monitor.thread = QThread(self)
        monitor.worker = FetchWorker(monitor.url)
        monitor.worker.moveToThread(monitor.thread)
        # This relay lives on the GUI thread. Signals emitted from
        # the HTTP thread are queued to its @Slot methods.
        relay = ResultRelay(self, monitor, monitor.generation)
        self._relays[monitor.prefix] = relay

        monitor.thread.started.connect(monitor.worker.run)
        monitor.worker.success.connect(relay.on_success, Qt.ConnectionType.QueuedConnection)
        monitor.worker.failure.connect(relay.on_failure, Qt.ConnectionType.QueuedConnection)
        monitor.worker.finished.connect(monitor.thread.quit, Qt.ConnectionType.DirectConnection)
        monitor.worker.finished.connect(monitor.worker.deleteLater)
        monitor.thread.finished.connect(relay.on_finished, Qt.ConnectionType.QueuedConnection)
        monitor.thread.finished.connect(monitor.thread.deleteLater)
        monitor.thread.start()
        self.refresh_table()

    def handle_content(self, monitor: Monitor, generation: int, payload: bytes, final_url: str, status_code: int, client_name: str, content_type: str):
        if not monitor.active or generation != monitor.generation:
            return
        monitor.checks += 1
        self.record(f"{monitor.url} — HTTP {status_code} via {client_name}.")
        try:
            kind, extension = resource_details(final_url, content_type)
            current = self.current_path(monitor, extension)
            fresh = comparable_content(payload, kind, monitor.mode, monitor.selector, monitor.patterns)
            if not current.exists():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                initial = storage_dir() / f"{monitor.prefix}_{stamp}{extension}"
                with initial.open("xb") as handle:
                    handle.write(payload)
                atomic_write(current, payload)
                monitor.status = "Initial snapshot created"
                self.record(f"{monitor.url} — initial snapshot: {initial.name}")
                self.record(f"Current reference: {current.name}")
            else:
                old = comparable_content(current.read_bytes(), kind, monitor.mode, monitor.selector, monitor.patterns)
                if old == fresh:
                    monitor.pending_hash = ""
                    monitor.pending_count = 0
                    monitor.status = "No change"
                    self.record(f"{monitor.url} — no change (HTTP {status_code}).")
                else:
                    new_hash = hashlib.sha256(fresh.encode("utf-8")).hexdigest()
                    if new_hash == monitor.pending_hash:
                        monitor.pending_count += 1
                    else:
                        monitor.pending_hash = new_hash
                        monitor.pending_count = 1
                    if monitor.pending_count < monitor.confirmations:
                        monitor.status = f"Pending confirmation ({monitor.pending_count}/{monitor.confirmations})"
                        self.record(f"{monitor.url} — change awaiting confirmation ({monitor.pending_count}/{monitor.confirmations}).")
                    else:
                        changes = diff_preview(old, fresh) if kind != "binary" else ["Binary content changed (SHA-256 differs)."]
                        atomic_write(current, payload)
                        monitor.changes += 1
                        monitor.pending_hash = ""
                        monitor.pending_count = 0
                        monitor.status = "Change detected"
                        self._latest_alert = (final_url, datetime.now())
                        self.status.setStyleSheet("color: #e53935; font-weight: bold; font-size: 14px;")
                        self.status.setText(f"CHANGE DETECTED — {final_url} | Reference updated.")
                        self.record(f"URL: {final_url} | Reference updated: yes", alert=True)
                        for line in changes:
                            self.record(f"DIFF : {line}")
                        if self.tray and self.tray.supportsMessages():
                            self.tray.showMessage("PageSentinel - Change detected", f"{monitor.url}\nReference updated.", QSystemTrayIcon.MessageIcon.Warning, 10000)
                        QApplication.alert(self, 5000)
        except (ValueError, OSError) as exc:
            self.handle_error(monitor, generation, str(exc), -1)
        self.refresh_table()

    def handle_error(self, monitor: Monitor, generation: int, message: str, status_code: int):
        if not monitor.active or generation != monitor.generation:
            return
        if status_code != -1:
            monitor.checks += 1
        monitor.errors += 1
        monitor.pending_hash = ""
        monitor.pending_count = 0
        monitor.status = f"HTTP error {status_code}" if status_code else "Check error"
        self.record(f"{monitor.url} — ERROR: {message}")
        if status_code in (403, 429):
            monitor.status = f"HTTP {status_code}: pausing 5 min"
        self.refresh_table()

    def check_finished(self, monitor: Monitor, relay: ResultRelay):
        # Called on the GUI thread after QThread.finished is emitted.
        # Ignore a stale relay if a new check has started.
        if self._relays.get(monitor.prefix) is not relay:
            return
        self._relays.pop(monitor.prefix, None)
        relay.deleteLater()
        monitor.busy = False
        monitor.thread = None
        monitor.worker = None
        if monitor.active and not self.closing and monitor.timer is not None:
            delay = max(monitor.seconds, 300 if monitor.status.startswith(("HTTP 403:", "HTTP 429:")) else 0)
            from datetime import timedelta
            monitor.next_at = datetime.now() + timedelta(seconds=delay)
            monitor.timer.start(delay * 1000)
        self.refresh_table()

    def refresh_table(self):
        selected = self.table.currentRow()
        self.table.setRowCount(len(self.monitors))
        for row, monitor in enumerate(self.monitors):
            next_check = monitor.next_at.strftime("%H:%M:%S") if monitor.next_at else "—"
            fields = (
                monitor.url,
                next((label for label, value in FREQUENCIES.items() if value == monitor.seconds), str(monitor.seconds)),
                monitor.mode, monitor.status, next_check,
            )
            for col, value in enumerate(fields):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, col, item)
                item.setText(value)
        if 0 <= selected < len(self.monitors):
            self.table.selectRow(selected)
        self.stats.setText(
            f"Checks: {sum(m.checks for m in self.monitors)} | "
            f"Changes: {sum(m.changes for m in self.monitors)} | "
            f"Errors: {sum(m.errors for m in self.monitors)}"
        )

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and self.isMinimized() and self.tray:
            QTimer.singleShot(0, self.hide)

    def quit_application(self):
        self.closing = True
        for monitor in self.monitors:
            self.deactivate(monitor)
        # HTTPX has bounded timeouts, but the thread must finish before
        # the window is destroyed to avoid a Qt crash.
        for monitor in self.monitors:
            if monitor.thread is not None and monitor.thread.isRunning():
                monitor.thread.quit()
                monitor.thread.wait()  # May wait for the in-progress HTTP request to finish.
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.tray and not self.closing:
            self.hide()
            event.ignore()
            return
        self.quit_application()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
