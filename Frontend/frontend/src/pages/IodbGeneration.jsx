import { useState, useRef } from 'react'
import { useFileInput } from '@common/hooks/useFileInput'
import { downloadBlob } from '@common/utils/fileDownload'
import {
  iodbParseTemplate, iodbParse,
  iodbGenerateIOLayout, iodbGenerateTagDB, iodbGenerateExplodedView,
  iodbGenerateIOList, iodbDownload,
} from '../services/api'

// ── Theme ──────────────────────────────────────────────────────────────────────
const T = {
  bg:     'var(--bg-app)', panel:  'var(--bg-main)', border: 'var(--border)',
  text:   'var(--text)', muted:  'var(--text-dim)', accent: 'var(--accent)',
  ok:     'var(--status-success-soft)', err:    'var(--status-error)', warn:   'var(--status-warning)',
  hdr:    'var(--bg-thead)', rowHv:  'var(--bg-row-hover)',
}

const GENERATED = new Set(['IOLayout', 'TagDB', 'ExplodedView', 'IOList'])

// ── Shared micro-components (module-level to avoid remount on re-render) ───────

function SectionLabel({ children, mt }) {
  return (
    <div style={{
      fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
      textTransform: 'uppercase', color: T.muted,
      marginTop: mt || 0, marginBottom: 8,
    }}>
      {children}
    </div>
  )
}

function BtnPrimary({ children, onClick, disabled, style }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      background: disabled ? 'var(--btn-primary-disabled-bg)' : T.accent,
      color: disabled ? T.muted : '#fff',
      border: 'none', borderRadius: 4, padding: '7px 12px',
      fontSize: 12, cursor: disabled ? 'default' : 'pointer',
      width: '100%', textAlign: 'left', ...style,
    }}>
      {children}
    </button>
  )
}

function BtnOutline({ children, onClick, disabled, style }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      background: 'transparent',
      color: disabled ? T.muted : T.accent,
      border: `1px solid ${disabled ? T.border : T.accent}`,
      borderRadius: 4, padding: '6px 12px',
      fontSize: 12, cursor: disabled ? 'default' : 'pointer',
      width: '100%', ...style,
    }}>
      {children}
    </button>
  )
}

