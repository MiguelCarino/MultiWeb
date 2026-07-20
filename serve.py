#!/usr/bin/env python3
"""
MultiWeb — local server + folder auto-discovery.

Serves a *root* folder (default: the parent of this MultiWeb folder) so that
every sibling project's index.html is same-origin and therefore framable, and
exposes a tiny JSON API the UI uses to auto-discover which subfolders are
websites (contain an index.html / index.htm).

    python3 serve.py                 # root = parent of MultiWeb, port 8787
    python3 serve.py --root ~/sites  # watch a different folder
    python3 serve.py --port 9000 --no-open

The UI (this MultiWeb folder) is served at  http://localhost:PORT/<MultiWeb>/
and opens automatically. The folder picker pops up there on load.
"""
import argparse
import json
import os
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

INDEX_NAMES = ("index.html", "index.htm")
API_PREFIX = "/__multiweb/api/"


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


def discover(root, self_name, only=None):
    """Immediate subfolders of `root` that contain an index file.
    If `only` (a set of names) is given, stat just those folders — the live
    poll uses this so it only touches the folders currently on the wall."""
    sites = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError:
        return sites
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name == self_name:  # don't frame ourselves
            continue
        if only is not None and entry.name not in only:
            continue
        index = next((n for n in INDEX_NAMES
                      if os.path.isfile(os.path.join(entry.path, n))), None)
        if not index:
            continue
        sites.append({
            "name": entry.name,
            "url": "/" + entry.name + "/",
            "index": index,
            "mtime": round(newest_mtime(entry.path), 1),
        })
    return sites


class Handler(SimpleHTTPRequestHandler):
    root = "."
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

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == API_PREFIX + "folders":
            only = None
            names = parse_qs(query).get("names", [""])[0]
            if names:
                only = set(filter(None, names.split(",")))
            self._send_json({
                "root": os.path.abspath(self.root),
                "self": self.self_name,
                "sites": discover(self.root, self.self_name, only),
            })
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
                    help="folder whose subfolders are watched (default: parent of MultiWeb)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        sys.exit(f"root folder not found: {root}")

    Handler.root = root
    Handler.self_name = self_name
    handler = partial(Handler, directory=root)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    ui_url = f"http://{args.host}:{args.port}/{self_name}/"
    sites = discover(root, self_name)
    print(f"MultiWeb serving  {root}")
    print(f"Discovered {len(sites)} site folder(s): "
          + ", ".join(s['name'] for s in sites[:12])
          + (" …" if len(sites) > 12 else ""))
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
