#!/usr/bin/env python3
"""
MultiWeb — local server + folder auto-discovery.

Serves a *root* folder (default: the parent of this MultiWeb folder) so that
every sibling project's index.html is same-origin and therefore framable, and
exposes a tiny JSON API the UI uses to auto-discover which subfolders are
websites (contain an index.html / index.htm).

    python3 serve.py                 # root = parent of MultiWeb, port 8787
    python3 serve.py --root ~/sites  # start on a different folder
    python3 serve.py --port 9000 --no-open

The `--root` is only the *starting* folder — you can point MultiWeb at any other
folder from the picker ("Change folder…") without restarting the server. The UI
itself is always served from the fixed  /__mw/  mount, so switching the watched
root never pulls the UI out from under you. The chosen root is remembered for the
life of the server process.

The UI opens automatically at  http://localhost:PORT/__mw/  and the folder
picker pops up there on load.
"""
import argparse
import json
import os
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

INDEX_NAMES = ("index.html", "index.htm")
API_PREFIX = "/__multiweb/api/"
UI_PREFIX = "/__mw/"          # the MultiWeb UI, served independently of the root


def has_index(folder):
    return next((n for n in INDEX_NAMES
                 if os.path.isfile(os.path.join(folder, n))), None)


def newest_mtime(folder):
    """Cheap freshness signal: newest mtime among files directly in `folder`
    plus one level down (covers css/js edits) — not a full recursive walk."""
    newest = 0.0
    try:
        for entry in os.scandir(folder):
            try:
                if entry.is_file():
                    newest = max(newest, entry.stat().st_mtime)
                elif entry.is_dir():
                    for sub in os.scandir(entry.path):
                        if sub.is_file():
                            newest = max(newest, sub.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return newest


def discover(root, self_name, only=None, ui_dir=None):
    """Immediate subfolders of `root` that contain an index file.
    If `only` (a set of names) is given, stat just those folders — the live
    poll uses this so it only touches the folders currently on the wall.
    The MultiWeb UI folder itself is never offered as a tile."""
    sites = []
    ui_dir = os.path.abspath(ui_dir) if ui_dir else None
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError:
        return sites
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if ui_dir and os.path.abspath(entry.path) == ui_dir:
            continue
        if only is not None and entry.name not in only:
            continue
        index = has_index(entry.path)
        if not index:
            continue
        sites.append({
            "name": entry.name,
            "url": "/" + entry.name + "/",
            "index": index,
            "mtime": round(newest_mtime(entry.path), 1),
        })
    return sites


def count_sites(folder):
    """How many immediate subfolders of `folder` are themselves websites.
    Best-effort; used to hint which candidate folders make a good root."""
    n = 0
    try:
        for entry in os.scandir(folder):
            if entry.is_dir() and not entry.name.startswith(".") and has_index(entry.path):
                n += 1
    except OSError:
        pass
    return n


def browse(path):
    """List the immediate subdirectories of `path` so the UI can walk the
    filesystem and pick a new root. Returns the folder, its parent, and each
    child dir annotated with whether it's itself a site and how many sites it
    contains."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    parent = os.path.dirname(path)
    dirs = []
    error = None
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            dirs.append({
                "name": entry.name,
                "path": entry.path,
                "isSite": bool(has_index(entry.path)),
                "sites": count_sites(entry.path),
            })
    except OSError as e:
        error = str(e)
    return {
        "path": path,
        "parent": parent if parent != path else None,
        "home": os.path.expanduser("~"),
        "sites": count_sites(path),
        "dirs": dirs,
        "error": error,
    }


class Handler(SimpleHTTPRequestHandler):
    root = "."             # mutable: the folder currently watched/framed
    ui_dir = "."           # fixed: this MultiWeb folder, always reachable
    self_name = "MultiWeb"

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _folders_payload(self, only=None):
        return {
            "root": os.path.abspath(Handler.root),
            "self": self.self_name,
            "sites": discover(Handler.root, self.self_name, only, Handler.ui_dir),
        }

    def translate_path(self, path):
        # Route the UI mount to the fixed MultiWeb folder and everything else to
        # the current (switchable) root, so changing the root never unmounts the
        # UI the browser is already running.
        p = path.split("?", 1)[0].split("#", 1)[0]
        if p == UI_PREFIX.rstrip("/") or p.startswith(UI_PREFIX):
            self.directory = self.ui_dir
            rest = p[len(UI_PREFIX):] if p.startswith(UI_PREFIX) else ""
            return super().translate_path("/" + rest)
        self.directory = Handler.root
        return super().translate_path(p)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        qs = parse_qs(query)

        if path == API_PREFIX + "folders":
            names = qs.get("names", [""])[0]
            only = set(filter(None, names.split(","))) if names else None
            self._send_json(self._folders_payload(only))
            return

        if path == API_PREFIX + "browse":
            self._send_json(browse(unquote(qs.get("path", [""])[0])))
            return

        if path == API_PREFIX + "setroot":
            target = os.path.abspath(os.path.expanduser(unquote(qs.get("path", [""])[0])))
            if not os.path.isdir(target):
                self._send_json({"error": "not a folder: " + target}, code=400)
                return
            Handler.root = target
            self._send_json(self._folders_payload())
            return

        super().do_GET()

    def end_headers(self):
        # Framing must be allowed for the multiviewer; disable page caching so
        # a tile reload always shows the freshly-saved file.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if API_PREFIX in self.path:  # keep the console quiet on poll traffic
            return
        super().log_message(fmt, *args)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    self_name = os.path.basename(here)
    default_root = os.path.dirname(here)

    ap = argparse.ArgumentParser(description="MultiWeb local server")
    ap.add_argument("--root", default=default_root,
                    help="starting folder whose subfolders are watched "
                         "(default: parent of MultiWeb; switchable from the UI)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        sys.exit(f"root folder not found: {root}")

    Handler.root = root
    Handler.ui_dir = here
    Handler.self_name = self_name
    handler = partial(Handler, directory=root)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    ui_url = f"http://{args.host}:{args.port}{UI_PREFIX}"
    sites = discover(root, self_name, ui_dir=here)
    print(f"MultiWeb serving  {root}")
    print(f"Discovered {len(sites)} site folder(s): "
          + ", ".join(s['name'] for s in sites[:12])
          + (" …" if len(sites) > 12 else ""))
    print("Switch to any other folder from the picker — no restart needed.")
    print(f"Open  {ui_url}")
    print("Ctrl+C to stop.")
    if not args.no_open:
        try:
            webbrowser.open(ui_url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
