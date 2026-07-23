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

Tabs can be linked or independent. With "same folder on all tabs" on (default),
every tab shares one root and switching it in one tab moves them all. Turn it off
and each tab keeps its own root — which works because framed sites are served
under a root-scoped URL (/__site/<rootId>/<name>/), so the server always knows
which folder a tile belongs to regardless of which tab opened it.

The UI opens automatically at  http://localhost:PORT/__mw/  and the folder
picker pops up there on load.
"""
import argparse
import hashlib
import json
import os
import sys
import webbrowser
from collections import OrderedDict
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

INDEX_NAMES = ("index.html", "index.htm")
API_PREFIX = "/__multiweb/api/"
UI_PREFIX = "/__mw/"          # the MultiWeb UI, served independently of the root
SITE_PREFIX = "/__site/"      # framed sites, scoped by root id so tabs can differ
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
COUNT_CAP = 80               # stop annotating "N sites" past this many subfolders
ROOTS_MAX = 64               # cap on remembered root ids (LRU) — bounds memory


def within(base, path):
    """True if `path` is `base` or lives underneath it. `base` None = anywhere."""
    if not base:
        return True
    base, path = os.path.abspath(base), os.path.abspath(path)
    try:
        return os.path.commonpath([base, path]) == base
    except ValueError:       # e.g. different drives on Windows
        return False


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


def browse(path, jail=None):
    """List the immediate subdirectories of `path` so the UI can walk the
    filesystem and pick a new root. Returns the folder, its parent, and each
    child dir annotated with whether it's itself a site and how many sites it
    contains. `jail`, if set, clamps navigation to that subtree.

    To stay responsive on large directories, the per-subfolder "how many sites"
    count is only computed when there are at most COUNT_CAP subfolders — past
    that it's dropped (null) rather than firing a scandir per entry."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if jail and not within(jail, path):
        path = os.path.abspath(jail)
    parent = os.path.dirname(path)
    if parent == path or (jail and not within(jail, parent)):
        parent = None
    dirs, error = [], None
    try:
        entries = [e for e in sorted(os.scandir(path), key=lambda e: e.name.lower())
                   if e.is_dir() and not e.name.startswith(".")]
        annotate = len(entries) <= COUNT_CAP
        for entry in entries:
            dirs.append({
                "name": entry.name,
                "path": entry.path,
                "isSite": bool(has_index(entry.path)),
                "sites": count_sites(entry.path) if annotate else None,
            })
    except OSError as e:
        error = str(e)
    return {
        "path": path,
        "parent": parent,
        "home": os.path.expanduser("~"),
        "jail": os.path.abspath(jail) if jail else None,
        "sites": count_sites(path),
        "dirs": dirs,
        "error": error,
    }


