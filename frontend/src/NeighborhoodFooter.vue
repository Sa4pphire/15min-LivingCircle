<script setup>
// An invented, decorative neighbourhood; never a map or an analysis result.
const project = ([x, y, z = 0]) => [414 + (x - y) * .88, 30 + (x + y) * .41 - z];
const polygon = points => points.map(point => project(point).join(",")).join(" ");
const path = points => points.map((point, index) => `${index ? "L" : "M"}${project(point).join(" ")}`).join(" ");
const ground = polygon([[0, 0], [390, 0], [390, 320], [0, 320]]);
const roads = [[[0, 157], [390, 157]], [[185, 0], [185, 320]]].map(path);
const plots = [[12, 12, 153, 126], [205, 12, 172, 126], [12, 178, 153, 127], [205, 178, 172, 127]]
  .map(([x, y, w, d]) => polygon([[x, y], [x + w, y], [x + w, y + d], [x, y + d]]));
function building({ x, y, w, d, h }) {
  return {
    roof: polygon([[x, y, h], [x + w, y, h], [x + w, y + d, h], [x, y + d, h]]),
    left: polygon([[x, y + d], [x + w, y + d], [x + w, y + d, h], [x, y + d, h]]),
    right: polygon([[x + w, y], [x + w, y + d], [x + w, y + d, h], [x + w, y, h]]),
    roofLine: path([[x + 9, y + 9, h], [x + w - 9, y + 9, h], [x + w - 9, y + d - 9, h]]),
  };
}
const buildings = [
  { x: 30, y: 28, w: 48, d: 70, h: 34 }, { x: 100, y: 26, w: 45, d: 43, h: 50 },
  { x: 230, y: 24, w: 52, d: 85, h: 42 }, { x: 310, y: 28, w: 44, d: 45, h: 27 },
  { x: 30, y: 209, w: 98, d: 47, h: 29 }, { x: 231, y: 211, w: 54, d: 69, h: 38 },
  { x: 310, y: 210, w: 44, d: 44, h: 58 },
].sort((a, b) => (a.x + a.y + a.w + a.d) - (b.x + b.y + b.w + b.d)).map(building);
const trees = [[96, 115], [121, 115], [146, 115], [320, 116], [344, 116],
  [49, 285], [74, 285], [99, 285], [326, 285], [348, 285]].map(point => project(point));
const origin = project([185, 157]);
const routes = [[[185, 157], [185, 64]], [[185, 157], [322, 157]],
  [[185, 157], [185, 287]], [[185, 157], [52, 157]]].map(path);
</script>

<template>
  <footer class="neighborhood-footer" aria-hidden="true">
    <div class="neighborhood-inner">
      <div class="neighborhood-note">
        <span class="neighborhood-note-mark"></span>
        <p>一个起点，连接生活。</p>
      </div>
      <svg class="neighborhood-art" viewBox="90 -30 730 400" focusable="false">
        <polygon :points="ground" class="neighborhood-ground" />
        <polygon v-for="(plot, index) in plots" :key="`plot-${index}`" :points="plot" class="neighborhood-plot" />
        <path v-for="(road, index) in roads" :key="`road-${index}`" :d="road" class="neighborhood-road" />
        <g class="neighborhood-routes">
          <path v-for="(route, index) in routes" :key="index" :d="route" pathLength="1" />
        </g>
        <g v-for="(block, index) in buildings" :key="`building-${index}`" class="neighborhood-building">
          <polygon :points="block.left" class="building-left" />
          <polygon :points="block.right" class="building-right" />
          <polygon :points="block.roof" class="building-roof" />
          <path :d="block.roofLine" class="building-roof-line" />
        </g>
        <g v-for="(tree, index) in trees" :key="`tree-${index}`" :transform="`translate(${tree[0]} ${tree[1]})`" class="neighborhood-tree">
          <path d="M0 0V-8" /><ellipse cy="-12" rx="6" ry="8" />
        </g>
        <g :transform="`translate(${origin[0]} ${origin[1]})`" class="neighborhood-origin">
          <ellipse rx="15" ry="7" class="origin-ring" /><ellipse rx="7" ry="3.5" class="origin-core" />
          <path d="M0-3V-18" /><circle cy="-22" r="4" />
        </g>
      </svg>
    </div>
  </footer>
</template>

<style scoped>
.neighborhood-footer { flex: none; height: clamp(150px, 22vh, 220px); overflow: hidden; background: #fbfdfc; }
.neighborhood-inner {
  position: relative; display: flex; align-items: center; justify-content: space-between; gap: 24px;
  width: min(100%, 1740px); height: 100%; margin-inline: auto; padding-inline: clamp(22px, 3.8vw, 64px);
}
.neighborhood-note { display: flex; align-items: center; gap: 12px; flex: none; }
.neighborhood-note p { margin: 0; color: #6b8b7c; font-size: clamp(14px, 1.1vw, 19px); font-weight: 400; letter-spacing: .04em; }
.neighborhood-note-mark { position: relative; width: 7px; height: 7px; border-radius: 50%; background: #6c9b86; }
.neighborhood-note-mark::after { content: ""; position: absolute; inset: -5px; border: 1px solid #d7e6dd; border-radius: 50%; }
.neighborhood-art { flex: none; width: min(56vw, 630px); height: 120%; overflow: visible; }
.neighborhood-ground { fill: #f3f7f4; }
.neighborhood-plot { fill: #eaf1ec; }
.neighborhood-road { fill: none; stroke: #fbfdfc; stroke-width: 25; }
.neighborhood-building { stroke: #a5bdb0; stroke-width: .95; stroke-linejoin: round; }
.building-left { fill: #f1f6f2; }
.building-right { fill: #e0ebe3; }
.building-roof { fill: #fbfdfc; }
.building-roof-line { fill: none; stroke: #d6e3da; stroke-width: .7; }
.neighborhood-tree path { fill: none; stroke: #96b5a3; stroke-width: 1.4; }
.neighborhood-tree ellipse { fill: #d1e4d5; stroke: #a5c1ae; stroke-width: .75; }
.neighborhood-origin { fill: #568c77; stroke: #568c77; stroke-width: 1.3; }
.origin-ring { fill: none; stroke: #afcbbc; }
.origin-core { fill: #d1e5d9; stroke: none; }
.neighborhood-routes path { fill: none; stroke: #77ab91; stroke-width: 2; stroke-linecap: round; stroke-dasharray: 1; stroke-dashoffset: 1; transition: stroke-dashoffset .7s cubic-bezier(.2,.7,.3,1); }
@media (hover: hover) and (pointer: fine) {
  .neighborhood-footer:hover .neighborhood-routes path { stroke-dashoffset: 0; }
}
@media (max-width: 700px) {
  .neighborhood-footer { height: 152px; }
  .neighborhood-inner { align-items: flex-end; padding-inline: 18px; gap: 0; }
  .neighborhood-note { z-index: 1; align-self: flex-end; margin-bottom: 20px; gap: 10px; }
  .neighborhood-note p { font-size: 12px; }
  .neighborhood-art { position: absolute; right: -80px; top: -15px; width: 360px; height: 168px; opacity: .65; }
}
@media (max-height: 600px) { .neighborhood-footer { height: 120px; } }
@media (prefers-reduced-motion: reduce), (hover: none) {
  .neighborhood-routes path { transition: none; stroke-dashoffset: 0; opacity: .55; }
}
</style>
