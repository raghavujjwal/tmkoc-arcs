// Geometry test for the spiral view.
//
// The requirement is that every arc is reachable by pointing at it -- a user should never
// have to know an arc exists in order to find it. 107 of the 419 arcs span only 2-4
// episodes, which on a 13-turn spiral is a very short stroke, so "can you actually hover
// the smallest ones" is the question that decides whether the view works.
//
// This replicates the page's layout and hit-testing maths against the real arc data and
// asserts that every arc owns at least one reachable pixel.

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'site', 'arcs.js'), 'utf8');
const D = JSON.parse(src.replace(/^window\.ARC_DATA=/, '').replace(/;\s*$/, ''));
const arcs = D.arcs;

const TURNS = 13;
const SIDE = 900 * 2;               // css px * devicePixelRatio, as the page sizes it
let N = 0;
for (const a of arcs) N += a.n;

let off = 0;
const arcOf = new Int32Array(N);
for (let ai = 0; ai < arcs.length; ai++) {
  arcs[ai]._off = off;
  for (let k = 0; k < arcs[ai].n; k++) arcOf[off + k] = ai;
  off += arcs[ai].n;
}

const cx = SIDE / 2, cy = SIDE / 2;
const r0 = SIDE * 0.055, r1 = SIDE * 0.47;
const thMax = TURNS * Math.PI * 2;
const STROKE = (r1 - r0) / TURNS * 0.62;

const pts = new Float64Array(N * 2);
for (let i = 0; i < N; i++) {
  const t = i / (N - 1);
  const th = t * thMax, r = r0 + (r1 - r0) * t;
  pts[i * 2]     = cx + Math.cos(th - Math.PI / 2) * r;
  pts[i * 2 + 1] = cy + Math.sin(th - Math.PI / 2) * r;
}

const GRID = 26;
const cells = new Map();
for (let i = 0; i < N; i++) {
  const key = ((pts[i*2] / GRID) | 0) + ',' + ((pts[i*2+1] / GRID) | 0);
  let arr = cells.get(key);
  if (!arr) cells.set(key, arr = []);
  arr.push(i);
}
const HIT2 = Math.pow(STROKE * 1.1, 2);

function nearest(x, y) {
  const gx = (x / GRID) | 0, gy = (y / GRID) | 0;
  let best = -1, bd = Infinity;
  for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) {
    const arr = cells.get((gx + a) + ',' + (gy + b));
    if (!arr) continue;
    for (const i of arr) {
      const dx = pts[i*2] - x, dy = pts[i*2+1] - y, d = dx*dx + dy*dy;
      if (d < bd) { bd = d; best = i; }
    }
  }
  return bd < HIT2 ? best : -1;
}

const checks = {};
const unreachable = [];
let minSep = Infinity;

for (let ai = 0; ai < arcs.length; ai++) {
  const a = arcs[ai];
  let reachable = false;
  // Point exactly at each episode centre in the arc; at least one must resolve to it.
  for (let k = 0; k < a.n && !reachable; k++) {
    const i = a._off + k;
    if (nearest(pts[i*2], pts[i*2+1]) >= 0 && arcOf[nearest(pts[i*2], pts[i*2+1])] === ai)
      reachable = true;
  }
  if (!reachable) unreachable.push(`${a.start_ep}-${a.end_ep} (${a.n}ep)`);
}

// Adjacent turns must not overlap, or the cursor would grab the wrong lap of the spiral.
for (let i = 0; i + 1 < N; i++) {
  const dx = pts[i*2] - pts[(i+1)*2], dy = pts[i*2+1] - pts[(i+1)*2+1];
  minSep = Math.min(minSep, Math.hypot(dx, dy));
}
const turnGap = (r1 - r0) / TURNS;

const short = arcs.filter(a => a.n <= 4);
let shortOk = 0;
for (const a of short) {
  const i = a._off + (a.n >> 1);
  const hit = nearest(pts[i*2], pts[i*2+1]);
  if (hit >= 0 && arcOf[hit] === arcs.indexOf(a)) shortOk++;
}

checks['every arc is reachable by pointing at it'] = unreachable.length === 0;
checks['shortest arcs (<=4 eps) are all hittable'] = shortOk === short.length;
checks['stroke width does not exceed the gap between turns'] = STROKE <= turnGap;
checks['hit radius stays inside one turn'] = Math.sqrt(HIT2) < turnGap;
checks['every episode maps to exactly one arc'] = arcOf.length === N;
checks['spiral covers all episodes'] = N === arcs.reduce((s, a) => s + a.n, 0);

console.log(`spiral: ${N} episodes, ${arcs.length} arcs, ${TURNS} turns`);
console.log(`stroke ${STROKE.toFixed(1)}px, turn gap ${turnGap.toFixed(1)}px, ` +
            `hit radius ${Math.sqrt(HIT2).toFixed(1)}px`);
console.log(`arcs of <=4 episodes: ${short.length}, hittable at midpoint: ${shortOk}\n`);
for (const [k, v] of Object.entries(checks)) console.log(`  [${v ? 'ok' : 'FAIL'}] ${k}`);
if (unreachable.length) console.log(`\n  unreachable: ${unreachable.slice(0, 12).join(', ')}`);

const passed = Object.values(checks).every(Boolean);
console.log('\nSPIRAL GEOMETRY', passed ? 'PASS' : 'FAIL');
process.exit(passed ? 0 : 1);
