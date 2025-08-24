#!/usr/bin/env python3
# 🐦 Birdfingers Package Manager — web GUI with jobs, snapshots, preview/diff, and PyPI search
# Works in venv or Windows embedded Python (python_embedded). Stdlib only.

import sys, os, re, json, threading, webbrowser, urllib.parse, subprocess, logging, time, uuid, argparse
from logging.handlers import RotatingFileHandler
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------- Env detection ----------

def in_virtualenv():
    return (getattr(sys, "base_prefix", sys.prefix) != sys.prefix) or (os.environ.get("VIRTUAL_ENV") is not None)

def _embedded_pth_path():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    try:
        names = os.listdir(exe_dir)
    except Exception:
        return None
    for name in names:
        if re.fullmatch(r"python\d+\._pth", name, flags=re.IGNORECASE):
            return os.path.join(exe_dir, name)
    p = os.path.join(exe_dir, "python._pth")
    if os.path.exists(p): return p
    for name in names:
        if name.lower().endswith("._pth"):
            return os.path.join(exe_dir, name)
    return None

def is_embedded_python():
    return os.name == "nt" and _embedded_pth_path() is not None

# ---------- Pip helpers ----------

def pip(*args):
    cmd = [sys.executable, "-m", "pip", *args]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

def ensure_pip():
    res = pip("--version")
    if res.returncode == 0:
        return True, res.stdout
    try:
        import ensurepip  # type: ignore
        out = subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        res2 = pip("--version")
        if res2.returncode == 0:
            return True, "Bootstrapped pip via ensurepip.\n" + (res2.stdout or "")
        return False, out.stdout
    except Exception:
        pass
    return False, "pip is not available. If this is an embedded Python, enable site-packages and install pip once."

def enable_site_packages_in_embedded():
    pth = _embedded_pth_path()
    if not pth:
        return False, "No pythonXY._pth found; not an embedded distro?"
    try:
        with open(pth, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return False, f"Failed to read {_embedded_pth_path()}: {e}"
    if any(line.strip() == "import site" for line in lines):
        return False, f"{os.path.basename(pth)} already enables site-packages."
    changed = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("#") and "import site" in line:
            new_lines.append("import site"); changed = True
        else:
            new_lines.append(line)
    if not changed:
        new_lines.append("import site"); changed = True
    try:
        with open(pth, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")
        return True, f"Updated {pth}: enabled site-packages (added 'import site')."
    except Exception as e:
        return False, f"Failed to write {pth}: {e}"

# ---------- Package listing ----------

try:
    from importlib.metadata import distributions
except ImportError:
    from importlib_metadata import distributions  # type: ignore

def list_installed():
    items = []
    for dist in distributions():
        try:
            name = dist.metadata["Name"] or ""
        except Exception:
            name = getattr(dist, "project_name", "") or ""
        ver = dist.version or "unknown"
        if name:
            items.append({"name": name, "version": ver})
    items.sort(key=lambda x: x["name"].lower())
    return items

# ---------- PyPI query (stdlib only) ----------

import urllib.request, urllib.error

def pypi_json(pkg):
    url = f"https://pypi.org/pypi/{pkg}/json"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)

def pypi_versions(pkg):
    data = pypi_json(pkg)
    rel = data.get("releases", {})
    def key(v):
        parts=[]
        for ch in v.replace("-", ".").split("."):
            try: parts.append((0,int(ch)))
            except: parts.append((1,ch))
        return parts
    return sorted(list(rel.keys()), key=key, reverse=True), data.get("info", {})

# ---------- Data & logging locations ----------

def default_log_dir():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for p in (exe_dir, sys.prefix, os.getcwd()):
        if os.access(p, os.W_OK): return p
    return os.getcwd()

DATA_DIR = os.environ.get("BIRDFINGERS_DATA_DIR") or default_log_dir()
LOG_PATH = os.path.join(DATA_DIR, "birdfingers.log")
JSONL_PATH = os.path.join(DATA_DIR, "birdfingers_audit.jsonl")
SNAP_DIR = os.path.join(DATA_DIR, "birdfingers_snapshots")
os.makedirs(SNAP_DIR, exist_ok=True)

def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("birdfingers")
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)
    jlogger = logging.getLogger("birdfingers_json")
    jlogger.setLevel(logging.INFO)
    jfh = RotatingFileHandler(JSONL_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    jfh.setFormatter(logging.Formatter("%(message)s"))
    jlogger.addHandler(jfh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)
    return logger, jlogger

LOGGER, JLOGGER = setup_logging()

def log_change(action, status, details, pip_out=""):
    meta = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "python": sys.executable,
        "sys_prefix": sys.prefix,
        "base_prefix": getattr(sys, "base_prefix", ""),
        "platform": sys.platform,
        "mode": "venv" if in_virtualenv() else ("embedded" if is_embedded_python() else "system"),
        "data_dir": DATA_DIR,
    }
    rec = {**meta, "action": action, "status": status, **details}
    LOGGER.info(f"{action} | {status} | pkg={details.get('package','')} "
                f"from={details.get('from_version','')} to={details.get('to_version','')} "
                f"code={details.get('returncode','')}")
    if pip_out:
        LOGGER.info("pip output:\n%s", pip_out.strip())
    JLOGGER.info(json.dumps({**rec, "pip_output": pip_out}, ensure_ascii=False))

# ---------- Jobs (streamed output) ----------

class Job:
    def __init__(self, kind, args):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.args = args
        self.text = ""
               # buffered output
        self.done = False
        self.returncode = None
        self.started = time.time()
        self.lock = threading.Lock()
    def append(self, s):
        with self.lock:
            self.text += s

JOBS = {}
JOBS_LOCK = threading.Lock()

def register_job(job):
    with JOBS_LOCK:
        JOBS[job.id] = job
    return job.id

