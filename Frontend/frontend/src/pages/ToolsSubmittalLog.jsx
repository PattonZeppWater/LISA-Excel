import { useState, useRef } from "react";
import {
  slDownloadTemplate,
  slParsePdf,
  slCompile,
  slCompilePdf,
  slLoad,
} from "../services/api";

// ── Constants ──────────────────────────────────────────────────────────────

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

const LABEL_STYLE = {
  fontSize: "0.72rem",
  color: "var(--text-dim)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  marginBottom: "3px",
  display: "block",
};

const TH_STYLE = {
  fontSize: "0.7rem", color: "var(--text-dim)", fontWeight: 600, textTransform: "uppercase",
  letterSpacing: "0.06em", padding: "4px 6px", textAlign: "left",
  borderBottom: "1px solid var(--border)",
};

const TD_STYLE = { padding: "3px 4px", verticalAlign: "middle" };

const SECTION_HEADING = {
  fontSize: "0.72rem",
  fontWeight: 700,
  color: "var(--text-label)",
  textTransform: "uppercase",
  letterSpacing: "0.1em",
  borderBottom: "1px solid var(--border)",
  paddingBottom: "4px",
  marginBottom: "12px",
};

const EMPTY_FIELDS = {
  project_name: "", project_number: "", end_customer: "", site_name: "",
  release_package: "", deliverable_id: "", revision: "00", date: "",
  subject: "", response_requested: true, filename: "",
};

const EMPTY_MARKUP = { rfi: [], equal_substitutions: [], deviations: [], exclusions: [] };

let _nextCommentId = 1;
let _nextRowId = 1;

