// Graph records are immutable: an edit replaces changed records, while this
// history retains only those records instead of copying the complete graph.
const copy = value => value === undefined ? null : JSON.parse(JSON.stringify(value));

export function graphSnapshot(graph) {
  return { nodes: graph.nodes.slice(), edges: graph.edges.slice() };
}

export function graphPatch(before, after) {
  const patch = { nodes: [], edges: [] };
  for (const key of ['nodes', 'edges']) {
    const oldItems = new Map(before[key].map((item, index) => [item.id, { item, index }]));
    const newItems = new Map(after[key].map((item, index) => [item.id, { item, index }]));
    for (const id of new Set([...oldItems.keys(), ...newItems.keys()])) {
      const old = oldItems.get(id), current = newItems.get(id);
      if (old?.item === current?.item) continue;
      if (old && current && JSON.stringify(old.item) === JSON.stringify(current.item)) continue;
      patch[key].push({ id, before: copy(old?.item), after: copy(current?.item),
        beforeIndex: old?.index, afterIndex: current?.index });
    }
    const commonOld = before[key].filter(item => newItems.has(item.id)).map(item => item.id);
    const commonNew = after[key].filter(item => oldItems.has(item.id)).map(item => item.id);
    if (commonOld.some((id, index) => id !== commonNew[index])) {
      patch[key + 'Order'] = { before: before[key].map(item => item.id), after: after[key].map(item => item.id) };
    }
  }
  return patch;
}

export function applyGraphPatch(graph, patch, direction) {
  for (const key of ['nodes', 'edges']) {
    const changes = new Map(patch[key].map(change => [change.id, change]));
    const present = new Set(graph[key].map(item => item.id));
    const items = [];
    for (const item of graph[key]) {
      if (!changes.has(item.id)) items.push(item);
      else if (changes.get(item.id)[direction]) items.push(copy(changes.get(item.id)[direction]));
    }
    const additions = patch[key].filter(change => change[direction] && !present.has(change.id));
    additions.sort((a, b) => a[direction + 'Index'] - b[direction + 'Index']);
    for (const change of additions) items.splice(change[direction + 'Index'], 0, copy(change[direction]));
    if (patch[key + 'Order']) {
      const lookup = new Map(items.map(item => [item.id, item]));
      graph[key] = patch[key + 'Order'][direction].map(id => lookup.get(id)).filter(Boolean);
    } else graph[key] = items;
  }
}

export class GraphHistory {
  constructor({ maxSteps = 30, maxBytes = 16 * 1024 * 1024 } = {}) {
    this.maxSteps = maxSteps; this.maxBytes = maxBytes; this.clear();
  }
  clear() { this.entries = []; this.cursor = 0; this.bytes = 0; }
  get undoCount() { return this.cursor; }
  get redoCount() { return this.entries.length - this.cursor; }
  record(before, after) {
    const patch = graphPatch(before, after);
    if (!patch.nodes.length && !patch.edges.length && !patch.nodesOrder && !patch.edgesOrder) return false;
    for (const entry of this.entries.splice(this.cursor)) this.bytes -= entry.bytes;
    const bytes = JSON.stringify(patch).length * 2;
    this.entries.push({ patch, bytes }); this.bytes += bytes; this.cursor++;
    // Always retain the most recent operation, including a large graph import.
    while (this.entries.length > 1 && (this.entries.length > this.maxSteps || this.bytes > this.maxBytes)) {
      this.bytes -= this.entries.shift().bytes; this.cursor--;
    }
    return true;
  }
  undo(graph) {
    if (!this.cursor) return false;
    applyGraphPatch(graph, this.entries[--this.cursor].patch, 'before'); return true;
  }
  redo(graph) {
    if (this.cursor >= this.entries.length) return false;
    applyGraphPatch(graph, this.entries[this.cursor++].patch, 'after'); return true;
  }
}
