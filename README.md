<div align="center">

# 🛡️ PageSentinel

### Because hitting F5 is not a cybersecurity monitoring strategy.

**A lightweight, open-source webpage change monitor for people who have better things to do than refresh a browser tab.**

`Python` · `PySide6` · `Windows` · `Linux` · `Local-first` · `No database`

</div>

---

## Another vulnerability? What a surprise. 🙃

A new PHP security advisory? A Node.js update that's *almost* here? Another Linux vulnerability? An npm package causing collective anxiety?

Welcome to cybersecurity, where you can spend half your day fixing problems and the other half checking whether the fix has finally been released.

You open the vendor's release page. Nothing. Refresh. Still nothing. Five minutes later, refresh again. Somewhere, your **F5 key files a workplace injury claim**.

**PageSentinel** was built to take that repetitive job off your hands. Tell it which webpages matter, choose how often to check them, and let it tell you when the content changes.

No massive monitoring platform. No twelve-microservice Kubernetes cluster. No mandatory SaaS account. Just a small desktop application doing a useful thing.

> **Important:** PageSentinel detects webpage changes, not vulnerabilities or patch availability by itself. An alert means **go inspect the page**, not **the CVE has been fixed**.

## How it works

1. **Enter a URL**, such as a vendor's security advisories or release notes page.
2. **Choose a frequency:** 10 seconds, 30 seconds, 10 minutes, 30 minutes, or 1 hour. **10 minutes** is the default.
3. **Choose a comparison mode** and, if useful, a CSS selector or exclusion regex.
4. **Set Confidence** to decide how many consecutive checks must see the *same* change.
5. **Add URL**, then click **Start**. Go back to work. Or make coffee. Both are valid.
6. If a change is confirmed, PageSentinel highlights it in the history, updates its reference, and may show a desktop notification where supported.

The first successful check creates an immutable timestamped HTML snapshot plus a working reference file. Later checks compare against that working reference. Only a **confirmed change** updates the reference; the original snapshot stays untouched.

## Features at a glance

| Feature | What it does |
| --- | --- |
| Multiple URLs | Monitor several webpages, each with its own settings |
| Flexible intervals | 10 s · 30 s · 10 min · 30 min · 1 h |
| Visible content | Compare extracted text rather than every byte of HTML |
| Strict HTML | Detect changes to the HTML source |
| CSS selector | Limit visible-content analysis to an element (for example `article` or `#content`) |
| Ignore regex | Remove matching text from the representation used for comparison |
| Confidence | `Low (1 check)`, `Medium (2 checks)`, `High (3 checks)` |
| Change preview | Show a short text diff when a change is confirmed |
| Local snapshots | Keep original HTML and update the current reference locally |
| Activity & statistics | Show checks, changes, errors, and upcoming checks |
| Notifications | Desktop notification / system tray when supported |
| HTTP fallback | On an HTTPX `403`, try cURL **once**; no endless retry storm |
| Zero database | Store monitor settings in a local JSON file |

## Screenshot

> **Add a screenshot here:** `docs/pagesentinel-screenshot.png`, ideally showing a confirmed change. No need for a screenshot of sixteen terminal windows—we're not trying to scare people.

```markdown
![PageSentinel interface](docs/pagesentinel-screenshot.png)
```

## Quick start — Windows (Git Bash)

From the project directory:

```bash
python --version
python -m venv .venv
./.venv/Scripts/python.exe -m pip install --upgrade pip
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe main.py
```

No need to activate the virtual environment when calling its Python executable directly.

**Using PowerShell instead?**

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

**Prerequisites:** Python 3 with a version supported by the current dependencies, internet access to the monitored URLs, and permission to write files next to the script or executable. The project uses `PySide6`, `httpx`, and `beautifulsoup4`.

## Quick start — Linux / WSL

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

On Debian/Ubuntu, if creating the environment fails:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
```

Running the GUI inside WSL requires a working graphical environment (for example, WSLg). **Build the Windows `.exe` on Windows, not inside WSL.**

## Build a standalone Windows executable

On **Windows**, from the project directory, after installing dependencies:

```bash
./.venv/Scripts/python.exe -m pip install --upgrade pyinstaller
./.venv/Scripts/python.exe -m PyInstaller \
    --onefile \
    --windowed \
    --clean \
    --noconfirm \
    --name PageSentinel \
    main.py
```

Find the resulting application at:

```text
dist/PageSentinel.exe
```

Copy `PageSentinel.exe` to a **writable folder** on the target Windows PC, such as `C:\Tools\PageSentinel\`. Python does **not** have to be installed on that PC. However, the cURL fallback needs a usable `curl.exe` on the target machine; it is **not** bundled automatically by PyInstaller.

For diagnostics, build a console-enabled edition:

```bash
./.venv/Scripts/python.exe -m PyInstaller \
    --onefile --console --clean --noconfirm \
    --name PageSentinel-Debug main.py
