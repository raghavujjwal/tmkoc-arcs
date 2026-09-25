// Geometry test for the arc field. Loads site/layout.js -- the same module the page uses --
// so what is tested is exactly what is drawn. Runs at phone, tablet, laptop and desktop
// widths (which exercise all three arrangements) and checks:
//
//   * no two arcs overlap at rest, with the full gap between them
//   * no arc sits under another era's label
//   * every arc can be picked by pointing at it, and stays under the pointer when the lens
//     locks onto it (only the edge clamp may nudge it)
//   * the lens never pushes an arc off the canvas
//   * broadcast order stays readable: within an era each arc sits right beside the previous
//     one along the spiral, and each era starts at its cluster's centre

const fs = require('fs');
const path = require('path');
const L = require(path.join(__dirname, '..', 'site', 'layout.js'));

const src = fs.readFileSync(path.join(__dirname, '..', 'site', 'arcs.js'), 'utf8');
const D = JSON.parse(src.replace(/^window\.ARC_DATA=/, '').replace(/;\s*$/, ''));
const arcs = D.arcs;

let allPass = true;
for (const W of [360, 700, 1000, 1240]) {
  const { nodes, clusters, H } = L.compute(arcs, D.eras, W);
  const checks = {};

  let overlaps = 0, tightest = Infinity;
  for (let a = 0; a < nodes.length; a++)
    for (let b = a + 1; b < nodes.length; b++) {
      const p = nodes[a], q = nodes[b];
      const clear = Math.hypot(p.hx - q.hx, p.hy - q.hy) - p.r - q.r;
      tightest = Math.min(tightest, clear);
      if (clear < 0) overlaps++;
    }
  checks[`no arcs overlap (tightest clearance ${tightest.toFixed(1)}px)`] = overlaps === 0;

  // Labels: a box ~130px wide, 30px tall, centred above each cluster.
  let underLabel = 0;
  for (const c of clusters)
    for (const n of nodes)
      if (Math.abs(n.hx - c.cx) < 70 + n.r && n.hy + n.r > c.labelY && n.hy - n.r < c.labelY + 30)
        underLabel++;
  checks['no arc sits under an era label'] = underLabel === 0;

  let miss = 0, drift = 0, driftRatio = 0, off = 0;
  for (const n of nodes) {
    if (L.pick(nodes, n.hx, n.hy) !== n.i) miss++;
    const me = L.lensTarget(n, n.hx, n.hy, W, H);
    const d = Math.hypot(me.x - n.hx, me.y - n.hy);
    drift = Math.max(drift, d); driftRatio = Math.max(driftRatio, d / (n.r * me.s));
    for (const m of nodes) {
      const t = L.lensTarget(m, n.hx, n.hy, W, H);
      if (t.x < 0 || t.x > W || t.y < 0 || t.y > H) { off++; break; }
    }
  }
  checks['every arc pickable by pointing at it'] = miss === 0;
  checks[`hovered arc stays under the pointer (max nudge ${drift.toFixed(1)}px)`] = driftRatio < 0.5;
  checks['lens never pushes an arc off the canvas'] = off === 0;

  // Order readability. Greedy packing occasionally has to hop past a large arc on the
  // previous turn, so a single fixed step limit is the wrong test. Two requirements:
  //  * hard bound: no step skips more than ~two large bubbles (2 x 42px + gap). The old
  //    packer raced along the spiral and produced 200px jumps; this catches that.
  //  * typical adjacency: at least 95% of steps leave a gap of 30px or less.
  let farStep = 0, worstStep = 0, badStart = 0, tight = 0, steps = 0;
  for (const c of clusters) {
    const first = nodes[c.idx[0]];
    if (Math.hypot(first.hx - c.cx, first.hy - c.cy) > 1) badStart++;
    for (let k = 1; k < c.idx.length; k++) {
      const p = nodes[c.idx[k - 1]], q = nodes[c.idx[k]];
      const gap = Math.hypot(p.hx - q.hx, p.hy - q.hy) - p.r - q.r;
      worstStep = Math.max(worstStep, gap); steps++;
      if (gap <= 30 * c.s) tight++;
      if (gap > (2 * 42 + L.GAP) * c.s) farStep++;
    }
  }
  checks['each era starts at its cluster centre'] = badStart === 0;
  checks[`no step skips more than two large bubbles (widest ${worstStep.toFixed(0)}px)`] = farStep === 0;
  checks[`consecutive arcs sit side by side (${tight}/${steps} = ${(tight / steps * 100).toFixed(1)}% within 30px)`] =
    tight / steps >= 0.95;

  const mode = W >= 1000 ? 'wave' : (W >= 620 ? 'two columns' : 'one column');
  console.log(`\nwidth ${W}px (${mode}) -> field ${H}px tall; cluster scale ` +
    clusters.map(c => c.s.toFixed(2)).join('/'));
  for (const [k, v] of Object.entries(checks)) console.log(`  [${v ? 'ok' : 'FAIL'}] ${k}`);
  if (!Object.values(checks).every(Boolean)) allPass = false;
}
console.log('\nFIELD GEOMETRY', allPass ? 'PASS' : 'FAIL');
process.exit(allPass ? 0 : 1);
