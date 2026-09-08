import * as THREE from "three";
import { OrbitControls } from "three/addons/OrbitControls.js";

/* ---------------- state ---------------- */

let state = null; // last payload from the server
let loopOverrides = {}; // id -> {z, width} (authoritative: server response)
let selected = null;

/* ---------------- three.js setup ---------------- */

const view = document.getElementById("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
view.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14161a);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x30343c, 1.1));
const key = new THREE.DirectionalLight(0xffffff, 1.6);
key.position.set(1, 2, 1.5);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.5);
fill.position.set(-1.5, 1, -1);
scene.add(fill);

const modelGroup = new THREE.Group();
const meshGroup = new THREE.Group();
const bridgeGroup = new THREE.Group();
const outlineGroup = new THREE.Group();
const footprintGroup = new THREE.Group();
modelGroup.add(meshGroup, bridgeGroup, outlineGroup, footprintGroup);
scene.add(modelGroup);

let mainMesh = null;
let grid = null;

// model (x, y, z) -> three (x, z, -y): height becomes the up axis
function toThree(x, y, z) {
  return new THREE.Vector3(x, z, -y);
}
function toModel(p) {
  return { x: p.x, y: -p.z, z: p.y };
}

/* ---------------- colors (viridis-ish) ---------------- */

const VIRIDIS = [
  [0.267, 0.005, 0.329], [0.283, 0.141, 0.458], [0.254, 0.265, 0.53], [0.207, 0.372, 0.553],
  [0.164, 0.471, 0.558], [0.128, 0.567, 0.551], [0.135, 0.659, 0.518], [0.267, 0.749, 0.441],
  [0.478, 0.821, 0.318], [0.741, 0.873, 0.15], [0.993, 0.906, 0.144],
];
function viridis(t) {
  t = Math.min(1, Math.max(0, t)) * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(t));
  const f = t - i;
  const a = VIRIDIS[i], b = VIRIDIS[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/* ---------------- mesh building ---------------- */

function buildMesh(payload) {
  meshGroup.clear();
  const { positions, faces } = payload;
  if (!faces.length) { mainMesh = null; return; }
  const n = positions.length / 3;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  let zmin = Infinity, zmax = -Infinity;
  for (let i = 0; i < n; i++) {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    const v = toThree(x, y, z);
    pos[i * 3] = v.x; pos[i * 3 + 1] = v.y; pos[i * 3 + 2] = v.z;
    if (z < zmin) zmin = z;
    if (z > zmax) zmax = z;
  }
  const span = Math.max(zmax - zmin, 1e-9);
  for (let i = 0; i < n; i++) {
    const z = positions[i * 3 + 2];
    const c = viridis((z - zmin) / span);
    col[i * 3] = c[0]; col[i * 3 + 1] = c[1]; col[i * 3 + 2] = c[2];
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  geo.setIndex(new THREE.BufferAttribute(new Uint32Array(faces), 1));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.55, metalness: 0.05,
    wireframe: document.getElementById("tg-wire").checked,
    side: THREE.DoubleSide,
  });
  mainMesh = new THREE.Mesh(geo, mat);
  meshGroup.add(mainMesh);
  document.getElementById("cb-min").textContent = zmin.toFixed(1);
  document.getElementById("cb-max").textContent = zmax.toFixed(1);
}

function ringPoints(ring, z) {
  const pts = [];
  for (const [x, y] of ring) pts.push(toThree(x, y, z));
  return pts;
}

function buildBridges(bridges) {
  bridgeGroup.clear();
  const matBar = new THREE.MeshStandardMaterial({
    color: 0x9aa3ad, roughness: 0.8, transparent: true, opacity: 0.85,
  });
  const matPlate = new THREE.MeshStandardMaterial({
    color: 0x6f7a85, roughness: 0.9, transparent: true, opacity: 0.55,
  });
  for (const b of bridges) {
    const shape = new THREE.Shape(b.poly.exterior.map(([x, y]) => new THREE.Vector2(x, y)));
    for (const hole of b.poly.holes) {
      const path = new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y)));
      shape.holes.push(path);
    }
    const geo = new THREE.ExtrudeGeometry(shape, { depth: b.z, bevelEnabled: false });
    geo.applyMatrix4(new THREE.Matrix4().makeRotationX(-Math.PI / 2));
    bridgeGroup.add(new THREE.Mesh(geo, b.kind === "plate" ? matPlate : matBar));
  }
  bridgeGroup.visible = document.getElementById("tg-bridges").checked;
}

