// Restrained map palette: the analytical SVG stays visually distinct from the base.
export const baiduMapStyle = [
  { featureType: "land", elementType: "geometry", stylers: { color: "#eaf0ecff" } },
  { featureType: "water", elementType: "geometry", stylers: { color: "#d8e9e8ff" } },
  { featureType: "green", elementType: "geometry", stylers: { color: "#d5e6d6ff" } },
  { featureType: "building", elementType: "geometry.fill", stylers: { color: "#f7faf7ff" } },
  { featureType: "building", elementType: "geometry.stroke", stylers: { color: "#d6e2daff" } },
  { featureType: "road", elementType: "geometry.fill", stylers: { color: "#fbfcf9ff" } },
  { featureType: "road", elementType: "geometry.stroke", stylers: { color: "#d2dfd9ff" } },
  { featureType: "poilabel", elementType: "labels.icon", stylers: { visibility: "off" } },
  { featureType: "poilabel", elementType: "labels.text.fill", stylers: { color: "#56716aff" } },
  { featureType: "road", elementType: "labels.text.fill", stylers: { color: "#406359ff" } },
];
