import { useState, useRef } from "react";
import {
  dfBuildMarkdown,
  dfBuildStructureZip,
  dfBuildContentsZip,
} from "../services/api";

// ── Constants ─────────────────────────────────────────────────────────────────

const MAX_DEPTH = 3;

const INPUT_STYLE = {
  background: "var(--bg-input)",
  color:      "var(--text)",
  border:     "1px solid var(--border-strong)",
  borderRadius: "4px",
  padding:    "4px 8px",
  fontSize:   "0.85rem",
  outline:    "none",
  flex:       1,
  minWidth:   0,
};

const ACCENT      = ["var(--accent)", "var(--status-success-soft)", "var(--status-warning)"]; // per depth (0,1,2)
const INDENT_PX   = 24;   // depth offset between cards (must match marginLeft)
const ROW_GAP_PX  = 10;   // tree container's vertical gap between cards

let _nextId = 1;
const newFolder = () => ({ id: _nextId++, name: "", files: [], children: [] });

// ── Immutable tree helpers ────────────────────────────────────────────────────

function updateAt(tree, path, updater) {
  if (path.length === 0) return tree;
  const [head, ...rest] = path;
  return tree.map((node, i) => {
    if (i !== head) return node;
    if (rest.length === 0) return updater(node);
    return { ...node, children: updateAt(node.children, rest, updater) };
  });
}

function removeAt(tree, path) {
  if (path.length === 1) return tree.filter((_, i) => i !== path[0]);
  const [head, ...rest] = path;
  return tree.map((node, i) => {
    if (i !== head) return node;
    return { ...node, children: removeAt(node.children, rest) };
  });
}

function moveAt(tree, path, delta) {
  if (path.length === 1) {
    const idx = path[0];
    const target = idx + delta;
    if (target < 0 || target >= tree.length) return tree;
    const next = [...tree];
    [next[idx], next[target]] = [next[target], next[idx]];
    return next;
  }
  const [head, ...rest] = path;
  return tree.map((node, i) => {
    if (i !== head) return node;
    return { ...node, children: moveAt(node.children, rest, delta) };
  });
}

// Pre-order traversal — produces a flat render list. Cards then live as
// siblings (not nested DOM), so every card has the same right edge and the
// action-cluster buttons line up vertically regardless of depth.
function flattenTree(tree, basePath = []) {
  const out = [];
  tree.forEach((node, i) => {
    const path = [...basePath, i];
    out.push({
      node,
      path,
      depth:   path.length - 1,
      isFirst: i === 0,
      isLast:  i === tree.length - 1,
    });
    out.push(...flattenTree(node.children, path));
  });
  return out;
}

// For each flattened entry, mark which ancestor depths are "closed" at this
// row — i.e., this row is the last descendant of the ancestor at that depth.
// Drives the horizontal elbow stub rendered at the end of each vertical line.
function annotateClosures(list) {
  return list.map((e, i) => {
    const next = list[i + 1];
    const closesAt = new Set();
    for (let a = 0; a < e.depth; a++) {
      if (!next || next.depth <= a || next.path[a] !== e.path[a]) {
        closesAt.add(a);
      }
    }
    return { ...e, closesAt };
  });
}

// ── Payload helpers ───────────────────────────────────────────────────────────

function payloadForJson(tree) {
  // Markdown / structure zip — file names only, no slots needed
  return tree.map(node => ({
    name: node.name,
    files: node.files.map(f => ({ name: f.name })),
    children: payloadForJson(node.children),
  }));
}

function payloadForContents(tree) {
  // Contents zip — needs slot identifiers + collected File objects.
  // Skip slots whose blob is null (files loaded from a markdown file have no
  // underlying blob); backend will emit zero-byte placeholders for those.
  const fileMap = {};
  function walk(nodes) {
    return nodes.map(node => ({
      name: node.name,
      files: node.files.map(f => {
        if (f.file) fileMap[f.slot] = f.file;
        return { slot: f.slot, name: f.name };
      }),
      children: walk(node.children),
    }));
  }
  return { tree: walk(tree), files: fileMap };
}

