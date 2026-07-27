import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  parseIdpWorkbook,
  getIdpTemplate,
  getIdpAutoCADStatus,
  browseIdpOutputFolder,
  generateIdpDwg,
  downloadIdpWorkbook,
  downloadIdpWireLabels,
  downloadIdpTemplate,
  exportIdpConduitList,
} from "../services/api";

// ── Inline styles (matches LISA dark theme) ────────────────────────────────

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

const TABLE_CELL = {
  padding: "2px 8px",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
  maxWidth: "200px",
  overflow: "hidden",
  textOverflow: "ellipsis",
  fontSize: "0.8rem",
  color: "var(--text)",
};

const TH_STYLE = {
  ...TABLE_CELL,
  background: "var(--bg-input)",
  color: "var(--text-muted)",
  fontWeight: 600,
  position: "sticky",
  top: 0,
  zIndex: 1,
};


// ── Conduit-list CSV helpers ───────────────────────────────────────────────
// A conduit list can be uploaded two ways:
//   .txt  — one conduit name per line (or comma-separated); every listed name is generated.
//   .csv  — headers "Conduit Name","Enabled/Disabled" where 1 = generate, 0 = skip.
// Export writes that CSV with every conduit enabled (1) by default so it can be edited
// in Excel and re-uploaded. Both upload forms feed the same "generate from list" flow.

function csvEscape(v) {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// Minimal RFC-4180-ish CSV parser: handles quoted fields, "" escapes, and CRLF/LF.
function parseCsvRows(text) {
  const rows = [];
  let row = [], field = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQ = false;
      } else field += c;
    } else if (c === '"') {
      inQ = true;
    } else if (c === ",") {
      row.push(field); field = "";
    } else if (c === "\n") {
      row.push(field); rows.push(row); row = []; field = "";
    } else if (c !== "\r") {
      field += c;
    }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.some(c => String(c).trim() !== ""));
}

// Enabled conduit names from a CSV → { names, enabled, disabled }. A row is DISABLED
// only when its 2nd column is explicitly 0 / no / false / off; a missing or blank flag
// counts as enabled (lenient). Names are de-duped case-insensitively.
function namesFromCsv(text) {
  const rows = parseCsvRows(text);
  if (!rows.length) return { names: [], enabled: 0, disabled: 0 };
  let start = 0;
  const h0 = String(rows[0][0] || "").trim().toLowerCase();
  if (h0.includes("conduit") || h0 === "name") start = 1;   // skip a header row
  const seen = new Set(), names = [];
  let disabled = 0;
  for (let i = start; i < rows.length; i++) {
    const name = String(rows[i][0] || "").trim();
    if (!name) continue;
    const flag = String(rows[i][1] ?? "").trim();
    let on;
    if (rows[i].length < 2 || flag === "") on = true;
    else {
      const num = Number(flag);
      on = Number.isNaN(num) ? !/^(no|false|disabled|off)$/i.test(flag) : num !== 0;
    }
    if (!on) { disabled++; continue; }
    const k = name.toUpperCase();
    if (!seen.has(k)) { seen.add(k); names.push(name); }
  }
  return { names, enabled: names.length, disabled };
}


// ── Component ──────────────────────────────────────────────────────────────

// Persist the working session so exiting LISA / switching tabs doesn't wipe the loaded
// workbook, the fills you edited, your output folder, or which drawings are generated.
const SESSION_KEY = "lisa_idp_session_v1";