function buildFootprints(loops) {
  footprintGroup.clear();
  const mat = new THREE.LineBasicMaterial({ color: 0x4da3ff, transparent: true, opacity: 0.6 });
  for (const loop of loops) {
    if (!loop.footprint) continue;
    const rings = [loop.footprint.exterior, ...loop.footprint.holes];
    for (const ring of rings) {
      const geo = new THREE.BufferGeometry().setFromPoints(ringPoints(ring, 0.05));
      footprintGroup.add(new THREE.LineLoop(geo, mat));
    }
  }
  footprintGroup.visible = document.getElementById("tg-footprints").checked;
}

function buildOutline(loop) {
  outlineGroup.clear();
  if (!loop || !loop.footprint) return;
  const mat = new THREE.LineBasicMaterial({ color: 0xff5555 });
  const rings = [loop.footprint.exterior, ...loop.footprint.holes];
  for (const ring of rings) {
    const geo = new THREE.BufferGeometry().setFromPoints(ringPoints(ring, loop.z + 0.15));
    outlineGroup.add(new THREE.LineLoop(geo, mat));
  }
}

function fitCamera() {
  if (!mainMesh) return;
  mainMesh.geometry.computeBoundingBox();
  const bb = mainMesh.geometry.boundingBox;
  const center = bb.getCenter(new THREE.Vector3());
  const size = bb.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1);
  const dir = new THREE.Vector3(0.6, 0.55, 1).normalize();
  camera.position.copy(center).addScaledVector(dir, radius * 2.6);
  camera.near = radius / 100;
  camera.far = radius * 50;
  controls.target.copy(center);
  controls.update();
  if (grid) scene.remove(grid);
  grid = new THREE.GridHelper(radius * 4, 20, 0x2c313a, 0x22262d);
  grid.position.set(center.x, bb.min.y, center.z);
  scene.add(grid);
}

/* ---------------- picking ---------------- */

function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function loopAt(modelPoint, zHit) {
  if (!state) return null;
  let best = null;
  for (const loop of state.loops) {
    const fp = loop.footprint;
    if (!fp) continue;
    if (!pointInRing(modelPoint.x, modelPoint.y, fp.exterior)) continue;
    if (fp.holes.some((h) => pointInRing(modelPoint.x, modelPoint.y, h))) continue;
    // prefer the topmost loop below the hit height
    if (best === null || loop.z > best.z) {
      if (loop.z <= zHit + 0.01 || best === null) best = loop;
    }
  }
  return best;
}

