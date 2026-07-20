import { useRef, useState, useCallback } from 'react'

// Reusable file-input helper: wires up a hidden <input type="file"> plus drag-and-drop
// over a target element. onFile(file) is invoked with the first selected/dropped File.
//
// Usage:
//   const { inputProps, dropZoneProps, triggerPicker, dragging } = useFileInput(loadFile)
//   <div {...dropZoneProps} onClick={triggerPicker}> ... <input {...inputProps} accept=".xlsx" /> </div>
export function useFileInput(onFile) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  const handleFiles = useCallback((files) => {
    if (files && files.length > 0) onFile(files[0])
  }, [onFile])

  const triggerPicker = useCallback(() => {
    if (inputRef.current) inputRef.current.click()
  }, [])

  const inputProps = {
    ref: inputRef,
    type: 'file',
    style: { display: 'none' },
    onChange: (e) => {
      handleFiles(e.target.files)
      e.target.value = '' // reset so selecting the same file again re-fires onChange
    },
  }

  const dropZoneProps = {
    onDragOver: (e) => { e.preventDefault(); setDragging(true) },
    onDragEnter: (e) => { e.preventDefault(); setDragging(true) },
    onDragLeave: (e) => { e.preventDefault(); setDragging(false) },
    onDrop: (e) => {
      e.preventDefault()
      setDragging(false)
      handleFiles(e.dataTransfer.files)
    },
  }

  return { inputProps, dropZoneProps, triggerPicker, dragging }
}
