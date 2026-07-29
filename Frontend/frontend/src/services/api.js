async function request(path, options = {}) {
  const res  = await fetch(path, options)
  const data = await res.json().catch(() => ({}))
  return { ok: res.ok, data }
}

// ── SAC_Generation ─────────────────────────────────────────────────────────────
export const sacGenHealth = () => request('/api/sac-gen/health')


// ── IODB_Generation ────────────────────────────────────────────────────────────

export async function iodbParseTemplate() {
  try {
    const res = await fetch('/api/iodb-gen/parse-template')
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Parse template failed' }))
      return { ok: false, error: err.error || 'Parse template failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbDownloadTemplate() {
  try {
    const res = await fetch('/api/iodb-gen/template')
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Template download failed' }))
      return { ok: false, error: err.error || 'Template download failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'Template_IODB.xlsx' }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbParse(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/iodb-gen/parse', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Parse failed' }))
      return { ok: false, error: err.error || 'Parse failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbGenerateIOLayout(sheets) {
  try {
    const res = await fetch('/api/iodb-gen/generate/iolayout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheets }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'IOLayout generation failed' }))
      return { ok: false, error: err.error || 'IOLayout generation failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbGenerateTagDB(sheets, configJson) {
  try {
    const res = await fetch('/api/iodb-gen/generate/tagdb', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheets, config_json: configJson }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'TagDB generation failed' }))
      return { ok: false, error: err.error || 'TagDB generation failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbGenerateExplodedView(sheets) {
  try {
    const res = await fetch('/api/iodb-gen/generate/explodedview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheets }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'ExplodedView generation failed' }))
      return { ok: false, error: err.error || 'ExplodedView generation failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbGenerateIOList(sheets, configJson) {
  try {
    const res = await fetch('/api/iodb-gen/generate/iolist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheets, config_json: configJson }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'IOList generation failed' }))
      return { ok: false, error: err.error || 'IOList generation failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbDownload(originalB64, sheets, filename) {
  try {
    const res = await fetch('/api/iodb-gen/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ original_b64: originalB64, sheets, filename }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Download failed' }))
      return { ok: false, error: err.error || 'Download failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : filename }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbValidateIOList(payload) {
  try {
    const res = await fetch('/api/iodb-gen/io-list/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return { ok: res.ok, data: await res.json().catch(() => ({})) }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}

export async function iodbExportIOList(payload) {
  try {
    const res = await fetch('/api/iodb-gen/io-list/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Export failed' }))
      return { ok: false, error: err.error || 'Export failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'IOList.xlsx' }
  } catch {
    return { ok: false, error: 'Could not reach IODB_Generation. Is the service running?' }
  }
}


// ── IDP_Generation ─────────────────────────────────────────────────────────────

export async function parseIdpWorkbook(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/idp-gen/parse', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Parse failed' }))
      return { ok: false, error: err.error || 'Parse failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function getIdpTemplate() {
  try {
    const res = await fetch('/api/idp-gen/parse-template')
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Template load failed' }))
      return { ok: false, error: err.error || 'Template load failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function getIdpAutoCADStatus() {
  try {
    const res = await fetch('/api/idp-gen/autocad-status')
    return { ok: res.ok, data: await res.json().catch(() => ({ running: false })) }
  } catch {
    return { ok: false, data: { running: false } }
  }
}

export async function browseIdpOutputFolder() {
  try {
    const res = await fetch('/api/idp-gen/browse-folder')
    return await res.json().catch(() => ({ path: null }))
  } catch {
    return { path: null, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function generateIdpDwg(payload, signal) {
  try {
    const res = await fetch('/api/idp-gen/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Generation failed' }))
      return { ok: false, error: err.error || 'Generation failed' }
    }
    return { ok: true, data: await res.json() }
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, aborted: true, error: 'Stopped' }
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function downloadIdpWorkbook(payload) {
  try {
    const res = await fetch('/api/idp-gen/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Download failed' }))
      return { ok: false, error: err.error || 'Download failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : payload.filename || 'IDP_Workbook.xlsx' }
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

// Save a conduit-list CSV to disk via a native Save-As dialog (browser downloads don't
// work inside the LISA desktop webview). payload: { csv, filename, default_dir }.
// Returns { ok, path } | { ok:false, cancelled:true } | { ok:false, error }.
export async function exportIdpConduitList(payload) {
  try {
    const res = await fetch('/api/idp-gen/export-conduit-list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || 'Export failed' }
    return data
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function exportIdpFillReport(payload) {
  try {
    const res = await fetch('/api/idp-gen/fill-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || 'Fill report failed' }
    return data
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function exportIdpWireLabelPrint(payload) {
  try {
    const res = await fetch('/api/idp-gen/wire-label-print', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || 'Wire label print failed' }
    return data
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function downloadIdpWireLabels(fillIndex, filename) {
  try {
    const res = await fetch('/api/idp-gen/wire-labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fill_index: fillIndex, filename }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Wire labels download failed' }))
      return { ok: false, error: err.error || 'Wire labels download failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'WireLabels.xlsx' }
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}

export async function downloadIdpTemplate() {
  try {
    const res = await fetch('/api/idp-gen/download-template')
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Template download failed' }))
      return { ok: false, error: err.error || 'Template download failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'IDP_Workbook.xlsx' }
  } catch {
    return { ok: false, error: 'Could not reach IDP_Generation. Is the service running?' }
  }
}


// ── Shared_DocForge ────────────────────────────────────────────────────────────

async function dfDownload(path, body, fallbackFilename) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Request failed' }))
      return { ok: false, error: err.error || 'Request failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : fallbackFilename }
  } catch {
    return { ok: false, error: 'Could not reach Shared_DocForge. Is the service running?' }
  }
}

export const dfBuildMarkdown      = (tree) => dfDownload('/api/docforge/markdown',      { tree }, 'DocForge_Structure.md')
export const dfBuildStructureZip  = (tree) => dfDownload('/api/docforge/zip-structure', { tree }, 'DocForge_Structure.zip')

export async function dfBuildContentsZip(tree, files) {
  // files: { slot: File, ... }
  try {
    const form = new FormData()
    form.append('payload', JSON.stringify({ tree }))
    for (const [slot, file] of Object.entries(files)) {
      form.append(slot, file)
    }
    const res = await fetch('/api/docforge/zip-contents', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Build failed' }))
      return { ok: false, error: err.error || 'Build failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'DocForge_Contents.zip' }
  } catch {
    return { ok: false, error: 'Could not reach Shared_DocForge. Is the service running?' }
  }
}


// ── Shared_SubmittalLog ────────────────────────────────────────────────────────

export async function slDownloadTemplate() {
  try {
    const res = await fetch('/api/submittal-log/submittal/template')
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Download failed' }))
      return { ok: false, error: err.error || 'Download failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'SubmittalCoverLetter_Template.docx' }
  } catch {
    return { ok: false, error: 'Could not reach Shared_SubmittalLog. Is the service running?' }
  }
}

export async function slParsePdf(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/submittal-log/submittal/parse-pdf', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Parse failed' }))
      return { ok: false, error: err.error || 'Parse failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach Shared_SubmittalLog. Is the service running?' }
  }
}

export async function slCompilePdf(body, file) {
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('payload', JSON.stringify(body))
    const res = await fetch('/api/submittal-log/submittal/compile-pdf', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Compile PDF failed' }))
      return { ok: false, error: err.error || 'Compile PDF failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'Submittal.pdf' }
  } catch {
    return { ok: false, error: 'Could not reach Shared_SubmittalLog. Is the service running?' }
  }
}

export async function slCompile(body) {
  try {
    const res = await fetch('/api/submittal-log/submittal/compile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Compile failed' }))
      return { ok: false, error: err.error || 'Compile failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'Submittal.docx' }
  } catch {
    return { ok: false, error: 'Could not reach Shared_SubmittalLog. Is the service running?' }
  }
}

export async function slLoad(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/submittal-log/submittal/load', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Load failed' }))
      return { ok: false, error: err.error || 'Load failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach Shared_SubmittalLog. Is the service running?' }
  }
}


// ── Shared_TimeSheets ──────────────────────────────────────────────────────────

export async function tsDownloadData() {
  try {
    const res = await fetch('/api/timesheets/download')
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Download failed' }))
      return { ok: false, error: err.error || 'Download failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach Shared_TimeSheets. Is the service running?' }
  }
}

export async function tsPreview(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/timesheets/preview', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Preview failed' }))
      return { ok: false, error: err.error || 'Preview failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach Shared_TimeSheets. Is the service running?' }
  }
}

export async function tsProcess(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/timesheets/process', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Process failed' }))
      return { ok: false, error: err.error || 'Process failed' }
    }
    return { ok: true, data: await res.json() }
  } catch {
    return { ok: false, error: 'Could not reach Shared_TimeSheets. Is the service running?' }
  }
}

export async function tsReporting(file) {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/timesheets/reporting', { method: 'POST', body: form })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Reporting failed' }))
      return { ok: false, error: err.error || 'Reporting failed' }
    }
    const blob        = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match       = disposition.match(/filename="?([^"]+)"?/)
    return { ok: true, blob, filename: match ? match[1] : 'timesheets_report.zip' }
  } catch {
    return { ok: false, error: 'Could not reach Shared_TimeSheets. Is the service running?' }
  }
}