function newComment() {
  return { id: _nextCommentId++, status: "OPEN", document_name: "", rows: [] };
}
function newRow() {
  return { id: _nextRowId++, by: "", revision: "", comment: "" };
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function ToolsSubmittalLog() {
  const [fields, setFields]     = useState({ ...EMPTY_FIELDS });
  const [comments, setComments] = useState([]);
  const [markup, setMarkup]     = useState({ ...EMPTY_MARKUP });
  const [pdfFile, setPdfFile]   = useState(null);
  const [loading, setLoading]   = useState(null);
  const [status, setStatus]     = useState(null);

  const pdfRef  = useRef(null);
  const loadRef = useRef(null);

  const busy = loading !== null;

  function setField(key, value) {
    setFields(prev => ({ ...prev, [key]: value }));
  }

  function addComment() {
    setComments(prev => [...prev, newComment()]);
  }
  function removeComment(id) {
    setComments(prev => prev.filter(c => c.id !== id));
  }
  function updateComment(id, key, value) {
    setComments(prev => prev.map(c => c.id === id ? { ...c, [key]: value } : c));
  }
  function addRow(commentId) {
    setComments(prev => prev.map(c =>
      c.id === commentId ? { ...c, rows: [...c.rows, newRow()] } : c
    ));
  }
  function removeRow(commentId, rowId) {
    setComments(prev => prev.map(c =>
      c.id === commentId ? { ...c, rows: c.rows.filter(r => r.id !== rowId) } : c
    ));
  }
  function updateRow(commentId, rowId, key, value) {
    setComments(prev => prev.map(c =>
      c.id === commentId
        ? { ...c, rows: c.rows.map(r => r.id === rowId ? { ...r, [key]: value } : r) }
        : c
    ));
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

  async function handleParsePdf(file) {
    if (!file) return;
    setLoading("pdf"); setStatus(null);
    const r = await slParsePdf(file);
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    const { rfi = [], equal_substitutions = [], deviations = [], exclusions = [], ignored = [] } = r.data;
    setMarkup({ rfi, equal_substitutions, deviations, exclusions });
    setPdfFile(file);
    const total = rfi.length + equal_substitutions.length + deviations.length + exclusions.length;
    setStatus({ type: "success", message: `Parsed ${total} markup items (${ignored.length} ignored).` });
  }

  async function handleDownloadTemplate() {
    setLoading("template"); setStatus(null);
    const r = await slDownloadTemplate();
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    triggerDownload(r.blob, r.filename);
  }

  async function handleLoad(file) {
    if (!file) return;
    setLoading("load"); setStatus(null);
    const r = await slLoad(file);
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    const { fields: f = {}, comments: c = [], markup: m = {} } = r.data;
    setFields({ ...EMPTY_FIELDS, ...f });
    setComments((c || []).map(item => ({
      id: _nextCommentId++,
      status: item.status || "OPEN",
      document_name: item.document_name || "",
      rows: (item.rows || []).map(row => ({ id: _nextRowId++, ...row })),
    })));
    setMarkup({ ...EMPTY_MARKUP, ...m });
    setStatus({ type: "success", message: "Document loaded." });
  }

  async function handleCompile() {
    setLoading("compile"); setStatus(null);
    const body = {
      fields,
      comments: comments.map(({ id, rows, ...rest }) => ({
        ...rest,
        rows: rows.map(({ id: _, ...r }) => r),
      })),
      markup,
    };
    const r = await slCompile(body);
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    triggerDownload(r.blob, r.filename);
    setStatus({ type: "success", message: `Downloaded ${r.filename}.` });
  }

  async function handleCompilePdf() {
    if (!pdfFile) return;
    setLoading("compile-pdf"); setStatus(null);
    const body = {
      fields,
      comments: comments.map(({ id, rows, ...rest }) => ({
        ...rest,
        rows: rows.map(({ id: _, ...r }) => r),
      })),
      markup,
    };
    const r = await slCompilePdf(body, pdfFile);
    setLoading(null);
    if (!r.ok) { setStatus({ type: "error", message: r.error }); return; }
    triggerDownload(r.blob, r.filename);
    setStatus({ type: "success", message: `Downloaded ${r.filename}.` });
  }

  const markupTotal = markup.rfi.length + markup.equal_substitutions.length +
                      markup.deviations.length + markup.exclusions.length;

  return (
    <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "20px", maxWidth: "760px" }}>

      {/* ── Action buttons ──────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
        <input
          ref={loadRef}
          type="file"
          accept=".docx"
          style={{ display: "none" }}
          onChange={e => { handleLoad(e.target.files[0]); e.target.value = ""; }}
        />
        <button
          className="btn btn-primary"
          onClick={handleDownloadTemplate}
          disabled={busy}
          style={{ fontSize: "0.82rem" }}
        >
          {loading === "template" ? "Downloading…" : "Download Template"}
        </button>
        <button
          className="btn btn-primary"
          onClick={() => loadRef.current?.click()}
          disabled={busy}
          style={{ fontSize: "0.82rem" }}
        >
          {loading === "load" ? "Loading…" : "Load Existing .DOCX"}
        </button>
        <button
          className="btn btn-primary"
          onClick={handleCompile}
          disabled={busy}
          style={{ fontSize: "0.82rem" }}
        >
          {loading === "compile" ? "Compiling…" : "Download .DOCX"}
        </button>
        <button
          className="btn btn-primary"
          onClick={handleCompilePdf}
          disabled={busy || !pdfFile}
          style={{
            fontSize: "0.82rem",
            opacity: pdfFile ? 1 : 0.45,
            cursor: pdfFile ? "pointer" : "not-allowed",
          }}
        >
          {loading === "compile-pdf" ? "Compiling…" : "Download PDF"}
        </button>
      </div>

      {status && <p className={`status-msg ${status.type}`}>{status.message}</p>}

      {/* ── Project fields ──────────────────────────────────────────────────── */}
      <div>
        <div style={SECTION_HEADING}>Project Info</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 16px" }}>
          <FieldInput label="Project Name"    value={fields.project_name}    onChange={v => setField("project_name", v)} />
          <FieldInput label="Project Number"  value={fields.project_number}  onChange={v => setField("project_number", v)} />
          <FieldInput label="End Customer"    value={fields.end_customer}    onChange={v => setField("end_customer", v)} />
          <FieldInput label="Site Name"       value={fields.site_name}       onChange={v => setField("site_name", v)} />
          <FieldInput label="Release Package" value={fields.release_package} onChange={v => setField("release_package", v)} />
          <FieldInput label="Deliverable ID"  value={fields.deliverable_id}  onChange={v => setField("deliverable_id", v)} />
          <FieldInput label="Revision"        value={fields.revision}        onChange={v => setField("revision", v)} placeholder="00" />
          <FieldInput label="Date"            value={fields.date}            onChange={v => setField("date", v)} placeholder="YYYY.MM.DD" />
        </div>
        <div style={{ marginTop: "10px" }}>
          <FieldInput label="Subject" value={fields.subject} onChange={v => setField("subject", v)} />
        </div>
        <div style={{ marginTop: "10px", display: "flex", alignItems: "center", gap: "16px" }}>
          <span style={LABEL_STYLE}>Response Requested</span>
          {["Yes", "No"].map((opt, i) => (
            <label key={opt} style={{ display: "flex", alignItems: "center", gap: "5px", cursor: "pointer", fontSize: "0.82rem", color: "var(--text)" }}>
              <input
                type="radio"
                name="response_requested"
                checked={fields.response_requested === (i === 0)}
                onChange={() => setField("response_requested", i === 0)}
                style={{ accentColor: "var(--accent)" }}
              />
              {opt}
            </label>
          ))}
        </div>
        <div style={{ marginTop: "10px" }}>
          <FieldInput label="Output Filename" value={fields.filename} onChange={v => setField("filename", v)} placeholder="Submittal_Rev00.docx" />
        </div>
      </div>

      {/* ── Review Comments ─────────────────────────────────────────────────── */}
      <div>
        <div style={{ ...SECTION_HEADING, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span>Review Comments ({comments.length})</span>
          <button className="btn btn-primary" onClick={addComment} style={{ fontSize: "0.75rem", padding: "2px 10px" }}>
            + Add Comment
          </button>
        </div>
        {comments.length === 0 ? (
          <p style={{ color: "var(--text-faint)", fontSize: "0.82rem" }}>No comments. Click "+ Add Comment" to begin.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {comments.map((comment, ci) => (
              <CommentCard
                key={comment.id}
                index={ci + 1}
                comment={comment}
                onUpdate={(key, value) => updateComment(comment.id, key, value)}
                onRemove={() => removeComment(comment.id)}
                onAddRow={() => addRow(comment.id)}
                onRemoveRow={(rowId) => removeRow(comment.id, rowId)}
                onUpdateRow={(rowId, key, value) => updateRow(comment.id, rowId, key, value)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Markup Summary ──────────────────────────────────────────────────── */}
      <div>
        <div style={SECTION_HEADING}>Markup Summary</div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <input
            ref={pdfRef}
            type="file"
            accept=".pdf"
            style={{ display: "none" }}
            onChange={e => { handleParsePdf(e.target.files[0]); e.target.value = ""; }}
          />
          <button
            className="btn btn-primary"
            onClick={() => pdfRef.current?.click()}
            disabled={busy}
            style={{ fontSize: "0.82rem" }}
          >
            {loading === "pdf" ? "Parsing…" : "Upload Bluebeam PDF"}
          </button>
          {markupTotal > 0 && (
            <div style={{ display: "flex", gap: "12px", fontSize: "0.78rem" }}>
              <MarkupPill label="RFI"         count={markup.rfi.length}                color="var(--accent)" />
              <MarkupPill label="Equal Subs"  count={markup.equal_substitutions.length} color="var(--status-success-soft)" />
              <MarkupPill label="Deviations"  count={markup.deviations.length}          color="var(--status-warning)" />
              <MarkupPill label="Exclusions"  count={markup.exclusions.length}          color="var(--status-error-soft)" />
            </div>
          )}
          {markupTotal === 0 && (
            <span style={{ color: "var(--text-faint)", fontSize: "0.82rem" }}>No markup items. Upload the marked-up Bluebeam PDF.</span>
          )}
          {markupTotal > 0 && (
            <button
              onClick={() => { setMarkup({ ...EMPTY_MARKUP }); setPdfFile(null); setStatus(null); }}
              style={{ background: "none", border: "none", color: "var(--text-danger-muted)", cursor: "pointer", fontSize: "0.78rem" }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

    </div>
  );
}


// ── Sub-components ─────────────────────────────────────────────────────────

function FieldInput({ label, value, onChange, placeholder }) {
  return (
    <div>
      <label style={LABEL_STYLE}>{label}</label>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder || ""}
        style={INPUT_STYLE}
      />
    </div>
  );
}

function CommentCard({ index, comment, onUpdate, onRemove, onAddRow, onRemoveRow, onUpdateRow }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ background: "var(--bg-subtle)", padding: "6px 10px", display: "flex", alignItems: "center", gap: "12px" }}>
        <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--text-label)", minWidth: "80px" }}>
          Comment {index}
        </span>
        <select
          value={comment.status}
          onChange={e => onUpdate("status", e.target.value)}
          style={{ ...INPUT_STYLE, width: "auto", minWidth: "90px" }}
        >
          <option value="OPEN">OPEN</option>
          <option value="CLOSED">CLOSED</option>
        </select>
        <button onClick={onRemove} style={{ marginLeft: "auto", background: "none", border: "none", color: "var(--text-danger-muted)", cursor: "pointer", fontSize: "1rem" }}>×</button>
      </div>
      <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)" }}>
        <FieldInput label="Document Name" value={comment.document_name} onChange={v => onUpdate("document_name", v)} />
      </div>
      <div style={{ padding: "0 10px 8px", borderTop: "1px solid var(--border)" }}>
        {comment.rows.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ ...TH_STYLE, width: "15%" }}>By</th>
                <th style={{ ...TH_STYLE, width: "12%" }}>Revision</th>
                <th style={{ ...TH_STYLE }}>Comment / Response</th>
                <th style={{ ...TH_STYLE, width: "28px" }}></th>
              </tr>
            </thead>
            <tbody>
              {comment.rows.map(row => (
                <tr key={row.id}>
                  <td style={TD_STYLE}><input type="text" value={row.by}       onChange={e => onUpdateRow(row.id, "by",       e.target.value)} style={INPUT_STYLE} /></td>
                  <td style={TD_STYLE}><input type="text" value={row.revision} onChange={e => onUpdateRow(row.id, "revision", e.target.value)} style={INPUT_STYLE} /></td>
                  <td style={TD_STYLE}><input type="text" value={row.comment}  onChange={e => onUpdateRow(row.id, "comment",  e.target.value)} style={INPUT_STYLE} /></td>
                  <td style={TD_STYLE}>
                    <button onClick={() => onRemoveRow(row.id)} style={{ background: "none", border: "none", color: "var(--text-danger-muted)", cursor: "pointer", fontSize: "1rem", padding: "0 2px" }}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <button className="btn btn-primary" onClick={onAddRow} style={{ fontSize: "0.72rem", padding: "2px 8px", marginTop: "6px" }}>+ Add Row</button>
      </div>
    </div>
  );
}

function MarkupPill({ label, count, color }) {
  return (
    <span style={{ color, fontWeight: count > 0 ? 600 : 400 }}>
      {label}: {count}
    </span>
  );
}
