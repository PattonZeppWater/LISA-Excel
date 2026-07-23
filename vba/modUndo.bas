Attribute VB_Name = "modUndo"
Option Explicit

'Custom Ctrl+Z / Ctrl+Y for the FillIndex sheet. Excel's native undo is wiped by
'the Worksheet_Change rebuild, so we drive our own stacks (in Sheet2) and bind the
'keys only while FillIndex is the active sheet (see ThisWorkbook). Binding is
'Application-global, so unbinding on deactivate/close is essential.
Private gBound As Boolean

'Ctrl+C / Ctrl+V are intentionally NOT rebound here -- they stay Excel-native so copy and
'paste behave exactly as they do everywhere else. (The old custom "smart values-only paste"
'shim interfered with normal Ctrl+V; a native paste on FillIndex is cleaned up afterwards by
'Worksheet_Change -> PZ_SanitizePastedRows, so nothing is lost by letting Excel handle it.)

'Re-assert the key bindings EVERY call -- do NOT short-circuit on a "already bound" flag.
'Excel silently drops OnKey assignments in a lot of situations (after certain dialogs, an
'add-in, a focus change, or a VBA state reset). If we trusted a sticky gBound flag we would
'skip re-binding and Ctrl+Z would quietly fall through to Excel's NATIVE undo -- which, after
'the macro rebuilds, either does nothing or reverts a huge batch at once. Re-asserting is a
'cheap, idempotent app setting, so we just do it whenever the undo sheet is (re)entered.
Public Sub PZ_BindKeys()
    On Error Resume Next
    Application.OnKey "^z", "PZ_KeyUndo"
    Application.OnKey "^y", "PZ_KeyRedo"
    Application.OnKey "^+z", "PZ_KeyRedo"   'Ctrl+Shift+Z = redo too
    gBound = True
End Sub

Public Sub PZ_UnbindKeys()
    On Error Resume Next
    If Not gBound Then Exit Sub
    Application.OnKey "^z"      'no proc arg -> restore Excel default
    Application.OnKey "^y"
    Application.OnKey "^+z"
    gBound = False
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

