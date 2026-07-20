// Download a Blob (or string / ArrayBuffer / typed array) as a file in the browser.
//   downloadBlob(data, filename)
export function downloadBlob(data, filename) {
  const blob = data instanceof Blob ? data : new Blob([data])
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'download'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export default downloadBlob
