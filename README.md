# PageSentinel v2.3 — English interface

Lightweight local web page change monitor for Windows and Linux (including WSL). No SQLite database required.

## HTTP 403 fallback

HTTPX is the primary HTTP client. If the origin responds with HTTP 403, PageSentinel makes **one** attempt with the Windows built-in `curl.exe` (or `curl` from PATH on other operating systems). The application never replaces snapshots with error pages. If both clients fail with 403, the next check is delayed by at least 5 minutes. HTTP 429 also applies a 5-minute delay.

Windows 10/11 usually includes `C:\Windows\System32\curl.exe`; on other systems install cURL separately. This file is invoked as an external process, so it must exist on the target computer even when `PageSentinel.exe` is bundled with PyInstaller. The log shows `HTTPX` or `cURL fallback` for each successful check. No additional Python packages are required.

## Features

- Monitor several URLs with individual check frequencies (10 seconds, 30 seconds, 10 minutes, 30 minutes, 1 hour).
- New entries default to **10 minutes**; saved entries keep their own frequencies.
- Compare visible content or strict HTML; optional CSS selector and regex exclusions.
- Choose Confidence: Low (1 check), Medium (2 checks, default), High (3 checks). Existing stored confirmation values remain supported.
- Preserve the original timestamped HTML snapshot and update `<prefix>_current.html` upon confirmed change.
- Store monitoring configuration in `pagesentinel.json` beside the executable/script.
- Show activity log, next checks and statistics; system tray and notifications when supported.
- Prominent red/bold change alert blocks in the plain-text history and persistent status message (no emoji or HTML rendering).
- Previously saved French mode values in `pagesentinel.json` are migrated in memory and retained as English on the next save.

## Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Build the Windows executable (on **Windows**, not WSL):

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --onefile --windowed --clean --name PageSentinel main.py
```

The executable is generated as `dist\PageSentinel.exe`. Keep it in a writable directory because configuration and HTML snapshots are stored beside it.

## Linux / WSL

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

## Known limitations

- HTTPX downloads HTML; it does not execute JavaScript.
- Dynamic page text can still trigger changes; use CSS selectors or regex exclusions.
- The decompressed response size limit is 200 MB; large responses can require substantial memory.
- Frequency intervals start after each completed HTTP check.
- Quitting may wait for an in-progress HTTP request to finish.
- Adjusting a saved monitor's comparison options requires removing and adding that monitor again; its snapshot files remain untouched.
- Counters are session-only.
