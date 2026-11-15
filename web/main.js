// Marswalk Radar — tactical tablet viewer for a simulated EVA at Gale Crater.
// Two entities share the modelled route: a scout robot that leaves first and the crew that follows.
// Reads only data/processed/gale_radar/ (contract: web/README.md). No external requests.
import * as THREE from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { createView3D } from './view3d.js';

const PKG = '../data/processed/gale_radar/';
const RANGES = [250, 500, 1000];
const SPEEDS = [10, 30, 60, 300];
const VIEWS = ['radar', 'overview', '3d'];
const ROBOT_MIN_GAP_M = 500;   // the scout robot leaves early enough to stay at least this far ahead of the crew
const LOOKAHEAD_M = 12;        // heading = direction to the route point this far ahead
const AHEAD_WINDOW_M = 200;    // "max slope ahead" window
const HEADING_TAU_S = 0.5;     // low-pass on the heading (the grid route turns in 45 deg steps)

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, f) => a + (b - a) * f;
const fmtHM = (min) => {
  const m = Math.max(0, Math.floor(min + 1e-6));
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
const fmtInt = (v) => Math.round(v).toLocaleString('en-US').replace('-', '−');
const fmtSigned = (v, digits = 0) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(digits)}`;
const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`);

// ------------------------------------------------------------------ loading

async function getJSON(rel) {
  const url = PKG + rel;
  let res;
  try { res = await fetch(url); } catch (e) { throw new Error(`Could not fetch ${url} (${e.message}).`); }
  if (!res.ok) throw new Error(`Could not load ${url} (HTTP ${res.status}).`);
  try { return await res.json(); } catch (e) { throw new Error(`${url} is not valid JSON (${e.message}).`); }
}

function loadTexture(rel) {
  return new Promise((resolve, reject) => {
    new THREE.TextureLoader().load(PKG + rel, resolve, undefined,
      () => reject(new Error(`Could not load image ${PKG + rel}.`)));
  });
}

function fatal(err) {
  console.error(err);
  $('fatal-msg').textContent = err && err.message ? err.message : String(err);
  $('fatal').hidden = false;
}

// ------------------------------------------------------------------ main