function ConfigArea({ value, onChange, fileRef, onFile, placeholder }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        style={{
          width: '100%', boxSizing: 'border-box', height: 80, resize: 'vertical',
          background: 'var(--bg-code)', border: `1px solid ${T.border}`, borderRadius: 4,
          color: T.text, fontSize: 11, padding: '6px 8px', fontFamily: 'monospace',
          display: 'block',
        }}
      />
      <button
        onClick={() => fileRef.current?.click()}
        style={{ fontSize: 11, color: T.muted, background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0' }}
      >
        ↑ upload .json
      </button>
      <input
        ref={fileRef} type="file" accept=".json" style={{ display: 'none' }}
        onChange={e => { const f = e.target.files[0]; if (f) { onFile(f); e.target.value = '' } }}
      />
    </div>
  )
}

// ── Sheet table — used for all non-COVER sheets ────────────────────────────────

function DataSheet({ sheet, sheetName, onChange }) {
  const { headers, rows, validations, readonly } = sheet
  const editable = sheetName === 'PLCEquipment' && !readonly

  const thStyle = {
    padding: '5px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
    background: T.hdr, borderRight: `1px solid ${T.border}`,
    borderBottom: `1px solid ${T.border}`,
    position: 'sticky', top: 0, zIndex: 1, whiteSpace: 'nowrap',
  }
  const tdStyle = {
    padding: '3px 8px', fontSize: 12, color: T.text,
    borderRight: `1px solid ${T.border}`, borderBottom: `1px solid ${T.border}`,
    maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  }
  const inputStyle = {
    background: 'transparent', border: 'none', color: T.text,
    fontSize: 12, width: '100%', outline: 'none', padding: 0, fontFamily: 'inherit',
  }

  return (
    <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
      <thead>
        <tr>{headers.map((h, i) => <th key={i} style={thStyle}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => (
          <tr key={ri}>
            {headers.map((h, ci) => {
              const val = row[ci] ?? ''
              if (!editable) {
                return <td key={ci} style={tdStyle}>{String(val)}</td>
              }
              const opts = validations?.[h]
              if (opts) {
                return (
                  <td key={ci} style={tdStyle}>
                    <select
                      value={String(val)}
                      onChange={e => onChange(ri, ci, e.target.value)}
                      style={{ ...inputStyle, cursor: 'pointer' }}
                    >
                      <option value="">—</option>
                      {opts.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </td>
                )
              }
              return (
                <td key={ci} style={tdStyle}>
                  <input
                    value={String(val)}
                    onChange={e => onChange(ri, ci, e.target.value)}
                    style={inputStyle}
                  />
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── COVER sheet — special key-value layout ─────────────────────────────────────

function CoverSheet({ sheet, onChangeRow, onChangePA }) {
  const { rows, process_areas } = sheet

  const thStyle = {
    padding: '5px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
    background: T.hdr, borderRight: `1px solid ${T.border}`,
    borderBottom: `1px solid ${T.border}`,
    position: 'sticky', top: 0, zIndex: 1,
  }
  const tdStyle = {
    padding: '3px 8px', fontSize: 12,
    borderRight: `1px solid ${T.border}`, borderBottom: `1px solid ${T.border}`,
  }
  const inputStyle = {
    background: 'transparent', border: 'none', color: T.text,
    fontSize: 12, width: '100%', outline: 'none', padding: 0, fontFamily: 'inherit',
  }

  return (
    <div style={{ padding: 20 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', maxWidth: 600, marginBottom: 28 }}>
        <thead>
          <tr>
            <th style={{ ...thStyle, width: 200 }}>Field</th>
            <th style={thStyle}>Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              <td style={{ ...tdStyle, color: T.muted }}>{row[0] ?? ''}</td>
              <td style={tdStyle}>
                <input
                  value={String(row[1] ?? '')}
                  onChange={e => onChangeRow(ri, 1, e.target.value)}
                  style={inputStyle}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {process_areas?.length > 0 && (
        <>
          <div style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
            textTransform: 'uppercase', color: T.muted, marginBottom: 8,
          }}>
            Process Areas
          </div>
          <table style={{ borderCollapse: 'collapse', maxWidth: 480 }}>
            <thead>
              <tr>
                <th style={{ ...thStyle, width: 120 }}>Abbreviation</th>
                <th style={thStyle}>Name</th>
              </tr>
            </thead>
            <tbody>
              {process_areas.map((pa, ri) => (
                <tr key={ri}>
                  <td style={tdStyle}>
                    <input
                      value={String(pa[0] ?? '')}
                      onChange={e => onChangePA(ri, 0, e.target.value)}
                      style={{ ...inputStyle, width: 100 }}
                    />
                  </td>
                  <td style={tdStyle}>
                    <input
                      value={String(pa[1] ?? '')}
                      onChange={e => onChangePA(ri, 1, e.target.value)}
                      style={inputStyle}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function IodbGeneration() {
  const [workbook, setWorkbook]     = useState(null)
  const [activeSheet, setActive]    = useState(null)
  const [tagdbCfg, setTagdbCfg]     = useState('')
  const [iolistCfg, setIolistCfg]   = useState('')
  const [status, setStatus]         = useState(null)
  const [loading, setLoading]       = useState(false)
  const [loadingOp, setOp]          = useState('')

  const tagdbFileRef  = useRef(null)
  const iolistFileRef = useRef(null)

  const { inputProps, dropZoneProps, triggerPicker, dragging } = useFileInput(loadFile)

  // ── Workbook loading ─────────────────────────────────────────────────────────

  async function loadFile(file) {
    setOp('parse'); setLoading(true); setStatus(null)
    const result = await iodbParse(file)
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    applyWorkbook(result.data)
  }

  async function loadTemplate() {
    setOp('template'); setLoading(true); setStatus(null)
    const result = await iodbParseTemplate()
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    applyWorkbook(result.data)
  }

  function applyWorkbook(data) {
    setWorkbook(data)
    const first = data.sheet_order.find(n => !data.sheets[n]?.hidden)
    setActive(first || null)
    setStatus({ type: 'ok', msg: `Loaded: ${data.filename}` })
    setTagdbCfg('')
    setIolistCfg('')
  }

  // ── Cell editing ─────────────────────────────────────────────────────────────

  function updateCell(sheetName, rowIdx, colIdx, value) {
    setWorkbook(prev => ({
      ...prev,
      sheets: {
        ...prev.sheets,
        [sheetName]: {
          ...prev.sheets[sheetName],
          rows: prev.sheets[sheetName].rows.map((r, ri) =>
            ri !== rowIdx ? r : r.map((c, ci) => ci !== colIdx ? c : value)
          ),
        },
      },
    }))
  }

  function updateProcessArea(rowIdx, colIdx, value) {
    setWorkbook(prev => ({
      ...prev,
      sheets: {
        ...prev.sheets,
        COVER: {
          ...prev.sheets.COVER,
          process_areas: prev.sheets.COVER.process_areas.map((r, ri) =>
            ri !== rowIdx ? r : r.map((c, ci) => ci !== colIdx ? c : value)
          ),
        },
      },
    }))
  }

  // ── Sheet update helper ───────────────────────────────────────────────────────

  function applyGeneratedSheet(name, { headers, rows }) {
    setWorkbook(prev => ({
      ...prev,
      sheets: {
        ...prev.sheets,
        [name]: { headers, rows, readonly: false, hidden: false },
      },
      sheet_order: prev.sheet_order.includes(name)
        ? prev.sheet_order
        : [...prev.sheet_order, name],
    }))
    setActive(name)
  }

  // ── Generate ─────────────────────────────────────────────────────────────────

  async function genIOLayout() {
    setOp('iolayout'); setLoading(true); setStatus(null)
    const result = await iodbGenerateIOLayout(workbook.sheets)
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    applyGeneratedSheet('IOLayout', result.data)
    const s = result.data.stats
    setStatus({ type: 'ok', msg: `IOLayout: ${s.total_rows} rows — ${s.cards_processed} cards` })
  }

  async function genTagDB() {
    let cfg
    try { cfg = JSON.parse(tagdbCfg) } catch { setStatus({ type: 'err', msg: 'TagDB config is not valid JSON' }); return }
    setOp('tagdb'); setLoading(true); setStatus(null)
    const result = await iodbGenerateTagDB(workbook.sheets, cfg)
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    applyGeneratedSheet('TagDB', result.data)
    setStatus({ type: 'ok', msg: `TagDB: ${result.data.stats.total} rows` })
  }

  async function genExplodedView() {
    setOp('exploded'); setLoading(true); setStatus(null)
    const result = await iodbGenerateExplodedView(workbook.sheets)
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    applyGeneratedSheet('ExplodedView', result.data)
    const w = result.data.warnings?.length ? ` (${result.data.warnings.length} unknown classes)` : ''
    setStatus({ type: 'ok', msg: `ExplodedView: ${result.data.stats.rows_written} rows${w}` })
  }

  async function genIOList() {
    let cfg
    try { cfg = JSON.parse(iolistCfg) } catch { setStatus({ type: 'err', msg: 'IOList config is not valid JSON' }); return }
    setOp('iolist'); setLoading(true); setStatus(null)
    const result = await iodbGenerateIOList(workbook.sheets, cfg)
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    applyGeneratedSheet('IOList', result.data)
    setStatus({ type: 'ok', msg: `IOList: ${result.data.stats.total} rows` })
  }

  async function handleDownload() {
    setOp('download'); setLoading(true); setStatus(null)
    const result = await iodbDownload(workbook.original_b64, workbook.sheets, workbook.filename)
    setLoading(false)
    if (!result.ok) { setStatus({ type: 'err', msg: result.error }); return }
    downloadBlob(result.blob, result.filename)
    setStatus({ type: 'ok', msg: `Downloaded: ${result.filename}` })
  }

  function readJsonFile(file, setter) {
    const reader = new FileReader()
    reader.onload = e => setter(e.target.result)
    reader.readAsText(file)
  }

  // ── Derived ───────────────────────────────────────────────────────────────────

  const isOp    = op => loading && loadingOp === op
  const visible = workbook
    ? workbook.sheet_order.filter(n => !workbook.sheets[n]?.hidden)
    : []
  const activeData = activeSheet && workbook ? workbook.sheets[activeSheet] : null
  const statusColor = !status ? T.muted : status.type === 'ok' ? T.ok : T.err

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div style={{ display: 'flex', height: '100%', fontFamily: 'inherit', overflow: 'hidden' }}>

      {/* ── Left panel ──────────────────────────────────────────────────────── */}
      <div style={{
        width: 264, minWidth: 264, background: T.bg,
        borderRight: `1px solid ${T.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 14px 8px' }}>

          <SectionLabel>Workbook</SectionLabel>

          {/* Drop zone */}
          <div
            {...dropZoneProps}
            onClick={triggerPicker}
            style={{
              border: `2px dashed ${dragging ? T.accent : T.border}`,
              borderRadius: 6, padding: '14px 10px', textAlign: 'center',
              cursor: 'pointer', background: dragging ? 'var(--bg-drag)' : 'transparent',
              color: T.muted, fontSize: 12, marginBottom: 8, transition: 'all 0.15s',
              userSelect: 'none',
            }}
          >
            {isOp('parse')
              ? <span style={{ color: T.text }}>Parsing…</span>
              : <>{isOp('template') ? 'Loading…' : 'Drop .xlsx here'}<br /><span style={{ fontSize: 11 }}>or click to browse</span></>
            }
          </div>
          <input {...inputProps} accept=".xlsx,.xls" />

          <BtnOutline onClick={loadTemplate} disabled={loading} style={{ marginBottom: 8 }}>
            {isOp('template') ? 'Loading template…' : 'Use Template'}
          </BtnOutline>

          {workbook && (
            <div style={{ fontSize: 11, color: T.muted, marginBottom: 4, wordBreak: 'break-all' }}>
              <span style={{ color: T.ok }}>●</span> {workbook.filename}
            </div>
          )}

          {/* Generate section — only when workbook loaded */}
          {workbook && (
            <>
              <SectionLabel mt={20}>Generate</SectionLabel>

              <BtnPrimary
                onClick={genIOLayout}
                disabled={loading}
                style={{ marginBottom: 6 }}
              >
                {isOp('iolayout') ? 'Generating IOLayout…' : 'IOLayout'}
              </BtnPrimary>

              <BtnPrimary
                onClick={genTagDB}
                disabled={loading || !tagdbCfg.trim()}
                style={{ marginBottom: 4 }}
              >
                {isOp('tagdb') ? 'Generating TagDB…' : 'TagDB'}
              </BtnPrimary>
              <ConfigArea
                value={tagdbCfg}
                onChange={setTagdbCfg}
                fileRef={tagdbFileRef}
                onFile={f => readJsonFile(f, setTagdbCfg)}
                placeholder={'{\n  "tagdb_config": { ... }\n}'}
              />

              <BtnPrimary
                onClick={genExplodedView}
                disabled={loading}
                style={{ marginBottom: 6 }}
              >
                {isOp('exploded') ? 'Generating ExplodedView…' : 'ExplodedView'}
              </BtnPrimary>

              <BtnPrimary
                onClick={genIOList}
                disabled={loading || !iolistCfg.trim()}
                style={{ marginBottom: 4 }}
              >
                {isOp('iolist') ? 'Generating IOList…' : 'IOList from JSON'}
              </BtnPrimary>
              <ConfigArea
                value={iolistCfg}
                onChange={setIolistCfg}
                fileRef={iolistFileRef}
                onFile={f => readJsonFile(f, setIolistCfg)}
                placeholder={'{\n  "io_list": [ ... ]\n}'}
              />

              <SectionLabel mt={16}>Download</SectionLabel>
              <BtnPrimary onClick={handleDownload} disabled={loading}>
                {isOp('download') ? 'Building workbook…' : 'Download Workbook'}
              </BtnPrimary>
            </>
          )}
        </div>

        {/* Status bar */}
        <div style={{
          padding: '8px 14px', borderTop: `1px solid ${T.border}`,
          fontSize: 12, color: statusColor, minHeight: 34,
          wordBreak: 'break-word',
        }}>
          {status?.msg || <span style={{ color: T.muted }}>No workbook loaded.</span>}
        </div>
      </div>

      {/* ── Right panel ─────────────────────────────────────────────────────── */}
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        background: T.panel, overflow: 'hidden',
      }}>
        {!workbook ? (
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: T.muted, fontSize: 14,
          }}>
            Upload a workbook or load the template to begin.
          </div>
        ) : (
          <>
            {/* Sheet tabs */}
            <div style={{
              display: 'flex', background: T.bg, borderBottom: `1px solid ${T.border}`,
              overflowX: 'auto', flexShrink: 0,
            }}>
              {visible.map(name => {
                const isActive    = activeSheet === name
                const isGenerated = GENERATED.has(name) && (workbook.sheets[name]?.rows?.length ?? 0) > 0
                return (
                  <button
                    key={name}
                    onClick={() => setActive(name)}
                    style={{
                      padding: '8px 14px', fontSize: 12, cursor: 'pointer', border: 'none',
                      background: isActive ? T.panel : 'transparent',
                      color: isActive ? T.text : T.muted,
                      borderBottom: isActive ? `2px solid ${T.accent}` : '2px solid transparent',
                      whiteSpace: 'nowrap', transition: 'color 0.1s', flexShrink: 0,
                    }}
                  >
                    {isGenerated && <span style={{ marginRight: 4, color: T.ok, fontSize: 9 }}>●</span>}
                    {name}
                  </button>
                )
              })}
            </div>

            {/* Sheet data */}
            <div style={{ flex: 1, overflow: 'auto' }}>
              {activeData && (
                activeSheet === 'COVER'
                  ? (
                    <CoverSheet
                      sheet={activeData}
                      onChangeRow={(ri, ci, v) => updateCell('COVER', ri, ci, v)}
                      onChangePA={updateProcessArea}
                    />
                  ) : (
                    <DataSheet
                      sheet={activeData}
                      sheetName={activeSheet}
                      onChange={(ri, ci, v) => updateCell(activeSheet, ri, ci, v)}
                    />
                  )
              )}
            </div>

            {/* Row count footer */}
            {activeData && (
              <div style={{
                padding: '4px 12px', borderTop: `1px solid ${T.border}`,
                fontSize: 11, color: T.muted, flexShrink: 0,
              }}>
                {activeData.rows?.length ?? 0} rows
                {GENERATED.has(activeSheet) && ' · generated'}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
