import { useState, useRef } from "react";
import {
  tsDownloadData,
  tsPreview,
  tsProcess,
  tsReporting,
} from "../services/api";

// ── Constants ──────────────────────────────────────────────────────────────

const TABS = ["Review", "Reporting", "Get Data"];

const INPUT_STYLE = {
  background: "var(--bg-input)",
  color: "var(--text)",
  border: "1px solid var(--border-strong)",
  borderRadius: "4px",
  padding: "4px 8px",
  fontSize: "0.82rem",
  outline: "none",
  width: "100%",
};

// Cell background for highlight colors
const CELL_BG = {
  red:    "rgba(255,102,102,0.35)",
  yellow: "rgba(255,255,0,0.25)",
};

const TH_STYLE = {
  padding: "3px 8px",
  background: "var(--bg-input)",
  color: "var(--text-muted)",
  fontWeight: 600,
  fontSize: "0.75rem",
  whiteSpace: "nowrap",
  borderBottom: "1px solid var(--border)",
  position: "sticky",
  top: 0,
  zIndex: 1,
};

const TD_BASE = {
  padding: "2px 8px",
  fontSize: "0.75rem",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
  maxWidth: "180px",
  overflow: "hidden",
  textOverflow: "ellipsis",
};


// ── Page ───────────────────────────────────────────────────────────────────

export default function SharedTimesheets() {
  const [activeTab, setActiveTab] = useState("Review");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", background: "var(--bg-app)" }}>
      <div style={{ flexShrink: 0, background: "var(--bg-main)", padding: "16px 20px 0" }}>
        <h1 className="page-title" style={{ marginBottom: "12px" }}>Timesheet Review</h1>
        <div style={{ display: "flex", gap: "2px", borderBottom: "1px solid var(--border)" }}>
          {TABS.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: "4px 16px",
                border: "none",
                borderBottom: activeTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
                background: "transparent",
                color: activeTab === tab ? "var(--text)" : "var(--text-dim)",
                cursor: "pointer",
                fontSize: "0.85rem",
              }}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {activeTab === "Review"    && <ReviewTab />}
        {activeTab === "Reporting" && <ReportingTab />}
        {activeTab === "Get Data"  && <GetDataTab />}
      </div>
    </div>
  );
}


// ── Review tab ────────────────────────────────────────────────────────────

function ReviewTab() {
  const [file, setFile]           = useState(null);
  const [dragging, setDragging]   = useState(false);
  const [loading, setLoading]     = useState(null); // "preview" | "process"
  const [preview, setPreview]     = useState(null); // { rows, highlights, stats }
  const [status, setStatus]       = useState(null); // { type, message }
  const fileRef = useRef(null);

  async function loadPreview(targetFile) {
    if (!targetFile) return;
    setLoading("preview");
    setStatus(null);
    const result = await tsPreview(targetFile);
    setLoading(null);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    setPreview(result.data);
  }

  function acceptFile(f) {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".xlsx")) {
      setStatus({ type: "error", message: "Only .xlsx files are supported." });
      return;
    }
    setFile(f);
    setPreview(null);
    setStatus(null);
    loadPreview(f);
  }

  async function handleProcess() {
    if (!file) return;
    setLoading("process");
    setStatus(null);
    const result = await tsProcess(file);
    setLoading(null);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    // decode base64 and trigger download
    const bytes    = atob(result.data.file_b64);
    const arr      = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    const blob = new Blob([arr], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = result.data.filename; a.click();
    URL.revokeObjectURL(url);
    const s = result.data.stats;
    setStatus({
      type: "success",
      message: `Downloaded. Stats — 🔴 ${s.red} red  🟡 ${s.yellow} yellow  ✅ ${s.ok} ok`,
    });
  }

  const busy = loading !== null;

  return (
    <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px" }}>

      {/* Controls row */}
      <div style={{ display: "flex", gap: "16px", alignItems: "flex-start", flexWrap: "wrap" }}>

        {/* Drop zone */}
        <div
          className={`drop-zone${dragging ? " dragging" : ""}${file ? " has-file" : ""}`}
          style={{ minWidth: "240px", maxWidth: "280px" }}
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={e => { e.preventDefault(); setDragging(false); acceptFile(e.dataTransfer.files[0]); }}
          onClick={() => fileRef.current?.click()}
        >
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx"
            style={{ display: "none" }}
            onChange={e => acceptFile(e.target.files[0])}
          />
          <span className="drop-zone-icon">📊</span>
          {file
            ? <><span className="drop-zone-filename">{file.name}</span><span className="drop-zone-hint">Click or drop to replace</span></>
            : <><span className="drop-zone-primary">Drop AIC timesheet here</span><span className="drop-zone-hint"> or click to browse — .xlsx</span></>
          }
        </div>

        {/* Buttons + status */}
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", minWidth: "200px" }}>
          {loading === "preview" && (
            <div className="loading-indicator">
              <span className="spinner" />
              Checking Viewpoint…
            </div>
          )}
          <button
            className="btn btn-primary"
            style={{ textAlign: "left", width: "190px" }}
            onClick={handleProcess}
            disabled={!file || busy}
          >
            {loading === "process" ? "Processing…" : "Process & Download"}
          </button>

          {status && <p className={`status-msg ${status.type}`}>{status.message}</p>}
        </div>

        {/* Stats after preview */}
        {preview?.stats && (
          <StatsBadge stats={preview.stats} />
        )}

      </div>

      {/* Legend */}
      <div style={{ display: "flex", gap: "16px", fontSize: "0.75rem", color: "var(--text-label)" }}>
        <span><span style={{ background: CELL_BG.red, padding: "1px 8px", borderRadius: "3px", color: "var(--text)" }}>RED</span> &nbsp;Job or phase not in data</span>
        <span><span style={{ background: CELL_BG.yellow, padding: "1px 8px", borderRadius: "3px", color: "var(--text)" }}>YELLOW</span> &nbsp;Hours exceed remaining units</span>
      </div>

      {/* Preview table */}
      {preview && (
        <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: "6px" }}>
          <PreviewTable rows={preview.rows} highlights={preview.highlights} />
        </div>
      )}

      {!file && (
        <p style={{ color: "var(--text-faint)", fontSize: "0.82rem" }}>Upload an AIC timesheet to get started.</p>
      )}
    </div>
  );
}


