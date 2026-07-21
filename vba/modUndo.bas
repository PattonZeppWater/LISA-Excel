Attribute VB_Name = "modUndo"
Option Explicit

'Custom Ctrl+Z / Ctrl+Y for the FillIndex sheet. Excel's native undo is wiped by
'the Worksheet_Change rebuild, so we drive our own stacks (in Sheet2) and bind the
'keys only while FillIndex is the active sheet (see ThisWorkbook). Binding is
'Application-global, so unbinding on deactivate/close is essential.
Private gBound As Boolean

'Last range copied with Ctrl+C while a bound sheet was active. Used by the Ctrl+V shim
'to do a smart, values-only paste on FillIndex (see PZ_KeyPaste / Sheet2.PZ_SmartValuesPaste).
Private gCopied As Range

Public Sub PZ_BindKeys()
    On Error Resume Next
    If gBound Then Exit Sub
    Application.OnKey "^z", "PZ_KeyUndo"
    Application.OnKey "^y", "PZ_KeyRedo"
    Application.OnKey "^+z", "PZ_KeyRedo"   'Ctrl+Shift+Z = redo too
    Application.OnKey "^c", "PZ_KeyCopy"    'capture the copied range for smart paste
    Application.OnKey "^v", "PZ_KeyPaste"   'smart, values-only paste on FillIndex
    gBound = True
End Sub

Public Sub PZ_UnbindKeys()
    On Error Resume Next
    Application.OnKey "^z"      'no proc arg -> restore Excel default
    Application.OnKey "^y"
    Application.OnKey "^+z"
    Application.OnKey "^c"
    Application.OnKey "^v"
    gBound = False
End Sub

'Ctrl+C shim: remember what was copied (so Ctrl+V can collapse hidden columns), then
'perform the normal copy so the clipboard + marching ants behave exactly as usual.
Public Sub PZ_KeyCopy()
    On Error Resume Next
    Set gCopied = Nothing
    If TypeName(Selection) = "Range" Then
        If Selection.Areas.Count = 1 Then Set gCopied = Selection
        Selection.Copy
    End If
End Sub

'Ctrl+V shim: on FillIndex, when the clipboard holds a range COPIED (not cut) from the
'same sheet, route to the smart values-only paste that fixes the hidden-spacer scatter /
'Rating spill. Everything else (cuts, external clipboard, other sheets) falls back to a
'normal Excel paste, so nothing else changes.
Public Sub PZ_KeyPaste()
    On Error GoTo Fallback
    If ActiveSheet.CodeName = "Sheet2" _
       And Application.CutCopyMode = xlCopy _
       And Not gCopied Is Nothing Then
        If gCopied.Worksheet.CodeName = "Sheet2" And TypeName(Selection) = "Range" Then
            If Sheet2.PZ_SmartValuesPaste(gCopied, Selection) Then
                Application.CutCopyMode = False
                Exit Sub
            End If
        End If
    End If
Fallback:
    On Error Resume Next
    ActiveSheet.Paste
End Sub

Public Sub PZ_KeyUndo()
    On Error Resume Next
    Select Case ActiveSheet.CodeName
        Case "Sheet2": Sheet2.PZ_DoUndo      'FillIndex
        Case "Sheet1": Sheet1.CI_DoUndo      'ConduitIndex
    End Select
End Sub

Public Sub PZ_KeyRedo()
    On Error Resume Next
    Select Case ActiveSheet.CodeName
        Case "Sheet2": Sheet2.PZ_DoRedo      'FillIndex
        Case "Sheet1": Sheet1.CI_DoRedo      'ConduitIndex
    End Select
End Sub

