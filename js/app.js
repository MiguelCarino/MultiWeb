/* ================================================================
   MultiWeb — app logic.
   Flow: discover folders → picker popup on start → tile the chosen
   folders → smart auto-refresh (reload only tiles whose files changed).
   ================================================================ */
(function () {
  'use strict';

  var API = '/__multiweb/api/folders';
  var LS_SEL = 'multiweb.selected';   // array of folder names
  var LS_COLS = 'multiweb.cols';
  var LS_AUTO = 'multiweb.auto';
  var LS_SMART = 'multiweb.smart';
  var LS_INT = 'multiweb.interval';
  var LS_RESDEF = 'multiweb.resDefault';
  var LS_RES = 'multiweb.res';         // per-tile res overrides, keyed by instance id
  var LS_CLONES = 'multiweb.clones';   // extra instances of a folder: [{id,name}]

  var $ = function (id) { return document.getElementById(id); };

  // Simulated screen resolutions, grouped for the dropdown.
  // w/h = 0 means "Fit" (fill the tile, no scaling).
  var PRESET_GROUPS = [
    { group: 'General', items: [
      { id: 'fit',     label: 'Fit',           w: 0,    h: 0 },
    ]},
    { group: 'Desktop 16:9', items: [
      { id: 'uhd',     label: '4K UHD',        w: 3840, h: 2160 },
      { id: 'qhd',     label: '1440p QHD',     w: 2560, h: 1440 },
      { id: 'fhd',     label: '1080p FHD',     w: 1920, h: 1080 },
      { id: 'hd',      label: '720p HD',       w: 1280, h: 720 },
    ]},
    { group: 'Ultrawide 21:9 · 32:9', items: [
      { id: 'uw-fhd',  label: 'UW 2560×1080',  w: 2560, h: 1080 },
      { id: 'uw-qhd',  label: 'UW 3440×1440',  w: 3440, h: 1440 },
      { id: 'uw-plus', label: 'UW 3840×1600',  w: 3840, h: 1600 },
      { id: 'suw',     label: 'Super 5120×1440', w: 5120, h: 1440 },
    ]},
    { group: 'Laptop 16:10', items: [
      { id: 'mbp16',   label: 'MacBook 1728×1117', w: 1728, h: 1117 },
      { id: 'laptop',  label: 'Laptop 1440×900',   w: 1440, h: 900 },
      { id: 'laptop-s',label: 'Laptop 1280×800',   w: 1280, h: 800 },
    ]},
    { group: 'Tablet', items: [
      { id: 'ipad',    label: 'iPad 768×1024',      w: 768,  h: 1024 },
      { id: 'ipad-l',  label: 'iPad wide 1024×768', w: 1024, h: 768 },
      { id: 'ipad-pro',label: 'iPad Pro 1024×1366', w: 1024, h: 1366 },
    ]},
    { group: 'Mobile', items: [
      { id: 'iphone',  label: 'iPhone 390×844',   w: 390,  h: 844 },
      { id: 'iphone-p',label: 'iPhone Pro 430×932', w: 430, h: 932 },
      { id: 'se',      label: 'iPhone SE 375×667', w: 375,  h: 667 },
      { id: 'pixel',   label: 'Pixel 412×915',    w: 412,  h: 915 },
    ]},
    { group: 'Aspect ratio', items: [
      { id: 'ar-1-1',  label: '1:1 square',       w: 1080, h: 1080 },
      { id: 'ar-4-3',  label: '4:3',              w: 1440, h: 1080 },
      { id: 'ar-3-2',  label: '3:2',              w: 1440, h: 960 },
      { id: 'ar-16-9', label: '16:9',             w: 1920, h: 1080 },
      { id: 'ar-21-9', label: '21:9',             w: 2560, h: 1097 },
      { id: 'ar-9-16', label: '9:16 portrait',    w: 1080, h: 1920 },
    ]},
  ];
  var PRESETS = PRESET_GROUPS.reduce(function (a, g) { return a.concat(g.items); }, []);
  function preset(id) { return PRESETS.find(function (p) { return p.id === id; }) || PRESETS[0]; }
  function resOptions(sel) {
    return PRESET_GROUPS.map(function (g) {
      var opts = g.items.map(function (p) {
        return '<option value="' + p.id + '"' + (p.id === sel ? ' selected' : '') + '>' + p.label + '</option>';
      }).join('');
      return '<optgroup label="' + g.group + '">' + opts + '</optgroup>';
    }).join('');
  }

  var state = {
    root: '',
    sites: [],            // [{name,url,index,mtime}]
    selected: [],         // names currently on the wall
    mtimes: {},           // name -> last-seen mtime
    solo: null,           // instance id of solo'd tile
    clones: [],           // extra instances: [{id,name}] — same folder, own tile
    resDefault: localStorage.getItem(LS_RESDEF) || 'fit',
    res: {},              // per-tile resolution overrides {instanceId:id}
    cols: localStorage.getItem(LS_COLS) || 'auto',
    auto: localStorage.getItem(LS_AUTO) !== '0',
    smart: localStorage.getItem(LS_SMART) !== '0',
    interval: parseInt(localStorage.getItem(LS_INT) || '3000', 10),
    timer: null,
    lastChange: null,
  };

  /* ---------------------------------------------------------------- discovery */
  function fetchFolders(names) {
    var url = API;
    if (names && names.length) url += '?names=' + encodeURIComponent(names.join(','));
    return fetch(url, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function isServed() { return location.protocol === 'http:' || location.protocol === 'https:'; }

  try { state.res = JSON.parse(localStorage.getItem(LS_RES) || '{}'); } catch (e) { state.res = {}; }
  try { state.clones = JSON.parse(localStorage.getItem(LS_CLONES) || '[]'); } catch (e) { state.clones = []; }

  /* ---------------------------------------------------------------- resolution sim */
  // res overrides are keyed by instance id; for a base tile id === folder name,
  // so pre-clone saved overrides keep applying.
  function effRes(id) { return state.res[id] || state.resDefault; }

  // Recompute the iframe transform for one tile-wrap based on its tile's res.
  function applyScale(wrap) {
    var tile = wrap.closest('.tile'); if (!tile) return;
    var frame = wrap.querySelector('iframe'); if (!frame) return;
    var badge = wrap.querySelector('.tile-res');
    var p = preset(tile.dataset.res || 'fit');

    if (!p.w) { // Fit: fill the tile, no scaling
      wrap.classList.remove('sim');
      frame.style.cssText = 'width:100%;height:100%;transform:none;position:static;';
      if (badge) badge.hidden = true;
      return;
    }
    wrap.classList.add('sim');
    var r = wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    var scale = Math.min(r.width / p.w, r.height / p.h);
    var offX = Math.round((r.width - p.w * scale) / 2);
    var offY = Math.round((r.height - p.h * scale) / 2);
    frame.style.cssText =
      'position:absolute;top:0;left:0;width:' + p.w + 'px;height:' + p.h + 'px;' +
      'transform-origin:top left;transform:translate(' + offX + 'px,' + offY + 'px) scale(' + scale + ');';
    if (badge) { badge.hidden = false; badge.textContent = p.w + '×' + p.h + ' · ' + Math.round(scale * 100) + '%'; }
  }

  var scaleRO = (typeof ResizeObserver !== 'undefined')
    ? new ResizeObserver(function (entries) { entries.forEach(function (e) { applyScale(e.target); }); })
    : null;

  /* ---------------------------------------------------------------- tiles */
  // A tile is identified by its instance id (data-id); its folder is data-name.
  // For a base tile the two are equal; a clone shares the name but has its own id.
  function tileById(id) { return document.querySelector('.tile[data-id="' + CSS.escape(id) + '"]'); }
  function tilesByName(name) { return document.querySelectorAll('.tile[data-name="' + CSS.escape(name) + '"]'); }
  function isMounted(tile) { return tile && tile.dataset.mounted === '1'; }
  function isVisible(tile) { return tile && tile.dataset.vis !== '0'; }

  // Lazy-mount: an iframe's src is only set once its tile scrolls near the
  // viewport, so a 40-tile wall doesn't spin up 40 live documents at once.
  function mountTile(tile) {
    if (isMounted(tile)) return;
    var site = state.sites.find(function (s) { return s.name === tile.dataset.name; });
    if (!site) return;
    var frame = tile.querySelector('iframe');
    tile.dataset.mounted = '1';
    tile.classList.add('loading');
    frame.src = bust(site.url);
  }

  // Only tiles near the viewport auto-refresh; offscreen changes are marked
  // dirty and applied the moment the tile scrolls back into view.
  var viewIO = (typeof IntersectionObserver !== 'undefined')
    ? new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          var tile = e.target;
          tile.dataset.vis = e.isIntersecting ? '1' : '0';
          if (!e.isIntersecting) return;
          if (!isMounted(tile)) mountTile(tile);
          else if (tile.dataset.dirty === '1') { tile.dataset.dirty = ''; reloadTile(tile.dataset.id); }
        });
      }, { root: null, rootMargin: '400px 0px', threshold: 0 })
    : null;

  function bust(url) { return url + (url.indexOf('?') < 0 ? '?' : '&') + '_mw=' + Date.now(); }

  // `inst` = {id, name}. A base tile has id === name; a clone shares the name.
  function makeTile(inst) {
    var name = inst.name;
    var site = state.sites.find(function (s) { return s.name === name; }) || { name: name, url: '/' + name + '/' };
    var isClone = inst.id !== name;
    var res = effRes(inst.id);
    var tile = document.createElement('div');
    tile.className = 'tile loading' + (isClone ? ' is-clone' : '');
    tile.dataset.id = inst.id;
    tile.dataset.name = name;
    tile.dataset.res = res;

    var bar = document.createElement('div');
    bar.className = 'tile-bar';
    bar.innerHTML =
      '<span class="tile-dot" title="Loaded"></span>' +
      '<span class="tile-name" title="' + name + '">' + name +
        (isClone ? '<span class="tile-copy" title="Clone of ' + name + '">copy</span>' : '') + '</span>' +
      '<select class="tile-res-sel" title="Simulated screen resolution">' + resOptions(res) + '</select>' +
      '<button class="tile-btn" data-act="clone" title="Clone this tile">⧉</button>' +
      '<button class="tile-btn" data-act="reload" title="Reload this tile">⟳</button>' +
      '<button class="tile-btn" data-act="solo" title="Expand / collapse">⤢</button>' +
      '<a class="tile-btn" data-act="open" href="' + site.url + '" target="_blank" rel="noopener" title="Open in new tab">↗</a>' +
      (isClone ? '<button class="tile-btn tile-btn-x" data-act="close" title="Remove this clone">✕</button>' : '');

    var wrap = document.createElement('div');
    wrap.className = 'tile-wrap';
    var loader = document.createElement('div');
    loader.className = 'tile-loader';
    loader.textContent = 'loading ' + name + '…';
    var frame = document.createElement('iframe');
    frame.loading = 'lazy';
    // src is set by mountTile() when the tile scrolls near the viewport.
    frame.addEventListener('load', function () {
      tile.classList.remove('loading');
      bar.querySelector('.tile-dot').classList.add('live');
      applyScale(wrap);
    });
    var badge = document.createElement('div');
    badge.className = 'tile-res'; badge.hidden = true;
    wrap.appendChild(frame);
    wrap.appendChild(badge);
    wrap.appendChild(loader);

    tile.appendChild(bar);
    tile.appendChild(wrap);

    bar.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-act]');
      if (!btn) return;
      var act = btn.dataset.act;
      if (act === 'reload') reloadTile(inst.id);
      else if (act === 'solo') toggleSolo(inst.id);
      else if (act === 'clone') cloneTile(inst);
      else if (act === 'close') removeClone(inst.id);
      // 'open' is a plain anchor — let it through
    });
    bar.querySelector('.tile-res-sel').addEventListener('change', function (e) {
      setTileRes(inst.id, e.target.value);
    });
    return tile;
  }

  function reloadTile(id) {
    var tile = tileById(id);
    if (!tile) return;
    if (!isMounted(tile)) return; // not loaded yet — it'll load fresh on mount
    var site = state.sites.find(function (s) { return s.name === tile.dataset.name; });
    if (!site) return;
    var frame = tile.querySelector('iframe');
    tile.classList.add('loading');
    frame.src = bust(site.url);
    tile.classList.add('flash');
    setTimeout(function () { tile.classList.remove('flash'); }, 900);
  }

  function setTileRes(id, resId) {
    state.res[id] = resId;
    localStorage.setItem(LS_RES, JSON.stringify(state.res));
    var tile = tileById(id); if (!tile) return;
    tile.dataset.res = resId;
    var sel = tile.querySelector('.tile-res-sel'); if (sel) sel.value = resId;
    applyScale(tile.querySelector('.tile-wrap'));
    updateDiag();
  }

  // Clone: add another instance of the same folder — its own tile, resolution
  // and solo state, but sharing the folder's URL and change-detection.
  function cloneId(name) {
    var k = 2, id;
    do { id = name + ' #' + (k++); } while (tileById(id) ||
      state.clones.some(function (c) { return c.id === id; }));
    return id;
  }
  function cloneTile(inst) {
    var clone = { id: cloneId(inst.name), name: inst.name };
    state.clones.push(clone);
    localStorage.setItem(LS_CLONES, JSON.stringify(state.clones));
    // Start the clone matching the source's simulated resolution.
    if (state.res[inst.id]) {
      state.res[clone.id] = state.res[inst.id];
      localStorage.setItem(LS_RES, JSON.stringify(state.res));
    }
    var tile = makeTile(clone);
    var src = tileById(inst.id), grid = $('grid');
    if (src && src.nextSibling) grid.insertBefore(tile, src.nextSibling);
    else grid.appendChild(tile);
    var wrap = tile.querySelector('.tile-wrap');
    applyScale(wrap);
    if (scaleRO) scaleRO.observe(wrap);
    if (viewIO) viewIO.observe(tile); else mountTile(tile);
    updateWallLabel();
    updateDiag();
  }
  function removeClone(id) {
    state.clones = state.clones.filter(function (c) { return c.id !== id; });
    localStorage.setItem(LS_CLONES, JSON.stringify(state.clones));
    if (state.res[id]) { delete state.res[id]; localStorage.setItem(LS_RES, JSON.stringify(state.res)); }
    var tile = tileById(id);
    if (tile) {
      var wrap = tile.querySelector('.tile-wrap');
      if (scaleRO && wrap) scaleRO.unobserve(wrap);
      if (viewIO) viewIO.unobserve(tile);
      tile.remove();
    }
    if (state.solo === id) { state.solo = null; $('stage').classList.remove('has-solo'); }
    updateWallLabel();
    updateDiag();
  }

  // Global default: applies to every tile and clears per-tile overrides.
  function setResDefault(id) {
    state.resDefault = id;
    state.res = {};
    localStorage.setItem(LS_RESDEF, id);
    localStorage.setItem(LS_RES, '{}');
    document.querySelectorAll('.tile').forEach(function (tile) {
      tile.dataset.res = id;
      var sel = tile.querySelector('.tile-res-sel'); if (sel) sel.value = id;
      applyScale(tile.querySelector('.tile-wrap'));
    });
    updateDiag();
  }

  function toggleSolo(id) {
    var stage = $('stage');
    if (state.solo === id) {
      state.solo = null;
      stage.classList.remove('has-solo');
      var t = tileById(id); if (t) t.classList.remove('solo');
    } else {
      document.querySelectorAll('.tile.solo').forEach(function (t) { t.classList.remove('solo'); });
      state.solo = id;
      stage.classList.add('has-solo');
      var el = tileById(id); if (el) el.classList.add('solo');
    }
    updateDiag();
  }

  function updateWallLabel() {
    var n = document.querySelectorAll('.tile').length;
    $('wallLabel').textContent = n
      ? n + ' tile' + (n > 1 ? 's' : '') + ' loaded'
      : 'No sites loaded';
    $('emptyState').hidden = n > 0;
  }

  // Build the ordered list of instances on the wall: each selected folder,
  // immediately followed by any of its clones. Skips folders no longer present.
  function wallInstances() {
    var have = {};
    state.sites.forEach(function (s) { have[s.name] = true; });
    var out = [];
    state.selected.forEach(function (name) {
      if (!have[name]) return;
      out.push({ id: name, name: name });
      state.clones.forEach(function (c) { if (c.name === name) out.push({ id: c.id, name: name }); });
    });
    return out;
  }

  function renderWall() {
    var grid = $('grid');
    if (scaleRO) scaleRO.disconnect();
    if (viewIO) viewIO.disconnect();
    grid.innerHTML = '';
    state.solo = null;
    $('stage').classList.remove('has-solo');

    wallInstances().forEach(function (inst) {
      var s = state.sites.find(function (x) { return x.name === inst.name; });
      if (s) state.mtimes[inst.name] = s.mtime;
      var tile = makeTile(inst);
      grid.appendChild(tile);
      var wrap = tile.querySelector('.tile-wrap');
      applyScale(wrap);
      if (scaleRO) scaleRO.observe(wrap);
      if (viewIO) viewIO.observe(tile); else mountTile(tile); // no IO → load eagerly
    });

    updateWallLabel();
    updateDiag();
  }

  /* ---------------------------------------------------------------- auto-refresh */
  function startTimer() {
    stopTimer();
    if (!state.auto || !state.selected.length) return;
    state.timer = setInterval(tick, state.interval);
  }
  function stopTimer() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  // A folder changed → refresh every tile showing it (base tile + any clones).
  // Reload now if visible; otherwise mark dirty so it refreshes on scroll-in.
  // Skips unmounted tiles entirely.
  function refreshChanged(name) {
    tilesByName(name).forEach(function (tile) {
      if (!isMounted(tile)) return;
      if (isVisible(tile)) reloadTile(tile.dataset.id);
      else tile.dataset.dirty = '1';
    });
  }

  function tick() {
    if (!state.selected.length || document.hidden) return;
    if (!state.smart) {
      // Full mode: reload every loaded, visible tile every interval.
      state.selected.forEach(refreshChanged);
      state.lastChange = 'all';
      updateDiag();
      return;
    }
    // Smart mode: only reload tiles whose newest-file mtime advanced.
    // Poll only stats the folders actually loaded (fewer syscalls server-side).
    fetchFolders(state.selected).then(function (data) {
      var changed = [];
      data.sites.forEach(function (s) {
        if (state.selected.indexOf(s.name) < 0) return;
        var prev = state.mtimes[s.name];
        if (prev !== undefined && s.mtime > prev) changed.push(s.name);
        state.mtimes[s.name] = s.mtime;
      });
      changed.forEach(refreshChanged);
      if (changed.length) { state.lastChange = changed.join(', '); updateDiag(); }
    }).catch(function () { /* server hiccup — skip this tick */ });
  }

  /* ---------------------------------------------------------------- picker */
  function openPicker() {
    var list = $('pickerList');
    var filter = $('pickerFilter');
    var pending = state.selected.slice();

    function draw() {
      var q = filter.value.trim().toLowerCase();
      var shown = state.sites.filter(function (s) { return !q || s.name.toLowerCase().indexOf(q) >= 0; });
      if (!shown.length) { list.innerHTML = '<div class="picker-empty">No folders match.</div>'; return; }
      // Recently changed = modified in the last 10 minutes.
      var now = Date.now() / 1000;
      list.innerHTML = shown.map(function (s) {
        var checked = pending.indexOf(s.name) >= 0 ? 'checked' : '';
        var fresh = (now - s.mtime) < 600 ? '<span class="pi-badge">changed</span>' : '';
        return '<label class="picker-item"><input type="checkbox" data-name="' + s.name + '" ' + checked + '>' +
               '<span class="pi-name">' + s.name + '</span>' + fresh + '</label>';
      }).join('');
    }
    function count() { $('pickerCount').textContent = pending.length + ' selected'; }

    list.onclick = function (e) {
      var cb = e.target.closest('input[type=checkbox]');
      if (!cb) return;
      var name = cb.dataset.name;
      var i = pending.indexOf(name);
      if (cb.checked && i < 0) pending.push(name);
      else if (!cb.checked && i >= 0) pending.splice(i, 1);
      count();
    };
    filter.oninput = draw;
    $('pickAll').onclick = function () { pending = state.sites.map(function (s) { return s.name; }); draw(); count(); };
    $('pickNone').onclick = function () { pending = []; draw(); count(); };
    $('pickChanged').onclick = function () {
      var now = Date.now() / 1000;
      pending = state.sites.filter(function (s) { return (now - s.mtime) < 600; }).map(function (s) { return s.name; });
      draw(); count();
    };
    $('pickerApply').onclick = function () {
      state.selected = state.sites.filter(function (s) { return pending.indexOf(s.name) >= 0; })
                                   .map(function (s) { return s.name; });
      localStorage.setItem(LS_SEL, JSON.stringify(state.selected));
      // Drop clones of folders that are no longer selected.
      state.clones = state.clones.filter(function (c) { return state.selected.indexOf(c.name) >= 0; });
      localStorage.setItem(LS_CLONES, JSON.stringify(state.clones));
      closePicker();
      renderWall();
      startTimer();
    };

    $('pickerSub').textContent = state.sites.length + ' folder(s) in ' + (state.root || 'root');
    filter.value = '';
    draw(); count();
    $('pickerScrim').hidden = false;
    filter.focus();
  }
  function closePicker() { $('pickerScrim').hidden = true; }

  /* ---------------------------------------------------------------- diagnostics */
  function updateDiag() {
    $('diagActive').textContent = document.querySelectorAll('.tile').length;
    $('diagTotal').textContent = state.sites.length;
    $('diagAuto').textContent = state.auto ? 'On' : 'Off';
    $('diagAuto').className = 'diag-val ' + (state.auto ? 'good' : 'warn');
    $('diagMode').textContent = state.smart ? 'Smart' : 'Full';
    $('diagInterval').textContent = (state.interval / 1000) + 's';
    $('diagLast').textContent = state.lastChange || '—';
    $('diagRoot').textContent = state.root ? state.root.split('/').pop() : '—';
    $('diagLayout').textContent = state.solo ? 'Solo' : (state.cols === 'auto' ? 'Auto' : state.cols + '-col');
    var rd = preset(state.resDefault);
    $('diagScreen').textContent = rd.w ? rd.label + ' (' + rd.w + '×' + rd.h + ')' : 'Fit';
  }

  /* ---------------------------------------------------------------- wiring */
  function setCols(cols) {
    state.cols = cols;
    localStorage.setItem(LS_COLS, cols);
    $('stage').dataset.cols = cols;
    document.querySelectorAll('#colsSeg button').forEach(function (b) {
      b.classList.toggle('active', b.dataset.cols === cols);
    });
    updateDiag();
  }

  function wire() {
    $('pickBtn').onclick = openPicker;
    $('emptyPickBtn').onclick = openPicker;
    $('pickerClose').onclick = closePicker;
    $('pickerCancel').onclick = closePicker;
    $('pickerScrim').addEventListener('click', function (e) { if (e.target === $('pickerScrim')) closePicker(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closePicker(); });

    $('colsSeg').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-cols]'); if (b) setCols(b.dataset.cols);
    });

    var autoChk = $('autoChk'); autoChk.checked = state.auto;
    autoChk.onchange = function () {
      state.auto = autoChk.checked; localStorage.setItem(LS_AUTO, state.auto ? '1' : '0');
      startTimer(); updateDiag();
    };
    var smartChk = $('smartChk'); smartChk.checked = state.smart;
    smartChk.onchange = function () {
      state.smart = smartChk.checked; localStorage.setItem(LS_SMART, state.smart ? '1' : '0'); updateDiag();
    };
    var intSel = $('intervalSel'); intSel.value = String(state.interval);
    intSel.onchange = function () {
      state.interval = parseInt(intSel.value, 10); localStorage.setItem(LS_INT, intSel.value);
      startTimer(); updateDiag();
    };
    $('reloadAllBtn').onclick = function () {
      document.querySelectorAll('.tile').forEach(function (t) {
        if (isMounted(t)) reloadTile(t.dataset.id);
      });
    };

    // Pause polling when the MultiWeb tab is backgrounded; catch up on return.
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stopTimer();
      else { startTimer(); tick(); }
    });

    var resSel = $('resSel');
    resSel.innerHTML = resOptions(state.resDefault);
    resSel.onchange = function () { setResDefault(resSel.value); };

    // Diagnostics dropdown (matches fleet navbar behaviour).
    var diagToggle = $('diagToggle'), diagBox = $('diagBox');
    diagToggle.onclick = function () {
      var open = diagBox.classList.toggle('open');
      diagToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    document.addEventListener('click', function (e) {
      if (!diagBox.contains(e.target) && !diagToggle.contains(e.target)) {
        diagBox.classList.remove('open'); diagToggle.setAttribute('aria-expanded', 'false');
      }
    });
    // Clock element also shows time in the diag dropdown.
    setInterval(function () { $('diagClock').textContent = ($('clockLocal') || {}).textContent || '--:--:--'; }, 1000);

    setCols(state.cols);
  }

  /* ---------------------------------------------------------------- boot */
  function boot() {
    wire();

    if (!isServed()) {
      $('emptyState').hidden = false;
      document.querySelector('.empty-title').textContent = 'Run MultiWeb through its server';
      document.querySelector('.empty-sub').innerHTML =
        'MultiWeb needs the local server to discover folders and to frame them same-origin.<br>' +
        'Start it with <code>python3 serve.py</code> in the MultiWeb folder, then open the printed URL.';
      $('emptyPickBtn').style.display = 'none';
      return;
    }

    fetchFolders().then(function (data) {
      state.root = data.root;
      state.sites = data.sites;
      try { state.selected = JSON.parse(localStorage.getItem(LS_SEL) || '[]'); } catch (e) { state.selected = []; }
      // Drop any remembered folders that no longer exist.
      var names = data.sites.map(function (s) { return s.name; });
      state.selected = state.selected.filter(function (n) { return names.indexOf(n) >= 0; });
      // Keep only clones whose folder still exists and is still selected.
      state.clones = state.clones.filter(function (c) {
        return names.indexOf(c.name) >= 0 && state.selected.indexOf(c.name) >= 0;
      });
      localStorage.setItem(LS_CLONES, JSON.stringify(state.clones));

      renderWall();
      startTimer();

      // "Ask at server start": pop the picker so you choose the wall each launch.
      openPicker();
    }).catch(function (err) {
      $('emptyState').hidden = false;
      document.querySelector('.empty-title').textContent = 'Could not reach the discovery API';
      document.querySelector('.empty-sub').textContent =
        'The folder API did not respond (' + err.message + '). Make sure you opened this page via serve.py.';
      $('emptyPickBtn').style.display = 'none';
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