async function start() {
  if (location.protocol === 'file:') {
    throw new Error('Opened from the file system: ES modules and the data package need an HTTP server.');
  }
  const site = await getJSON('site.json');
  const [route, track, stops, rad, weather] = await Promise.all([
    getJSON(site.files.route), getJSON(site.files.curiosity_track), getJSON(site.files.stops),
    getJSON(site.files.radiation), getJSON(site.files.weather),
  ]);
  const [texHill, texHazard, texReturn] = await Promise.all([
    loadTexture(site.layers.hillshade.file), loadTexture(site.layers.hazard.file),
    loadTexture(site.layers.return_time.file),
  ]);

  const grid = site.grids.terrain_2m;
  const rgrid = site.grids.routing_4m;
  const W = grid.width_m, H = grid.height_m;
  const scenario = site.scenario;
  const evaBudget = scenario.eva_duration_h * 60;
  const evaTotal = route.summary.eva_total_min;
  const [lh, lm] = scenario.start_lmst.split(':').map(Number);
  const startLmstMin = lh * 60 + lm;
  const slopeLimit = scenario.hazard_slope_deg;
  // Scout robot: constant speed along the route (assumption in the YAML). It leaves early enough to stay
  // ROBOT_MIN_GAP_M ahead of the crew on the whole route, rounded up to 10 min.
  const robotKmh = scenario.robot_speed_kmh ?? 1, robotV = (robotKmh * 1000) / 60;   // metres per minute
  const robotLead = Math.ceil(Math.max(...route.points.map((p) => (p.dist_m + ROBOT_MIN_GAP_M) / robotV - p.t_min)) / 10) * 10;
  const T0 = -robotLead;                                  // mission clock: the robot leaves at T0, the crew at 0
  const crewT = (t) => clamp(t, 0, evaTotal);             // route time of the crew at mission clock t

  // ---------------------------------------------------------------- route sampling
  const P = route.points;
  const N = P.length;
  const totalDist = P[N - 1].dist_m;

  function seg(key, v) {          // largest i with P[i][key] <= v
    let lo = 0, hi = N - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (P[mid][key] <= v) lo = mid; else hi = mid - 1; }
    return Math.min(lo, N - 2);
  }
  function sampleBy(key, v) {
    const i = seg(key, v), a = P[i], b = P[i + 1];
    const span = b[key] - a[key], f = span > 0 ? clamp((v - a[key]) / span, 0, 1) : 0;
    return { i, x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f), z: lerp(a.z, b.z, f), t: lerp(a.t_min, b.t_min, f),
      slope: lerp(a.slope_deg, b.slope_deg, f), dist: lerp(a.dist_m, b.dist_m, f) };
  }
  const sampleT = (t) => sampleBy('t_min', t);
  const sampleD = (d) => sampleBy('dist_m', d);
  const robotPos = (t) => sampleD(clamp((t - T0) * robotV, 0, totalDist));
  function headingAt(pos) {       // compass bearing in radians (0 = north, clockwise), or null
    let a = pos, b = sampleD(Math.min(totalDist, pos.dist + LOOKAHEAD_M));
    if (Math.hypot(b.x - a.x, b.y - a.y) < 1) { a = sampleD(Math.max(0, pos.dist - LOOKAHEAD_M)); b = pos; }
    const dx = b.x - a.x, dy = b.y - a.y;
    return Math.hypot(dx, dy) < 0.5 ? null : Math.atan2(dx, dy);
  }
  function maxSlopeAhead(pos) {
    let m = pos.slope;
    for (let i = pos.i + 1; i < N && P[i].dist_m <= pos.dist + AHEAD_WINDOW_M; i++) m = Math.max(m, P[i].slope_deg);
    return m;
  }

  // ---------------------------------------------------------------- return-time grid (CPU copy)
  const retW = rgrid.width_px, retH = rgrid.height_px;
  const ret = new Uint8Array(retW * retH);
  {
    const cv = document.createElement('canvas');
    cv.width = retW; cv.height = retH;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(texReturn.image, 0, 0);
    const px = ctx.getImageData(0, 0, retW, retH).data;
    for (let i = 0; i < ret.length; i++) ret[i] = px[i * 4];
  }
  function returnMinutes(x, y) {  // minutes to walk to the pickup, or null if not reachable
    const c = clamp(Math.floor(x / rgrid.px_x_m), 0, retW - 1);
    const r = clamp(Math.floor(retH - y / rgrid.px_y_m), 0, retH - 1);
    let v = ret[r * retW + c];
    if (v === 255) {              // route points sit on reachable cells; tolerate a 1-cell rounding miss
      for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
        const rr = clamp(r + dr, 0, retH - 1), cc = clamp(c + dc, 0, retW - 1);
        v = Math.min(v, ret[rr * retW + cc]);
      }
    }
    return v === 255 ? null : v * 2;
  }

  // ---------------------------------------------------------------- three.js scene (2D radar)
  const canvas = $('radar-canvas');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setClearColor(0x070A0E, 1);
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 2000);

  for (const t of [texHill, texHazard]) {
    t.colorSpace = THREE.NoColorSpace;
    t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  }
  texReturn.colorSpace = THREE.NoColorSpace;
  texReturn.magFilter = texReturn.minFilter = THREE.NearestFilter;
  texReturn.generateMipmaps = false;

  // One shader for the whole ground: tinted relief + red slope mask + amber return isochrone.
  const terrainMat = new THREE.ShaderMaterial({
    depthTest: false,
    uniforms: {
      tHill: { value: texHill }, tHazard: { value: texHazard }, tReturn: { value: texReturn },
      uRetSize: { value: new THREE.Vector2(retW, retH) },
      uLevel: { value: 1e6 },
    },
    vertexShader: /* glsl */`
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform sampler2D tHill, tHazard, tReturn;
      uniform vec2 uRetSize;
      uniform float uLevel;
      varying vec2 vUv;
      const vec3 RED = vec3(1.0, 0.231, 0.188);
      const vec3 AMBER = vec3(1.0, 0.702, 0.0);
      float ret(vec2 ij) { return texture2D(tReturn, (ij + 0.5) / uRetSize).r; }
      float differs(vec2 uv, float beyond) {              // 1 if a reachable cell at uv is on the other side of the level
        float r = texture2D(tReturn, uv).r;
        return r < 0.998 ? abs(step(uLevel, r * 510.0) - beyond) : 0.0;
      }
      void main() {
        float h = texture2D(tHill, vUv).r;
        float t = clamp((h - 0.2) / 0.8, 0.0, 1.0);
        vec3 col = mix(vec3(0.020, 0.030, 0.045), vec3(0.30, 0.37, 0.45), t * t);
        col = mix(col, RED, texture2D(tHazard, vUv).a * 0.7);

        // return time in minutes: bilinear between cell centres, never across unreachable (255) cells
        vec2 st = vUv * uRetSize - 0.5;
        vec2 i0 = floor(st);
        vec2 f = st - i0;
        float a = ret(i0), b = ret(i0 + vec2(1.0, 0.0)), c = ret(i0 + vec2(0.0, 1.0)), d = ret(i0 + vec2(1.0, 1.0));
        float nearest = texture2D(tReturn, vUv).r;
        float reach = nearest < 0.998 ? 1.0 : 0.0;
        float ok = max(max(a, b), max(c, d)) < 0.998 ? 1.0 : 0.0;
        float mb = mix(mix(a, b, f.x), mix(c, d, f.x), f.y) * 510.0;
        float m = ok > 0.5 ? mb : nearest * 510.0;
        float beyond = step(uLevel, m) * reach;
        col = mix(col, AMBER, beyond * 0.13);
        float clean = ok * reach * (1.0 - step(0.001, fwidth(ok) + fwidth(reach)));
        float cellsPerPx = max(fwidth(st.x), fwidth(st.y));
        vec2 du = dFdx(vUv) * 1.3, dv = dFdy(vUv) * 1.3;
        float thin = max(max(differs(vUv + du, beyond), differs(vUv - du, beyond)), max(differs(vUv + dv, beyond), differs(vUv - dv, beyond))) * reach;
        float wide = (1.0 - smoothstep(1.0, 2.2, abs(mb - uLevel) / max(fwidth(mb), 1e-4))) * step(cellsPerPx, 0.6);
        col = mix(col, AMBER, max(thin * step(0.6, cellsPerPx), wide * clean));
        gl_FragColor = vec4(col, 1.0);
      }`,
  });
  const terrain = new THREE.Mesh(new THREE.PlaneGeometry(W, H), terrainMat);
  terrain.position.set(W / 2, H / 2, 0);
  scene.add(terrain);

  const lineMats = [];
  function makeLine(xy, opts, order) {
    const pos = new Float32Array((xy.length / 2) * 3);
    for (let i = 0, j = 0; i < xy.length; i += 2, j += 3) { pos[j] = xy[i]; pos[j + 1] = xy[i + 1]; pos[j + 2] = 1; }
    const geo = new LineGeometry();
    geo.setPositions(pos);
    const mat = new LineMaterial({ worldUnits: false, depthTest: false, transparent: true, ...opts });
    mat.userData.px = opts.linewidth;
    lineMats.push(mat);
    const line = new Line2(geo, mat);
    if (opts.dashed) line.computeLineDistances();
    line.frustumCulled = false;
    line.renderOrder = order;
    scene.add(line);
    return line;
  }

  const trackLines = [];        // Curiosity historical track: white, dashed, 60 %
  {
    let cur = [];
    const flush = () => { if (cur.length >= 4) trackLines.push(makeLine(cur, { color: 0xffffff, opacity: 0.6, linewidth: 1.6, dashed: true }, 1)); cur = []; };
    for (const p of track.points) { if (p.break_before) flush(); cur.push(p.x, p.y); }
    flush();
  }
  // Modelled route: full line dimmed (already walked by the crew) + bright line for what is left.
  const routeXY = []; for (const p of P) routeXY.push(p.x, p.y);
  const routeRev = []; for (let i = N - 1; i >= 0; i--) routeRev.push(P[i].x, P[i].y);
  makeLine(routeXY, { color: 0x34C759, opacity: 0.35, linewidth: 3 }, 2);
  const routeAhead = makeLine(routeRev, { color: 0x34C759, opacity: 1, linewidth: 4 }, 3);

  const crewMark = new THREE.Group();   // crew marker: white arrow, constant size on screen
  {
    const tri = (s) => { const sh = new THREE.Shape(); sh.moveTo(0, 11 * s); sh.lineTo(7.5 * s, -8 * s); sh.lineTo(0, -4 * s); sh.lineTo(-7.5 * s, -8 * s); sh.closePath(); return new THREE.ShapeGeometry(sh); };
    const outline = new THREE.Mesh(tri(1.45), new THREE.MeshBasicMaterial({ color: 0x070A0E, depthTest: false }));
    const body = new THREE.Mesh(tri(1), new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false }));
    outline.renderOrder = 5; body.renderOrder = 6;
    crewMark.add(outline, body);
    crewMark.position.z = 2;
    scene.add(crewMark);
  }

  // ---------------------------------------------------------------- state
  const q = new URLSearchParams(location.search);
  const qNum = (k, d) => (q.has(k) && Number.isFinite(Number(q.get(k))) ? Number(q.get(k)) : d);
  const state = {
    t: clamp(qNum('t', T0), T0, evaTotal),
    playing: q.get('play') !== '0',
    speed: SPEEDS.includes(qNum('speed', 60)) ? qNum('speed', 60) : 60,
    range: RANGES.includes(qNum('range', 250)) ? qNum('range', 250) : 250,
    view: VIEWS.includes(q.get('view')) ? q.get('view') : 'radar',
    heading: null,
    snapHeading: true,
    w: 1, h: 1,
    sel: null,                  // stop shown in the pop-up
  };

  // ---------------------------------------------------------------- markers (HTML over the canvas)
  const routeStops = stops.filter((s) => s.order !== null && s.order !== undefined).sort((a, b) => a.order - b.order);
  function stopClass(s, t) {    // t = crew route time
    if (s.role === 'context') return 'context';
    if (s.role === 'pickup') return 'pickup';
    return t >= s.arrive_t_min ? 'visited' : 'next';
  }
  function nextStop(t) {        // first stop the crew has not left yet
    for (const s of routeStops) {
      if (s.role === 'start') continue;
      if (s.depart_t_min === undefined || t < s.depart_t_min) return s;
    }
    return routeStops[routeStops.length - 1];
  }
  function makeMarker(name, onClick) {
    const el = document.createElement('div');
    el.className = 'mk';
    el.innerHTML = `<div class="glyph"></div><div class="lbl">${esc(name)}<span class="d"></span></div>`;
    $('markers').appendChild(el);
    const edge = document.createElement('div');
    edge.className = 'edge';
    edge.innerHTML = '<div class="tip"></div>';
    const elbl = document.createElement('div');
    elbl.className = 'elbl';
    edge.hidden = elbl.hidden = true;
    $('edge-arrows').append(edge, elbl);
    if (onClick) { el.addEventListener('click', onClick); edge.addEventListener('click', onClick); }
    return { el, d: el.querySelector('.d'), edge, elbl, name, sx: 0, sy: 0 };
  }
  const stopMk = stops.map((s) => {
    const m = makeMarker(s.name, (e) => { e.stopPropagation(); state.sel = state.sel === s ? null : s; selChanged(); });
    m.s = s;
    return m;
  });
  const robotMk = makeMarker('Robot', null);

  // ---------------------------------------------------------------- static UI
  $('lg-slope').textContent = slopeLimit;
  {
    const NS = 'http://www.w3.org/2000/svg', svg = $('rings');
    for (const r of [25, 50, 75, 100]) { const c = document.createElementNS(NS, 'circle'); c.setAttribute('r', r); svg.appendChild(c); }
    for (const [x1, y1, x2, y2] of [[-100, 0, -6, 0], [6, 0, 100, 0], [0, -100, 0, -6], [0, 6, 0, 100]]) {
      const l = document.createElementNS(NS, 'line');
      l.setAttribute('x1', x1); l.setAttribute('y1', y1); l.setAttribute('x2', x2); l.setAttribute('y2', y2); svg.appendChild(l);
    }
    for (const r of [25, 50, 75, 100]) {
      const tx = document.createElementNS(NS, 'text'), k = Math.SQRT1_2 * r;
      tx.setAttribute('x', k + 1.5); tx.setAttribute('y', k + 5); tx.dataset.frac = r / 100; svg.appendChild(tx);
    }
  }
  function updateRingLabels() {
    for (const tx of $('rings').querySelectorAll('text')) tx.textContent = fmtDist(state.range * Number(tx.dataset.frac)).replace('.00', '').replace(/(\.\d)0 km/, '$1 km');
  }

  // timeline: mission clock from the robot's departure (T0) to the crew's arrival at the pickup
  const tlFrac = (t) => ((t - T0) / (evaTotal - T0)) * 100;
  {
    const marks = $('tl-marks');
    const lab0 = document.createElement('div');
    lab0.className = 'tl-mark robot'; lab0.style.left = '0%'; lab0.textContent = 'ROBOT';
    marks.append(lab0);
    routeStops.forEach((s, k) => {
      const dep = s.depart_t_min !== undefined ? s.depart_t_min : s.arrive_t_min;
      const bar = document.createElement('div');
      bar.className = 'tl-dwell'; bar.dataset.k = k;
      bar.style.left = `${tlFrac(s.arrive_t_min)}%`; bar.style.width = `max(3px, ${tlFrac(dep) - tlFrac(s.arrive_t_min)}%)`;
      if (s.role === 'pickup') bar.style.transform = 'translateX(-100%)';
      const lab = document.createElement('div');
      lab.className = 'tl-mark' + (k === routeStops.length - 1 ? ' last' : ''); lab.dataset.k = k;
      lab.style.left = `${tlFrac((s.arrive_t_min + dep) / 2)}%`;
      lab.textContent = s.role === 'start' ? 'CREW' : s.role === 'pickup' ? 'PICKUP' : `${s.order}`;
      lab.title = bar.title = s.name;
      marks.append(bar, lab);
    });
    $('tl-input').min = T0; $('tl-input').max = evaTotal;
  }

  // info panel
  {
    const s = route.summary, v = route.validation;
    const li = (arr) => `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
    const row = (k, val, kind) => `<tr><td>${esc(k)}</td><td class="num">${esc(val)}</td><td class="fine">${esc(kind)}</td></tr>`;
    $('info-body').innerHTML = `
      <h3>Scenario</h3>
      <table>
        ${row('Route', `${scenario.start} → ${scenario.stops.length} science stops → ${scenario.pickup}`, 'assumed')}
        ${row('Scout robot', `${robotKmh} km/h on the same route; leaves ${robotLead} min before the crew`, 'assumed')}
        ${row('Simulation panel (3D)', 'conditions set by hand to test the alerts; thresholds are illustrative', 'simulated')}
        ${row('Radiation statistics (3D)', `${rad.climatology.label} ${rad.climatology.hour_axis}`, 'measured')}
        ${row('Crew', `${scenario.crew} on foot`, 'assumed')}
        ${row('Base walking speed', `${scenario.base_speed_kmh} km/h on flat ground`, 'assumed')}
        ${row('Time at each science stop', `${scenario.dwell_min_per_stop} min`, 'assumed')}
        ${row('EVA budget · crew start', `${scenario.eva_duration_h} h · ${scenario.start_lmst} LMST`, 'assumed')}
        ${row('Slope limit', `${scenario.hazard_slope_deg}°`, 'assumed')}
      </table>
      <h3>Modelled route</h3>
      <table>
        ${row('Distance', `${fmtDist(s.distance_m)} (straight line ${fmtDist(s.straight_line_m)})`, 'derived')}
        ${row('Estimated time', `${s.walking_time_min} min walking + ${s.dwell_time_min} min at stops = ${s.eva_total_min} of ${s.eva_budget_min} min`, 'derived')}
        ${row('Smallest return margin', `${s.min_return_margin_min} min`, 'derived')}
        ${row('Elevation gain / loss', `+${Math.round(s.elevation_gain_m)} m / −${Math.round(s.elevation_loss_m)} m`, 'derived')}
        ${row('Steepest slope on the route', `${s.max_slope_deg}° (limit ${scenario.hazard_slope_deg}°)`, 'derived')}
        ${row('Within ' + v.curiosity_corridor_m + ' m of Curiosity\'s track', `${Math.round(v.share_within_corridor * 100)} %`, 'derived')}
      </table>
      <h3>Assumptions</h3>${li(site.assumptions)}
      <h3>Warnings</h3>${li(site.warnings)}
      <h3>Sources</h3>
      <table>${site.sources.map((x) => `<tr><td>${esc(x.data)}</td><td>${esc(x.provider)}</td><td class="num">${esc(x.downloaded)}</td></tr>`).join('')}</table>`;
  }

  // modals
  for (const m of document.querySelectorAll('.modal')) {
    m.addEventListener('click', (e) => { if (e.target === m || e.target.closest('[data-close]')) m.hidden = true; });
  }
  $('btn-info').addEventListener('click', () => { $('info-modal').hidden = false; });
  if (q.get('panel') === 'info') $('info-modal').hidden = false;
  const photo = { stop: null, k: 0 };
  function showPhoto() {
    const s = photo.stop, p = s.photos[photo.k];
    $('ph-title').textContent = s.name;
    $('ph-img').src = PKG + p.file;
    $('ph-img').alt = `Curiosity Navcam image near ${s.name}, sol ${p.sol}`;
    $('ph-caption').textContent = `Curiosity Navcam · sol ${p.sol} · ${p.date_utc} · ${photo.k + 1}/${s.photos.length} · ${p.credit}`;
    $('ph-prev').disabled = $('ph-next').disabled = s.photos.length < 2;
  }
  $('sp-photo-btn').addEventListener('click', () => { if (state.sel && state.sel.photos.length) { photo.stop = state.sel; photo.k = 0; showPhoto(); $('photo-modal').hidden = false; } });
  $('ph-prev').addEventListener('click', () => { const n = photo.stop.photos.length; photo.k = (photo.k + n - 1) % n; showPhoto(); });
  $('ph-next').addEventListener('click', () => { photo.k = (photo.k + 1) % photo.stop.photos.length; showPhoto(); });


  // ---------------------------------------------------------------- simulated conditions (3D view)
  // Hand-set values that trigger spoken alerts. Nothing here is sensor data. The radiation statistics are
  // historical: hourly means of this calendar day over every year of RAD data, in the hours around the clock.
  const clim = rad.climatology;
  const today = new Date();
  const dayKey = `${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const dayLabel = today.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const h2 = (h) => String(((h % 24) + 24) % 24).padStart(2, '0');
  function radStats(hour) {     // hours h−1 … h+1 of this calendar day, all years
    const d = clim.by_day[dayKey], h0 = Math.floor(hour);
    let sum = 0, n = 0, lo = Infinity, hi = -Infinity;
    if (d) for (let k = -1; k <= 1; k++) {
      const h = (((h0 + k) % 24) + 24) % 24;
      if (d.n[h]) { sum += d.mean[h] * d.n[h]; n += d.n[h]; lo = Math.min(lo, d.min[h]); hi = Math.max(hi, d.max[h]); }
    }
    return { avg: n ? sum / n : null, min: n ? lo : null, max: n ? hi : null, label: `${dayLabel} · ${h2(h0 - 1)}–${h2(h0 + 2)} h · ${clim.years[0]}–${clim.years[1]}` };
  }
  const pAll = weather.sols.flatMap((s) => s.hourly.map((h) => h.pressure_pa)).filter((v) => Number.isFinite(v));
  const pLo = Math.min(...pAll), pHi = Math.max(...pAll);
  const hours = (s) => { const [a, b] = s.split(':').map(Number); return a + b / 60; };
  const w0 = hours(weather.eva_window_lmst.start), w1 = hours(weather.eva_window_lmst.end);
  const RLO = clim.hourly_range.min, RHI = clim.hourly_range.max;
  const COND = [                // thresholds are assumptions of the viewer, except where a source is named
    { id: 'o2', label: 'Oxygen', unit: '%', min: 0, max: 100, step: 1, def: 85, bad: (v) => v < 20, say: 'Oxygen low', fmt: (v) => v.toFixed(0), spoken: (v) => `${v.toFixed(0)} percent` },
    { id: 'co2', label: 'CO₂', unit: 'mmHg', min: 0, max: 15, step: 0.1, def: 1, bad: (v) => v > 7.6, say: 'Carbon dioxide high', fmt: (v) => v.toFixed(1), spoken: (v) => `${v.toFixed(1)} millimetres of mercury` },
    { id: 'suit', label: 'Suit pressure', unit: 'kPa', min: 0, max: 40, step: 0.1, def: 29.6, bad: (v) => v < 25, say: 'Suit pressure low', fmt: (v) => v.toFixed(1), spoken: (v) => `${v.toFixed(1)} kilopascals` },
    // alert outside the range REMS measured in the sols of the package
    { id: 'atm', label: 'Atmospheric pressure', unit: 'Pa', min: 550, max: 1050, step: 5, def: Math.round((pLo + pHi) / 10) * 5, bad: (v) => v < pLo || v > pHi, say: 'Atmospheric pressure out of range', fmt: (v) => v.toFixed(0), spoken: (v) => `${v.toFixed(0)} pascals` },
    // slider on a log scale between the lowest and highest hourly mean RAD ever measured; alert = package threshold
    { id: 'rad', label: 'Radiation', unit: 'µGy/day', min: 0, max: 1000, step: 1, auto: true, toV: (x) => RLO * Math.pow(RHI / RLO, x / 1000), toX: (v) => (1000 * Math.log(clamp(v, RLO, RHI) / RLO)) / Math.log(RHI / RLO), bad: (v) => v > rad.alert.threshold, say: 'Radiation high', fmt: (v) => fmtInt(v), spoken: (v) => `${Math.round(v)} micrograys per day` },
    { id: 'tod', label: 'Time of day', unit: 'LMST', min: 0, max: 23.75, step: 0.25, auto: true, bad: (v, manual) => (manual || state.t >= 0) && (v < w0 || v >= w1), say: 'Outside the EVA window', fmt: (v) => fmtHM(v * 60), spoken: (v) => `${Math.floor(v)} ${String(Math.round((v % 1) * 60)).padStart(2, '0')} local time` },
  ];
  const sim = { manual: {}, sound: true, active: new Set(), audio: null };
  const simVal = (c, auto) => (sim.manual[c.id] !== undefined ? sim.manual[c.id] : c.auto ? auto[c.id] : c.def);
  for (const c of COND) {
    const row = document.createElement('label');
    row.className = 'sim-row';
    row.innerHTML = `<span class="nm">${esc(c.label)}</span><span class="val num"></span><input type="range" min="${c.min}" max="${c.max}" step="${c.step}">`;
    $('sim-rows').appendChild(row);
    c.row = row; c.val = row.querySelector('.val'); c.input = row.querySelector('input');
    c.input.addEventListener('input', () => { const x = Number(c.input.value); sim.manual[c.id] = c.toV ? c.toV(x) : x; updateSim(); });
  }
  $('sim-toggle').addEventListener('click', () => {
    const open = $('sim-panel').hidden;
    $('sim-panel').hidden = !open;
    $('sim-toggle').setAttribute('aria-expanded', String(open));
  });
  $('sim-reset').addEventListener('click', () => { sim.manual = {}; updateSim(); });
  $('sim-sound').addEventListener('change', (e) => { sim.sound = e.target.checked; if (!sim.sound && window.speechSynthesis) speechSynthesis.cancel(); });
  if (window.speechSynthesis) speechSynthesis.getVoices();   // starts loading the voice list

  function announce(text) {     // short chime, then a spoken message with a voice installed on the device
    if (!sim.sound) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        sim.audio = sim.audio || new AC();
        const ac = sim.audio, t0 = ac.currentTime;
        [880, 660].forEach((f, i) => {
          const o = ac.createOscillator(), g = ac.createGain();
          o.frequency.value = f; o.connect(g); g.connect(ac.destination);
          g.gain.setValueAtTime(0.0001, t0 + i * 0.16);
          g.gain.exponentialRampToValueAtTime(0.25, t0 + i * 0.16 + 0.02);
          g.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.16 + 0.15);
          o.start(t0 + i * 0.16); o.stop(t0 + i * 0.16 + 0.16);
        });
      }
      if (window.speechSynthesis) {
        const voice = speechSynthesis.getVoices().find((v) => v.localService && v.lang.startsWith('en'));
        if (voice) {            // only on-device voices: no request leaves the page
          const u = new SpeechSynthesisUtterance(text);
          u.voice = voice; u.lang = voice.lang; u.rate = 1;
          speechSynthesis.cancel();
          speechSynthesis.speak(u);
        }
      }
    } catch (e) { console.warn(e); }
  }

  function updateSim() {
    const clock = (((startLmstMin + state.t) / 60) % 24 + 24) % 24;
    const tod = sim.manual.tod !== undefined ? sim.manual.tod : clock;
    const st = radStats(tod);
    const auto = { rad: st.avg ?? rad.reference.value, tod: clock };
    const now = new Set(), chips = [], fresh = [];
    for (const c of COND) {
      const manual = sim.manual[c.id] !== undefined, v = simVal(c, auto), bad = c.bad(v, manual);
      c.val.textContent = `${c.fmt(v)} ${c.unit}${c.auto && !manual ? ' · auto' : ''}`;
      if (document.activeElement !== c.input) c.input.value = c.toX ? c.toX(v) : v;
      c.row.classList.toggle('bad', bad);
      if (bad) {
        now.add(c.id); chips.push(`<span class="chip-alert">${esc(c.say.toUpperCase())} · <span class="num">${c.fmt(v)} ${c.unit}</span></span>`);
        if (!sim.active.has(c.id)) fresh.push(`${c.say}. ${c.spoken(v)}.`);
      }
    }
    sim.active = now;
    $('v3-alerts').innerHTML = chips.join('');
    if (fresh.length) announce(`Warning. ${fresh.join(' ')}`);
    const radNow = simVal(COND.find((c) => c.id === 'rad'), auto), n = (v) => (v === null ? '–' : fmtInt(v));
    $('rad-now').textContent = n(radNow);
    $('rad-now').parentElement.classList.toggle('over', radNow > rad.alert.threshold);
    $('rad-avg').textContent = n(st.avg); $('rad-min').textContent = n(st.min); $('rad-max').textContent = n(st.max);
    $('rad-win').textContent = st.label;
  }

  // stop pop-up (one per stop, opened from its marker)
  function selChanged() {
    const s = state.sel;
    $('stop-pop').hidden = !s;
    if (s) {
      const p = s.photos[0];
      $('sp-name').textContent = s.role === 'science' ? `${s.order} · ${s.name}` : s.name;
      $('sp-photo-btn').hidden = !p;
      $('sp-photo-btn').parentElement.classList.toggle('no-photo', !p);
      if (p) { $('sp-photo').src = PKG + p.file; $('sp-photo').alt = `Curiosity Navcam image near ${s.name}`; $('sp-credit').textContent = p.credit; }
    }
    refresh();
  }
  $('sp-close').addEventListener('click', () => { state.sel = null; selChanged(); });
  $('stop-pop').addEventListener('click', (e) => e.stopPropagation());
  $('stage').addEventListener('click', () => { if (state.sel) { state.sel = null; selChanged(); } });

  // controls
  function syncControls() {
    for (const b of $('seg-range').children) { b.classList.toggle('on', Number(b.dataset.range) === state.range); b.disabled = state.view === 'overview'; }
    for (const b of $('seg-view').children) b.classList.toggle('on', b.dataset.view === state.view);
    for (const b of $('seg-speed').children) b.classList.toggle('on', Number(b.dataset.speed) === state.speed);
    $('btn-play').textContent = state.playing ? '❚❚' : '▶';
    $('app').classList.toggle('overview', state.view === 'overview');
    $('app').classList.toggle('v3d', state.view === '3d');
    $('view3d').hidden = state.view !== '3d';
    updateRingLabels();
  }
  let view3d = null, view3dLoading = false;   // built on first use (it loads the elevation grid and the HiRISE image)
  function ensure3D() {
    if (view3d || view3dLoading) return;
    view3dLoading = true;
    createView3D({
      PKG, site, route, stops, state,
      images: { hill: texHill.image, hazard: texHazard.image },
      fn: { sampleT, sampleD, headingAt, stopClass, crewT, robotPos },
    }).then((v) => { view3d = v; view3dLoading = false; if (state.view === '3d') v.enter(); })
      .catch((err) => { view3dLoading = false; console.error(err); $('v3-loading').hidden = false; $('v3-loading').textContent = `The 3D view could not start: ${err && err.message ? err.message : err}`; });
  }
  $('seg-range').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { state.range = Number(b.dataset.range); syncControls(); refresh(); } });
  $('seg-view').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    state.view = b.dataset.view;
    if (state.view === '3d') ensure3D();
    syncControls(); layout(); refresh();
    if (state.view === '3d' && view3d) view3d.enter();
  });
  $('seg-speed').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { state.speed = Number(b.dataset.speed); syncControls(); } });
  function togglePlay() {
    if (!state.playing && state.t >= evaTotal) { state.t = T0; state.snapHeading = true; }
    state.playing = !state.playing; syncControls();
  }
  $('btn-play').addEventListener('click', togglePlay);
  $('tl-input').addEventListener('input', (e) => { state.t = clamp(Number(e.target.value), T0, evaTotal); state.snapHeading = true; refresh(); });
  $('chk-rover').addEventListener('change', (e) => { for (const l of trackLines) l.visible = e.target.checked; refresh(); });
  window.addEventListener('keydown', (e) => {
    if (e.target.closest('select, input[type="checkbox"]')) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    if (e.code === 'Escape') { for (const m of document.querySelectorAll('.modal')) m.hidden = true; if (state.sel) { state.sel = null; selChanged(); } }
  });

  // ---------------------------------------------------------------- layout
  function layout() {
    const area = $('radar-area');
    state.w = Math.max(1, area.clientWidth); state.h = Math.max(1, area.clientHeight);
    const pr = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(pr);
    renderer.setSize(state.w, state.h, false);
    for (const m of lineMats) m.linewidth = m.userData.px * pr;
    $('rings').style.width = $('rings').style.height = `${state.h}px`;   // outer ring = range = half the height
    if (view3d) view3d.resize();
  }
  window.addEventListener('resize', () => { layout(); refresh(); });

  // ---------------------------------------------------------------- per-frame update
  const v3 = new THREE.Vector3();
  const EDGE_INSET = 20;
  function place(m, x, y, cls, dist, overview, edgeLabel, selected) {   // marker on the map, or an arrow on the screen edge
    v3.set(x, y, 0).project(camera);
    const sx = (v3.x * 0.5 + 0.5) * state.w, sy = (-v3.y * 0.5 + 0.5) * state.h;
    const inside = sx >= 10 && sx <= state.w - 10 && sy >= 10 && sy <= state.h - 10;
    m.el.className = `mk ${cls}${selected ? ' sel' : ''}`;
    m.el.hidden = !inside;
    m.edge.hidden = inside || cls === 'context' || cls === 'visited';
    m.elbl.hidden = m.edge.hidden || !edgeLabel;
    m.sx = clamp(sx, EDGE_INSET, state.w - EDGE_INSET); m.sy = clamp(sy, EDGE_INSET, state.h - EDGE_INSET);
    const dTxt = dist < 3 ? '' : fmtDist(dist);
    if (inside) {
      m.el.style.transform = `translate(${sx.toFixed(1)}px, ${sy.toFixed(1)}px)`;
      m.d.textContent = overview ? '' : dTxt;
    } else if (!m.edge.hidden) {
      const cx = state.w / 2, cy = state.h / 2, dx = sx - cx, dy = sy - cy;
      const k = Math.min((cx - EDGE_INSET) / Math.max(1e-6, Math.abs(dx)), (cy - EDGE_INSET) / Math.max(1e-6, Math.abs(dy)));
      const ex = cx + dx * k, ey = cy + dy * k, len = Math.hypot(dx, dy);
      m.sx = ex; m.sy = ey;
      m.edge.className = `edge ${cls}`;
      m.edge.style.transform = `translate(${ex.toFixed(1)}px, ${ey.toFixed(1)}px) rotate(${Math.atan2(dx, -dy)}rad)`;
      if (!m.elbl.hidden) {
        m.elbl.className = `elbl ${cls}`;
        m.elbl.style.left = `${clamp(ex - (dx / len) * 30, 90, state.w - 90)}px`; m.elbl.style.top = `${clamp(ey - (dy / len) * 30, 24, state.h - 24)}px`;
        m.elbl.textContent = `${m.name} · ${dTxt}`;
      }
    }
  }

  function refresh() {
    const t = state.t, tc = crewT(t);
    const pos = sampleT(tc), rob = robotPos(t);
    const remaining = evaBudget - tc;
    const overview = state.view === 'overview';

    const target = headingAt(pos);
    if (state.heading === null || state.snapHeading) { if (target !== null) state.heading = target; state.snapHeading = false; }
    const hdg = state.heading ?? 0;

    // header + timeline (all views)
    $('hdr-lmst').textContent = fmtHM(startLmstMin + t);
    $('hdr-eva').textContent = t < 0 ? `T−${fmtHM(Math.ceil(-t))}` : `${fmtHM(tc)} / ${fmtHM(evaBudget)}`;
    $('tl-fill').style.left = `${tlFrac(0)}%`; $('tl-fill').style.width = `${tlFrac(tc) - tlFrac(0)}%`;
    $('tl-thumb').style.left = `${tlFrac(t)}%`;
    $('tl-robot').style.left = `${tlFrac(rob.t)}%`;
    if (document.activeElement !== $('tl-input')) $('tl-input').value = t;
    $('tl-time').textContent = `${t < 0 ? '−' + fmtHM(Math.ceil(-t)) : fmtHM(tc)} / ${fmtHM(evaTotal)}`;
    for (const el of $('tl-marks').children) {
      if (el.dataset.k === undefined) continue;
      el.classList.remove('next', 'visited', 'pickup'); el.classList.add(stopClass(routeStops[Number(el.dataset.k)], tc));
    }
    // cards (same in every view)
    const ahead = maxSlopeAhead(pos);
    $('tr-slope').innerHTML = `${pos.slope.toFixed(1)}<small>°</small>`;
    $('tr-ahead').innerHTML = `${ahead.toFixed(1)}<small>°</small>`;
    $('tr-elev').innerHTML = `${fmtInt(pos.z)}<small>m</small>`;
    $('tr-bar').style.width = `${clamp(pos.slope / slopeLimit, 0, 1) * 100}%`;
    $('tr-bar-ahead').style.left = `calc(${clamp(ahead / slopeLimit, 0, 1) * 100}% - 2px)`;
    const back = returnMinutes(pos.x, pos.y);
    const margin = back === null ? null : remaining - back;
    $('wn-elapsed').textContent = fmtHM(tc);
    $('wn-remaining').textContent = fmtHM(remaining);
    $('wn-margin').innerHTML = margin === null ? '–' : `${fmtSigned(Math.floor(margin)).replace('+', '')}<small>min</small>`;
    $('wn-margin').parentElement.classList.toggle('neg', margin !== null && margin < 0);

    updateSim();
    if (state.view === '3d') return;

    // camera: crew at the centre and heading up (radar), or the whole area north up (overview)
    const half = overview ? (H / 2) * 1.03 : state.range;
    const aspect = state.w / state.h, mpp = (2 * half) / state.h;
    camera.left = -half * aspect; camera.right = half * aspect; camera.top = half; camera.bottom = -half;
    if (overview) { camera.up.set(0, 1, 0); camera.position.set(W / 2, H / 2, 1000); camera.lookAt(W / 2, H / 2, 0); }
    else { camera.up.set(Math.sin(hdg), Math.cos(hdg), 0); camera.position.set(pos.x, pos.y, 1000); camera.lookAt(pos.x, pos.y, 0); }
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();

    crewMark.position.x = pos.x; crewMark.position.y = pos.y;
    crewMark.rotation.z = -hdg;
    crewMark.scale.setScalar(mpp);
    routeAhead.geometry.instanceCount = N - 1 - pos.i;
    for (const l of trackLines) { l.material.dashSize = 7 * mpp; l.material.gapSize = 6 * mpp; }
    terrainMat.uniforms.uLevel.value = 2 * Math.floor(remaining / 2) + 1;   // odd level: between two even cell values
    renderer.render(scene, camera);
    $('north-needle').style.transform = `rotate(${overview ? 0 : -hdg}rad)`;

    // markers
    const nxt = nextStop(tc);
    for (const m of stopMk) {
      const s = m.s;
      place(m, s.x, s.y, stopClass(s, tc), Math.hypot(s.x - pos.x, s.y - pos.y), overview, s === nxt, s === state.sel);
    }
    place(robotMk, rob.x, rob.y, 'robot', Math.hypot(rob.x - pos.x, rob.y - pos.y), overview, true, false);

    // stop pop-up
    const s = state.sel;
    if (s) {
      const m = stopMk.find((x) => x.s === s), pop = $('stop-pop'), cls = stopClass(s, tc);
      pop.className = `only-2d ${cls}`;
      pop.style.transform = `translate(${clamp(m.sx + 18, 8, state.w - 276).toFixed(0)}px, ${clamp(m.sy - 24, 8, state.h - 190).toFixed(0)}px)`;
      const set = (k1, v1, k2, v2) => { $('sp-k1').textContent = k1; $('sp-v1').innerHTML = v1; $('sp-k2').textContent = k2; $('sp-v2').innerHTML = v2; };
      const unit = (v, u) => `${v}<small>${u}</small>`;
      if (s.role === 'context') set('Straight line', unit(fmtInt(Math.hypot(s.x - pos.x, s.y - pos.y)), 'm'), 'Route', 'off route');
      else if (tc < s.arrive_t_min) set('By route', unit(fmtInt(s.dist_m - pos.dist), 'm'), 'ETA', unit(Math.ceil(s.arrive_t_min - tc), 'min'));
      else if (s.depart_t_min !== undefined && tc < s.depart_t_min) set('Status', 'on site', 'Departs in', unit(Math.ceil(s.depart_t_min - tc), 'min'));
      else if (s.role === 'pickup' || s.role === 'start') set('Status', s.role === 'start' ? 'start' : 'arrived', 'LMST', fmtHM(startLmstMin + s.arrive_t_min));
      else set('Status', 'visited', 'Left at', unit(fmtHM(startLmstMin + s.depart_t_min), 'LMST'));
      $('sp-v3').innerHTML = unit(fmtSigned(s.z - pos.z), 'm');
    }
  }

  // ---------------------------------------------------------------- loop
  let lastNow = performance.now(), lastRefresh = 0;
  function frame(now) {
    const dt = Math.min(0.25, (now - lastNow) / 1000);
    lastNow = now;
    let dirty = false;
    if (state.playing) {
      state.t = Math.min(evaTotal, state.t + (dt * state.speed) / 60);
      if (state.t >= evaTotal) { state.playing = false; syncControls(); }
      dirty = true;
    }
    if (state.heading !== null) {                         // ease the heading towards the route direction
      const target = headingAt(sampleT(crewT(state.t)));
      if (target !== null) {
        let d = target - state.heading;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        if (Math.abs(d) > 1e-3) { state.heading += d * (1 - Math.exp(-dt / HEADING_TAU_S)); dirty = true; }
      }
    }
    // in the 3D view the scene follows the clock every frame; the header and the timeline go at ~10 Hz
    if (dirty && (state.view !== '3d' || now - lastRefresh > 95)) { refresh(); lastRefresh = now; }
    if (state.view === '3d' && view3d) view3d.frame(dt);
    requestAnimationFrame(frame);
  }

  syncControls();
  layout();
  refresh();
  if (state.view === '3d') ensure3D();
  document.documentElement.dataset.ready = '1';
  requestAnimationFrame(frame);
}

window.addEventListener('error', (e) => { if (!document.documentElement.dataset.ready) fatal(e.error || new Error(e.message)); });
start().catch(fatal);
