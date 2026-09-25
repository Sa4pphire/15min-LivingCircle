// 合成数据仅用于前端交互展示，不代表新江湾城的真实路网或设施。
export const presets = [
  { id: "center", label: "示意点 A", hint: "社区中部", x: 444, y: 310 },
  { id: "west", label: "示意点 B", hint: "西侧绿道", x: 294, y: 264 },
  { id: "east", label: "示意点 C", hint: "东侧路口", x: 640, y: 381 },
];

export const categories = [
  { id: "shopping", label: "生活购物", short: "购", color: "#C37954", baseGap: 26, description: "日常采购与便民服务" },
  { id: "medical", label: "医疗健康", short: "医", color: "#598A86", baseGap: 34, description: "基层医疗与药房" },
  { id: "education", label: "教育托育", short: "学", color: "#8180A6", baseGap: 19, description: "学校与托育设施" },
  { id: "leisure", label: "绿地休闲", short: "绿", color: "#66946E", baseGap: 13, description: "公园与日常活动空间" },
];

export const facilities = [
  { id: "s1", category: "shopping", name: "便民商店 01", x: 390, y: 237 },
  { id: "s2", category: "shopping", name: "社区超市 02", x: 607, y: 285 },
  { id: "s3", category: "shopping", name: "生活服务点 03", x: 710, y: 452 },
  { id: "s4", category: "shopping", name: "便民商店 04", x: 228, y: 449 },
  { id: "m1", category: "medical", name: "社区卫生点 01", x: 373, y: 391 },
  { id: "m2", category: "medical", name: "药房 02", x: 708, y: 242 },
  { id: "m3", category: "medical", name: "健康服务点 03", x: 171, y: 320 },
  { id: "e1", category: "education", name: "托育点 01", x: 536, y: 184 },
  { id: "e2", category: "education", name: "学校 02", x: 686, y: 386 },
  { id: "e3", category: "education", name: "学习空间 03", x: 271, y: 285 },
  { id: "l1", category: "leisure", name: "口袋公园 01", x: 445, y: 470 },
  { id: "l2", category: "leisure", name: "滨水绿地 02", x: 212, y: 170 },
  { id: "l3", category: "leisure", name: "社区绿地 03", x: 737, y: 338 },
];

// 可悬停的合成建筑轮廓，不代表真实建筑或设施入口。
export const buildingPois = [
  { id: "b1", name: "社区建筑 01", d: "M 245 205 H 306 V 237 H 245 Z" },
  { id: "b2", name: "社区建筑 02", d: "M 326 205 H 364 V 237 H 326 Z" },
  { id: "b3", name: "社区建筑 03", d: "M 480 204 H 555 V 237 H 480 Z" },
  { id: "b4", name: "社区建筑 04", d: "M 245 355 H 297 V 390 H 245 Z" },
  { id: "b5", name: "社区建筑 05", d: "M 479 354 H 537 V 395 H 479 Z" },
  { id: "b6", name: "社区建筑 06", d: "M 248 505 H 341 V 524 H 248 Z" },
  { id: "b7", name: "社区建筑 07", d: "M 706 502 H 758 V 526 H 706 Z" },
];

// 路径是界面示意线，不参与计算；真实版由 C++ 返回 reachableEdges / grayEdges。
export const streets = [
  { id: "north", d: "M 75 148 H 834" },
  { id: "middle", d: "M 75 301 H 834" },
  { id: "south", d: "M 75 454 H 834" },
  { id: "west", d: "M 180 58 V 573" },
  { id: "midwest", d: "M 420 58 V 573" },
  { id: "mideast", d: "M 651 58 V 573" },
  { id: "east", d: "M 807 58 V 573" },
  { id: "diagonal", d: "M 86 555 L 180 454 L 420 301 L 651 148 L 765 56" },
];

// 命中区域使用有名称的短街段；背景道路仍保持完整连续。
export const interactiveStreets = [
  { id: "north-west", name: "北侧步行街西段", d: "M 75 148 H 420" },
  { id: "north-east", name: "北侧步行街东段", d: "M 420 148 H 834" },
  { id: "middle-west", name: "社区中路西段", d: "M 75 301 H 420" },
  { id: "middle-east", name: "社区中路东段", d: "M 420 301 H 834" },
  { id: "south-west", name: "南侧步行街西段", d: "M 75 454 H 420" },
  { id: "south-east", name: "南侧步行街东段", d: "M 420 454 H 834" },
  { id: "west-north", name: "西侧纵路北段", d: "M 180 58 V 301" },
  { id: "west-south", name: "西侧纵路南段", d: "M 180 301 V 573" },
  { id: "midwest-north", name: "中轴步道北段", d: "M 420 58 V 301" },
  { id: "midwest-south", name: "中轴步道南段", d: "M 420 301 V 573" },
  { id: "mideast-north", name: "东侧纵路北段", d: "M 651 58 V 301" },
  { id: "mideast-south", name: "东侧纵路南段", d: "M 651 301 V 573" },
  { id: "east-north", name: "东缘步道北段", d: "M 807 58 V 301" },
  { id: "east-south", name: "东缘步道南段", d: "M 807 301 V 573" },
  { id: "diagonal-north", name: "斜向步道北段", d: "M 420 301 L 651 148 L 765 56" },
  { id: "diagonal-south", name: "斜向步道南段", d: "M 86 555 L 180 454 L 420 301" },
];

export const gapStreets = {
  shopping: [
    { id: "shopping-axis-south", streetId: "midwest-south", name: "中轴步道南段", d: "M 420 373 V 453" },
    { id: "shopping-axis-north", streetId: "midwest-north", name: "中轴步道北段", d: "M 420 160 V 227" },
    { id: "shopping-middle-east", streetId: "middle-east", name: "社区中路东段", d: "M 510 301 H 620" },
  ],
  medical: [
    { id: "medical-west-north", streetId: "west-north", name: "西侧纵路北段", d: "M 180 150 V 300" },
    { id: "medical-east-south", streetId: "mideast-south", name: "东侧纵路南段", d: "M 651 304 V 454" },
    { id: "medical-axis-north", streetId: "midwest-north", name: "中轴步道北段", d: "M 420 150 V 235" },
  ],
  education: [
    { id: "education-west-south", streetId: "west-south", name: "西侧纵路南段", d: "M 180 302 V 448" },
    { id: "education-axis-south", streetId: "midwest-south", name: "中轴步道南段", d: "M 420 304 V 403" },
    { id: "education-east-north", streetId: "mideast-north", name: "东侧纵路北段", d: "M 651 151 V 250" },
  ],
  leisure: [
    { id: "leisure-axis-north", streetId: "midwest-north", name: "中轴步道北段", d: "M 420 149 V 250" },
    { id: "leisure-east-south", streetId: "mideast-south", name: "东侧纵路南段", d: "M 651 303 V 370" },
    { id: "leisure-west-south", streetId: "west-south", name: "西侧纵路南段", d: "M 180 305 V 380" },
  ],
};

export function mockWalkingMinutes(origin, point) {
  // 只用于视觉演示；并非沿真实路网计算出的步行耗时。
  return Math.max(2, Math.round(Math.hypot(origin.x - point.x, origin.y - point.y) / 14 + 1));
}