// Parse a markdown file produced by build_markdown(...) back into a tree.
// Format: header line `# Structure`, then nested bullets where folders are
// `- **Name**` and files are `- filename`. Indentation = 2 spaces per depth.
function parseMarkdown(text) {
  const tree  = [];
  const stack = [];   // [{ depth, node }]
  const lines = text.split(/\r?\n/);

  for (const raw of lines) {
    const m = raw.match(/^( *)- (.+?)\s*$/);
    if (!m) continue;
    const indent  = m[1].length;
    const content = m[2];

    const folderMatch = content.match(/^\*\*(.+)\*\*$/);

    if (folderMatch) {
      const depth = indent / 2;
      if (!Number.isInteger(depth) || depth < 0 || depth >= MAX_DEPTH) continue;
      while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();

      const node = { id: _nextId++, name: folderMatch[1], files: [], children: [] };
      if (stack.length === 0) tree.push(node);
      else                    stack[stack.length - 1].node.children.push(node);
      stack.push({ depth, node });
    } else {
      const parentDepth = indent / 2 - 1;
      if (!Number.isInteger(parentDepth) || parentDepth < 0) continue;
      let parent = null;
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i].depth === parentDepth) { parent = stack[i].node; break; }
      }
      if (!parent) continue;
      parent.files.push({ slot: `f_${_nextId++}`, name: content, file: null });
    }
  }

  return tree;
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a   = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ToolsDocForge() {
  const [tree, setTree]       = useState([]);
  const [loading, setLoading] = useState(null);  // "md" | "zip" | "contents"
  const [status, setStatus]   = useState(null);
  const loadRef = useRef(null);

  const busy = loading !== null;
  const totalFiles = countFiles(tree);
  const totalFolders = countFolders(tree);

  async function handleLoadMarkdown(file) {
    if (!file) return;
    if (tree.length > 0 && !window.confirm("Replace the current structure with the contents of this file?")) return;
    setStatus(null);
    try {
      const text   = await file.text();
      const parsed = parseMarkdown(text);
      setTree(parsed);
      const tiers = countFolders(parsed);
      const files = countFiles(parsed);
      const msg = files > 0
        ? `Loaded ${tiers} tier${tiers !== 1 ? "s" : ""} and ${files} file reference${files !== 1 ? "s" : ""}. Re-attach files via "+ Content" to include them in a Contents ZIP.`
        : `Loaded ${tiers} tier${tiers !== 1 ? "s" : ""} from ${file.name}.`;
      setStatus({ type: "success", message: msg });
    } catch (e) {
      setStatus({ type: "error", message: `Failed to parse markdown: ${e.message}` });
    }
  }

  function addRoot() {
    setTree(t => [...t, newFolder()]);
  }
  function addChild(path) {
    setTree(t => updateAt(t, path, n => ({ ...n, children: [...n.children, newFolder()] })));
  }
  function removeNode(path) {
    setTree(t => removeAt(t, path));
  }
  function renameNode(path, name) {
    setTree(t => updateAt(t, path, n => ({ ...n, name })));
  }
  function addFiles(path, fileList) {
    const incoming = Array.from(fileList || []).map(file => ({
      slot: `f_${_nextId++}`,
      name: file.name,
      file,
    }));
    if (incoming.length === 0) return;
    setTree(t => updateAt(t, path, n => ({ ...n, files: [...n.files, ...incoming] })));
  }
  function removeFile(path, slot) {
    setTree(t => updateAt(t, path, n => ({ ...n, files: n.files.filter(f => f.slot !== slot) })));
  }
  function moveNode(path, delta) {
    setTree(t => moveAt(t, path, delta));
  }

  async function handleMarkdown() {
    setLoading("md"); setStatus(null);
    const r = await dfBuildMarkdown(payloadForJson(tree));
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    triggerDownload(r.blob, r.filename);
    setStatus({ type: "success", message: `Downloaded ${r.filename}.` });
  }

  async function handleStructureZip() {
    setLoading("zip"); setStatus(null);
    const r = await dfBuildStructureZip(payloadForJson(tree));
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    triggerDownload(r.blob, r.filename);
    setStatus({ type: "success", message: `Downloaded ${r.filename}.` });
  }

  async function handleContentsZip() {
    setLoading("contents"); setStatus(null);
    const { tree: jsonTree, files } = payloadForContents(tree);
    const r = await dfBuildContentsZip(jsonTree, files);
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    triggerDownload(r.blob, r.filename);
    setStatus({ type: "success", message: `Downloaded ${r.filename}.` });
  }

  return (
    <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px", maxWidth: "1100px" }}>

      <div>
        <h1 className="page-title" style={{ marginBottom: "4px" }}>DocForge</h1>
        <p style={{ color: "var(--text-label)", fontSize: "0.85rem", margin: 0 }}>
          Build a three-tier structure and export it as Markdown, an empty-structure ZIP,
          or a ZIP populated with the files you attach.
        </p>
      </div>

      {/* Output toolbar */}
      <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
        <input
          ref={loadRef}
          type="file"
          accept=".md,.txt,text/markdown"
          style={{ display: "none" }}
          onChange={e => { handleLoadMarkdown(e.target.files[0]); e.target.value = ""; }}
        />
        <button
          className="btn btn-primary"
          onClick={() => loadRef.current?.click()}
          disabled={busy}
          style={{ fontSize: "0.82rem" }}
        >
          Load Markdown
        </button>
        <span style={{ borderLeft: "1px solid var(--border)", height: "22px", margin: "0 4px" }} />
        <button
          className="btn btn-primary"
          onClick={handleMarkdown}
          disabled={busy || tree.length === 0}
          style={{ fontSize: "0.82rem" }}
        >
          {loading === "md" ? "Building…" : "Download Markdown"}
        </button>
        <button
          className="btn btn-primary"
          onClick={handleStructureZip}
          disabled={busy || tree.length === 0}
          style={{ fontSize: "0.82rem" }}
        >
          {loading === "zip" ? "Zipping…" : "Download Structure (Empty)"}
        </button>
        <button
          className="btn btn-primary"
          onClick={handleContentsZip}
          disabled={busy || tree.length === 0}
          style={{ fontSize: "0.82rem" }}
        >
          {loading === "contents" ? "Zipping…" : "Download Contents (ZIP)"}
        </button>
        <button
          className="btn btn-primary"
          disabled
          style={{ fontSize: "0.82rem", opacity: 0.45, cursor: "not-allowed" }}
          title="Coming soon"
        >
          Compile PDF
        </button>

        {totalFolders > 0 && (
          <span style={{ color: "var(--text-label)", fontSize: "0.78rem", marginLeft: "auto" }}>
            {totalFolders} tier{totalFolders !== 1 ? "s" : ""} · {totalFiles} file{totalFiles !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {status && <p className={`status-msg ${status.type}`}>{status.message}</p>}

      {/* Tree */}
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {tree.length === 0 && (
          <p style={{ color: "var(--text-faint)", fontSize: "0.82rem" }}>
            Empty. Click <strong>+ Add Top Tier</strong> to begin.
          </p>
        )}
        {annotateClosures(flattenTree(tree)).map(({ node, path, depth, isFirst, isLast, closesAt }) => (
          <FolderNode
            key={node.id}
            node={node}
            path={path}
            depth={depth}
            isFirst={isFirst}
            isLast={isLast}
            closesAt={closesAt}
            onAddChild={addChild}
            onRemove={removeNode}
            onRename={renameNode}
            onAddFiles={addFiles}
            onRemoveFile={removeFile}
            onMove={moveNode}
          />
        ))}
        <button
          className="btn btn-primary"
          onClick={addRoot}
          style={{ fontSize: "0.82rem", alignSelf: "flex-start" }}
        >
          + Add Top Tier
        </button>
      </div>
    </div>
  );
}

// ── Folder node (recursive) ───────────────────────────────────────────────────

function FolderNode({ node, path, depth, isFirst, isLast, closesAt, onAddChild, onRemove, onRename, onAddFiles, onRemoveFile, onMove }) {
  const fileRef = useRef(null);
  const canHaveChildren = depth < MAX_DEPTH - 1;
  const accent = ACCENT[depth] || "var(--accent)";

  const iconBtnStyle = (disabled) => ({
    background: "none",
    border: "none",
    color: disabled ? "var(--text-faint)" : "var(--text-label)",
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: "0.9rem",
    padding: "0 4px",
    lineHeight: 1,
  });

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border)",
        borderLeft: `3px solid ${accent}`,
        borderRadius: "6px",
        padding: "10px 12px",
        marginLeft: `${depth * INDENT_PX}px`,   // depth indent without nesting DOM
        position: "relative",                   // anchor for ancestor guide lines
      }}
    >
      {/* Continuous accent stripes for ancestor tiers — each line sits in the
          column where that ancestor's own borderLeft was drawn, and extends up
          through the preceding gap so it visually fuses with the ancestor. */}
      {Array.from({ length: depth }, (_, a) => (
        <div
          key={`anc-v-${a}`}
          style={{
            position: "absolute",
            left:     `${(a - depth) * INDENT_PX}px`,
            top:      `-${ROW_GAP_PX}px`,
            bottom:   0,
            width:    "3px",
            background: ACCENT[a] || "var(--accent)",
            pointerEvents: "none",
          }}
        />
      ))}

      {/* Horizontal elbow stub — small "└" tick at the bottom of each ancestor
          line whose subtree closes on this row. Forms a visual encapsulation. */}
      {Array.from({ length: depth }, (_, a) => closesAt?.has(a) ? (
        <div
          key={`anc-h-${a}`}
          style={{
            position: "absolute",
            left:     `${(a - depth) * INDENT_PX}px`,
            bottom:   0,
            width:    "14px",
            height:   "3px",
            background: ACCENT[a] || "var(--accent)",
            pointerEvents: "none",
          }}
        />
      ) : null)}
      {/* Header row */}
      <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: "0.78rem", color: accent, fontWeight: 700, minWidth: "48px" }}>
          TIER {depth + 1}
        </span>
        <input
          type="text"
          value={node.name}
          onChange={e => onRename(path, e.target.value)}
          placeholder={`Tier ${depth + 1} name`}
          style={{ ...INPUT_STYLE, maxWidth: "320px" }}
        />
        <input
          ref={fileRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={e => { onAddFiles(path, e.target.files); e.target.value = ""; }}
        />
        <div style={{ marginLeft: "auto", display: "flex", gap: "8px", alignItems: "center" }}>
          {canHaveChildren && (
            <button
              className="btn btn-primary"
              onClick={() => onAddChild(path)}
              style={{ fontSize: "0.75rem", padding: "3px 10px" }}
            >
              + Sub-Tier
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={() => fileRef.current?.click()}
            style={{ fontSize: "0.75rem", padding: "3px 10px" }}
          >
            + Content
          </button>
          <button
            onClick={() => !isFirst && onMove(path, -1)}
            disabled={isFirst}
            style={iconBtnStyle(isFirst)}
            title="Move up"
          >
            ↑
          </button>
          <button
            onClick={() => !isLast && onMove(path, +1)}
            disabled={isLast}
            style={iconBtnStyle(isLast)}
            title="Move down"
          >
            ↓
          </button>
          <button
            onClick={() => onRemove(path)}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-danger-muted)",
              cursor: "pointer",
              fontSize: "1.1rem",
              padding: "0 4px",
              lineHeight: 1,
            }}
            title="Remove tier"
          >
            ×
          </button>
        </div>
      </div>

      {/* Files in this folder */}
      {node.files.length > 0 && (
        <div style={{ marginTop: "8px", marginLeft: "60px", display: "flex", flexDirection: "column", gap: "3px" }}>
          {node.files.map(f => (
            <div
              key={f.slot}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                fontSize: "0.78rem",
                color: "var(--text)",
              }}
            >
              <span style={{ color: "var(--text-dim)" }}>📄</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.name}>
                {f.name}
              </span>
              <span style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>
                {formatBytes(f.file?.size)}
              </span>
              <button
                onClick={() => onRemoveFile(path, f.slot)}
                style={{ background: "none", border: "none", color: "var(--text-danger-muted)", cursor: "pointer", fontSize: "0.95rem" }}
                title="Remove file"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

    </div>
  );
}

// ── Misc helpers ──────────────────────────────────────────────────────────────

function countFolders(tree) {
  return tree.reduce((sum, n) => sum + 1 + countFolders(n.children), 0);
}
function countFiles(tree) {
  return tree.reduce((sum, n) => sum + n.files.length + countFiles(n.children), 0);
}
function formatBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024)       return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
