import { useRef, useState, useCallback } from 'react'

// Shared file-input hook: a hidden <input type=file> plus a drag-and-drop zone.
//
//   const { inputProps, dropZoneProps, triggerPicker, dragging } = useFileInput(onFile)
//
//   inputProps    -> spread onto a hidden <input> (ref/type/hidden/onChange). Pass `accept` yourself.
//   dropZoneProps -> spread onto the drop-zone element (onDragOver / onDragLeave / onDrop).
//   triggerPicker -> opens the file picker (wire to the drop zone's onClick).
//   dragging      -> true while a file is being dragged over the drop zone.
//
// onFile is called with the first selected/dropped File.
export function useFileInput(onFile) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  const handleFiles = useCallback((files) => {
    const f = files && files[0]
    if (f && typeof onFile === 'function') onFile(f)
  }, [onFile])

  const triggerPicker = useCallback(() => {
    inputRef.current?.click()
  }, [])

  const inputProps = {
    ref: inputRef,
    type: 'file',
    style: { display: 'none' },
    onChange: (e) => {
      handleFiles(e.target.files)
      e.target.value = '' // allow re-selecting the same file
    },
  }

  const dropZoneProps = {
    onDragOver: (e) => { e.preventDefault(); setDragging(true) },
    onDragLeave: () => setDragging(false),
    onDrop: (e) => {
      e.preventDefault()
      setDragging(false)
      handleFiles(e.dataTransfer?.files)
    },
  }

  return { inputProps, dropZoneProps, triggerPicker, dragging }
}

export default useFileInput
