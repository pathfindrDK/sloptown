import type { FileEntry } from "../api/types";

export type TreeNode = {
  name: string;
  path: string;
  dir: boolean;
  children: TreeNode[];
  file?: FileEntry;
  changed: boolean; // file changed, or a descendant changed
};

const HIDDEN = [/^\.git\//, /(^|\/)node_modules\//, /^\.opencode\//, /^\.data\//];

export function buildTree(files: FileEntry[], changed: Set<string> = new Set()): TreeNode[] {
  const root: TreeNode = { name: "", path: "", dir: true, children: [], changed: false };
  for (const f of files) {
    if (HIDDEN.some((re) => re.test(f.path))) continue;
    const parts = f.path.split("/");
    let node = root;
    parts.forEach((name, i) => {
      const path = parts.slice(0, i + 1).join("/");
      const isFile = i === parts.length - 1;
      let child = node.children.find((c) => c.name === name && c.dir === !isFile);
      if (!child) {
        child = { name, path, dir: !isFile, children: [], changed: false };
        node.children.push(child);
      }
      if (isFile) {
        child.file = f;
        child.changed = f.changed || changed.has(f.path);
      }
      node = child;
    });
  }
  const finish = (n: TreeNode): boolean => {
    n.children.sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name) : a.dir ? -1 : 1));
    if (n.dir) n.changed = n.children.map(finish).some(Boolean);
    return n.changed;
  };
  finish(root);
  return root.children;
}