def get_job(job_id):
    with JOBS_LOCK:
        return JOBS.get(job_id)

def _run_and_stream(job, cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1, universal_newlines=True)
    for line in p.stdout:
        job.append(line)
    p.wait()
    job.returncode = p.returncode
    job.done = True

def start_job_install_exact(pkg, ver):
    job = Job("install_exact", {"package": pkg, "version": ver})
    jid = register_job(job)
    cmd = [sys.executable, "-m", "pip", "install", f"{pkg}=={ver}"]
    threading.Thread(target=_run_and_stream, args=(job, cmd), daemon=True).start()
    return jid

def start_job_install_name(pkg, ver=None):
    job = Job("install", {"package": pkg, "version": ver})
    jid = register_job(job)
    target = f"{pkg}=={ver}" if ver else pkg
    cmd = [sys.executable, "-m", "pip", "install", target]
    threading.Thread(target=_run_and_stream, args=(job, cmd), daemon=True).start()
    return jid

def start_job_uninstall_multi(pkgs):
    job = Job("uninstall", {"packages": pkgs})
    jid = register_job(job)
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", *pkgs]
    threading.Thread(target=_run_and_stream, args=(job, cmd), daemon=True).start()
    return jid

def start_job_restore_requirements(req_path):
    job = Job("restore_snapshot", {"requirements": req_path})
    jid = register_job(job)
    cmd = [sys.executable, "-m", "pip", "install", "-r", req_path]
    threading.Thread(target=_run_and_stream, args=(job, cmd), daemon=True).start()
    return jid

# ---------- Snapshots, freeze & diff ----------

def freeze_requirements():
    return pip("freeze").stdout

def parse_requirements_text(text):
    pkgs = {}
    others = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if " @ " in s or s.startswith("-e "):
            others.append(s)
            continue
        if "==" in s:
            name, ver = s.split("==", 1)
            pkgs[name.lower()] = {"name": name, "version": ver}
        else:
            others.append(s)
    return pkgs, others

def diff_envs(current_pkgs, target_pkgs):
    installs, uninstalls, unchanged = [], [], []
    for key, tgt in target_pkgs.items():
        cur = current_pkgs.get(key)
        if not cur:
            installs.append({"name": tgt["name"], "from": None, "to": tgt["version"]})
        elif cur["version"] != tgt["version"]:
            installs.append({"name": tgt["name"], "from": cur["version"], "to": tgt["version"]})
        else:
            unchanged.append({"name": tgt["name"], "version": tgt["version"]})
    for key, cur in current_pkgs.items():
        if key not in target_pkgs:
            uninstalls.append({"name": cur["name"], "from": cur["version"]})
    return installs, uninstalls, unchanged

def _safe_name(s):
    s = (s or "").strip() or "snapshot"
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s[:60]

