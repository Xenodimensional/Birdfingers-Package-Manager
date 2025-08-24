# 🐦 Birdfingers Package Manager

A friendly, local **web UI** for managing Python packages in **virtual environments** and **Windows embedded Python** (`python_embedded`). List installed packages, inspect details, uninstall in batches, install exact versions from PyPI, and create **snapshots** you can preview, diff, and restore.

- **Zero extra deps** — runs with the Python stdlib.
- **Works in venv _and_ python_embedded _and_ system Python** (with helpers to enable `site-packages` and bootstrap `pip`).
- **Snapshots**: freeze, preview vs current, diff A↔B, restore.

<img width="1897" height="926" alt="Packages" src="https://github.com/user-attachments/assets/35b530f8-7eb7-459a-93ab-f555f6d6af60" />


## ✨ Features

- 📦 Packages tab
  - Search + live list of installed packages
  - Select multiple packages (chips), bulk uninstall
  - One-click **Details** (`pip show -f`)
  - Load available **versions** from PyPI

- ⬇️ Install tab
  - Search PyPI for a package
  - Install **latest** or an **exact version**
  - Accepts full specs like `package==1.2.3`

- 🧊 Snapshots tab
  - Save snapshots (`requirements.txt` + metadata)
  - **Preview** changes vs current environment
  - **Diff A↔B** between snapshots
  - **Restore** from snapshot; **View/Download/Delete**

- 🧰 Utilities
  - Enable `site-packages` in embedded Python (`pythonXY._pth`)
  - Check / install `pip` via `ensurepip` if available
  - Live **Output** with job status
  - Rolling text & JSONL **logs**

---

## 🧩 Requirements

- Python **3.8+**
- Internet access for **PyPI** searches/version lists
- For embedded Python: write access to the folder if you want to enable `site-packages`

---

Unzip into your system Python location, embedded Python folder or venv (where the Python executable is located) and open index.html for help and also to launch the package manager.

---

## 🚀 Run

Place `birdfingers_pkgmgr.py` next to `python.exe` (or run it with your preferred Python): Use the 'birdfingers.bat' file to launch on Windows.

```bash
python birdfingers_pkgmgr.py
# opens http://127.0.0.1:8765/

