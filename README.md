# Portfolio Tracker

A personal finance web app for Canadian investors. You only enter **transactions**
— everything else (holdings, dashboard, dividends, gains/losses, tax, charts)
auto-calculates from them. Live prices come free from Yahoo Finance, and all your
data stays **on your own computer** (a local file — nothing is uploaded anywhere).

> Built for Canadian accounts (TFSA / RRSP / FHSA / RDSP / Non-Reg), CAD + USD
> holdings, and TD Direct Investing / CIBC Investor's Edge imports.

---

## What you need

1. **Python 3.10 or newer** (3.13 recommended) — https://www.python.org/downloads/
   - On **Windows**, during install **tick "Add Python to PATH"**.
2. **An internet connection** — for live prices/FX (Yahoo Finance) and the charting
   library (loaded from a CDN).
3. **A web browser** (Chrome, Edge, Firefox, Safari — anything modern).

That's it. No accounts, no API keys, no paid services.

---

## Setup (one time)

**1. Get the code.** Either download the ZIP from GitHub (green *Code* button →
*Download ZIP*) and unzip it, or clone it:
```
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
```

**2. (Recommended) Create a virtual environment** — keeps these packages separate
from the rest of your system:
```
python -m venv .venv
```
Activate it:
- **Windows:** `.venv\Scripts\activate`
- **macOS / Linux:** `source .venv/bin/activate`

**3. Install the dependencies:**
```
pip install -r requirements.txt
```
(If `pip` complains it's out of date: `python -m pip install --upgrade pip`, then retry.)

---

## Run it

```
python app.py
```
- On **Windows** you can instead just double-click **`run.bat`**.
- Then open **http://localhost:5000** in your browser.
- To stop it, press **Ctrl+C** in the terminal (or close the window).

The first run creates an empty database (`instance/finance.db`) automatically.

---

## First steps in the app

1. **Add your transactions** (the app calculates everything from these):
   - **Import** tab → upload a **TD** or **CIBC** export (CSV for both, PDF for TD),
     or download the **blank CSV template**, fill it in, and import that.
   - Or add them by hand on the **Transactions** tab.
2. On the **Accounts** tab, set each account's **type** (TFSA / RDSP / …) and
   **time horizon** — these drive the tax, contribution-room, and liquidity views.
3. Open the **Settings** tab to set FX preferences and your contribution-room info.
4. Explore the **Dashboard** (customizable), **Charts**, **Dividends**,
   **Performance**, **Tax & ACB**, and the rest.

---

## Your data & backups

- Everything lives in **`instance/finance.db`** on your machine. It is **never**
  uploaded and is **git-ignored**, so it won't end up on GitHub.
- In **Settings → Data & Backup** you can:
  - **Export transactions (CSV)** — a complete, re-importable copy.
  - **Download / Restore a database backup** — a full snapshot you can restore later.
  - **Reset Database** — wipe everything back to a clean slate (with confirmations).

---

## Troubleshooting

- **`python` not found** → Python isn't installed or not on PATH. Reinstall and tick
  "Add Python to PATH" (Windows), then open a new terminal.
- **`pip install` fails** → run `python -m pip install --upgrade pip` and try again.
- **Prices show blank / "—"** → check your internet connection; Yahoo Finance may be
  briefly rate-limiting. Prices refresh automatically every few minutes.
- **Port 5000 already in use** → close whatever's using it, or stop the other copy of
  the app.

---

## Tech (for the curious)

Python + Flask + SQLite, vanilla HTML/CSS/JS with Chart.js, `yfinance` for prices.
No build step, no framework — just `python app.py`.