def save_snapshot(name, comment):
    os.makedirs(SNAP_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base = f"{_safe_name(name) or 'snapshot'}_{ts}"
    req_path = os.path.join(SNAP_DIR, base + ".txt")
    meta_path = os.path.join(SNAP_DIR, base + ".json")

    req_text = freeze_requirements()
    count = len([l for l in req_text.splitlines() if l and not l.startswith("#")])

    meta = {
        "id": base,
        "name": name,
        "comment": comment,
        "created_utc": ts,
        "python": sys.executable,
        "mode": "venv" if in_virtualenv() else ("embedded" if is_embedded_python() else "system"),
        "count": count,
        "requirements": os.path.basename(req_path),
        "dir": SNAP_DIR,
        "path": req_path,
    }

    try:
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(req_text)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise RuntimeError(f"Failed to write snapshot files in {SNAP_DIR}: {e}")

    return meta

def list_snapshots():
    if not os.path.isdir(SNAP_DIR): return []
    metas = []
    for name in os.listdir(SNAP_DIR):
        if name.endswith(".json"):
            try:
                with open(os.path.join(SNAP_DIR, name), "r", encoding="utf-8") as f:
                    metas.append(json.load(f))
            except Exception:
                continue
    metas.sort(key=lambda m: m.get("created_utc",""), reverse=True)
    return metas

def get_snapshot(id_):
    meta_path = os.path.join(SNAP_DIR, id_ + ".json")
    req_path = os.path.join(SNAP_DIR, id_ + ".txt")
    if not (os.path.exists(meta_path) and os.path.exists(req_path)):
        return None, None
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return meta, req_path

def delete_snapshot(id_):
    meta_path = os.path.join(SNAP_DIR, id_ + ".json")
    req_path = os.path.join(SNAP_DIR, id_ + ".txt")
    ok = True
    for p in (meta_path, req_path):
        try:
            if os.path.exists(p): os.remove(p)
        except Exception:
            ok = False
    return ok

def preview_snapshot_vs_current(id_):
    meta, req_path = get_snapshot(id_)
    if not meta: return None
    with open(req_path, "r", encoding="utf-8") as f:
        target_text = f.read()
    target_pkgs, target_other = parse_requirements_text(target_text)
    cur_text = freeze_requirements()
    cur_pkgs, cur_other = parse_requirements_text(cur_text)
    installs, uninstalls, unchanged = diff_envs(cur_pkgs, target_pkgs)
    cmds = []
    if uninstalls:
        cmds.append("python -m pip uninstall -y " + " ".join(sorted(x["name"] for x in uninstalls)))
    for it in installs:
        cmds.append(f"python -m pip install {it['name']}=={it['to']}")
    return {
        "snapshot": meta,
        "counts": {"install": len(installs), "uninstall": len(uninstalls), "unchanged": len(unchanged),
                   "other_lines_target": len(target_other)},
        "installs": installs, "uninstalls": uninstalls, "unchanged": unchanged,
        "commands": cmds,
        "notes": "Preview is based on 'pip freeze' pins. Dependency resolution at install time may vary."
    }

def preview_snapshot_vs_snapshot(a_id, b_id):
    a_meta, a_req = get_snapshot(a_id)
    b_meta, b_req = get_snapshot(b_id)
    if not (a_meta and b_meta): return None
    a_pkgs, a_other = parse_requirements_text(open(a_req,"r",encoding="utf-8").read())
    b_pkgs, b_other = parse_requirements_text(open(b_req,"r",encoding="utf-8").read())
    installs, uninstalls, unchanged = diff_envs(a_pkgs, b_pkgs)
    cmds = []
    if uninstalls:
        cmds.append("python -m pip uninstall -y " + " ".join(sorted(x["name"] for x in uninstalls)))
    for it in installs:
        cmds.append(f"python -m pip install {it['name']}=={it['to']}")
    return {
        "a": a_meta, "b": b_meta,
        "counts": {"install": len(installs), "uninstall": len(uninstalls), "unchanged": len(unchanged),
                   "other_a": len(a_other), "other_b": len(b_other)},
        "installs": installs, "uninstalls": uninstalls, "unchanged": unchanged,
        "commands": cmds,
        "notes": "Diff is package/version based on '=='. Lines with URLs/editables appear in 'other_*'."
    }

# ---------- Simple actions (non-job) ----------

def uninstall(pkg):
    res = pip("uninstall", "-y", pkg)
    log_change("uninstall", "success" if res.returncode == 0 else "failure",
               {"package": pkg, "returncode": str(res.returncode)}, res.stdout)
    return res

def install_exact_sync(pkg, ver):
    before = {p["name"]: p["version"] for p in list_installed()}
    res = pip("install", f"{pkg}=={ver}")
    log_change("install_exact", "success" if res.returncode == 0 else "failure",
               {"package": pkg, "from_version": str(before.get(pkg)),
                "to_version": ver, "returncode": str(res.returncode)}, res.stdout)
    return res

def show_details(pkg):
    return pip("show", "-f", pkg)

# ---------- Web UI ----------

INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Birdfingers Package Manager</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>
  :root{
    --bg: #f6f7fb; --card:#ffffff; --fg:#0f172a; --muted:#475569; --line:#e5e7eb;
    --accent:#0ea5e9; --chipbg:#eef2ff; --chipfg:#3730a3; --danger:#ef4444; --ok:#059669;
    --topH: 64px;         /* JS updates this */
    --bottomH: 260px;     /* JS updates this */
  }
  .dark{
    --bg:#0b1020; --card:#11162a; --fg:#e3ecff; --muted:#a6b0c3; --line:#233147;
    --accent:#38bdf8; --chipbg:#1e293b; --chipfg:#dbeafe; --danger:#f87171; --ok:#34d399;
  }

  /* page scrolls; we pad for fixed top/bottom bars */
  html,body{height:auto; overflow:auto;}
  body { font-family: system-ui, Segoe UI, Roboto, sans-serif; margin:0; background:var(--bg); color:var(--fg); }
  #app{ min-height:100vh; padding-top: var(--topH); padding-bottom: var(--bottomH); }

  .topbar{
    position:fixed; left:0; right:0; top:0; z-index:7;
    background:var(--card); border-bottom:1px solid var(--line); padding:12px 16px;
  }
  .row{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .title{ font-size:20px; font-weight:700; margin-right:8px; }
  .meta{ color:var(--muted); }

  .tabs{ margin-left:auto; display:flex; gap:8px; }
  .tabbtn{ padding:8px 12px; border:1px solid var(--line); border-radius:999px; background:var(--card); cursor:pointer }
  .tabbtn.active{ background:var(--accent); color:#fff; border-color:var(--accent); }

  .middle{ padding:12px 16px; }
  .search{ padding:8px; width:320px; background:var(--card); border:1px solid var(--line); border-radius:8px; color:var(--fg); }

  .tablewrap{ margin-top:12px; border:1px solid var(--line); border-radius:12px; background:var(--card); }
  table{ width:100%; border-collapse:collapse; }
  th, td{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }
  thead th{ position:sticky; top: var(--topH); background:var(--card); z-index:1; }
  tr:hover td{ background: rgba(14,165,233,0.08); }

  .bottombar{
    position:fixed; left:0; right:0; bottom:0; z-index:5;
    background:var(--card); border-top:1px solid var(--line); padding:12px 16px;
  }
  .chip{ display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; background:var(--chipbg); color:var(--chipfg); margin:2px; cursor:default; user-select:none; }
  .chip button{ all:unset; cursor:pointer; font-weight:700; padding:0 4px; }
  .chip button:hover{ color:var(--danger); }

  button{ padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--fg); cursor:pointer }
  button.primary{ background:var(--accent); color:#fff; border-color:var(--accent); }
  button.danger{ border-color:var(--danger); color:#fff; background:var(--danger); }
  button.ghost{ background:transparent; }
  input[type=text]{ padding:8px; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:8px; }

  #out{ white-space:pre-wrap; background:#0b1020; color:#cfe8ff; padding:12px; border-radius:8px; height:200px; overflow:auto; border:1px solid var(--line); }

  [data-tip]{ position:relative; }
  [data-tip]:hover::after{
    content: attr(data-tip); position:absolute; left:0; top:100%; transform:translateY(6px);
    background: #111827; color:#f9fafb; font-size:12px; padding:6px 8px; border-radius:6px; white-space:pre-wrap;
    max-width:460px; box-shadow:0 4px 16px rgba(0,0,0,.25); z-index:5;
  }

  .muted{ color:var(--muted); font-size:12px; }
  textarea{ width:100%; min-height:64px; padding:8px; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:8px; }
  .two{ display:flex; gap:16px; flex-wrap:wrap; }
  .card{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; }
</style>
</head>
<body>
<div id="app">
  <div class="topbar">
    <div class="row">
      <div class="title" title="Because birds have tiny, fast fingers.">🐦 Birdfingers Package Manager</div>
      <span class="meta">Mode: <span id="mode" class="meta"></span> · Python: <span id="py" class="meta"></span> · Port: <span id="port" class="meta"></span></span>
      <button id="toggleTheme" data-tip="Toggle between light/dark. Saved in your browser.">🌗 Dark mode</button>
      <button id="enableSite" data-tip="Embedded Python only.\nAdds 'import site' to pythonXY._pth so site-packages and pip work.">Enable site-packages</button>
      <button id="checkPip" data-tip="Check if pip is available. If not, try ensurepip (if bundled).">Check / install pip</button>
      <button id="refreshTop" data-tip="Reload the package list.">Refresh</button>
      <div class="tabs">
        <button id="tabPackages" class="tabbtn active" data-tip="Browse installed packages">Packages</button>
        <button id="tabInstall" class="tabbtn" data-tip="Search PyPI and install new packages">Install</button>
        <button id="tabSnapshots" class="tabbtn" data-tip="Manage environment snapshots (freeze/restore/diff)">Snapshots</button>
      </div>
    </div>
  </div>

  <div class="middle">
    <!-- PACKAGES VIEW -->
    <div id="viewPackages">
      <input id="q" class="search" type="search" placeholder="Search packages…" title="Type to filter by package name" />
      <div class="tablewrap" id="tableWrap" data-tip="Scroll this section to browse packages. Header stays visible.">
        <table id="tbl">
          <thead><tr><th>Package</th><th>Version</th><th style="width:520px">Actions</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <!-- INSTALL VIEW -->
    <div id="viewInstall" style="display:none;">
      <div class="two">
        <div class="card" style="flex:1 1 380px;">
          <div class="row">
            <input id="pkgSearch" type="text" placeholder="Package name (e.g. 'requests')" style="min-width:260px;">
            <button id="btnPkgSearch" class="primary" data-tip="Look up package on PyPI and list available versions.">Search PyPI</button>
          </div>
          <div id="pkgInfo" class="muted" style="margin-top:8px;"></div>
          <div class="row" style="margin-top:8px;">
            <select id="pkgVersions" style="min-width:240px;" title="Available versions (latest first)"></select>
            <button id="btnInstallLatest" data-tip="Install the latest version">Install latest</button>
            <button id="btnInstallChosen" class="primary" data-tip="Install the selected version">Install selected</button>
          </div>
        </div>
        <div class="card" style="flex:1 1 380px;">
          <div class="muted">Tip: you can also paste a full requirement like <code>package==1.2.3</code> in the name field and use “Install selected”.</div>
        </div>
      </div>
    </div>

    <!-- SNAPSHOTS VIEW -->
    <div id="viewSnapshots" style="display:none;">
      <div class="two">
        <div class="card" style="flex:2 1 520px;">
          <div class="row" style="margin-bottom:10px;">
            <input id="snapName" type="text" placeholder="Snapshot name (optional)" style="min-width:220px;">
            <textarea id="snapComment" placeholder="Optional comment/notes for this snapshot"></textarea>
          </div>
          <div class="row" style="margin-bottom:10px;">
            <button id="btnSaveSnapshot" class="primary" data-tip="Freeze current environment into a snapshot (requirements.txt + metadata).">Save snapshot</button>
            <button id="refreshSnaps">Refresh</button>
            <span class="muted">Saved under <code>birdfingers_snapshots/</code> (see /api/paths).</span>
          </div>
          <div class="tablewrap">
            <table id="snapTbl">
              <thead><tr><th>Name</th><th>Created (UTC)</th><th>#Pkgs</th><th>Comment</th><th style="width:480px">Actions</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
        <div class="card" style="flex:1 1 380px;">
          <strong>Diff snapshots</strong>
          <div class="row" style="margin-top:8px;">
            <select id="diffA" title="Snapshot A"></select>
            <select id="diffB" title="Snapshot B"></select>
            <button id="btnDiffAB" data-tip="Show differences between snapshot A and B (what to install/uninstall).">Diff A ↔ B</button>
          </div>
          <div class="muted" style="margin-top:8px;">Or use <b>Preview</b> in the table to compare a snapshot with your current environment.</div>
        </div>
      </div>
    </div>
  </div>

  <div class="bottombar">
    <div class="row" style="justify-content:space-between; align-items:flex-start;">
      <div style="flex:1 1 auto; min-width:320px;">
        <div class="row" style="margin-bottom:8px;">
          <strong>Selected:</strong>
          <div id="chips" class="row"></div>
          <button id="clearSel" class="ghost" data-tip="Remove all selected packages.">Deselect all</button>
          <button id="refreshPkg" class="ghost" data-tip="Reload installed packages.">Refresh</button>
        </div>
        <div class="row" style="flex-wrap:wrap; gap:8px;">
          <button id="btnDetails" data-tip="Show 'pip show -f' for each selected package.">Show details</button>
          <button id="btnUninstall" class="danger" data-tip="Uninstall ALL selected packages.">Uninstall selected</button>
          <select id="verList" title="Version list for the active package (click a chip to make it active)."></select>
          <button id="btnLoadVersions" data-tip="Load versions from PyPI for the active package.">Load versions</button>
          <button id="btnInstall" class="primary" data-tip="Install the selected version for the active package.">Install version</button>
          <span id="activeFor" class="meta"></span>
        </div>
      </div>
      <div style="flex:1 1 520px; max-width:50%;">
        <div class="row" style="justify-content:space-between;">
          <strong>Output</strong>
          <div class="muted">Job: <span id="jobId">none</span> · <span id="jobState">idle</span></div>
        </div>
        <div id="out" title="Live command output, logs, and errors."></div>
      </div>
    </div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const out = msg => { const el = $("#out"); el.textContent += msg + "\\n"; el.scrollTop = el.scrollHeight; };

let packages = [];
let selected = new Set();
let activePkg = null;

let currentJob = null, jobPos = 0, pollTimer = null;

/* Fit layout to fixed top/bottom bars */
function fitBars(){
  const top = document.querySelector('.topbar');
  const bottom = document.querySelector('.bottombar');

  if (top){
    const hTop = Math.ceil(top.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--topH', hTop + 'px');
  }
  if (bottom){
    const hBottom = Math.ceil(bottom.getBoundingClientRect().height) + 16; // breathing room
    document.documentElement.style.setProperty('--bottomH', hBottom + 'px');
  }
}
window.addEventListener('load', fitBars);
window.addEventListener('resize', fitBars);

function startPolling(job_id){
  currentJob = job_id; jobPos = 0;
  $("#jobId").textContent = job_id; $("#jobState").textContent = "running";
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async ()=>{
    try{
      const r = await fetch("/api/job/poll?"+new URLSearchParams({job_id, pos: String(jobPos)}));
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      if (data.text){ $("#out").textContent += data.text; $("#out").scrollTop = $("#out").scrollHeight; }
      jobPos = data.pos;
      if (data.done){
        $("#jobState").textContent = "done (" + data.returncode + ")";
        clearInterval(pollTimer); pollTimer = null; currentJob = null;
        if ($("#viewPackages").style.display !== "none"){ const list = await api("/api/list"); packages = list.packages || []; renderTable(); }
        if ($("#viewSnapshots").style.display !== "none"){ loadSnapshots(); }
      }
    }catch(e){
      $("#jobState").textContent = "error";
      clearInterval(pollTimer); pollTimer = null; currentJob = null;
    }
  }, 600);
}

function setActive(pkg){
  activePkg = pkg || null;
  $("#activeFor").textContent = activePkg ? `Active: ${activePkg}` : "";
  $$("#chips .chip").forEach(el=>{
    el.style.outline = (el.dataset.pkg === activePkg) ? "2px solid var(--accent)" : "none";
  });
}
function renderChips(){
  const box = $("#chips"); box.innerHTML = "";
  if (selected.size === 0){ setActive(null); return; }
  for (const pkg of Array.from(selected).sort()){
    const chip = document.createElement("span");
    chip.className = "chip"; chip.dataset.pkg = pkg; chip.title = "Click to make active";
    chip.innerHTML = `<span>${pkg}</span><button title="Deselect">×</button>`;
    chip.addEventListener("click", (e)=>{
      if (e.target.tagName.toLowerCase() === "button"){ selected.delete(pkg); if (activePkg === pkg) setActive(null); renderChips(); }
      else { setActive(pkg); }
    });
    box.appendChild(chip);
  }
  if (!activePkg || !selected.has(activePkg)) setActive(Array.from(selected)[0]);
}
function renderTable() {
  const q = ($("#q").value || "").toLowerCase();
  const tbody = $("#tbl tbody"); tbody.innerHTML = "";
  packages.filter(p => p.name.toLowerCase().includes(q)).forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.name}</td><td>${p.version}</td>
      <td>
        <button data-p="${p.name}" class="sel" data-tip="Add '${p.name}' to the action row.">Select</button>
        <button data-p="${p.name}" class="details" data-tip="Show details for '${p.name}'.">Details</button>
        <button data-p="${p.name}" class="un" data-tip="Uninstall '${p.name}'.">Uninstall</button>
        <button data-p="${p.name}" class="vers" data-tip="Load available versions from PyPI for '${p.name}'.">Load versions</button>
      </td>`;
    tbody.appendChild(tr);
  });
}
function activateTab(which){
  $("#viewPackages").style.display = which === "packages" ? "" : "none";
  $("#viewInstall").style.display = which === "install" ? "" : "none";
  $("#viewSnapshots").style.display = which === "snapshots" ? "" : "none";
  $("#tabPackages").classList.toggle("active", which==="packages");
  $("#tabInstall").classList.toggle("active", which==="install");
  $("#tabSnapshots").classList.toggle("active", which==="snapshots");
  localStorage.setItem("bf_tab", which);
  fitBars();
}
async function api(path, opts={}) {
  const r = await fetch(path, {headers: {'Content-Type':'application/json'}, ...opts});
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}
async function init() {
  const info = await api("/api/info");
  $("#mode").textContent = info.mode; $("#py").textContent = info.python; $("#port").textContent = info.port;
  const list = await api("/api/list"); packages = list.packages || []; renderTable(); renderChips();
  const tab = localStorage.getItem("bf_tab") || "packages"; activateTab(tab);
  if (tab === "snapshots") loadSnapshots();
  fitBars();
}
(function initTheme(){
  const saved = localStorage.getItem("bf_theme") || "light";
  if (saved === "dark") document.documentElement.classList.add("dark");
  $("#toggleTheme").addEventListener("click", ()=>{
    document.documentElement.classList.toggle("dark");
    localStorage.setItem("bf_theme", document.documentElement.classList.contains("dark") ? "dark" : "light");
  });
})();

async function reloadPackages(){
  packages = (await api("/api/list")).packages;
  renderTable();
  out("Refreshed.");
}
$("#refreshTop").addEventListener("click", reloadPackages);
$("#refreshPkg").addEventListener("click", reloadPackages);

$("#q").addEventListener("input", renderTable);

document.addEventListener("click", async (e)=>{
  const btn = e.target.closest("button"); if (!btn) return;
  const pkg = btn.getAttribute("data-p"); if (!pkg) return;
  if (btn.classList.contains("sel")){ selected.add(pkg); renderChips(); out("Selected " + pkg); return; }
  if (btn.classList.contains("details")){ const r = await api("/api/show?"+new URLSearchParams({pkg})); out(r.output); return; }
  if (btn.classList.contains("un")){
    if (!confirm("Uninstall "+pkg+"?")) return;
    const j = await api("/api/job/uninstall_multi", {method:"POST", body:JSON.stringify({packages:[pkg]})});
    out("Started uninstall job " + j.job_id + " for " + pkg); startPolling(j.job_id); return;
  }
  if (btn.classList.contains("vers")){
    selected.add(pkg); renderChips(); setActive(pkg); $("#verList").innerHTML = "";
    try{
      const r = await api("/api/versions?"+new URLSearchParams({pkg}));
      r.versions.forEach(v=>{ const opt = document.createElement("option"); opt.value = v; opt.textContent = v; $("#verList").appendChild(opt); });
      out("Loaded versions for "+pkg+" (latest first).");
    } catch(err){ out("Failed to fetch versions for "+pkg+": "+err.message); }
  }
});
$("#clearSel").addEventListener("click", ()=>{ selected.clear(); renderChips(); });

$("#btnDetails").addEventListener("click", async ()=>{
  if (selected.size === 0) return alert("Select at least one package.");
  for (const pkg of selected){ const r = await api("/api/show?"+new URLSearchParams({pkg})); out(r.output); }
});
$("#btnUninstall").addEventListener("click", async ()=>{
  if (selected.size === 0) return alert("Select at least one package.");
  if (!confirm("Uninstall ALL selected packages?")) return;
  const j = await api("/api/job/uninstall_multi", {method:"POST", body:JSON.stringify({packages:[...selected]})});
  out("Started uninstall job " + j.job_id + " for " + selected.size + " package(s).");
  selected.clear(); renderChips(); startPolling(j.job_id);
});
$("#btnLoadVersions").addEventListener("click", async ()=>{
  if (!activePkg) return alert("Click a chip to choose the active package first.");
  $("#verList").innerHTML = "";
  try{
    const r = await api("/api/versions?"+new URLSearchParams({pkg:activePkg}));
    r.versions.forEach(v=>{ const opt = document.createElement("option"); opt.value = v; opt.textContent = v; $("#verList").appendChild(opt); });
    out("Loaded versions for "+activePkg+".");
  } catch(err){ out("Failed to fetch versions: "+err.message); }
});
$("#btnInstall").addEventListener("click", async ()=>{
  if (!activePkg) return alert("Pick an active package (click a chip).");
  const ver = $("#verList").value; if (!ver) return alert("Choose a version.");
  const j = await api("/api/job/install_exact", {method:"POST", body:JSON.stringify({pkg:activePkg, version:ver})});
  out("Started install job " + j.job_id + " for " + activePkg + "==" + ver); startPolling(j.job_id);
});

/* Install tab */
$("#tabPackages").addEventListener("click", ()=>activateTab("packages"));
$("#tabInstall").addEventListener("click", ()=>activateTab("install"));
$("#tabSnapshots").addEventListener("click", ()=>{ activateTab("snapshots"); loadSnapshots(); });

$("#btnPkgSearch").addEventListener("click", async ()=>{
  const name = ($("#pkgSearch").value || "").trim();
  if (!name) return alert("Enter a package name.");
  $("#pkgVersions").innerHTML = ""; $("#pkgInfo").textContent = "Searching…";
  try{
    const r = await api("/api/pypi/info?"+new URLSearchParams({pkg:name}));
    const info = r.info || {};
    $("#pkgInfo").innerHTML = `<strong>${info.name || name}</strong> — ${info.summary || ""}<br>
      Latest: ${r.latest || ""} · Requires Python: ${info.requires_python || "?"}`;
    (r.versions || []).forEach(v=>{ const o = document.createElement("option"); o.value = v; o.textContent = v; $("#pkgVersions").appendChild(o); });
  }catch(err){ $("#pkgInfo").textContent = "Not found on PyPI."; }
});
$("#btnInstallLatest").addEventListener("click", async ()=>{
  const name = ($("#pkgSearch").value || "").trim(); if (!name) return;
  const vsel = $("#pkgVersions"); const ver = vsel.options.length ? vsel.options[0].value : null;
  const j = await api("/api/job/install_name", {method:"POST", body:JSON.stringify({pkg:name, version:ver})});
  out("Installing latest " + name + (ver?("=="+ver):"") + " … job " + j.job_id); startPolling(j.job_id);
});
$("#btnInstallChosen").addEventListener("click", async ()=>{
  const name = ($("#pkgSearch").value || "").trim(); if (!name) return;
  const ver = $("#pkgVersions").value || null;
  const j = await api("/api/job/install_name", {method:"POST", body:JSON.stringify({pkg:name, version:ver})});
  out("Installing " + name + (ver?("=="+ver):"") + " … job " + j.job_id); startPolling(j.job_id);
});

/* Snapshots view */
async function loadSnapshots(){
  const data = await api("/api/snapshots");
  const items = data.items || [];
  const tbody = $("#snapTbl tbody"); tbody.innerHTML = "";
  items.forEach(m=>{
    const tr = document.createElement("tr");
    const comment = (m.comment || "").replace(/\\s+/g," ").slice(0,160);
    tr.innerHTML = `<td>${m.name || m.id}</td>
      <td>${m.created_utc}</td>
      <td>${m.count || ""}</td>
      <td title="${(m.comment||"").replaceAll('"','&quot;')}">${comment}</td>
      <td>
        <button data-id="${m.id}" class="snap-preview" data-tip="Preview changes vs current environment">Preview</button>
        <button data-id="${m.id}" class="snap-restore" data-tip="Install packages from this snapshot (pip install -r)">Restore</button>
        <button data-id="${m.id}" class="snap-view" data-tip="View requirements.txt">View</button>
        <button data-id="${m.id}" class="snap-download" data-tip="Download requirements.txt">Download</button>
        <button data-id="${m.id}" class="snap-delete danger" data-tip="Delete snapshot">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  });
  // populate A/B selectors
  const selA = $("#diffA"), selB = $("#diffB");
  [selA, selB].forEach(sel => {
    sel.innerHTML = "";
    items.forEach(m=>{
      const o=document.createElement("option");
      o.value=m.id; o.textContent=m.name||m.id; sel.appendChild(o);
    });
  });
}
$("#refreshSnaps").addEventListener("click", loadSnapshots);

document.addEventListener("click", async (e)=>{
  const b = e.target.closest("button"); if (!b) return;
  if (b.classList.contains("snap-preview")){
    const id = b.getAttribute("data-id");
    const r = await api("/api/snapshot/preview?"+new URLSearchParams({id}));
    if (!r || r.error){ out("Preview failed."); return; }
    const lines = [];
    lines.push("=== Preview vs current ===");
    lines.push(`Snapshot: ${r.snapshot.id}  (${r.counts.install} install / ${r.counts.uninstall} uninstall / ${r.counts.unchanged} unchanged)`);
    if (r.installs.length){ lines.push("Install/upgrade:"); r.installs.forEach(x=>lines.push(`  - ${x.name}: ${x.from||'∅'} -> ${x.to}`)); }
    if (r.uninstalls.length){ lines.push("Uninstall:"); r.uninstalls.forEach(x=>lines.push(`  - ${x.name} (${x.from})`)); }
    if (r.commands.length){ lines.push("Commands:"); r.commands.forEach(c=>lines.push("  " + c)); }
    lines.push(r.notes);
    out(lines.join("\\n"));
  } else if (b.classList.contains("snap-restore")){
    const id = b.getAttribute("data-id");
    if (!confirm("Restore snapshot "+id+"? This may change many packages.")) return;
    const j = await api("/api/job/restore", {method:"POST", body:JSON.stringify({id})});
    out("Started restore job " + j.job_id + " for snapshot " + id); startPolling(j.job_id);
  } else if (b.classList.contains("snap-view")){
    const id = b.getAttribute("data-id");
    const r = await api("/api/snapshot/view?"+new URLSearchParams({id}));
    out("----- requirements for "+id+" -----\\n"+r.text+"\\n--------------------");
  } else if (b.classList.contains("snap-download")){
    const id = b.getAttribute("data-id"); window.open("/api/snapshot/download?"+new URLSearchParams({id}), "_blank");
  } else if (b.classList.contains("snap-delete")){
    const id = b.getAttribute("data-id"); if (!confirm("Delete snapshot "+id+"?")) return;
    const r = await api("/api/snapshot/delete", {method:"POST", body:JSON.stringify({id})});
    out(r.ok ? "Deleted snapshot "+id : "Failed to delete "+id); loadSnapshots();
  }
});
$("#btnDiffAB").addEventListener("click", async ()=>{
  const a = $("#diffA").value, b = $("#diffB").value; if (!a || !b) return;
  const r = await api("/api/snapshot/diff?"+new URLSearchParams({a, b}));
  if (!r || r.error){ out("Diff failed."); return; }
  const lines = [];
  lines.push("=== Diff: A -> B ===");
  lines.push(`A=${r.a.id}  B=${r.b.id}  (${r.counts.install} install / ${r.counts.uninstall} uninstall / ${r.counts.unchanged} unchanged)`);
  if (r.installs.length){ lines.push("Install/upgrade:"); r.installs.forEach(x=>lines.push(`  - ${x.name}: ${x.from||'∅'} -> ${x.to}`)); }
  if (r.uninstalls.length){ lines.push("Uninstall:"); r.uninstalls.forEach(x=>lines.push(`  - ${x.name} (${x.from})`)); }
  if (r.commands.length){ lines.push("Commands:"); r.commands.forEach(c=>lines.push("  " + c)); }
  lines.push(r.notes);
  out(lines.join("\\n"));
});

/* Save snapshot (wired) */
$("#btnSaveSnapshot").addEventListener("click", async ()=>{
  const name = ($("#snapName").value || "").trim();
  const comment = ($("#snapComment").value || "").trim();
  try{
    const r = await api("/api/snapshot/save", {method:"POST", body: JSON.stringify({name, comment})});
    out("Saved snapshot: " + (r.meta?.id || "(no id)"));
    if (r.meta?.path){ out("Path: " + r.meta.path); }
    loadSnapshots();
  }catch(err){
    out("Snapshot save failed: " + err.message);
  }
});

// Embedded helpers
$("#enableSite").addEventListener("click", async ()=>{ const r = await api("/api/enable_site", {method:"POST"}); out(r.message); });
$("#checkPip").addEventListener("click", async ()=>{ const r = await api("/api/ensure_pip", {method:"POST"}); out(r.message); });

init().catch(e=> out("Init failed: "+e.message));
</script>
</body>
</html>
"""

# ---------- HTTP Handlers ----------

SERVER_PORT = 8765

def env_info():
    ok, pv = ensure_pip()  # probe only
    return {
        "python": sys.executable,
        "pip": pv.strip() if pv else "",
        "mode": "venv" if in_virtualenv() else ("embedded" if is_embedded_python() else "system"),
        "port": SERVER_PORT,
        "data_dir": DATA_DIR,
        "snap_dir": SNAP_DIR,
        "log_path": LOG_PATH,
    }

def json_response(handler, obj, code=200, ctype="application/json; charset=utf-8"):
    data = (json.dumps(obj) if isinstance(obj, (dict,list)) else str(obj)).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

class App(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body); return

        if self.path.startswith("/api/info"):
            return json_response(self, env_info())

        if self.path.startswith("/api/paths"):
            return json_response(self, {
                "data_dir": DATA_DIR,
                "snap_dir": SNAP_DIR,
                "log_path": LOG_PATH,
                "python": sys.executable
            })

        if self.path.startswith("/api/list"):
            return json_response(self, {"packages": list_installed()})

        if self.path.startswith("/api/versions"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            pkg = (qs.get("pkg") or [""])[0]
            try:
                vers, _ = pypi_versions(pkg)
                return json_response(self, {"versions": vers})
            except Exception as e:
                return json_response(self, {"error": str(e)}, 500)

        if self.path.startswith("/api/pypi/info"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            pkg = (qs.get("pkg") or [""])[0]
            try:
                vers, info = pypi_versions(pkg)
                latest = vers[0] if vers else None
                info_out = {"name": info.get("name"), "summary": info.get("summary"),
                            "requires_python": info.get("requires_python")}
                return json_response(self, {"versions": vers, "latest": latest, "info": info_out})
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return json_response(self, {"error": "not found"}, 404)
                return json_response(self, {"error": str(e)}, 500)

        if self.path.startswith("/api/show"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            pkg = (qs.get("pkg") or [""])[0]
            res = show_details(pkg)
            return json_response(self, {"output": res.stdout})

        if self.path.startswith("/api/snapshots"):
            return json_response(self, {"items": list_snapshots()})

        if self.path.startswith("/api/snapshot/view"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            sid = (qs.get("id") or [""])[0]
            meta, req_path = get_snapshot(sid)
            if not meta: return json_response(self, {"error":"not found"}, 404)
            with open(req_path, "r", encoding="utf-8") as f:
                txt = f.read()
            return json_response(self, {"text": txt})

        if self.path.startswith("/api/snapshot/download"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            sid = (qs.get("id") or [""])[0]
            meta, req_path = get_snapshot(sid)
            if not meta: self.send_error(404); return
            with open(req_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(req_path)}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path.startswith("/api/snapshot/preview"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            sid = (qs.get("id") or [""])[0]
            data = preview_snapshot_vs_current(sid)
            if not data: return json_response(self, {"error":"not found"}, 404)
            return json_response(self, data)

        if self.path.startswith("/api/snapshot/diff"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            a = (qs.get("a") or [""])[0]; b = (qs.get("b") or [""])[0]
            data = preview_snapshot_vs_snapshot(a, b)
            if not data: return json_response(self, {"error":"not found"}, 404)
            return json_response(self, data)

        if self.path.startswith("/api/job/poll"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            jid = (qs.get("job_id") or [""])[0]
            pos = int((qs.get("pos") or ["0"])[0])
            job = get_job(jid)
            if not job: return json_response(self, {"error":"job not found"}, 404)
            with job.lock:
                text = job.text[pos:] if pos < len(job.text) else ""
                newpos = len(job.text)
                done = job.done
                rc = job.returncode
            return json_response(self, {"text": text, "pos": newpos, "done": done, "returncode": rc})

        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n).decode("utf-8") if n else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if self.path == "/api/uninstall":
            res = uninstall(data.get("pkg",""))
            return json_response(self, {"output": res.stdout, "code": res.returncode})

        if self.path == "/api/set":
            res = install_exact_sync(data.get("pkg",""), data.get("version",""))
            return json_response(self, {"output": res.stdout, "code": res.returncode})

        if self.path == "/api/enable_site":
            ok, msg = enable_site_packages_in_embedded()
            return json_response(self, {"ok": ok, "message": msg})

        if self.path == "/api/ensure_pip":
            ok, msg = ensure_pip()
            return json_response(self, {"ok": ok, "message": msg})

        if self.path == "/api/snapshot/save":
            name = data.get("name","") or "snapshot"
            comment = data.get("comment","")
            try:
                meta = save_snapshot(name, comment)
                return json_response(self, {"meta": meta})
            except Exception as e:
                return json_response(self, {"error": str(e), "dir": SNAP_DIR}, 500)

        if self.path == "/api/snapshot/delete":
            sid = data.get("id","")
            ok = delete_snapshot(sid)
            return json_response(self, {"ok": ok})

        if self.path == "/api/job/install_exact":
            jid = start_job_install_exact(data.get("pkg",""), data.get("version",""))
            return json_response(self, {"job_id": jid})

        if self.path == "/api/job/install_name":
            jid = start_job_install_name(data.get("pkg",""), data.get("version"))
            return json_response(self, {"job_id": jid})

        if self.path == "/api/job/uninstall_multi":
            pkgs = data.get("packages", []) or []
            jid = start_job_uninstall_multi(pkgs)
            return json_response(self, {"job_id": jid})

        if self.path == "/api/job/restore":
            sid = data.get("id","")
            meta, req_path = get_snapshot(sid)
            if not meta: return json_response(self, {"error":"snapshot not found"}, 404)
            jid = start_job_restore_requirements(req_path)
            return json_response(self, {"job_id": jid})

        self.send_error(404)

def serve(open_browser=True, port=8765):
    global SERVER_PORT
    SERVER_PORT = port
    addr = ("127.0.0.1", port)
    httpd = HTTPServer(addr, App)
    url = f"http://{addr[0]}:{addr[1]}/"
    LOGGER.info("Birdfingers UI on %s", url)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Birdfingers Package Manager")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BIRDFINGERS_PORT", "8765")),
                    help="Port to serve the web UI (default 8765)")
    args = ap.parse_args()
    print(f"\n=== 🐦 Birdfingers Package Manager (web) ===\nOpen http://127.0.0.1:{args.port}/\n")
    serve(open_browser=True, port=args.port)
