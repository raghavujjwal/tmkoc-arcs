/* Layout, lens and picking for the arc field -- the single source of truth, loaded by the
   page (window.ArcLayout) and by scripts/check_field.js (require), so the geometry the
   test checks is the geometry the page draws.

   Layout: each era is a spiral cluster. The era's first arc sits at the centre and later
   arcs wind outward, so broadcast order reads centre -> rim, then on to the next era. Arcs
   are packed greedily along an Archimedean spiral: each one takes the first spot past its
   predecessor that keeps a clear gap from every arc already placed. The pitch is sized for
   typical arcs; where a large one would crowd the previous turn, the spiral simply skips
   ahead, which is what gives the clusters their loose, organic edge instead of a grid.

   The five clusters then flow down the page in a staggered zigzag, each rotated so its
   outer end points toward the next era. */
(function(root){
  var GAP = 12;                     // clear space between any two arcs, px
  var LENS_R = 170, LENS_K = 3.0, MAG = 2.3;

  function radiusOf(a){ return Math.max(4, Math.min(21, 2.8 + 2.1*Math.sqrt(a.n))); }

  function spiralCluster(radii){
    var sorted = radii.slice().sort(function(a,b){return a-b;});
    var p75 = sorted[Math.floor(sorted.length*0.75)] || 8;
    var pitch = 2*p75 + GAP;        // distance between spiral turns
    var pts = [{x:0, y:0, r:radii[0]}], th = 0, floor = 0;
    function free(x, y, r){
      for(var j=pts.length-1;j>=0;j--){
        var p = pts[j], dx = p.x-x, dy = p.y-y, need = p.r + r + GAP;
        if(dx*dx + dy*dy < need*need) return false;
      }
      return true;
    }
    for(var k=1;k<radii.length;k++){
      var r = radii[k], placed = false;
      while(!placed){
        var base = Math.max(pitch*th/(2*Math.PI), floor);
        th += Math.min(0.2, 1.6/Math.max(base, 6));
        base = Math.max(pitch*th/(2*Math.PI), floor);
        // When a large arc on the previous turn blocks the spiral, step OUTWARD at this
        // angle rather than racing ahead along the curve -- racing ahead left gaps of up to
        // 200px between consecutive arcs and made the order hard to follow.
        for(var rho = base; rho <= base + pitch; rho += 2){
          var x = rho*Math.cos(th), y = rho*Math.sin(th);
          if(free(x, y, r)){
            pts.push({x:x, y:y, r:r, th:th});
            floor = Math.max(floor, rho - pitch);   // never fold back inside a pushed turn
            placed = true; break;
          }
        }
      }
    }
    var R = 0;
    pts.forEach(function(p){ R = Math.max(R, Math.sqrt(p.x*p.x + p.y*p.y) + p.r); });
    return {pts:pts, R:R};
  }

  // Does cluster b's circle touch the label box above cluster a?
  var LABEL_W = 70, LABEL_H = 30;
  function hitsLabel(a, b){
    var top = a.cy - a.R - 34, x0 = a.cx - LABEL_W, x1 = a.cx + LABEL_W, y0 = top, y1 = top + LABEL_H;
    var nx = Math.max(x0, Math.min(b.cx, x1)), ny = Math.max(y0, Math.min(b.cy, y1));
    var dx = b.cx - nx, dy = b.cy - ny;
    return dx*dx + dy*dy < (b.R + 6)*(b.R + 6);
  }

  function compute(arcs, eras, W){
    // Wide: a W-shaped zigzag across the page (top, bottom, top, ...). Medium: two
    // staggered columns. Narrow: one column.
    var mode = W >= 1000 ? 'wave' : (W >= 620 ? 'two' : 'one');
    var margin = mode === 'one' ? 12 : 24, LABEL = 34, TOP = 50, SEP = LABEL + 14;
    var byEra = {}; arcs.forEach(function(a, i){ (byEra[a.era] = byEra[a.era] || []).push(i); });
    var eraList = eras.filter(function(e){ return byEra[e]; }), n = eraList.length;

    // Largest cluster radius each mode can hold without neighbours touching.
    var Rmax = mode === 'wave'
      ? (W - 2*margin - (n > 1 ? SEP*(n-1)/2 : 0)) / (n + 1)
      : mode === 'two' ? (W - 2*margin) * 0.36 : (W - 2*margin) / 2;

    var clusters = eraList.map(function(era){
      var idx = byEra[era];
      var sp = spiralCluster(idx.map(function(i){ return radiusOf(arcs[i]); }));
      var s = Math.min(1, Rmax / sp.R);            // shrink a cluster only if it cannot fit
      return {era:era, idx:idx, pts:sp.pts, R:sp.R*s, s:s,
              first:arcs[idx[0]].start_ep, last:arcs[idx[idx.length-1]].end_ep};
    });

    // Horizontal positions per mode; vertical position found by moving each cluster down
    // only as far as needed to clear every earlier cluster and its label.
    var step = n > 1 ? (W - 2*margin - 2*Rmax) / (n - 1) : 0;
    clusters.forEach(function(c, k){
      if(mode === 'wave') c.cx = margin + Rmax + k*step;
      else if(mode === 'two') c.cx = k % 2 === 0 ? margin + c.R + W*0.04 : W - margin - c.R - W*0.04;
      else c.cx = W/2;
      c.cy = TOP + LABEL + c.R;
      // In two-column mode, stagger so the chain visibly zigzags rather than forming rows.
      if(mode === 'two' && k > 0) c.cy = Math.max(c.cy, clusters[k-1].cy + 0.55*c.R);
      for(;;){
        var clear = true;
        for(var j=0;j<k && clear;j++){
          var o = clusters[j], dx = o.cx - c.cx, dy = o.cy - c.cy, need = o.R + c.R + 14;
          if(dx*dx + dy*dy < need*need) clear = false;
          // Circles clearing is not enough: each cluster's label box must clear the other.
          else if(hitsLabel(o, c) || hitsLabel(c, o)) clear = false;
        }
        if(clear && c.cy - c.R - LABEL >= TOP) break;
        c.cy += 4;
      }
    });

    // Rotate each spiral so its last arc faces the next era's cluster.
    clusters.forEach(function(c, k){
      var last = c.pts[c.pts.length-1], endAng = Math.atan2(last.y, last.x), rot = 0;
      if(k < clusters.length - 1){
        var nx = clusters[k+1];
        rot = Math.atan2(nx.cy - c.cy, nx.cx - c.cx) - endAng;
      } else rot = Math.PI*0.15 - endAng;
      var cs = Math.cos(rot), sn = Math.sin(rot);
      c.pts.forEach(function(p){
        var x = p.x*cs - p.y*sn, y = p.x*sn + p.y*cs;
        p.hx = c.cx + x*c.s; p.hy = c.cy + y*c.s; p.rr = p.r*c.s;
      });
      c.labelY = c.cy - c.R - LABEL;
    });

    var nodes = [], H = 0;
    clusters.forEach(function(c){
      c.idx.forEach(function(i, k){
        var p = c.pts[k];
        nodes[i] = {i:i, r:p.rr, hx:p.hx, hy:p.hy};
      });
      H = Math.max(H, c.cy + c.R);
    });
    return {nodes:nodes, clusters:clusters, H:Math.ceil(H + 40), W:W};
  }

  // Where the lens wants a node to sit when focused on (fx, fy): a Sarkar-Brown fisheye,
  // clamped so arcs near an edge bunch up instead of leaving the canvas.
  function lensTarget(n, fx, fy, W, H){
    var dx = n.hx - fx, dy = n.hy - fy, d = Math.sqrt(dx*dx + dy*dy), x = n.hx, y = n.hy, s = 1;
    if(d < LENS_R){
      var u = d / LENS_R, dd = LENS_R*(LENS_K+1)*u/(LENS_K*u + 1);
      if(d > 0.001){ x = fx + dx/d*dd; y = fy + dy/d*dd; }
      s = 1 + (MAG-1)*(1-u)*(1-u);
    }
    var pad = n.r*s + 3;
    return {x:Math.max(pad, Math.min(W - pad, x)), y:Math.max(pad, Math.min(H - pad, y)), s:s};
  }

  // Picking uses resting positions; the lens then centres on the picked arc so it stays
  // under the pointer.
  function pick(nodes, x, y){
    var best = -1, bd = 1e18;
    for(var i=0;i<nodes.length;i++){
      var n = nodes[i]; if(!n) continue;
      var dx = n.hx - x, dy = n.hy - y, d = dx*dx + dy*dy;
      if(d < bd){ bd = d; best = i; }
    }
    if(best < 0) return -1;
    var reach = Math.max(26, nodes[best].r*1.9);
    return bd < reach*reach ? best : -1;
  }

  var API = {GAP:GAP, LENS_R:LENS_R, LENS_K:LENS_K, MAG:MAG,
             radiusOf:radiusOf, compute:compute, lensTarget:lensTarget, pick:pick};
  if(typeof module !== 'undefined' && module.exports) module.exports = API;
  else root.ArcLayout = API;
})(this);
