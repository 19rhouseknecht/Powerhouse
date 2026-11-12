/* A small 3D view of a room's lighting layout: <canvas data-light-layout='{"room":{length,width,height},"positions":[[x,y],..],
   "spacing":[sx,sy],"fixture":..,"count":..,"footcandles":..,"target":..}'>. Drag to turn it; double-click to reset.
   Plain canvas 2D with a hand-rolled projection: no libraries. Drawn far to near so nearer things cover farther ones. */
(function () {
  function draw(canvas, view) {
    var data = canvas._layout, room = data.room, ctx = canvas.getContext('2d');
    var ratio = window.devicePixelRatio || 1, cssW = canvas.clientWidth || 320, cssH = canvas.clientHeight || 220;
    if (canvas.width !== Math.round(cssW * ratio)) { canvas.width = Math.round(cssW * ratio); canvas.height = Math.round(cssH * ratio); }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    var L = room.length, W = room.width, H = room.height;
    var yaw = view.yaw, pitch = view.pitch, cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    function rot(x, y, z) {                       /* room coordinates (ft, centred) -> view: [screen x, screen y, depth] */
      x -= L / 2; y -= W / 2; z -= H / 2;
      var rx = x * cy - y * sy, ry = x * sy + y * cy;
      return [rx, -(z * cp - ry * sp), ry * cp + z * sp];
    }
    var corners = [], i;
    for (i = 0; i < 8; i++) corners.push(rot(i & 1 ? L : 0, i & 2 ? W : 0, i & 4 ? H : 0));
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    corners.forEach(function (c) { minX = Math.min(minX, c[0]); maxX = Math.max(maxX, c[0]); minY = Math.min(minY, c[1]); maxY = Math.max(maxY, c[1]); });
    var scale = Math.min((cssW - 24) / (maxX - minX), (cssH - 40) / (maxY - minY));
    var ox = cssW / 2 - (minX + maxX) / 2 * scale, oy = (cssH - 16) / 2 - (minY + maxY) / 2 * scale + 4;
    function P(x, y, z) { var r = rot(x, y, z); return [ox + r[0] * scale, oy + r[1] * scale, r[2]]; }
    function poly(points, fill, stroke) {
      ctx.beginPath(); points.forEach(function (p, n) { n ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); }); ctx.closePath();
      if (fill) { ctx.fillStyle = fill; ctx.fill(); }
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
    }
    /* floor */
    poly([P(0, 0, 0), P(L, 0, 0), P(L, W, 0), P(0, W, 0)], '#1f1b18', '#57534e');
    /* the two walls farthest from the viewer, so the room reads as a box */
    var walls = [
      {pts: [P(0, 0, 0), P(L, 0, 0), P(L, 0, H), P(0, 0, H)], mid: rot(L / 2, 0, H / 2)[2]},
      {pts: [P(L, 0, 0), P(L, W, 0), P(L, W, H), P(L, 0, H)], mid: rot(L, W / 2, H / 2)[2]},
      {pts: [P(L, W, 0), P(0, W, 0), P(0, W, H), P(L, W, H)], mid: rot(L / 2, W, H / 2)[2]},
      {pts: [P(0, W, 0), P(0, 0, 0), P(0, 0, H), P(0, W, H)], mid: rot(0, W / 2, H / 2)[2]}
    ].sort(function (a, b) { return a.mid - b.mid; });
    walls.slice(0, 2).forEach(function (w) { poly(w.pts, 'rgba(120,113,108,.18)', 'rgba(168,162,158,.45)'); });
    /* pools of light on the floor (added together, so overlaps brighten) */
    var reach = Math.max(data.spacing ? Math.max(data.spacing[0], data.spacing[1]) : 4, 2) * 0.8;
    ctx.save(); ctx.globalCompositeOperation = 'lighter';
    data.positions.forEach(function (p) {
      var c = P(p[0], p[1], 0), edge = P(p[0] + reach, p[1], 0), edge2 = P(p[0], p[1] + reach, 0);
      var r = Math.max(Math.hypot(edge[0] - c[0], edge[1] - c[1]), Math.hypot(edge2[0] - c[0], edge2[1] - c[1]));
      var g = ctx.createRadialGradient(c[0], c[1], 0, c[0], c[1], r);
      g.addColorStop(0, 'rgba(253,224,150,.42)'); g.addColorStop(0.55, 'rgba(253,200,100,.14)'); g.addColorStop(1, 'rgba(253,200,100,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(c[0], c[1], r, 0, Math.PI * 2); ctx.fill();
    });
    ctx.restore();
    /* beams and fixtures, far ones first */
    data.positions.map(function (p) { return {p: p, d: rot(p[0], p[1], H)[2]}; }).sort(function (a, b) { return a.d - b.d; }).forEach(function (f) {
      var top = P(f.p[0], f.p[1], H), base = P(f.p[0], f.p[1], 0), half = reach * 0.45, a = P(f.p[0] - half * cy, f.p[1] + half * sy, 0), b = P(f.p[0] + half * cy, f.p[1] - half * sy, 0);
      var beam = ctx.createLinearGradient(top[0], top[1], base[0], base[1]);
      beam.addColorStop(0, 'rgba(254,240,200,.30)'); beam.addColorStop(1, 'rgba(254,240,200,0)');
      ctx.beginPath(); ctx.moveTo(top[0], top[1]); ctx.lineTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.closePath(); ctx.fillStyle = beam; ctx.fill();
      var glow = ctx.createRadialGradient(top[0], top[1], 0, top[0], top[1], 9);
      glow.addColorStop(0, 'rgba(255,251,235,1)'); glow.addColorStop(0.4, 'rgba(253,230,138,.85)'); glow.addColorStop(1, 'rgba(253,230,138,0)');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(top[0], top[1], 9, 0, Math.PI * 2); ctx.fill();
    });
    /* the ceiling's edge and the near walls, outlined only */
    ctx.setLineDash([4, 4]); poly([P(0, 0, H), P(L, 0, H), P(L, W, H), P(0, W, H)], null, 'rgba(168,162,158,.55)'); ctx.setLineDash([]);
    walls.slice(2).forEach(function (w) { [[0, 3], [1, 2]].forEach(function (e) { ctx.beginPath(); ctx.moveTo(w.pts[e[0]][0], w.pts[e[0]][1]); ctx.lineTo(w.pts[e[1]][0], w.pts[e[1]][1]); ctx.strokeStyle = 'rgba(168,162,158,.35)'; ctx.stroke(); }); });
    /* words */
    ctx.fillStyle = '#d6d3d1'; ctx.font = '600 11px system-ui, sans-serif'; ctx.textBaseline = 'bottom';
    ctx.fillText(L + ' × ' + W + ' ft · ' + H + ' ft ceiling', 8, cssH - 6);
    var right = data.count + ' lights · ' + data.footcandles + ' fc (target ' + data.target + ')';
    ctx.fillText(right, cssW - 8 - ctx.measureText(right).width, cssH - 6);
  }
  function start(canvas) {
    try { canvas._layout = JSON.parse(canvas.getAttribute('data-light-layout')); } catch (e) { return; }
    if (!canvas._layout || !canvas._layout.room || !canvas._layout.positions) return;
    var home = {yaw: -0.6, pitch: 0.62}, view = {yaw: home.yaw, pitch: home.pitch}, drag = null;
    canvas.addEventListener('pointerdown', function (e) { drag = {x: e.clientX, y: e.clientY, yaw: view.yaw, pitch: view.pitch}; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', function (e) {
      if (!drag) return;
      view.yaw = drag.yaw + (e.clientX - drag.x) * 0.012;
      view.pitch = Math.min(1.45, Math.max(0.15, drag.pitch + (e.clientY - drag.y) * 0.008));
      draw(canvas, view);
    });
    ['pointerup', 'pointercancel'].forEach(function (n) { canvas.addEventListener(n, function () { drag = null; }); });
    canvas.addEventListener('dblclick', function () { view.yaw = home.yaw; view.pitch = home.pitch; draw(canvas, view); });
    draw(canvas, view);
    window.addEventListener('resize', function () { draw(canvas, view); });
    canvas.setAttribute('data-drawn', '1');
  }
  function all() { document.querySelectorAll('canvas[data-light-layout]').forEach(start); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', all); else all();
})();