```

> Windows SmartScreen or endpoint protection may warn about unfamiliar unsigned executables. Download only from a source you trust and review the code if in doubt.

## Confidence: fewer false alarms, more useful alerts

| Setting | Behavior |
| --- | --- |
| **Low (1 check)** | Alert on the first observed difference |
| **Medium (2 checks)** | Require the same new content in two consecutive checks (**default**) |
| **High (3 checks)** | Require the same new content in three consecutive checks |

For example, with a 10-minute frequency and Medium confidence, an initial difference is marked as **pending confirmation**. If the next check sees the same changed content, PageSentinel raises the alert and updates its reference. If the page returns to the original content—or changes to something else—the confirmation sequence resets.

**Confidence is not a percentage** or a prediction of whether an update is a security fix. It's simply the number of matching observations required.

## Visible content vs. Strict HTML

**Visible content** parses HTML, ignores common non-editorial elements (such as scripts, styles, navigation, headers, and footers), and compares normalized text. It's usually the better starting point for release notes and articles. It can still notice changing clocks, counters, or other dynamic text.

**Strict HTML** compares the HTML source as text. It's useful if you care about markup changes, but it may produce more alerts from invisible changes.

For tighter targeting, specify a **CSS selector** such as `article`, `main`, or `#release-notes` in Visible content mode. An **Ignore (regex)** pattern removes matching text from the data being compared without changing stored snapshots. If the selector matches nothing or a regex is invalid, PageSentinel reports a check error rather than silently overwriting the reference.

## What gets saved—and where?

PageSentinel stores data **beside `main.py` or beside `PageSentinel.exe`**:

```text
PageSentinel/
├── PageSentinel.exe
├── pagesentinel.json
├── example_com_updates_ab12cd3456_20261023_123605_000000.html
└── example_com_updates_ab12cd3456_current.html
```

- `pagesentinel.json` holds configured URLs and their settings. It is created when configuration is saved; you do not need to ship it with the app.
- The timestamped `.html` file is the **original snapshot**, preserved for reference.
- `_current.html` is the **comparison baseline**, replaced only after a confirmed change.
- The filename includes a URL-derived hash so different monitored addresses are less likely to collide.
- Removing a monitor from the interface does **not** delete its HTML snapshot files.
- The check/change/error counters are **session-only**.

Do **not** place the executable in a directory where ordinary users cannot write, such as `C:\Program Files`, unless you adapt the storage location first.

## What happens when a website says “nope”?

PageSentinel uses **HTTPX** as its main HTTP client. If the server returns **HTTP 403**, it makes **one** attempt with an external **cURL** executable. The log identifies which client succeeded. If that also fails with a 403, PageSentinel waits at least five minutes before retrying. HTTP 429 responses are also slowed down.

This is a compatibility fallback, **not a guarantee of access**. Respect website policies, rate limits, and access restrictions. PageSentinel never replaces a valid HTML baseline with an HTTP error page.

## Known limitations

- Pages rendered mostly by JavaScript may not expose their meaningful content to HTTPX/cURL: neither is a browser engine.
- Dynamic text can still trigger false positives; use selectors, exclusions, or higher confidence.
- The maximum decompressed response size is **200 MB**; a large HTML page can consume considerable memory.
- A check interval starts **after the previous check completes**, not at a fixed wall-clock timestamp.
- Closing the app may wait for an ongoing request to finish.
- To change an existing monitor's comparison settings in the current version, remove and add it again; the HTML snapshots remain on disk.
- System tray support and native notifications depend on the operating system and desktop environment.
- cURL fallback depends on an external executable being installed and available on the target computer.

## Troubleshooting

**`HTTP 403`** — The site declined the HTTPX request. Look in the log to see whether cURL fallback worked. A browser succeeding does not guarantee that automated HTTP clients will be accepted.

**`No comparable content found`** — The selected content is empty after filtering. Check the page, CSS selector, and regex exclusions.

**`Page too large`** — The response exceeded the configured **200 MB** limit. Check whether the URL points to a normal HTML page.

**The `.exe` runs but saves nothing** — Move it to a writable folder and check file permissions.

**The Linux/WSL interface crashes** — Try running with Python directly on Windows for the Windows target, and capture diagnostic output with `python -X faulthandler -u main.py` if necessary.

## Source code & downloads

- **Source code:** [GitLab/GitHub repository — add link]
- **Windows executable:** [Releases — add link]
- **The story behind PageSentinel:** [Medium article — add link]

Contributions, bug reports, and constructive suggestions are welcome. If you find a way to make HTML monitoring less noisy, you're already my favorite person this week.

## Final thoughts

PageSentinel won't patch a CVE. It won't do threat intelligence for you. And it definitely won't make npm supply-chain incidents disappear.

But it can stop you from refreshing the same advisory page every five minutes.

**Your F5 key can finally take a vacation.** 😎

---

*Built with Python, a healthy dislike of repetitive tasks, and absolutely no need for a Kubernetes cluster.*
