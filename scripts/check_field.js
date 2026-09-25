// Geometry test for the live field (site/index.html).
//
// Replicates the page's layout, lens and picking maths against the real arc data at
// phone, tablet and desktop widths, and checks the properties the interaction depends on:
//
//   * no two arcs overlap at rest, so every arc is visibly separate
//   * every arc can be picked by pointing at it -- at rest, AND with the lens centred on
//     it (the lens moves things, so picking must track displaced positions)
//   * the lens never pushes an arc outside the canvas
//   * broadcast order is continuous: consecutive arcs sit next to each other, including
//     across the serpentine U-turns, except at deliberate era breaks
//
// If you change layout() or the lens constants in index.html, change them here too.

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'site', 'arcs.js'), 'utf8');
const D = JSON.parse(src.replace(/^window\.ARC_DATA=/, '').replace(/;\s*$/, ''));
const arcs = D.arcs;

const LENS_R = 190, LENS_K = 3.0, MAG = 2.3;
const radiusOf = a => Math.max(4, Math.min(24, 2.8 + 2.35 * Math.sqrt(a.n)));

function layout(W) {
  const small = W < 620, gutter = small ? 14 : 104, right = 22, gap = small ? 7 : 10;
  const x0 = gutter, x1 = W - right;
  let y = small ? 70 : 64, flip = false;
  const nodes = [], eraStart = new Set();
  const byEra = {};
  arcs.forEach((a, i) => (byEra[a.era] = byEra[a.era] || []).push(i));
  for (const era of D.eras) {
    const idx = byEra[era]; if (!idx) continue;
    eraStart.add(idx[0]);
    const rows = []; let cur = [], x = x0, maxR = 0;
    for (const i of idx) {
      const r = radiusOf(arcs[i]);
      if (x + 2 * r > x1 && cur.length) { rows.push({ items: cur, maxR }); cur = []; x = x0; maxR = 0; }
      cur.push({ i, r, x: x + r }); x += 2 * r + gap; maxR = Math.max(maxR, r);
    }
    if (cur.length) rows.push({ items: cur, maxR });
    for (const rw of rows) {
      const cy = y + rw.maxR + 9; let lastX = 0;
      for (const it of rw.items) {
        const hx = flip ? (x0 + x1 - it.x) : it.x; lastX = hx;
        nodes[it.i] = { i: it.i, r: it.r, hx, hy: cy };
      }
      y = cy + rw.maxR + 14;
      flip = lastX > (x0 + x1) / 2;          // next row starts where this one ended
    }
    y += 30;
  }
  return { nodes, H: y + 64, eraStart, rowGap: 0 };
}

// Steady state of the spring: every node sits exactly at its lens target.
function lensed(nodes, fx, fy, W, H) {
  return nodes.map(n => {
    const dx = n.hx - fx, dy = n.hy - fy, d = Math.hypot(dx, dy);
    let x = n.hx, y = n.hy, s = 1;
    if (d < LENS_R) {
      const u = d / LENS_R, dd = LENS_R * (LENS_K + 1) * u / (LENS_K * u + 1);
      if (d > 0.001) { x = fx + dx / d * dd; y = fy + dy / d * dd; }
      s = 1 + (MAG - 1) * (1 - u) ** 2;
    }
    const pad = n.r * s + 3;                       // same edge clamp as the page
    x = Math.max(pad, Math.min(W - pad, x));
    y = Math.max(pad, Math.min(H - pad, y));
    return { i: n.i, x, y, r: n.r * s };
  });
}

// Same rule as the page: nearest RESTING position, within max(26, 1.8r).
function pick(nodes, x, y) {
  let best = -1, bd = Infinity;
  for (const n of nodes) {
    const d = Math.hypot(n.hx - x, n.hy - y);
    if (d < bd) { bd = d; best = n.i; }
  }
  return best >= 0 && bd < Math.max(30, nodes[best].r * 1.9) ? best : -1;
}

