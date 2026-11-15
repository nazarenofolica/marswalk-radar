// Marswalk Radar: first-person 3D view. The camera sits at the eye height of the lead crew member and walks
// the modelled route over the HiRISE DEM: terrain, route, stop beacons, the second crew member and the scout
// robot ahead. The visor frame, the cards and the simulation panel are HTML (index.html, main.js). Reads only data/processed/gale_radar/. No external requests.
import * as THREE from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

const TILE = 64;                    // terrain tile size in grid cells (frustum culling unit)
const LOD_STRIDES = [1, 2, 4, 8];   // a tile is drawn with every 1, 2, 4 or 8 grid cells (4 m to 32 m)…
const LOD_DIST = [650, 1400, 2800]; // …depending on its distance to the camera, in metres
const SKIRT_M = 10;                 // depth of the tile skirts that hide seams between detail levels
const EYE_M = 1.7;                  // camera height above the ground
const PITCH0 = -0.05;               // resting look direction: slightly below the horizon
const COLORS = { next: 0x00E5FF, visited: 0xB388FF, pickup: 0xFFFFFF, context: 0x7C8794, robot: 0x4DA3FF };

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, f) => a + (b - a) * f;
const wrapPi = (a) => Math.atan2(Math.sin(a), Math.cos(a));
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Could not load image ${url}.`));
    img.src = url;
  });
}

export async function createView3D(ctx) {
  const { PKG, site, route, stops, images, state, fn } = ctx;
  const root = $('view3d'), canvas = $('v3-canvas');

  // ---------------------------------------------------------------- data
  const grid = site.grids.terrain_2m, rg = site.grids.routing_4m;
  const W = grid.width_m, H = grid.height_m;
  const gw = rg.width_px, gh = rg.height_px, cx = rg.px_x_m, cy = rg.px_y_m;
  const enc = site.layers.elevation.encoding;
  const elevUrl = PKG + site.layers.elevation.file;
  const [elevRes, photoImg] = await Promise.all([fetch(elevUrl), loadImage(PKG + site.layers.hirise.file)]);
  if (!elevRes.ok) throw new Error(`Could not load ${elevUrl} (HTTP ${elevRes.status}).`);
  const raw = new Uint16Array(await elevRes.arrayBuffer());
  if (raw.length !== gw * gh) throw new Error(`${elevUrl} has ${raw.length} cells, site.json says ${gw * gh}.`);
  const zBase = enc.min;                                   // world z = elevation − zBase (keeps float32 precise)
  const elev = new Float32Array(gw * gh);
  for (let i = 0; i < elev.length; i++) elev[i] = enc.offset + enc.scale * raw[i] - zBase;

  function zAt(x, y) {                                     // bilinear between cell centres, metres above zBase
    const fc = clamp(x / cx - 0.5, 0, gw - 1.001), fr = clamp(gh - y / cy - 0.5, 0, gh - 1.001);
    const c = Math.floor(fc), r = Math.floor(fr), u = fc - c, v = fr - r, i = r * gw + c;
    return lerp(lerp(elev[i], elev[i + 1], u), lerp(elev[i + gw], elev[i + gw + 1], u), v);
  }
  function normalAt(x, y, out) {
    const e = 4;
    return out.set(-(zAt(x + e, y) - zAt(x - e, y)) / (2 * e), -(zAt(x, y + e) - zAt(x, y - e)) / (2 * e), 1).normalize();
  }

  const P = route.points, N = P.length, totalDist = P[N - 1].dist_m;

  // ---------------------------------------------------------------- renderer and scene
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
  renderer.setClearColor(0x07090C, 1);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(68, 1, 0.15, 15000);
  camera.up.set(0, 0, 1);

  const FOG = new THREE.Color(0.45, 0.305, 0.215), VOID = new THREE.Color(0.03, 0.03, 0.036);
  const texOf = (img, aniso) => {
    const t = new THREE.Texture(img);
    t.colorSpace = THREE.NoColorSpace;
    t.anisotropy = Math.min(aniso, renderer.capabilities.getMaxAnisotropy());
    t.needsUpdate = true;
    return t;
  };

  // Terrain: tinted HiRISE image lit by the package hillshade, plus the red slope mask. The tint is a style,
  // not measured colour.
  const terrainMat = new THREE.ShaderMaterial({
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 2, side: THREE.DoubleSide,
    uniforms: {
      tHill: { value: texOf(images.hill, 8) }, tPhoto: { value: texOf(photoImg, 8) }, tHazard: { value: texOf(images.hazard, 4) },
      uSize: { value: new THREE.Vector2(W, H) }, uFogK: { value: 1 / 4300 }, uFog: { value: FOG }, uVoid: { value: VOID },
    },
    vertexShader: /* glsl */`
      uniform vec2 uSize;
      varying vec2 vUv; varying vec3 vN; varying float vDist;
      void main() {
        vec3 world = (modelMatrix * vec4(position, 1.0)).xyz;   // tiles are only translated
        vUv = world.xy / uSize;
        vN = normal;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vDist = length(mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      uniform sampler2D tHill, tPhoto, tHazard;
      uniform vec2 uSize;
      uniform float uFogK;
      uniform vec3 uFog, uVoid;
      varying vec2 vUv; varying vec3 vN; varying float vDist;
      const vec3 SUN_DIR = vec3(-0.620, 0.620, 0.481);    // from the NW like the package hillshade
      const vec3 RED = vec3(1.0, 0.231, 0.188);
      void main() {
        float hill = texture2D(tHill, vUv).r;
        float photo = texture2D(tPhoto, vUv).r;
        float lit = clamp((hill - 0.5) / 0.42, 0.0, 1.0);
        float alb = smoothstep(0.02, 0.60, photo);
        vec3 n = normalize(vN);
        float sunAmt = max(dot(n, SUN_DIR), 0.0) * (0.5 + 0.85 * lit);
        vec3 albedo = mix(vec3(0.19, 0.105, 0.075), vec3(0.92, 0.60, 0.41), alb);
        vec3 col = albedo * (vec3(1.22, 0.96, 0.78) * sunAmt * 0.95 + vec3(0.085, 0.115, 0.185) * (0.5 + 0.5 * n.z));
        col = mix(col, RED * (0.78 + 0.22 * lit), texture2D(tHazard, vUv).a * 0.55);
        col = mix(col, uFog, 1.0 - exp(-pow(vDist * uFogK, 2.0)));
        float edge = min(min(vUv.x, 1.0 - vUv.x) * uSize.x, min(vUv.y, 1.0 - vUv.y) * uSize.y);
        gl_FragColor = vec4(mix(uVoid, col, smoothstep(0.0, 70.0, edge)), 1.0);
      }`,
  });

  // Terrain mesh: tiles with vertices at cell centres. Each tile has four index sets (every 1, 2, 4 or 8 cells),
  // chosen by distance to the camera, and a skirt along its border that hides the seams between levels.
  const tiles = [];
  {
    const indexCache = new Map();
    const indexFor = (rows, cols, stride) => {
      const key = `${rows}x${cols}/${stride}`;
      if (!indexCache.has(key)) {
        const idx = [];
        const at = (r, c) => r * (cols + 1) + c;
        const T = (rows + 1) * (cols + 1), B = T + cols + 1, L = B + cols + 1, R = L + rows + 1;   // skirt vertex blocks
        const quad = (a, b, c, d) => idx.push(a, b, c, a, c, d);
        for (let r = 0; r < rows; r += stride) for (let c = 0; c < cols; c += stride) {
          const r2 = Math.min(rows, r + stride), c2 = Math.min(cols, c + stride);
          quad(at(r, c), at(r2, c), at(r2, c2), at(r, c2));
        }
        for (let c = 0; c < cols; c += stride) {
          const c2 = Math.min(cols, c + stride);
          quad(at(0, c), at(0, c2), T + c2, T + c);
          quad(at(rows, c), at(rows, c2), B + c2, B + c);
        }
        for (let r = 0; r < rows; r += stride) {
          const r2 = Math.min(rows, r + stride);
          quad(at(r, 0), at(r2, 0), L + r2, L + r);
          quad(at(r, cols), at(r2, cols), R + r2, R + r);
        }
        indexCache.set(key, new THREE.BufferAttribute(new Uint16Array(idx), 1));
      }
      return indexCache.get(key);
    };
    for (let r0 = 0; r0 < gh - 1; r0 += TILE) for (let c0 = 0; c0 < gw - 1; c0 += TILE) {
      const rows = Math.min(TILE, gh - 1 - r0), cols = Math.min(TILE, gw - 1 - c0);
      const inner = (rows + 1) * (cols + 1), total = inner + 2 * (cols + 1) + 2 * (rows + 1);
      const pos = new Float32Array(total * 3), nor = new Int8Array(total * 3);
      const ox = (c0 + cols / 2 + 0.5) * cx, oy = (gh - r0 - rows / 2 - 0.5) * cy;   // tile centre
      let n = 0;
      for (let r = r0; r <= r0 + rows; r++) for (let c = c0; c <= c0 + cols; c++) {
        const w = Math.max(0, c - 1), e = Math.min(gw - 1, c + 1), up = Math.max(0, r - 1), dn = Math.min(gh - 1, r + 1);
        const nx = -(elev[r * gw + e] - elev[r * gw + w]) / ((e - w) * cx), ny = -(elev[up * gw + c] - elev[dn * gw + c]) / ((dn - up) * cy);
        const inv = 127 / Math.hypot(nx, ny, 1);
        nor[n] = Math.round(nx * inv); nor[n + 1] = Math.round(ny * inv); nor[n + 2] = Math.round(inv);
        pos[n++] = (c + 0.5) * cx - ox; pos[n++] = (gh - r - 0.5) * cy - oy; pos[n++] = elev[r * gw + c];
      }
      let m = inner;
      const skirt = (r, c) => {                             // copy of a border vertex, dropped below the surface
        const s = (r * (cols + 1) + c) * 3, d = m++ * 3;
        pos[d] = pos[s]; pos[d + 1] = pos[s + 1]; pos[d + 2] = pos[s + 2] - SKIRT_M;
        nor[d] = nor[s]; nor[d + 1] = nor[s + 1]; nor[d + 2] = nor[s + 2];
      };
      for (let c = 0; c <= cols; c++) skirt(0, c);
      for (let c = 0; c <= cols; c++) skirt(rows, c);
      for (let r = 0; r <= rows; r++) skirt(r, 0);
      for (let r = 0; r <= rows; r++) skirt(r, cols);
      const pa = new THREE.BufferAttribute(pos, 3), na = new THREE.BufferAttribute(nor, 3, true);
      const geos = LOD_STRIDES.map((stride) => {
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', pa); g.setAttribute('normal', na); g.setIndex(indexFor(rows, cols, stride));
        return g;
      });
      geos[0].computeBoundingSphere();
      for (const g of geos) g.boundingSphere = geos[0].boundingSphere;
      const mesh = new THREE.Mesh(geos[0], terrainMat);
      mesh.position.set(ox, oy, 0);
      mesh.matrixAutoUpdate = false;
      mesh.updateMatrix();
      scene.add(mesh);
      tiles.push({ mesh, geos, x: ox, y: oy, z: geos[0].boundingSphere.center.z, lod: 0 });
    }
  }

  // sky: a dusk-toned gradient that the terrain fog blends into (stylised, not a measured sky)
  const sky = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthTest: false, depthWrite: false,
    uniforms: { uFog: { value: FOG }, uVoid: { value: VOID } },
    vertexShader: /* glsl */`varying vec3 vDir; void main() { vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform vec3 uFog, uVoid; varying vec3 vDir;
      void main() {
        float h = normalize(vDir).z;
        vec3 mid = vec3(0.215, 0.150, 0.128), zen = vec3(0.048, 0.050, 0.068);
        vec3 col = h >= 0.0 ? mix(mix(uFog, mid, smoothstep(0.0, 0.16, h)), zen, smoothstep(0.10, 0.75, h))
                            : mix(uFog, uVoid, smoothstep(0.0, 0.05, -h));
        gl_FragColor = vec4(col, 1.0);
      }`,
  }));
  sky.scale.setScalar(6000); sky.renderOrder = -10; sky.frustumCulled = false;
  scene.add(sky);

  scene.add(new THREE.HemisphereLight(0xFFF1E0, 0x3A2A22, 1.2));
  const sun = new THREE.DirectionalLight(0xFFE2C4, 2.4);     // same direction as the package hillshade (NW)
  sun.position.set(-1, 1, 1.4);
  scene.add(sun);

  // ---------------------------------------------------------------- route line and stop beacons
  function addLine(flat, opts, order) {
    const geo = new LineGeometry();
    geo.setPositions(flat);
    const line = new Line2(geo, new LineMaterial({ worldUnits: false, transparent: true, depthWrite: false, ...opts }));
    line.frustumCulled = false;
    line.renderOrder = order;
    scene.add(line);
    return line;
  }
  const routeFwd = [], routeRev = [];
  for (let i = 0; i < N; i++) routeFwd.push(P[i].x, P[i].y, zAt(P[i].x, P[i].y) + 0.25);
  for (let i = N - 1; i >= 0; i--) routeRev.push(P[i].x, P[i].y, zAt(P[i].x, P[i].y) + 0.25);
  addLine(routeFwd, { color: 0x34C759, opacity: 0.3, linewidth: 2.5 }, 2);
  const aheadLine = addLine(routeRev, { color: 0x34C759, opacity: 0.95, linewidth: 4 }, 3);

  const beamGeo = new THREE.PlaneGeometry(1, 1).rotateX(Math.PI / 2).translate(0, 0, 0.5);   // x across, z up 0..1
  function makeBeam(x, y) {                                 // light column, always turned towards the camera
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
      uniforms: { uColor: { value: new THREE.Color() }, uAlpha: { value: 0.8 } },
      vertexShader: /* glsl */`varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */`
        uniform vec3 uColor; uniform float uAlpha; varying vec2 vUv;
        void main() {
          float x = (vUv.x - 0.5) * 2.0;
          float a = (exp(-x * x * 9.0) * 0.55 + exp(-x * x * 120.0)) * pow(1.0 - vUv.y, 1.5) * uAlpha;
          gl_FragColor = vec4(uColor * a, a);
        }`,
    });
    const mesh = new THREE.Mesh(beamGeo, mat);
    mesh.position.set(x, y, zAt(x, y));
    mesh.renderOrder = 4;
    scene.add(mesh);
    return { mesh, mat };
  }
  const faceCamera = (b, height) => {
    const p = b.mesh.position, d = Math.hypot(p.x - camera.position.x, p.y - camera.position.y);
    b.mesh.rotation.z = Math.atan2(camera.position.y - p.y, camera.position.x - p.x) + Math.PI / 2;
    b.mesh.scale.set(Math.max(2.5, d * 0.028), 1, height);
  };
  const beacons = stops.map((s) => ({ s, cls: '', ...makeBeam(s.x, s.y) }));
  const robotBeam = makeBeam(0, 0);
  robotBeam.mat.uniforms.uColor.value.setHex(COLORS.robot);

  // ---------------------------------------------------------------- figures
  const std = (color, extra) => new THREE.MeshStandardMaterial({ color, roughness: 0.8, ...extra });
  function makeAstronaut(accent) {                          // the second crew member, seen from the lead's eyes
    const fig = new THREE.Group();                          // y-up figure, forward = −z, feet at y = 0
    const suit = std(0xE8ECF1), dark = std(0x39414D, { roughness: 0.9 });
    const visor = std(0xB98A2E, { roughness: 0.25, metalness: 0.8, emissive: 0x4A3208 });
    const add = (geo, mat, x, y, z, parent = fig) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); parent.add(m); return m; };
    add(new THREE.CapsuleGeometry(0.27, 0.42, 4, 12), suit, 0, 1.22, 0);            // torso
    add(new THREE.BoxGeometry(0.5, 0.66, 0.26), suit, 0, 1.3, 0.27);                // life-support pack
    add(new THREE.BoxGeometry(0.3, 0.05, 0.02), new THREE.MeshBasicMaterial({ color: accent }), 0, 1.5, 0.405);
    add(new THREE.CylinderGeometry(0.17, 0.2, 0.08, 16), dark, 0, 1.6, 0);          // neck ring
    add(new THREE.SphereGeometry(0.235, 24, 16), suit, 0, 1.8, 0);                  // helmet
    add(new THREE.SphereGeometry(0.205, 24, 16), visor, 0, 1.8, -0.07);             // visor
    const limb = (x, y, len, r, mid, endGeo, endY, endZ) => {   // swings about a pivot at the hip or shoulder
      const pivot = new THREE.Group();
      pivot.position.set(x, y, 0);
      add(new THREE.CapsuleGeometry(r, len, 4, 10), suit, 0, mid, 0, pivot);
      add(endGeo, dark, 0, endY, endZ, pivot);
      fig.add(pivot);
      return pivot;
    };
    const boot = () => new THREE.BoxGeometry(0.2, 0.12, 0.34), glove = () => new THREE.SphereGeometry(0.1, 12, 8);
    const legL = limb(-0.15, 0.92, 0.52, 0.125, -0.515, boot(), -0.86, -0.04), legR = limb(0.15, 0.92, 0.52, 0.125, -0.515, boot(), -0.86, -0.04);
    const armL = limb(-0.39, 1.5, 0.42, 0.095, -0.28, glove(), -0.6, 0), armR = limb(0.39, 1.5, 0.42, 0.095, -0.28, glove(), -0.6, 0);
    fig.rotation.x = Math.PI / 2;                           // y-up figure into the z-up world; forward becomes +y (north)
    const group = new THREE.Group();
    group.add(fig);
    scene.add(group);
    return { group, fig, legL, legR, armL, armR, phase: 0 };
  }
  function makeRobot() {                                    // six-wheeled scout, z up, forward = +y
    const g = new THREE.Group();
    const white = std(0xE8ECF1), dark = std(0x2B323C, { roughness: 0.9 }), panel = std(0x1B3A6B, { roughness: 0.35, metalness: 0.5 });
    const add = (geo, mat, x, y, z) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); g.add(m); return m; };
    add(new THREE.BoxGeometry(1.1, 1.7, 0.42), white, 0, 0, 0.72);                  // body
    add(new THREE.BoxGeometry(1.5, 1.5, 0.05), panel, 0, -0.15, 0.98);              // solar deck
    for (const x of [-0.72, 0.72]) for (const y of [-0.72, 0, 0.72]) {
      add(new THREE.CylinderGeometry(0.3, 0.3, 0.24, 18).rotateZ(Math.PI / 2), dark, x, y, 0.3);
    }
    add(new THREE.CylinderGeometry(0.045, 0.045, 1.0, 10).rotateX(Math.PI / 2), white, 0, 0.62, 1.48);   // mast
    add(new THREE.BoxGeometry(0.42, 0.2, 0.2), white, 0, 0.66, 2.05);               // camera head
    add(new THREE.BoxGeometry(0.3, 0.02, 0.08), new THREE.MeshBasicMaterial({ color: COLORS.robot }), 0, 0.55, 2.05);   // rear light
    scene.add(g);
    return g;
  }
  const buddy = makeAstronaut(0xFFB300), robot = makeRobot();
  const UP = new THREE.Vector3(0, 0, 1), nrm = new THREE.Vector3();

  // ---------------------------------------------------------------- camera: look around by dragging
  const cam = { heading: null, yaw: 0, pitch: PITCH0, z: null };
  let drag = null;
  canvas.addEventListener('pointerdown', (e) => { canvas.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY }; });
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    cam.yaw -= (e.clientX - drag.x) * 0.005;
    cam.pitch = clamp(cam.pitch + (e.clientY - drag.y) * 0.004, -1.1, 1.0);
    drag.x = e.clientX; drag.y = e.clientY;
  });
  const drop = () => { drag = null; };
  canvas.addEventListener('pointerup', drop);
  canvas.addEventListener('pointercancel', drop);
  canvas.addEventListener('dblclick', () => { cam.yaw = 0; cam.pitch = PITCH0; });

  function resize() {
    const w = root.clientWidth, h = root.clientHeight;
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  const last = { x: null, y: null, t: 0 };
  let stride = 0;
  function frame(dt) {
    if (document.hidden) return;
    const calm = reducedMotion.matches;
    const t = state.t, tc = fn.crewT(t), pos = fn.sampleT(tc), rob = fn.robotPos(t);
    const moved = last.x === null ? 0 : Math.hypot(pos.x - last.x, pos.y - last.y);
    const jumped = last.x === null || moved > 80 || Math.abs(t - last.t) > 12;
    last.x = pos.x; last.y = pos.y; last.t = t;
    const ease = (tau) => (calm || jumped ? 1 : 1 - Math.exp(-dt / tau));

    // camera: at the lead's eyes, facing along the path (long look-ahead, low-pass) plus the user's offset
    const pa = fn.sampleD(Math.max(0, pos.dist - 4)), pb = fn.sampleD(Math.min(totalDist, pos.dist + 40));
    const want = Math.hypot(pb.x - pa.x, pb.y - pa.y) > 2 ? Math.atan2(pb.x - pa.x, pb.y - pa.y) : (cam.heading ?? state.heading ?? 0);
    cam.heading = cam.heading === null || jumped ? want : cam.heading + wrapPi(want - cam.heading) * ease(1.1);
    const eyeZ = zAt(pos.x, pos.y) + EYE_M;
    cam.z = cam.z === null || jumped ? eyeZ : cam.z + (eyeZ - cam.z) * ease(0.15);
    const bearing = cam.heading + cam.yaw, cp = Math.cos(cam.pitch);
    camera.position.set(pos.x, pos.y, cam.z);
    camera.lookAt(pos.x + Math.sin(bearing) * cp, pos.y + Math.cos(bearing) * cp, cam.z + Math.sin(cam.pitch));
    camera.updateMatrixWorld();
    sky.position.copy(camera.position);
    for (const tl of tiles) {                                // level of detail by distance
      const d = Math.hypot(tl.x - pos.x, tl.y - pos.y, tl.z - cam.z);
      const lod = d < LOD_DIST[0] ? 0 : d < LOD_DIST[1] ? 1 : d < LOD_DIST[2] ? 2 : 3;
      if (lod !== tl.lod) { tl.lod = lod; tl.mesh.geometry = tl.geos[lod]; }
    }
    aheadLine.geometry.instanceCount = N - 1 - pos.i;

    // second crew member: 7 m ahead and to the left, clear of the centre of the view
    const walking = moved > 1e-4 && !jumped;
    stride += ((walking ? 1 : 0) - stride) * (1 - Math.exp(-dt / 0.18));
    const hdg = state.heading ?? cam.heading;
    const bp = fn.sampleD(Math.min(totalDist, pos.dist + 7));
    const bx = bp.x - Math.cos(hdg) * 2.2, by = bp.y + Math.sin(hdg) * 2.2;
    buddy.group.position.set(bx, by, zAt(bx, by));
    buddy.group.rotation.z = -hdg;
    const st = calm ? 0 : stride;
    buddy.phase += dt * 7.5 * st;
    const sw = Math.sin(buddy.phase) * 0.55 * st;
    buddy.legL.rotation.x = sw; buddy.legR.rotation.x = -sw;
    buddy.armL.rotation.x = -sw * 0.7; buddy.armR.rotation.x = sw * 0.7;

    // scout robot: on the route ahead, sitting on the local slope
    const rh = fn.headingAt(rob) ?? hdg;
    normalAt(rob.x, rob.y, nrm);
    robot.position.set(rob.x, rob.y, zAt(rob.x, rob.y));
    robot.quaternion.setFromUnitVectors(UP, nrm);
    robot.rotateZ(-rh);
    robotBeam.mesh.position.set(rob.x, rob.y, zAt(rob.x, rob.y) + 2.2);
    faceCamera(robotBeam, 55);

    for (const b of beacons) {
      const cls = fn.stopClass(b.s, tc);
      if (b.cls !== cls) {
        b.cls = cls;
        b.mat.uniforms.uColor.value.setHex(COLORS[cls]);
        b.mat.uniforms.uAlpha.value = cls === 'context' ? 0.3 : cls === 'visited' ? 0.5 : 0.85;
      }
      faceCamera(b, b.s.role === 'pickup' ? 130 : 90);
    }
    renderer.render(scene, camera);
  }

  function enter() { resize(); last.x = null; cam.yaw = 0; cam.pitch = PITCH0; }

  resize();
  $('v3-loading').hidden = true;
  return { frame, resize, enter };
}
