# MultiWeb — live local site wall

Watch every project folder's website at once. MultiWeb auto-discovers the
subfolders of a root directory that contain an `index.html`, tiles each one in
a grid of live iframes, and **auto-refreshes tiles as their files change** — so
you see your edits across many sites without alt-tabbing.

Built for watching the whole `~/Github/*` carino.systems fleet while editing.

## Run

```bash
cd MultiWeb
python3 serve.py            # root = parent folder (…/Github), port 8787, opens browser
```

Other options:

```bash
python3 serve.py --root ~/sites   # start on a different folder
python3 serve.py --port 9000      # different port
python3 serve.py --no-open        # don't auto-open the browser
python3 serve.py --anywhere       # let the folder browser leave your home folder
```

On launch the **folder picker pops up** so you choose which folders load onto
the wall. Your selection is remembered per browser; reopen it any time with the
**▤ Folders** button.

### Switching the scanned folder

`--root` only sets the *starting* folder. From the picker, **Change folder…**
opens a filesystem browser — walk into any directory (or type/paste a path),
see how many site folders each candidate holds, then **Scan this folder** to
point MultiWeb there. No restart needed; the server remembers the choice for its
lifetime. The UI is served from a fixed `/__mw/` mount that's independent of the
scanned root, so switching folders never pulls the page out from under you.

### One folder, or one per tab

The picker's **Same folder on all tabs** toggle controls scope:

- **On** (default) — every MultiWeb tab shares one folder; switching it in any
  tab moves them all. A tab notices within a couple of seconds and resyncs.
- **Off** — each browser tab keeps its own folder, so you can watch, say,
  `~/Github` in one tab and `~/clients/acme` in another at the same time.

This works because framed sites are served under a **root-scoped URL**
(`/__site/<rootId>/<name>/`), so the server always knows which folder a tile
belongs to regardless of which tab opened it. The toggle is a server-wide mode;
each tab is identified by a `sessionStorage` id that survives reloads.

## Why a server?

Browsers can't list a directory, and most sites block being framed. `serve.py`
solves both:

- Serves the **root folder** so every subfolder's `index.html` is *same-origin*
  and therefore framable, and serves its own UI from a fixed `/__mw/` mount that
  stays put when you switch the scanned root.
- Exposes `GET /__multiweb/api/folders` → JSON of subfolders that have an index
  file, each with a newest-file `mtime` used for change detection;
  `…/browse?path=` to walk the filesystem; `…/setroot?path=` to switch the
  watched folder without restarting; `…/link?on=` for the all-tabs toggle; and
  `…/current` as a cheap cross-tab heartbeat. Requests carry a `tab=` id so the
  server can hand each tab its own folder when tabs are unlinked.

Because framing and discovery both depend on the server, MultiWeb runs locally
rather than as a static `*.carino.systems` deploy.

## Controls

| Control | Does |
|---|---|
| **▤ Folders** | Open the picker (select all / clear / only recently changed / filter) |
| **Change folder…** | Browse the filesystem and switch which root folder MultiWeb scans, live |
| **Same folder on all tabs** | On: all tabs share one folder. Off: each tab picks its own |
| **Columns** | Auto / 1 / 2 / 3 / 4 grid density |
| **Screen** | Simulate a resolution on **every** tile, grouped: Desktop 16:9 (720p–4K), Ultrawide/Super 21:9·32:9, Laptop 16:10, Tablet, Mobile, and pure aspect ratios (1:1, 4:3, 3:2, 16:9, 21:9, 9:16 portrait). The site renders at that pixel size and is scaled to fit the tile (a `w×h · scale%` badge shows the fit). Per-tile dropdowns override the global default. |
| **Auto-refresh** | Toggle live reloading |
| **Interval** | 1–10 s poll cadence |
| **Smart** | Reload only tiles whose files actually changed (off = reload all every tick) |
| **⟳ Reload all** | Force-reload every tile |
| per-tile `⧉ / ⟳ / ⤢ / ↗` | Clone / reload / expand (solo) / open in a new tab |
| per-tile `✕` (clones only) | Remove that clone |

### Cloning a site

The per-tile **⧉** button drops a second tile of the same folder onto the wall,
right next to the original. Each clone is an independent instance with its own
**Screen** resolution and solo state, but it shares the folder's URL and live
change-detection — so a save reloads the original and every clone at once. The
classic use is watching one site at several resolutions side by side (e.g.
desktop next to iPhone). Clones persist across reloads; remove one with its **✕**,
and deselecting a folder in the picker clears its clones too. Clones add no server
load — they reuse the same folder poll.

## Layout

```
serve.py          local server + discovery/browse/setroot API; UI at /__mw/
index.html        navbar (brand/clock + wall controls + social/status) + grid + picker modal
css/styles.css    Carino navbar tokens + grid/tile/modal styles
js/app.js         discovery, picker, tiles, smart auto-refresh
carino-clock.js   shared fleet navbar clock
fonts/            self-hosted IBM Plex + Red Hat Display
```

## Staying local

MultiWeb serves your own files and can list folders, so it's careful not to
answer anyone but you:

- **Loopback guard.** When bound to `localhost` (the default), requests whose
  `Host` isn't loopback, or that carry a cross-site `Origin`, are refused — so a
  random web page you have open can't reach `http://localhost:8787` and read your
  folder listings (a DNS-rebinding / CORS leak). Bind to `0.0.0.0` and the guard
  steps aside on purpose, with a warning, since you've chosen to expose it.
- **Home-folder jail.** The **Change folder…** browser and `setroot` are confined
  to your home folder (widened only if `--root` points outside it). Pass
  `--anywhere` to browse the whole filesystem.
- No wildcard CORS header, and nothing is sent anywhere off the machine.

## Notes / limits

- **Local folders only.** Tiles are your own subfolders. Arbitrary production
  URLs that send `X-Frame-Options: DENY` won't render — that's the browser, not
  MultiWeb.
- **Polling, not inotify.** "Smart" refresh compares file mtimes each interval;
  it's not a filesystem watcher, but it's plenty for watch-while-you-edit.
- It's a **viewer**, not an editor.

## Performance

A 40-tile wall of live documents is kept cheap by:

- **Lazy mounting** — an iframe's `src` is only set once its tile scrolls within
  400 px of the viewport (`IntersectionObserver`), so opening the wall doesn't
  spin up every site at once.
- **Visibility-gated refresh** — auto-refresh only reloads tiles that are on
  screen; a folder that changes while its tile is scrolled away is marked dirty
  and reloads the instant it scrolls back into view.
- **Tab-hidden pause** — polling stops when the MultiWeb tab is backgrounded and
  catches up on return.
- **Scoped polling** — the smart-refresh poll sends `?names=` so the server only
  stats the folders currently loaded, not the whole tree.
- **One poll, not two** — that same smart poll already carries the current
  root/link, so it doubles as the cross-tab sync; the standalone heartbeat only
  runs when the wall is idle (nothing loaded, or auto-refresh off).
- **Bounded memory** — the root-id registry and per-tab roots are LRU-capped, so
  a long session of browsing around can't grow them without limit; and the
  folder browser skips its per-subfolder "N sites" count past ~80 entries to
  stay responsive on large directories.
- Iframe scaling recomputes via a shared `ResizeObserver` (one callback per tile,
  no polling for layout).