export default function IdpGeneration() {
  const _sess = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY) || "null") || {}; }
    catch { return {}; }
  }, []);

  const [wb, setWb]                     = useState(_sess.wb || null);   // { conduit_index, fill_index, original_b64, filename }
  const [outputFolder, setOutputFolder] = useState(_sess.outputFolder || "");
  const [projectNumber, setProjectNumber] = useState(_sess.projectNumber || "");
  const [fileSuffix, setFileSuffix]       = useState(_sess.fileSuffix || "");
  const [makeProject, setMakeProject]     = useState(_sess.makeProject || false);  // "Make it a project"
  const [activeTab, setActiveTab]       = useState("conduit");
  const [autocad, setAutocad]           = useState(null);   // { running, version }
  const [loading, setLoading]           = useState(null);   // page-level op key
  const [rowLoading, setRowLoading]     = useState({});     // { conduit_ident: true }
  const [rowResult, setRowResult]       = useState(_sess.rowResult || {});  // { conduit_ident: { ok, path, warnings, error } } (persisted)
  const [status, setStatus]             = useState(null);   // { type, message }
  const [dragging, setDragging]         = useState(false);
  const [genLog, setGenLog]             = useState("");
  const [jsonPasteOpen, setJsonPasteOpen] = useState(false);
  const [jsonPasteText, setJsonPasteText] = useState("");
  const [jsonPasteErr,  setJsonPasteErr]  = useState(null);
  const [editingCell,   setEditingCell]   = useState(null); // { index: "conduit"|"fill", row, col }
  const [genAll,        setGenAll]        = useState(null);  // { current, total } while "Generate All" runs
  const fileInputRef    = useRef(null);
  const autocadPollRef  = useRef(null);
  const stopRef         = useRef(false);   // "Stop Generating" flag for Generate All
  const abortMap        = useRef({});       // { conduit_ident: AbortController } for in-flight generations
  const [txtNames, setTxtNames]       = useState(null);  // conduit names to generate, from an uploaded .txt/.csv list
  const [txtFileName, setTxtFileName] = useState("");
  const [listSummary, setListSummary] = useState("");    // e.g. "CSV: 12 enabled, 3 disabled"
  const txtInputRef     = useRef(null);

  // ── AutoCAD status polling ────────────────────────────────────────────────

  const pollAutocad = useCallback(async () => {
    // The backend runs single-threaded (COM needs one CoInitialized thread), so a status
    // poll that lands while a generate is in flight collides on the server and can reset
    // the connection -- surfacing as "Could not reach IDP_Generation" and losing that
    // conduit. Skip the poll whenever a generation is running (abortMap has live entries).
    if (Object.keys(abortMap.current).length > 0) return;
    const result = await getIdpAutoCADStatus();
    if (result.ok) setAutocad(result.data);
  }, []);

  useEffect(() => {
    pollAutocad();
    autocadPollRef.current = setInterval(pollAutocad, 10000);
    return () => clearInterval(autocadPollRef.current);
  }, [pollAutocad]);

  useEffect(() => {
    setJsonPasteOpen(false);
    setJsonPasteText("");
    setJsonPasteErr(null);
  }, [activeTab]);

  // ── Persist the session on change ─────────────────────────────────────────
  // Keeps the loaded workbook + edited fills + settings + generated status across
  // app exits / tab switches so you never have to re-drop the workbook.
  useEffect(() => {
    // Debounced so typing in the small fields doesn't re-serialize the ~MB workbook on
    // every keystroke; saves ~0.6s after the last change.
    const t = setTimeout(() => {
      const data = { wb, outputFolder, projectNumber, fileSuffix, makeProject, rowResult };
      try {
        localStorage.setItem(SESSION_KEY, JSON.stringify(data));
      } catch {
        // storage quota exceeded (large workbook): drop the heavy original bytes but keep
        // the fills/settings/generated-status so at least those survive the reload.
        try {
          const lite = { ...data, wb: wb ? { ...wb, original_b64: undefined } : null };
          localStorage.setItem(SESSION_KEY, JSON.stringify(lite));
        } catch { /* still too big — session not persisted this change */ }
      }
    }, 600);
    return () => clearTimeout(t);
  }, [wb, outputFolder, projectNumber, fileSuffix, makeProject, rowResult]);

  // ── Match an uploaded conduit-name list against the loaded workbook ────────
  const txtMatch = useMemo(() => {
    if (!txtNames || !wb || !wb.conduit_index) return null;
    const byTag = new Map();
    wb.conduit_index.forEach((r, i) => {
      const tag = String(r.Cond_Tag ?? "").trim().toUpperCase();
      const ident = r.Cond_Ident ?? i;
      if (tag && !byTag.has(tag)) byTag.set(tag, ident);
    });
    const matched = [], notFound = [];
    for (const nm of txtNames) {
      const key = String(nm).trim().toUpperCase();
      if (byTag.has(key)) matched.push({ name: nm, ident: byTag.get(key) });
      else notFound.push(nm);
    }
    return { matched, notFound };
  }, [txtNames, wb]);

  // ── File handling ────────────────────────────────────────────────────────

  // Clear any uploaded conduit-name list (.txt/.csv). Called whenever a NEW workbook is
  // loaded so a stale list from the previous file can't linger or mis-match the new
  // conduits (which hid the "Generate from list" option for the newly loaded workbook).
  function clearUploadedList() {
    setTxtNames(null);
    setTxtFileName("");
    setListSummary("");
    if (txtInputRef.current) txtInputRef.current.value = "";
  }

  async function acceptFile(file) {
    if (!file) return;
    const ext = file.name.split(".").pop().toLowerCase();
    if (!["xlsx", "xlsm"].includes(ext)) {
      setStatus({ type: "error", message: "Only .xlsx and .xlsm files are supported." });
      return;
    }
    setLoading("parse");
    setStatus(null);
    setRowResult({});
    const result = await parseIdpWorkbook(file);
    setLoading(null);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    setWb(result.data);
    clearUploadedList();
    setActiveTab("conduit");
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    acceptFile(e.dataTransfer.files[0]);
  }

  // ── Browse output folder ─────────────────────────────────────────────────

  async function handleBrowseFolder() {
    const result = await browseIdpOutputFolder();
    if (result.path) {
      setOutputFolder(result.path);
    } else if (result.error) {
      setStatus({ type: "error", message: `Browse failed: ${result.error}` });
    }
  }

  // ── Load template ────────────────────────────────────────────────────────

  async function handleLoadTemplate() {
    setLoading("template");
    setStatus(null);
    setRowResult({});
    const result = await getIdpTemplate();
    setLoading(null);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    setWb(result.data);
    clearUploadedList();
    setActiveTab("conduit");
  }

  // ── Populate fills ───────────────────────────────────────────────────────

  function handlePopulateFills() {
    if (!wb) return;

    const updatedConduit = wb.conduit_index.map(conduitRow => {
      const conduitTag = conduitRow["Cond_Tag"];
      const matchingFills = wb.fill_index.filter(
        f => f["Cond_Tag"] != null && String(f["Cond_Tag"]) === String(conduitTag)
      );

      const fillArray = [];

      for (const fillRow of matchingFills) {
        const wireType = (fillRow["Wire_Type"] || "").trim();

        if (wireType.toUpperCase() === "THHN") {
          for (let w = 1; w <= 4; w++) {
            const color = (fillRow[`Wire${w}_Color`] || "").trim();
            const size  = (fillRow[`Wire${w}_Size`]  || "").trim();
            if (!color && !size) continue;

            const idx = fillArray.findIndex(
              f => f.type === wireType && f.color === color && f.size === size
            );
            if (idx >= 0) {
              fillArray[idx].quantity++;
            } else if (fillArray.length < 10) {
              fillArray.push({ type: wireType, color, size, quantity: 1 });
            }
          }
        } else {
          const color = (fillRow["Wire1_Color"] || "").trim();
          const size  = (fillRow["Wire1_Size"]  || "").trim();

          const idx = fillArray.findIndex(
            f => f.type === wireType && f.color === color && f.size === size
          );
          if (idx >= 0) {
            fillArray[idx].quantity++;
          } else if (fillArray.length < 10) {
            fillArray.push({ type: wireType, color, size, quantity: 1 });
          }
        }
      }

      const updated = { ...conduitRow };
      for (let i = 1; i <= 10; i++) {
        const slot = String(i).padStart(2, "0");
        const fill = fillArray[i - 1] || null;
        updated[`Fill${slot}_Type`]     = fill ? fill.type     || null : null;
        updated[`Fill${slot}_Color`]    = fill ? fill.color    || null : null;
        updated[`Fill${slot}_Size`]     = fill ? fill.size     || null : null;
        updated[`Fill${slot}_Quantity`] = fill ? fill.quantity          : null;
      }
      return updated;
    });

    setWb(prev => ({ ...prev, conduit_index: updatedConduit }));
    setStatus({ type: "success", message: `Fills populated for ${updatedConduit.length} conduit(s).` });
  }

  // ── Generate one DWG ────────────────────────────────────────────────────

  async function handleGenerate(conduitIdent, opts = {}) {
    if (!outputFolder.trim()) {
      setStatus({ type: "error", message: "Set an output folder before generating." });
      return;
    }
    if (makeProject && !projectNumber.trim()) {
      setStatus({ type: "error", message: "Enter a Project number to generate a project (or uncheck “Make it a project”)." });
      return;
    }

    // Warn before overwriting a drawing that was already generated (guards against
    // clobbering finished work). Skipped when Generate All already confirmed up front.
    if (!opts.skipOverwriteConfirm && rowResult[conduitIdent]?.ok) {
      const p = rowResult[conduitIdent].path || "(the output folder)";
      const proceed = window.confirm(
        `A drawing for this conduit was already generated:\n\n${p}\n\nRegenerate and OVERWRITE it?`
      );
      if (!proceed) return { skipped: true };
    }

    // NEC conduit-fill notice: if this conduit carries too many wires for its size,
    // tell the engineer before we generate (non-blocking -- we still generate).
    const _condRow = wb.conduit_index.find(r => String(r.Cond_Ident) === String(conduitIdent));
    if (_condRow?.Fill_Warning) {
      const tsW = new Date().toLocaleTimeString();
      setGenLog(prev => `[${tsW}] ⚠ ${_condRow.Fill_Warning}` + (prev ? "\n\n" + prev : ""));
    }

    setRowLoading(prev => ({ ...prev, [conduitIdent]: true }));
    setRowResult(prev => ({ ...prev, [conduitIdent]: null }));

    const ctrl = new AbortController();
    abortMap.current[conduitIdent] = ctrl;

    // Drawing number: the conduit's project-sequential start (Seq_Start), which the
    // backend computed so continuation sheets consume consecutive numbers (15e -> 16e ->
    // 17e) and each conduit starts after the previous one's continuations. Falls back to
    // position-based numbering for workbooks parsed by an older backend.
    const _cond = wb.conduit_index.find(r => String(r.Cond_Ident) === String(conduitIdent));
    const seq = _cond?.Seq_Start
      ?? ((wb.conduit_index.findIndex(r => String(r.Cond_Ident) === String(conduitIdent)) + 1) || 1);
    // Title-block "N OF <max>": total SHEETS in the project (conduits + their
    // continuation sheets), not just the conduit count.
    const sheetMax = wb.conduit_index.reduce((sum, r) => sum + (r.Sheet_Count || 1), 0)
      || wb.conduit_index.length;

    const payload = {
      conduit_ident:  conduitIdent,
      conduit_index:  wb.conduit_index,
      fill_index:     wb.fill_index,
      output_folder:  outputFolder,
      ref_docs:       wb.ref_docs || {},
      deviation_notes: wb.deviation_notes || {},
      project_desc:   wb.project_desc || {},
      project_number: projectNumber.trim(),
      seq_num:        seq,
      sheet_max:      sheetMax,   // title block "SHEET n OF <max>"
      file_suffix:    fileSuffix.trim() || "e",
      make_project:   makeProject,
    };

    // Generate, retrying ONCE on a transient "Could not reach" network error (a dropped/
    // reset connection to the single-threaded backend). Generation is idempotent server-
    // side (it re-copies the template and overwrites the output), so a retry can't produce
    // a partial/double drawing. A real server error (non-network) or an abort is NOT
    // retried.
    let result;
    for (let attempt = 0; attempt < 2; attempt++) {
      result = await generateIdpDwg(payload, ctrl.signal);
      const transient = !result.ok && !result.aborted &&
        /could not reach/i.test(result.error || "");
      if (!transient || stopRef.current) break;
      await new Promise(res => setTimeout(res, 800));   // brief pause, then one retry
    }

    delete abortMap.current[conduitIdent];
    setRowLoading(prev => ({ ...prev, [conduitIdent]: false }));

    // Stopped mid-generation: the UI stops waiting immediately (the DWG already being
    // drawn in AutoCAD finishes server-side — a COM draw can't be safely interrupted).
    if (result.aborted) {
      setRowResult(prev => ({ ...prev, [conduitIdent]: { ok: false, error: "Stopped", aborted: true } }));
      const tsA = new Date().toLocaleTimeString();
      const condTagA = wb.conduit_index.find(r => String(r.Cond_Ident) === String(conduitIdent))?.Cond_Tag ?? conduitIdent;
      setGenLog(prev => `[${tsA}] ■ ${condTagA} — stopped` + (prev ? "\n\n" + prev : ""));
      return result;
    }

    const success = result.ok && result.data?.success !== false;
    setRowResult(prev => ({
      ...prev,
      [conduitIdent]: success
        ? { ok: true,  path: result.data.output_path, warnings: result.data.warnings }
        : { ok: false, error: result.error || result.data?.error || "Generation failed", warnings: result.data?.warnings },
    }));

    const ts = new Date().toLocaleTimeString();
    const condTag = wb.conduit_index.find(r => String(r.Cond_Ident) === String(conduitIdent))?.Cond_Tag ?? conduitIdent;
    let entry;
    if (success) {
      const fname = result.data.output_path?.split(/[\\/]/).pop() ?? "";
      entry = `[${ts}] ✓ ${fname}`;
      if (result.data.warnings?.length) {
        entry += "\n" + result.data.warnings.map(w => `  ⚠ ${w}`).join("\n");
      }
    } else {
      const errMsg = result.error || result.data?.error || "Generation failed";
      entry = `[${ts}] ✗ ${condTag}\n  ${errMsg}`;
      if (result.data?.warnings?.length) {
        entry += "\n" + result.data.warnings.map(w => `  ⚠ ${w}`).join("\n");
      }
    }
    setGenLog(prev => entry + (prev ? "\n\n" + prev : ""));
    return result;
  }

  // ── Stop: abort in-flight request(s) ─────────────────────────────────────
  // Aborting stops the frontend from waiting and halts the queue. The DWG already
  // being drawn in AutoCAD finishes server-side (a COM draw can't be yanked mid-call).
  function stopRow(conduitIdent) {
    try { abortMap.current[conduitIdent]?.abort(); } catch { /* noop */ }
  }
  function stopAll() {
    stopRef.current = true;
    Object.values(abortMap.current).forEach(c => { try { c.abort(); } catch { /* noop */ } });
  }

  // ── Generate every conduit (sequential — AutoCAD/COM is single-threaded) ──

  async function handleGenerateAll() {
    if (!wb || !wb.conduit_index.length) return;
    if (!outputFolder.trim()) {
      setStatus({ type: "error", message: "Set an output folder before generating." });
      return;
    }
    const idents = wb.conduit_index.map((r, i) => r["Cond_Ident"] ?? i);

    // Warn once if some drawings are already generated — Generate All overwrites them.
    const alreadyN = idents.filter(id => rowResult[id]?.ok).length;
    if (alreadyN > 0) {
      const proceed = window.confirm(
        `${alreadyN} of ${idents.length} conduit(s) already have a generated drawing.\n\n` +
        `Generate All will regenerate and OVERWRITE those. Proceed?`
      );
      if (!proceed) return;
    }

    stopRef.current = false;
    setStatus(null);
    setGenLog(prev => `[${new Date().toLocaleTimeString()}] ▶ Generate All — ${idents.length} conduit(s)` + (prev ? "\n\n" + prev : ""));
    let done = 0;
    for (let k = 0; k < idents.length; k++) {
      if (stopRef.current) break;                 // Stop requested — halt before starting the next conduit
      setGenAll({ current: k + 1, total: idents.length });
      const r = await handleGenerate(idents[k], { skipOverwriteConfirm: true });   // sequential: one DWG finishes before the next starts
      if (r?.aborted || stopRef.current) break;    // stopped during / after this conduit
      done = k + 1;
    }
    const stopped = stopRef.current;
    stopRef.current = false;
    setGenAll(null);
    setStatus(stopped
      ? { type: "error",   message: `Generate All stopped — ${done} of ${idents.length} conduit(s) processed.` }
      : { type: "success", message: `Generate All finished — ${idents.length} conduit(s) processed. See the log for per-file results.` });
  }

  // ── Upload a conduit list (.txt or .csv) → generate only those ───────────
  // .txt: one name per line / comma-separated — every listed name is generated.
  // .csv: "Conduit Name","Enabled/Disabled" (1/0) — only the enabled (1) names.
  async function handleListFile(file) {
    if (!file) return;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    try {
      // Strip a leading UTF-8 BOM (Excel's "CSV UTF-8" adds one) so it never sticks to
      // the first conduit name / header cell.
      const text = (await file.text()).replace(/^\uFEFF/, "");
      if (ext === "csv") {
        const { names, disabled } = namesFromCsv(text);
        setTxtNames(names);
        setTxtFileName(file.name);
        setListSummary(`CSV: ${names.length} enabled` + (disabled ? `, ${disabled} disabled` : ""));
      } else {
        // .txt — trim, drop blanks, de-dupe (case-insensitive); all listed = generate
        const seen = new Set(); const uniq = [];
        for (const raw of text.split(/[\r\n,]+/)) {
          const nm = raw.trim();
          if (!nm) continue;
          const k = nm.toUpperCase();
          if (!seen.has(k)) { seen.add(k); uniq.push(nm); }
        }
        setTxtNames(uniq);
        setTxtFileName(file.name);
        setListSummary("");
      }
    } catch {
      setStatus({ type: "error", message: `Could not read the ${ext === "csv" ? ".csv" : ".txt"} file.` });
    }
    if (txtInputRef.current) txtInputRef.current.value = "";  // allow re-selecting the same file
  }

  // ── Export a CSV of every conduit name + an Enabled/Disabled (1/0) column ──
  // All conduits default to enabled (1). Edit the file in Excel (set 0 to skip a
  // conduit), then re-upload it via "Upload conduit list" to generate the enabled ones.
  async function handleExportConduitCsv() {
    if (!wb || !wb.conduit_index) return;
    const seen = new Set();
    const rows = [["Conduit Name", "Enabled/Disabled"]];
    for (const r of wb.conduit_index) {
      const tag = String(r.Cond_Tag ?? "").trim();
      if (!tag) continue;
      const k = tag.toUpperCase();
      if (seen.has(k)) continue;
      seen.add(k);
      rows.push([tag, "1"]);
    }
    if (rows.length === 1) {
      setStatus({ type: "error", message: "No conduit names to export." });
      return;
    }
    // Build the CSV text (no BOM \u2014 the backend writes it with utf-8-sig). A browser blob
    // download does not work in the LISA desktop webview, so we hand the text to the
    // backend, which pops a native Save-As dialog (defaulting to the output folder) and
    // writes the file to the location the user picks.
    const csv = rows.map(cols => cols.map(csvEscape).join(",")).join("\r\n");
    const base = (wb.filename || "workbook").replace(/\.[^.]+$/, "");
    setLoading("exportcsv");
    const result = await exportIdpConduitList({
      csv,
      filename: `${base}_conduit_list.csv`,
      default_dir: outputFolder || "",
    });
    setLoading(null);
    if (result.cancelled) return;              // user closed the Save dialog \u2014 no message
    if (!result.ok) {
      setStatus({ type: "error", message: `CSV export failed: ${result.error}` });
      return;
    }
    setStatus({ type: "success", message: `Conduit list CSV saved to: ${result.path}` });
  }

  async function handleGenerateFromList() {
    if (!txtMatch || !txtMatch.matched.length) return;
    if (!outputFolder.trim()) {
      setStatus({ type: "error", message: "Set an output folder before generating." });
      return;
    }
    const idents = txtMatch.matched.map(m => m.ident);
    const alreadyN = idents.filter(id => rowResult[id]?.ok).length;
    if (alreadyN > 0) {
      const proceed = window.confirm(
        `${alreadyN} of ${idents.length} conduit(s) in the list already have a generated drawing.\n\n` +
        `This will regenerate and OVERWRITE those. Proceed?`
      );
      if (!proceed) return;
    }
    stopRef.current = false;
    setStatus(null);
    setGenLog(prev => `[${new Date().toLocaleTimeString()}] ▶ Generate from list (${txtFileName}) — ${idents.length} conduit(s)` + (prev ? "\n\n" + prev : ""));
    let done = 0;
    for (let k = 0; k < idents.length; k++) {
      if (stopRef.current) break;
      setGenAll({ current: k + 1, total: idents.length });
      const r = await handleGenerate(idents[k], { skipOverwriteConfirm: true });
      if (r?.aborted || stopRef.current) break;
      done = k + 1;
    }
    const stopped = stopRef.current;
    stopRef.current = false;
    setGenAll(null);
    setStatus(stopped
      ? { type: "error",   message: `Generate from list stopped — ${done} of ${idents.length} processed.` }
      : { type: "success", message: `Generate from list finished — ${idents.length} conduit(s) processed. See the log.` });
  }

  // ── Remove a conduit from the list (session only — workbook untouched) ────

  function handleRemoveConduit(rowIdx) {
    setWb(prev => {
      const removed = prev.conduit_index[rowIdx];
      const ident = removed?.["Cond_Ident"] ?? rowIdx;
      setRowLoading(rl => { const n = { ...rl }; delete n[ident]; return n; });
      setRowResult(rr => { const n = { ...rr }; delete n[ident]; return n; });
      return { ...prev, conduit_index: prev.conduit_index.filter((_, i) => i !== rowIdx) };
    });
  }

  // ── Download workbook ────────────────────────────────────────────────────

  async function handleDownload() {
    if (!wb) return;
    setLoading("download");
    const result = await downloadIdpWorkbook({
      original_b64:  wb.original_b64,
      conduit_index: wb.conduit_index,
      fill_index:    wb.fill_index,
      filename:      wb.filename,
    });
    setLoading(null);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    const url = URL.createObjectURL(result.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = result.filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Wire Labels ──────────────────────────────────────────────────────────

  async function handleWireLabels() {
    if (!wb) return;
    setLoading("wirelabels");
    const result = await downloadIdpWireLabels(wb.fill_index, wb.filename);
    setLoading(null);
    if (!result.ok) {
      setStatus({ type: "error", message: result.error });
      return;
    }
    const url = URL.createObjectURL(result.blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = result.filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── JSON paste / copy ────────────────────────────────────────────────────

  function handleCopyJson() {
    if (!wb) return;
    const data = activeTab === "conduit" ? wb.conduit_index : wb.fill_index;
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setStatus({ type: "success", message: `${activeTab === "conduit" ? "Conduit" : "Fill"} index copied to clipboard (${data.length} rows).` });
  }

  function handleApplyJson() {
    try {
      const parsed = JSON.parse(jsonPasteText);
      if (!Array.isArray(parsed)) { setJsonPasteErr("Must be a JSON array [ {...}, ... ]"); return; }
      if (activeTab === "conduit") {
        setWb(prev => ({ ...prev, conduit_index: parsed }));
      } else {
        setWb(prev => ({ ...prev, fill_index: parsed }));
      }
      setJsonPasteErr(null);
      setJsonPasteOpen(false);
      setJsonPasteText("");
      const label = activeTab === "conduit" ? "Conduit" : "Fill";
      setStatus({ type: "success", message: `${label} index updated (${parsed.length} row${parsed.length !== 1 ? "s" : ""}).` });
    } catch (e) {
      setJsonPasteErr("Invalid JSON: " + e.message);
    }
  }

  // ── Cell editing ─────────────────────────────────────────────────────────

  function handleCellClick(indexName, rowIdx, colName) {
    setEditingCell({ index: indexName, row: rowIdx, col: colName });
  }

  function handleCellCommit(indexName, rowIdx, colName, rawValue) {
    const value = rawValue === "" ? null : rawValue;
    const key = indexName === "conduit" ? "conduit_index" : "fill_index";
    setWb(prev => ({
      ...prev,
      [key]: prev[key].map((r, i) => i === rowIdx ? { ...r, [colName]: value } : r),
    }));
    setEditingCell(null);
  }

  const busy        = loading !== null;
  const generating  = genAll !== null;
  const genBtnStyle = { width: "190px", textAlign: "left" };

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="docx-page" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>

      {/* ── TOP SECTION (frozen) ────────────────────────────────────────────── */}
      <div style={{ flexShrink: 0, width: "100%", background: "var(--bg-main)", paddingBottom: "12px" }}>
        <h1 className="page-title">IDP DWG Generator</h1>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <AutoCADStatusPill autocad={autocad} />
        </div>

        <div style={{ display: "flex", gap: "20px", alignItems: "stretch", flexWrap: "wrap", marginTop: "12px" }}>

          {/* ── Left column: drop zone + stacked buttons ── */}
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", minWidth: "240px", maxWidth: "280px" }}>
            <div
              className={`drop-zone${dragging ? " dragging" : ""}${wb ? " has-file" : ""}`}
              onDragOver={e => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xlsm"
                style={{ display: "none" }}
                onChange={e => acceptFile(e.target.files[0])}
              />
              <span className="drop-zone-icon">&#128202;</span>
              {wb
                ? <><span className="drop-zone-filename">{wb.filename}</span><span className="drop-zone-hint">Click or drop to replace</span></>
                : <><span className="drop-zone-primary">Drag &amp; drop an IDP workbook</span><span className="drop-zone-hint">or click to browse — .xlsx, .xlsm</span></>
              }
            </div>

            {loading === "parse" && <p style={{ color: "var(--text-label)", fontSize: "0.82rem" }}>Reading workbook…</p>}
            {status && (
              status.type === "error"
                ? <textarea
                    readOnly
                    className={`status-msg ${status.type}`}
                    value={status.message}
                    style={{ resize: "vertical", minHeight: "48px", width: "100%",
                             fontFamily: "inherit", fontSize: "0.82rem", cursor: "text" }}
                  />
                : <p className={`status-msg ${status.type}`}>{status.message}</p>
            )}
          </div>

          {/* ── Right column: output folder + generation actions ── */}
          <div style={{ display: "flex", flexDirection: "column", gap: "8px", width: "300px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <span style={{ color: "var(--text-label)", fontSize: "0.78rem" }}>Output folder</span>
              <div style={{ display: "flex", gap: "6px" }}>
                <input
                  type="text"
                  value={outputFolder}
                  onChange={e => setOutputFolder(e.target.value)}
                  placeholder="C:\Projects\P001\DWG"
                  style={{ ...INPUT_STYLE }}
                />
                <button
                  className="btn btn-secondary"
                  onClick={handleBrowseFolder}
                  title="Browse for output folder"
                  style={{ whiteSpace: "nowrap", padding: "4px 14px", fontSize: "1rem", fontWeight: 700, lineHeight: 1 }}
                >
                  …
                </button>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <span style={{ color: "var(--text-label)", fontSize: "0.78rem" }}>Project number</span>
              <div style={{ display: "flex", gap: "6px" }}>
                <input
                  type="text"
                  value={projectNumber}
                  onChange={e => setProjectNumber(e.target.value)}
                  placeholder="e.g. 56.1077"
                  style={{ ...INPUT_STYLE, flex: 2 }}
                />
                <input
                  type="text"
                  value={fileSuffix}
                  onChange={e => setFileSuffix(e.target.value)}
                  placeholder="suffix (e)"
                  title="File suffix — defaults to 'e' if left blank"
                  style={{ ...INPUT_STYLE, flex: 1, minWidth: "60px" }}
                />
              </div>
              {projectNumber.trim() && (
                <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>
                  Files: {projectNumber.trim()}-01{(fileSuffix.trim() || "e")}, {projectNumber.trim()}-02{(fileSuffix.trim() || "e")}, …
                </span>
              )}
            </div>

            <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={makeProject}
                onChange={e => setMakeProject(e.target.checked)}
                style={{ width: "16px", height: "16px", marginTop: "2px", cursor: "pointer", flexShrink: 0 }}
              />
              <span style={{ fontSize: "0.82rem", color: "var(--text-label)" }}>
                Make it a project
                <span style={{ display: "block", color: "var(--text-dim)", fontSize: "0.72rem" }}>
                  Assembles an AutoCAD Electrical project (.wdp/.aepx) with the GENERAL sheets + your
                  conduits, in the output folder. Needs a project number.
                </span>
                {makeProject && !projectNumber.trim() && (
                  <span style={{ display: "block", color: "var(--status-error)", fontSize: "0.72rem", marginTop: "2px" }}>
                    Enter a project number above.
                  </span>
                )}
              </span>
            </label>

            <button
              className="btn btn-primary"
              style={genBtnStyle}
              onClick={handleGenerateAll}
              disabled={!wb || !wb.conduit_index.length || busy || generating || autocad?.running !== true}
              title={autocad?.running !== true ? "AutoCAD not detected — open AutoCAD before generating" : "Generate a DWG for every conduit in the list"}
            >
              {generating
                ? <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                    <span className="spinner" />{`Generating ${genAll.current}/${genAll.total}…`}
                  </span>
                : "▶ Generate All"}
            </button>

            {generating && (
              <button
                className="btn"
                style={{ ...genBtnStyle, background: "var(--status-error)", borderColor: "var(--status-error)", color: "#fff" }}
                onClick={stopAll}
                title="Stop generating (halts the queue and stops waiting on the current conduit)"
              >
                ■ Stop
              </button>
            )}

            {/* ── Generate from a conduit-name list (.txt or .csv) ── */}
            <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "4px",
                          borderTop: "1px solid var(--border)", paddingTop: "8px" }}>
              <input ref={txtInputRef} type="file" accept=".txt,.csv" style={{ display: "none" }}
                     onChange={e => handleListFile(e.target.files[0])} />
              <button className="btn btn-primary" style={genBtnStyle}
                      onClick={() => txtInputRef.current?.click()} disabled={!wb || busy || generating}
                      title={"Upload a conduit list to generate just those:\n• .txt — one conduit name per line\n• .csv — a \"Conduit Name\",\"Enabled/Disabled\" (1/0) file (only the 1s generate)"}>
                Upload conduit list (.txt / .csv)
              </button>
              {listSummary && (
                <span style={{ color: "var(--text-dim)", fontSize: "0.70rem" }}>{listSummary}</span>
              )}
              {txtMatch && (
                <span style={{ color: "var(--text-label)", fontSize: "0.72rem" }}>
                  {txtFileName}: <b style={{ color: "var(--status-success-soft)" }}>{txtMatch.matched.length}</b> matched
                  {txtMatch.notFound.length ? <>, <b style={{ color: "var(--status-warning)" }}>{txtMatch.notFound.length}</b> not found</> : null}
                </span>
              )}
              {txtMatch && txtMatch.notFound.length > 0 && (
                <span style={{ color: "var(--status-warning)", fontSize: "0.70rem", whiteSpace: "normal" }}
                      title={txtMatch.notFound.join(", ")}>
                  Not in workbook: {txtMatch.notFound.slice(0, 6).join(", ")}
                  {txtMatch.notFound.length > 6 ? ` +${txtMatch.notFound.length - 6} more` : ""}
                </span>
              )}
              {txtMatch && txtMatch.matched.length > 0 && (
                <button className="btn btn-primary" style={genBtnStyle}
                        onClick={handleGenerateFromList}
                        disabled={!wb || busy || generating || autocad?.running !== true}
                        title={autocad?.running !== true ? "AutoCAD not detected — open AutoCAD before generating" : "Generate only the conduits in the uploaded list"}>
                  {generating
                    ? <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                        <span className="spinner" />{`Generating ${genAll?.current}/${genAll?.total}…`}
                      </span>
                    : `▶ Generate from list (${txtMatch.matched.length})`}
                </button>
              )}
            </div>
          </div>

          {/* ── Generation log ── */}
          <div style={{ display: "flex", flexDirection: "column", gap: "4px", flex: 1, minWidth: "240px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: "var(--text-label)", fontSize: "0.78rem" }}>Generation Log</span>
              {genLog && (
                <button
                  onClick={() => setGenLog("")}
                  style={{ background: "none", border: "none", color: "var(--text-dim)", fontSize: "0.72rem", cursor: "pointer", padding: "0 2px" }}
                >
                  Clear
                </button>
              )}
            </div>
            <textarea
              readOnly
              value={genLog || "No generation activity yet."}
              style={{
                flex: 1,
                background: "var(--bg-input)",
                color: "var(--text)",
                border: "1px solid var(--border-strong)",
                borderRadius: "4px",
                padding: "6px 8px",
                fontSize: "0.75rem",
                fontFamily: "monospace",
                resize: "none",
                outline: "none",
                minHeight: "120px",
              }}
            />
          </div>

        </div>
      </div>

      {/* ── Tab bar ──────────────────────────────────────────────────────────── */}
      {wb && (
        <div style={{ flexShrink: 0, display: "flex", gap: "2px", background: "var(--bg-app)", padding: "6px 20px 0", borderBottom: "1px solid var(--border)" }}>
          {[["conduit", "Conduit Index"], ["fill", "Fill Index"]].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              style={{
                padding: "4px 14px",
                border: "none",
                borderBottom: activeTab === key ? "2px solid var(--accent)" : "2px solid transparent",
                background: "transparent",
                color: activeTab === key ? "var(--text)" : "var(--text-dim)",
                cursor: "pointer",
                fontSize: "0.82rem",
              }}
            >
              {label} ({key === "conduit" ? wb.conduit_index.length : wb.fill_index.length})
            </button>
          ))}
        </div>
      )}

      {/* ── JSON toolbar ─────────────────────────────────────────────────────── */}
      {wb && (
        <div style={{ flexShrink: 0, background: "var(--bg-app)", borderBottom: "1px solid var(--border)", padding: "4px 20px", display: "flex", flexDirection: "column", gap: "4px" }}>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <button
              onClick={() => { setJsonPasteOpen(v => !v); setJsonPasteErr(null); }}
              style={{ background: "none", border: "1px solid var(--border-strong)", color: jsonPasteOpen ? "var(--accent-cyan)" : "var(--text-label)", borderRadius: "4px", padding: "3px 8px", cursor: "pointer", fontSize: "0.75rem" }}
              title="Paste JSON to replace this index"
            >
              📋 Paste JSON
            </button>
            <button
              onClick={handleCopyJson}
              style={{ background: "none", border: "1px solid var(--border-strong)", color: "var(--text-label)", borderRadius: "4px", padding: "3px 8px", cursor: "pointer", fontSize: "0.75rem" }}
              title="Copy current index as JSON"
            >
              📤 Copy JSON
            </button>
            <button
              onClick={handleExportConduitCsv}
              style={{ background: "none", border: "1px solid var(--border-strong)", color: "var(--text-label)", borderRadius: "4px", padding: "3px 8px", cursor: "pointer", fontSize: "0.75rem" }}
              title={'Download a CSV of every conduit name with an Enabled/Disabled (1/0) column.\nEdit it in Excel (set 0 to skip a conduit), then re-upload via "Upload conduit list" to generate the enabled ones.'}
            >
              ⬇ Conduit list CSV
            </button>
          </div>
          {jsonPasteOpen && (
            <div style={{ display: "flex", flexDirection: "column", gap: "4px", paddingBottom: "4px" }}>
              <textarea
                rows={5}
                placeholder={`Paste ${activeTab === "conduit" ? "conduit_index" : "fill_index"} JSON array here…`}
                value={jsonPasteText}
                onChange={e => setJsonPasteText(e.target.value)}
                style={{ width: "100%", background: "var(--bg-input)", color: "var(--text)", border: "1px solid var(--border-strong)", borderRadius: "4px", padding: "6px 8px", fontSize: "0.78rem", resize: "vertical", outline: "none", fontFamily: "monospace", boxSizing: "border-box" }}
              />
              {jsonPasteErr && <p style={{ color: "var(--status-error-soft)", fontSize: "0.75rem", margin: "0" }}>{jsonPasteErr}</p>}
              <div style={{ display: "flex", gap: "6px" }}>
                <button
                  className="btn btn-primary"
                  style={{ padding: "3px 12px", fontSize: "0.78rem" }}
                  onClick={handleApplyJson}
                  disabled={!jsonPasteText.trim()}
                >
                  Apply
                </button>
                <button
                  className="btn btn-secondary"
                  style={{ padding: "3px 10px", fontSize: "0.78rem" }}
                  onClick={() => { setJsonPasteOpen(false); setJsonPasteText(""); setJsonPasteErr(null); }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Sheet tables ────────────────────────────────────────────────────── */}
      {wb && (
        <div style={{ flex: 1, minHeight: 0, overflow: "auto", background: "var(--bg-app)" }}>
          {activeTab === "conduit" && (
            <ConduitTable
              rows={wb.conduit_index}
              rowLoading={rowLoading}
              rowResult={rowResult}
              onGenerate={handleGenerate}
              onStop={stopRow}
              onRemove={handleRemoveConduit}
              generating={generating}
              autocadOk={autocad?.running === true}
              editingCell={editingCell}
              onCellClick={(rowIdx, colName) => handleCellClick("conduit", rowIdx, colName)}
              onCellCommit={(rowIdx, colName, val) => handleCellCommit("conduit", rowIdx, colName, val)}
            />
          )}
          {activeTab === "fill" && (
            <FillTable
              rows={wb.fill_index}
              editingCell={editingCell}
              onCellClick={(rowIdx, colName) => handleCellClick("fill", rowIdx, colName)}
              onCellCommit={(rowIdx, colName, val) => handleCellCommit("fill", rowIdx, colName, val)}
            />
          )}
        </div>
      )}

      {/* ── Empty state ──────────────────────────────────────────────────────── */}
      {!wb && !loading && (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)" }}>
          Upload an IDP workbook to get started.
        </div>
      )}
    </div>
  );
}


// ── AutoCAD status pill ────────────────────────────────────────────────────

function AutoCADStatusPill({ autocad }) {
  if (autocad === null) {
    return <span style={{ fontSize: "0.75rem", color: "var(--text-dim)" }}>Checking AutoCAD…</span>;
  }
  const color = autocad.running ? "var(--status-success-soft)" : "var(--status-error-soft)";
  const label = autocad.running
    ? `● AutoCAD ready${autocad.version ? ` (${autocad.version})` : ""}`
    : "● AutoCAD not detected";
  return (
    <span style={{ fontSize: "0.75rem", color, fontWeight: 500 }}>
      {label}
    </span>
  );
}


// ── Template column orders ─────────────────────────────────────────────────

const CONDUIT_TEMPLATE_COLS = [
  "Cond_Ident", "Cond_Tag", "Cond_Type", "Cond_Size",
  "Src_Raw", "Dst_Raw", "Src_Jbox", "Dst_Jbox",
  "Src_Name01", "Src_Name02", "Src_Name03",
  "Dst_Name01", "Dst_Name02", "Dst_Name03",
  "Fill01_Type", "Fill01_Color", "Fill01_Size", "Fill01_Quantity",
  "Fill02_Type", "Fill02_Color", "Fill02_Size", "Fill02_Quantity",
  "Fill03_Type", "Fill03_Color", "Fill03_Size", "Fill03_Quantity",
  "Fill04_Type", "Fill04_Color", "Fill04_Size", "Fill04_Quantity",
  "Fill05_Type", "Fill05_Color", "Fill05_Size", "Fill05_Quantity",
  "Fill06_Type", "Fill06_Color", "Fill06_Size", "Fill06_Quantity",
  "Fill07_Type", "Fill07_Color", "Fill07_Size", "Fill07_Quantity",
  "Fill08_Type", "Fill08_Color", "Fill08_Size", "Fill08_Quantity",
  "Fill09_Type", "Fill09_Color", "Fill09_Size", "Fill09_Quantity",
  "Fill10_Type", "Fill10_Color", "Fill10_Size", "Fill10_Quantity",
];

const FILL_TEMPLATE_COLS = [
  "Fill_Ident", "Cond_Tag",
  "Src_Raw", "Dst_Raw",
  "Wire_Type", "Wire_Count",
  "Loop_SrcDesc", "Loop_DstDesc",
  "Src_TermBlockDesc", "Src_TermBlockVisibilityState", "Src_TermBlockPic",
  "Dst_TermBlockDesc", "Dst_TermBlockVisibilityState", "Dst_TermBlockPic",
  "Wire1_Color", "Wire1_Size", "Wire1_SrcTermBlk", "Wire1_SrcTermNum", "Wire1_DstTermBlk", "Wire1_DstTermNum", "Wire1_SrcLabel", "Wire1_DstLabel",
  "Wire2_Color", "Wire2_Size", "Wire2_SrcTermBlk", "Wire2_SrcTermNum", "Wire2_DstTermBlk", "Wire2_DstTermNum", "Wire2_SrcLabel", "Wire2_DstLabel",
  "Wire3_Color", "Wire3_Size", "Wire3_SrcTermBlk", "Wire3_SrcTermNum", "Wire3_DstTermBlk", "Wire3_DstTermNum", "Wire3_SrcLabel", "Wire3_DstLabel",
  "Wire4_Color", "Wire4_Size", "Wire4_SrcTermBlk", "Wire4_SrcTermNum", "Wire4_DstTermBlk", "Wire4_DstTermNum", "Wire4_SrcLabel", "Wire4_DstLabel",
];

function templateOrdered(templateCols, dataKeys) {
  return [
    ...templateCols.filter(c => dataKeys.includes(c)),
    ...dataKeys.filter(c => !templateCols.includes(c)),
  ];
}


// ── Conduit Index table ────────────────────────────────────────────────────

function ConduitTable({ rows, rowLoading, rowResult, onGenerate, onStop, onRemove, generating, autocadOk, editingCell, onCellClick, onCellCommit }) {
  if (!rows.length) return <p style={{ padding: "16px", color: "var(--text-dim)" }}>No conduit rows found.</p>;

  // Internal, backend-computed fields (NEC fill %, sheet numbering), not data columns.
  const _hiddenCols = new Set(["Fill_Warning", "Fill_Pct", "Fill_Over", "Seq_Start", "Sheet_Count"]);
  const displayCols = templateOrdered(CONDUIT_TEMPLATE_COLS, Object.keys(rows[0]))
    .filter(c => !_hiddenCols.has(c));

  return (
    <table style={{ borderCollapse: "collapse", minWidth: "100%", fontSize: "0.8rem" }}>
      <thead>
        <tr>
          <th style={{ ...TH_STYLE, width: "28px", minWidth: "28px" }}></th>
          <th style={{ ...TH_STYLE, minWidth: "120px" }}>Generate</th>
          {displayCols.map(col => (
            <th key={col} style={TH_STYLE}>{col}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          const ident = row["Cond_Ident"] ?? i;
          const isLoading = rowLoading[ident];
          const result = rowResult[ident];
          const overfill = row["Fill_Over"];       // true when over the NEC limit (dangerous)
          const overfillMsg = row["Fill_Warning"]; // over-fill explanation, or null
          const fillPct = row["Fill_Pct"];         // NEC fill %, or null if not evaluable

          return (
            <tr key={i}
              title={overfillMsg || undefined}
              style={{
                background: overfill
                  ? "var(--status-error-soft, #5b1a1a)"
                  : (i % 2 === 0 ? "var(--bg-app)" : "var(--bg-row-alt)"),
              }}>
              <td style={{ ...TABLE_CELL, width: "28px", minWidth: "28px", textAlign: "center", padding: "2px 4px" }}>
                <button
                  onClick={() => onRemove(i)}
                  disabled={isLoading || generating}
                  title="Remove this conduit from the list"
                  style={{
                    background: "none", border: "none", cursor: (isLoading || generating) ? "default" : "pointer",
                    color: (isLoading || generating) ? "var(--text-faint)" : "var(--status-error-soft)", fontSize: "0.95rem", lineHeight: 1, padding: "2px 4px",
                  }}
                >
                  ✕
                </button>
              </td>
              <td style={{ ...TABLE_CELL, minWidth: "140px" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  {isLoading ? (
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <span className="spinner" title="Generating…" />
                      <button
                        className="btn"
                        style={{ padding: "2px 10px", fontSize: "0.75rem", background: "var(--status-error)", borderColor: "var(--status-error)", color: "#fff" }}
                        onClick={() => onStop(ident)}
                        title="Stop this generation (the DWG already drawing in AutoCAD will finish)"
                      >
                        ■ Stop
                      </button>
                    </div>
                  ) : (
                    <button
                      className="btn btn-primary"
                      style={{ padding: "2px 10px", fontSize: "0.75rem" }}
                      disabled={generating || !autocadOk}
                      onClick={() => onGenerate(ident)}
                      title={!autocadOk ? "AutoCAD not detected — open AutoCAD before generating" : ""}
                    >
                      ▶ Generate
                    </button>
                  )}
                  {result && (
                    result.ok
                      ? <span style={{ color: "var(--status-success-soft)", fontSize: "0.72rem" }}>
                          ✓ {result.path?.split(/[\\/]/).pop()}
                          {result.warnings?.length > 0 && <span style={{ color: "var(--status-warning)", marginLeft: "4px" }}>⚠</span>}
                        </span>
                      : <span style={{ color: "var(--status-error-soft)", fontSize: "0.72rem" }}>✗ see log</span>
                  )}
                  {fillPct != null && (
                    <span title={overfillMsg || `NEC conduit fill: ${fillPct}% of the conduit's internal area`}
                      style={{
                        fontSize: "0.72rem",
                        fontWeight: overfill ? 700 : 500,
                        color: overfill ? "var(--status-error, #ff6b6b)" : "var(--text-dim)",
                      }}>
                      {overfill ? `⚠ Fill ${fillPct}% — too many wires` : `Fill ${fillPct}%`}
                    </span>
                  )}
                </div>
              </td>
              {displayCols.map(col => {
                const isEditing = editingCell?.index === "conduit" && editingCell?.row === i && editingCell?.col === col;
                const val = row[col];
                if (isEditing) {
                  return (
                    <td key={col} style={{ ...TABLE_CELL, padding: 0 }}>
                      <input
                        autoFocus
                        defaultValue={val ?? ""}
                        onBlur={e => onCellCommit(i, col, e.target.value)}
                        onKeyDown={e => {
                          if (e.key === "Enter")  onCellCommit(i, col, e.target.value);
                          if (e.key === "Escape") onCellCommit(i, col, val ?? "");
                        }}
                        style={{ width: "100%", minWidth: "80px", background: "var(--bg-input)", color: "var(--text)", border: "1px solid var(--accent)", outline: "none", padding: "2px 6px", fontSize: "0.78rem" }}
                      />
                    </td>
                  );
                }
                return (
                  <td key={col}
                    style={{ ...TABLE_CELL, cursor: "pointer", color: val == null ? "var(--text-faint)" : "var(--text)" }}
                    title={val != null ? String(val) : ""}
                    onClick={() => onCellClick(i, col)}
                  >
                    {val ?? ""}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}


// ── Fill Index table ───────────────────────────────────────────────────────

function FillTable({ rows, editingCell, onCellClick, onCellCommit }) {
  if (!rows.length) return <p style={{ padding: "16px", color: "var(--text-dim)" }}>No fill rows found.</p>;

  const displayCols = templateOrdered(FILL_TEMPLATE_COLS, Object.keys(rows[0]));

  return (
    <table style={{ borderCollapse: "collapse", minWidth: "100%", fontSize: "0.8rem" }}>
      <thead>
        <tr>
          {displayCols.map(col => (
            <th key={col} style={TH_STYLE}>{col}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} style={{ background: i % 2 === 0 ? "var(--bg-app)" : "var(--bg-row-alt)" }}>
            {displayCols.map(col => {
              const isEditing = editingCell?.index === "fill" && editingCell?.row === i && editingCell?.col === col;
              const val = row[col];
              if (isEditing) {
                return (
                  <td key={col} style={{ ...TABLE_CELL, padding: 0 }}>
                    <input
                      autoFocus
                      defaultValue={val ?? ""}
                      onBlur={e => onCellCommit(i, col, e.target.value)}
                      onKeyDown={e => {
                        if (e.key === "Enter")  onCellCommit(i, col, e.target.value);
                        if (e.key === "Escape") onCellCommit(i, col, val ?? "");
                      }}
                      style={{ width: "100%", minWidth: "80px", background: "var(--bg-input)", color: "var(--text)", border: "1px solid var(--accent)", outline: "none", padding: "2px 6px", fontSize: "0.78rem" }}
                    />
                  </td>
                );
              }
              return (
                <td key={col}
                  style={{ ...TABLE_CELL, cursor: "pointer", color: val == null ? "var(--text-faint)" : "var(--text)" }}
                  title={val != null ? String(val) : ""}
                  onClick={() => onCellClick(i, col)}
                >
                  {val ?? ""}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