class Handler(SimpleHTTPRequestHandler):
    shared_root = "."      # the root all linked tabs share
    ui_dir = "."           # fixed: this MultiWeb folder, always reachable
    self_name = "MultiWeb"
    link_tabs = True       # global toggle: all tabs share shared_root
    tab_roots = OrderedDict()   # tabId -> path (LRU), used when link_tabs is off
    roots_by_id = OrderedDict() # rootId -> abspath (LRU), routes /__site/<id>/
    jail = None            # if set, browse/setroot are confined to this subtree
    host_check = True      # reject non-loopback Host / cross-origin (DNS rebinding)
    allowed_hosts = set(LOOPBACK)

    @staticmethod
    def rid(path):
        """Stable short id for a root path, registered so framed-site URLs
        (/__site/<id>/…) can be routed back to the right directory. The registry
        is an LRU capped at ROOTS_MAX; live roots (shared + per-tab) are never
        evicted, so a long browsing session can't grow it without bound."""
        p = os.path.abspath(path)
        h = hashlib.sha1(p.encode("utf-8")).hexdigest()[:12]
        r = Handler.roots_by_id
        r[h] = p
        r.move_to_end(h)
        if len(r) > ROOTS_MAX:
            live = {os.path.abspath(Handler.shared_root)}
            live |= {os.path.abspath(v) for v in Handler.tab_roots.values()}
            for k in list(r.keys()):
                if len(r) <= ROOTS_MAX:
                    break
                if r[k] not in live:
                    del r[k]
        return h

    @staticmethod
    def set_tab_root(tab, path):
        t = Handler.tab_roots
        t[tab] = os.path.abspath(path)
        t.move_to_end(tab)
        while len(t) > ROOTS_MAX:
            t.popitem(last=False)   # forget the least-recently-used tab

    @staticmethod
    def root_for_tab(tab):
        if Handler.link_tabs or not tab:
            return Handler.shared_root
        return Handler.tab_roots.get(tab, Handler.shared_root)

    def _guard(self):
        """Block DNS-rebinding and cross-origin reads: a remote page can point
        its DNS at 127.0.0.1, but it can't forge a loopback Host header, and a
        cross-site fetch carries its own Origin. Skipped when bound to all
        interfaces (the operator has explicitly opted into exposure)."""
        if not Handler.host_check:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host and host not in Handler.allowed_hosts:
            self._deny("bad Host header")
            return False
        origin = self.headers.get("Origin")
        if origin:
            oh = (urlparse(origin).hostname or "").lower()
            if oh and oh not in Handler.allowed_hosts:
                self._deny("cross-origin request refused")
                return False
        return True

    def _deny(self, msg, code=403):
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _folders_payload(self, root, only=None):
        rid = Handler.rid(root)
        sites = discover(root, self.self_name, only, Handler.ui_dir)
        for s in sites:  # scope each tile's URL to its root so tabs can differ
            s["url"] = SITE_PREFIX + rid + "/" + s["name"] + "/"
        return {
            "root": os.path.abspath(root),
            "rootId": rid,
            "link": Handler.link_tabs,
            "self": self.self_name,
            "sites": sites,
        }

    def translate_path(self, path):
        # Three roots on one server: the fixed UI mount, the root-scoped framed
        # sites, and (legacy) the shared root. Routing purely on the URL means a
        # tile request needs no knowledge of which tab opened it.
        p = path.split("?", 1)[0].split("#", 1)[0]
        if p == UI_PREFIX.rstrip("/") or p.startswith(UI_PREFIX):
            self.directory = self.ui_dir
            rest = p[len(UI_PREFIX):] if p.startswith(UI_PREFIX) else ""
            return super().translate_path("/" + rest)
        if p.startswith(SITE_PREFIX):
            rid, _, tail = p[len(SITE_PREFIX):].partition("/")
            self.directory = Handler.roots_by_id.get(rid, Handler.shared_root)
            return super().translate_path("/" + tail)
        self.directory = Handler.shared_root
        return super().translate_path(p)

    def do_GET(self):
        if not self._guard():
            return
        path, _, query = self.path.partition("?")
        qs = parse_qs(query)
        tab = (qs.get("tab", [""])[0] or "").strip()

        if path == API_PREFIX + "folders":
            names = qs.get("names", [""])[0]
            only = set(filter(None, names.split(","))) if names else None
            self._send_json(self._folders_payload(Handler.root_for_tab(tab), only))
            return

        if path == API_PREFIX + "browse":
            self._send_json(browse(unquote(qs.get("path", [""])[0]), Handler.jail))
            return

        if path == API_PREFIX + "current":
            # Cheap heartbeat (no scandir): lets each tab notice when another
            # tab moved the shared root, or flipped the link toggle.
            root = Handler.root_for_tab(tab)
            self._send_json({
                "root": os.path.abspath(root),
                "rootId": Handler.rid(root),
                "link": Handler.link_tabs,
            })
            return

        if path == API_PREFIX + "link":
            on = qs.get("on", ["1"])[0] not in ("0", "false", "")
            if on:
                # Linking adopts the calling tab's folder as the shared one, so
                # "same folder on all tabs" means *this* folder everywhere.
                if tab and tab in Handler.tab_roots:
                    Handler.shared_root = Handler.tab_roots[tab]
                Handler.link_tabs = True
            else:
                Handler.link_tabs = False
                if tab and tab not in Handler.tab_roots:
                    Handler.set_tab_root(tab, Handler.shared_root)
            self._send_json(self._folders_payload(Handler.root_for_tab(tab)))
            return

        if path == API_PREFIX + "setroot":
            target = os.path.abspath(os.path.expanduser(unquote(qs.get("path", [""])[0])))
            if not os.path.isdir(target):
                self._send_json({"error": "not a folder: " + target}, code=400)
                return
            if Handler.jail and not within(Handler.jail, target):
                self._send_json({"error": "outside the allowed area: " + target}, code=403)
                return
            if Handler.link_tabs or not tab:
                Handler.shared_root = target
            else:
                Handler.set_tab_root(tab, target)
            self._send_json(self._folders_payload(target))
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
    ap.add_argument("--anywhere", action="store_true",
                    help="let the folder browser reach the whole filesystem "
                         "(default: confined to your home folder)")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        sys.exit(f"root folder not found: {root}")

    # Confine browse/setroot to a subtree. Default = home; widened to the common
    # ancestor if --root sits outside home; lifted entirely with --anywhere.
    home = os.path.abspath(os.path.expanduser("~"))
    if args.anywhere:
        jail = None
    elif within(home, root):
        jail = home
    else:
        try:
            jail = os.path.commonpath([home, root])
        except ValueError:
            jail = None
    Handler.jail = jail

    # DNS-rebinding guard only makes sense on a loopback bind; binding to all
    # interfaces is an explicit choice to expose the tool, so we step aside.
    exposed = args.host in ("0.0.0.0", "::", "")
    Handler.host_check = not exposed
    Handler.allowed_hosts = set(LOOPBACK) | {args.host.lower()}

    Handler.shared_root = root
    Handler.ui_dir = here
    Handler.self_name = self_name
    Handler.rid(root)  # pre-register so the first framed sites route immediately
    handler = partial(Handler, directory=root)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    ui_url = f"http://{args.host}:{args.port}{UI_PREFIX}"
    sites = discover(root, self_name, ui_dir=here)
    print(f"MultiWeb serving  {root}")
    print(f"Discovered {len(sites)} site folder(s): "
          + ", ".join(s['name'] for s in sites[:12])
          + (" …" if len(sites) > 12 else ""))
    print("Switch to any other folder from the picker — no restart needed.")
    print(f"Folder browser: {'whole filesystem' if jail is None else 'confined to ' + jail}")
    if exposed:
        print("!! Bound to all interfaces — the loopback guard is off and other "
              "devices can reach this server and its folder browser.")
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