const raycaster = new THREE.Raycaster();
let downPos = null;
renderer.domElement.addEventListener("pointerdown", (e) => { downPos = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener("pointerup", (e) => {
  if (!downPos || Math.hypot(e.clientX - downPos[0], e.clientY - downPos[1]) > 4) return;
  if (!mainMesh) return;
  const r = renderer.domElement.getBoundingClientRect();
  raycaster.setFromCamera(
    new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1),
    camera
  );
  const hit = raycaster.intersectObject(mainMesh)[0];
  if (hit) {
    const m = toModel(hit.point);
    const loop = loopAt(m, m.z);
    if (loop) selectLoop(loop.id);
  }
});

/* ---------------- panel ---------------- */

const $ = (id) => document.getElementById(id);

function selectLoop(id) {
  selected = id;
  const loop = state.loops.find((l) => l.id === id);
  if (!loop) return;
  $("loop-select").value = String(id);
  $("loop-info").textContent = `#${loop.id}  ${loop.cls || "-"} ${loop.kind}  ${loop.points} pts`;
  if (document.activeElement !== $("loop-z")) $("loop-z").value = loop.z;
  if (document.activeElement !== $("loop-w")) $("loop-w").value = loop.width;
  buildOutline(loop);
}

function fillPanel() {
  const cfg = state.config;
  $("loop-select").innerHTML = state.loops
    .map((l) => `<option value="${l.id}">${l.id}: ${l.cls || "-"} ${l.kind}</option>`)
    .join("");
  if (selected === null || !state.loops.some((l) => l.id === selected)) {
    selected = state.loops[0]?.id ?? null;
  }
  if (selected !== null) selectLoop(selected);
  const setVal = (id, v) => {
    if (document.activeElement !== $(id)) $(id).value = v;
  };
  setVal("br-mode", cfg.bridges.mode);
  setVal("br-style", cfg.bridges.style);
  setVal("br-width", cfg.bridges.width);
  setVal("br-z", cfg.bridges.z);
  setVal("br-branches", cfg.bridges.branches);
  setVal("br-radius", cfg.bridges.radius);
  setVal("def-z", cfg.default_z);
  setVal("def-w", cfg.default_width);
  setVal("def-scale", cfg.scale);
}

function status() {
  const mesh = state.mesh;
  const bits = [];
  if (mesh.faces.length) {
    bits.push(`${mesh.faces.length / 3 | 0} tris`);
    bits.push(`volume ${mesh.volume} mm³`);
    bits.push(mesh.watertight ? "watertight ✓" : "NOT WATERTIGHT ✗");
  }
  $("status").textContent = [...bits, ...state.notes].join(" · ");
}

/* ---------------- server round-trips ---------------- */

let busy = 0;
async function api(path, body) {
  busy++;
  $("spinner").classList.add("busy");
  try {
    const res = await fetch(path, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || res.statusText);
    return json;
  } finally {
    busy--;
    if (!busy) $("spinner").classList.remove("busy");
  }
}

function currentOverrides() {
  if (selected !== null) {
    const z = parseFloat($("loop-z").value);
    const w = parseFloat($("loop-w").value);
    if (!Number.isNaN(z) || !Number.isNaN(w)) {
      loopOverrides[String(selected)] = {
        z: Number.isNaN(z) ? null : z,
        width: Number.isNaN(w) ? null : w,
      };
    }
  }
  return loopOverrides;
}

function buildBody() {
  currentOverrides();
  return {
    scale: parseFloat($("def-scale").value) || state.config.scale,
    default_z: parseFloat($("def-z").value) || state.config.default_z,
    default_width: parseFloat($("def-w").value) || state.config.default_width,
    classes: state.config.classes,
    loops: loopOverrides,
    bridges: {
      mode: $("br-mode").value,
      style: $("br-style").value,
      width: parseFloat($("br-width").value) || state.config.bridges.width,
      z: parseFloat($("br-z").value) || state.config.bridges.z,
      branches: parseInt($("br-branches").value, 10) || state.config.bridges.branches,
      radius: Math.max(parseFloat($("br-radius").value) || 0, 0),
    },
  };
}

function applyState(newState) {
  state = newState;
  loopOverrides = state.config.loops;
  buildMesh(state.mesh);
  buildBridges(state.bridges);
  buildFootprints(state.loops);
  fillPanel();
  status();
}

async function rebuild() {
  try {
    applyState(await api("/api/build", buildBody()));
  } catch (err) {
    $("status").textContent = `error: ${err.message}`;
  }
}

let rebuildTimer = null;
function scheduleRebuild() {
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(rebuild, 500);
}

for (const id of ["loop-z", "loop-w", "br-mode", "br-style", "br-width", "br-z", "br-branches", "br-radius", "def-z", "def-w", "def-scale"]) {
  $(id).addEventListener("input", scheduleRebuild);
}
$("loop-select").addEventListener("change", (e) => selectLoop(parseInt(e.target.value, 10)));
$("btn-rebuild").addEventListener("click", rebuild);
$("btn-save").addEventListener("click", async () => {
  clearTimeout(rebuildTimer);
  try {
    const r = await api("/api/save", buildBody());
    applyState(r);
    $("status").textContent = `saved ${r.path}`;
  } catch (err) {
    $("status").textContent = `error: ${err.message}`;
  }
});
$("btn-export").addEventListener("click", async () => {
  clearTimeout(rebuildTimer);
  try {
    const r = await api("/api/export", buildBody());
    applyState(r);
    $("status").textContent = `exported ${r.path}`;
  } catch (err) {
    $("status").textContent = `error: ${err.message}`;
  }
});
$("tg-wire").addEventListener("change", (e) => {
  if (mainMesh) mainMesh.material.wireframe = e.target.checked;
});
$("tg-bridges").addEventListener("change", (e) => { bridgeGroup.visible = e.target.checked; });
$("tg-footprints").addEventListener("change", (e) => { footprintGroup.visible = e.target.checked; });

/* ---------------- render loop ---------------- */

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  if (w === 0 || h === 0) return;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(view);
renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

/* ---------------- boot ---------------- */

(async () => {
  resize();
  state = await api("/api/project");
  loopOverrides = state.config.loops;
  buildMesh(state.mesh);
  buildBridges(state.bridges);
  buildFootprints(state.loops);
  fillPanel();
  fitCamera();
  status();
})();