function StatsBadge({ stats }) {
  return (
    <div style={{ display: "flex", gap: "12px", alignItems: "center", padding: "8px 14px", background: "var(--bg-input)", border: "1px solid var(--border)", borderRadius: "6px", fontSize: "0.82rem" }}>
      <span style={{ color: "var(--status-red-bright)" }}>🔴 {stats.red} red</span>
      <span style={{ color: "var(--status-yellow)" }}>🟡 {stats.yellow} yellow</span>
      <span style={{ color: "var(--status-success-soft)" }}>✅ {stats.ok} ok</span>
    </div>
  );
}


function PreviewTable({ rows, highlights }) {
  if (!rows?.length) return null;

  // Build O(1) highlight map: "rowIdx,colIdx" → color
  const hlMap = {};
  for (const h of highlights) {
    hlMap[`${h.row},${h.col}`] = h.color;
  }

  const headers = rows[0];
  const dataRows = rows.slice(1);

  return (
    <table style={{ borderCollapse: "collapse", minWidth: "100%", fontSize: "0.75rem" }}>
      <thead>
        <tr>
          {headers.map((h, ci) => (
            <th key={ci} style={TH_STYLE}>{h || String.fromCharCode(65 + ci)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {dataRows.map((row, ri) => (
          <tr key={ri} style={{ background: ri % 2 === 0 ? "var(--bg-app)" : "var(--bg-row-alt)" }}>
            {row.map((cell, ci) => {
              // ri+1 because row 0 is the header row
              const hlColor = hlMap[`${ri + 1},${ci}`];
              return (
                <td
                  key={ci}
                  style={{
                    ...TD_BASE,
                    color: cell ? "var(--text)" : "var(--text-faint)",
                    background: hlColor ? CELL_BG[hlColor] : undefined,
                  }}
                  title={cell || ""}
                >
                  {cell || ""}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}


// ── Reporting tab ─────────────────────────────────────────────────────────

function ReportingTab() {
  const [file, setFile]         = useState(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading]   = useState(false);
  const [status, setStatus]     = useState(null);
  const fileRef = useRef(null);

  function acceptFile(f) {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".zip")) {
      setStatus({ type: "error", message: "Only .zip files are supported." });
      return;
    }
    setFile(f);
    setStatus(null);
  }

  async function handleReport() {
    if (!file) return;
    setLoading(true);
    setStatus(null);
    const result = await tsReporting(file);
    setLoading(false);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    const url = URL.createObjectURL(result.blob);
    const a   = document.createElement("a");
    a.href = url; a.download = result.filename; a.click();
    URL.revokeObjectURL(url);
    setStatus({ type: "success", message: `Downloaded ${result.filename}.` });
  }

  return (
    <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px", maxWidth: "500px" }}>
      <p style={{ color: "var(--text-label)", fontSize: "0.82rem", margin: 0 }}>
        Upload a zip of AIC timesheet xlsx files to generate a Vista-import CSV and per-employee PDFs.
      </p>

      <div
        className={`drop-zone${dragging ? " dragging" : ""}${file ? " has-file" : ""}`}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); acceptFile(e.dataTransfer.files[0]); }}
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          style={{ display: "none" }}
          onChange={e => acceptFile(e.target.files[0])}
        />
        <span className="drop-zone-icon">🗜</span>
        {file
          ? <><span className="drop-zone-filename">{file.name}</span><span className="drop-zone-hint">Click or drop to replace</span></>
          : <><span className="drop-zone-primary">Drop timesheet zip here</span><span className="drop-zone-hint"> or click to browse — .zip</span></>
        }
      </div>

      <button
        className="btn btn-primary"
        style={{ width: "200px", textAlign: "left" }}
        onClick={handleReport}
        disabled={!file || loading}
      >
        {loading ? "Generating…" : "Generate Report"}
      </button>

      {status && <p className={`status-msg ${status.type}`}>{status.message}</p>}
    </div>
  );
}


// ── Get Data tab ──────────────────────────────────────────────────────────

function GetDataTab() {
  const [loading, setLoading] = useState(false);
  const [status, setStatus]   = useState(null);

  async function handleDownload() {
    setLoading(true);
    setStatus(null);
    const result = await tsDownloadData();
    setLoading(false);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    const bytes = atob(result.data.file_b64);
    const arr   = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    const blob = new Blob([arr], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = result.data.filename; a.click();
    URL.revokeObjectURL(url);
    setStatus({ type: "success", message: `Downloaded ${result.data.filename}.` });
  }

  return (
    <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px", maxWidth: "420px" }}>
      <p style={{ color: "var(--text-label)", fontSize: "0.82rem", margin: 0 }}>
        Fetches all remaining units from the Viewpoint API and downloads them as an Excel file (Job / Phase / RemainingUnits).
      </p>
      <button
        className="btn btn-primary"
        style={{ width: "220px", textAlign: "left" }}
        onClick={handleDownload}
        disabled={loading}
      >
        {loading ? "Fetching from Viewpoint…" : "Download Remaining Units"}
      </button>
      {status && <p className={`status-msg ${status.type}`}>{status.message}</p>}
    </div>
  );
}
