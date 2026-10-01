"""Export a roads-and-nodes SVG detail for one manually annotated junction."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
NS = "{http://www.w3.org/2000/svg}"
ET.register_namespace("", NS[1:-1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junction_id")
    args = parser.parse_args()
    graph = json.loads((ROOT / "data/networks/synthetic-preview.json").read_text("utf-8"))
    record = next(r for r in graph["sourceGraph"]["manualJunctionAnnotations"]
                  if r["id"] == args.junction_id)
    x, y = record["centerMeters"]
    left, top, size = x - 90, -y - 90, 180
    root = deepcopy(ET.parse(ROOT / "data/networks/synthetic-preview-audit.svg").getroot())
    root.set("viewBox", f"{left} {top} {size} {size}")
    root.set("width", "1000")
    root.set("height", "1000")
    root.find(NS + "title").text = f"{record.get('name', args.junction_id)}：转弯与过街校对（未实地核实）"
    for group in root.findall(NS + "g"):
        for item in list(group):
            if item.tag == NS + "circle":
                px, py = float(item.get("cx")), float(item.get("cy"))
                keep = left <= px <= left + size and top <= py <= top + size
            else:
                values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", item.get("d", ""))]
                xs, ys = values[::2], values[1::2]
                keep = (bool(xs) and max(xs) >= left and min(xs) <= left + size
                        and max(ys) >= top and min(ys) <= top + size)
            if not keep:
                group.remove(item)
    output = ROOT / f"data/networks/{args.junction_id}.svg"
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    ET.parse(output)
    print(output)


if __name__ == "__main__":
    main()