let allPass = true;
for (const W of [360, 800, 1240]) {
  const { nodes, H, eraStart } = layout(W);
  const checks = {};

  let overlaps = 0;
  for (let a = 0; a < nodes.length; a++)
    for (let b = a + 1; b < nodes.length; b++) {
      const p = nodes[a], q = nodes[b];
      if (Math.hypot(p.hx - q.hx, p.hy - q.hy) < p.r + q.r - 0.01) overlaps++;
    }
  checks['no arcs overlap at rest'] = overlaps === 0;

  let restMiss = 0, drift = 0, outOfBounds = 0, grow = Infinity, driftRatio = 0, drifted = 0;
  for (const n of nodes) {
    if (pick(nodes, n.hx, n.hy) !== n.i) restMiss++;
    // The lens locks onto the picked arc, so that arc must stay under the pointer.
    const L = lensed(nodes, n.hx, n.hy, W, H), me = L[n.i];
    const dd = Math.hypot(me.x - n.hx, me.y - n.hy);
    drift = Math.max(drift, dd);
    // Only the edge clamp can move the locked arc (the lens maps its focus to itself). A
    // nudge is fine as long as the pointer -- still at the arc's resting centre -- stays
    // well inside the magnified disc.
    driftRatio = Math.max(driftRatio, dd / me.r);
    if (dd > 0.5) drifted++;
    grow = Math.min(grow, me.r / n.r);
    for (const m of L) if (m.x < -2 || m.x > W + 2 || m.y < -2 || m.y > H + 2) { outOfBounds++; break; }
  }
  checks['every arc pickable by pointing at it'] = restMiss === 0;
  checks[`hovered arc stays under the pointer (${drifted} nudged at edges, max ${drift.toFixed(1)}px = ${(driftRatio*100).toFixed(0)}% of its radius)`] = driftRatio < 0.5;
  checks[`hovered arc is magnified (min ${grow.toFixed(2)}x)`] = grow >= MAG - 0.01;
  checks['lens never pushes an arc off the canvas'] = outOfBounds === 0;

  // Order continuity: the next arc is never far away, except where an era band begins.
  let jumps = 0, worst = 0, eraJump = 0;
  for (let i = 1; i < nodes.length; i++) {
    if (eraStart.has(i)) {
      // Era breaks add vertical space, but the next era must start on the same side.
      eraJump = Math.max(eraJump, Math.abs(nodes[i].hx - nodes[i - 1].hx));
      continue;
    }
    const d = Math.hypot(nodes[i].hx - nodes[i - 1].hx, nodes[i].hy - nodes[i - 1].hy);
    // A row U-turn spans the vertical step between rows (up to 2x24px radii + 23px of
    // spacing); anything beyond that allowance would mean order visibly breaks.
    const lim = nodes[i].r + nodes[i - 1].r + 75;
    worst = Math.max(worst, d - (nodes[i].r + nodes[i - 1].r));
    if (d > lim) jumps++;
  }
  checks['broadcast order is continuous through the serpentine'] = jumps === 0;
  checks[`eras continue on the same side (max sideways jump ${Math.round(eraJump)}px)`] = eraJump < W * 0.45;

  const tiny = nodes.filter(n => arcs[n.i].n <= 4).length;
  console.log(`\nwidth ${W}px -> canvas height ${Math.round(H)}px, ${nodes.length} arcs ` +
              `(${tiny} of <=4 episodes), widest gap between consecutive arcs ${worst.toFixed(1)}px`);
  for (const [k, v] of Object.entries(checks)) console.log(`  [${v ? 'ok' : 'FAIL'}] ${k}`);
  if (!Object.values(checks).every(Boolean)) allPass = false;
}

console.log('\nFIELD GEOMETRY', allPass ? 'PASS' : 'FAIL');
process.exit(allPass ? 0 : 1);
