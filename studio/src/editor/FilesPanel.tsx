import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, File, Folder, Lock } from "lucide-react";
import { api } from "../api/client";
import type { FileEntry } from "../api/types";
import { buildTree, type TreeNode } from "../lib/fileTree";
import { useT } from "../lib/i18n";
import { useLoad } from "../lib/useLoad";
import { Loading } from "../components/Loading";
import { LoadFailed } from "../components/LoadFailed";

type Props = {
  projectId: string;
  version: number; // bumped by files.changed
  ready: boolean; // project container status; refetch when it becomes ready
  changedFiles: string[];
  activePath: string | null;
  onOpen: (path: string) => void;
};

export function FilesPanel({ projectId, version, ready, changedFiles, activePath, onOpen }: Props) {
  const t = useT();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const { data, failed, retry } = useLoad(() => api.files(projectId), [projectId, version, ready]);
  const files: FileEntry[] | null = data?.files ?? null;

  const tree = useMemo(() => buildTree(files ?? [], new Set(changedFiles)), [files, changedFiles]);

  if (!files) return failed ? <LoadFailed onRetry={retry} /> : <Loading label={t("common.loading")} />;
  if (!tree.length) return <div className="empty">{t("files.empty")}</div>;

  const toggle = (path: string) =>
    setCollapsed((s) => {
      const n = new Set(s);
      if (n.has(path)) n.delete(path);
      else n.add(path);
      return n;
    });

  const render = (nodes: TreeNode[], depth: number) =>
    nodes.map((n) => (
      <li key={n.path}>
        <button
          className={`tree__row${n.path === activePath ? " is-active" : ""}${n.file?.locked ? " is-locked" : ""}`}
          style={{ paddingLeft: `calc(var(--miira-space-2) + ${depth} * var(--miira-space-4))` }}
          onClick={() => (n.dir ? toggle(n.path) : onOpen(n.path))}
          title={n.file?.locked ? t("code.locked") : n.path}
        >
          {n.dir ? (
            collapsed.has(n.path) ? <ChevronRight size={14} /> : <ChevronDown size={14} />
          ) : (
            <span className="tree__indent" />
          )}
          {n.dir ? <Folder size={14} /> : <File size={14} />}
          <span className="tree__name">{n.name}</span>
          {n.file?.locked && <Lock size={12} className="muted" />}
          {n.changed && <span className="tree__changed" title={t("files.changed")} />}
        </button>
        {n.dir && !collapsed.has(n.path) && <ul className="tree">{render(n.children, depth + 1)}</ul>}
      </li>
    ));

  return <ul className="tree tree--root">{render(tree, 0)}</ul>;
}
