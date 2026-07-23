VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "ThisWorkbook"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

'Bind the custom Ctrl+Z / Ctrl+Y handlers only while the FillIndex sheet (codename
'Sheet2) is active, and always release them otherwise so other sheets/workbooks
'keep Excel's native undo.

Private Sub Workbook_Open()
    On Error Resume Next: Sheet2.PZ_EnsureHeaders: On Error GoTo 0
    On Error Resume Next: Sheet2.PZ_HideCtrlCols: On Error GoTo 0
    On Error Resume Next: Sheet1.CI_EnsureHeaders: Sheet1.CI_EnsureValidations: On Error GoTo 0
    On Error Resume Next: Sheet8.RD_EnsureHeaders: Sheet8.RD_RefreshRows: On Error GoTo 0
    On Error Resume Next: Sheet10.PD_EnsureHeaders: Sheet10.PD_EnsureTextFormat: On Error GoTo 0
    On Error Resume Next
    Application.EnableEvents = True   'recover if a prior error left events off
    Application.Calculation = xlCalculationAutomatic   'never open stuck in manual calc
    On Error Resume Next: Sheet2.PZ_AutoWidth: On Error GoTo 0   'fit FillIndex columns on open
    PZ_SyncUndoKeys ActiveSheet
End Sub

Private Sub Workbook_Activate()
    On Error Resume Next
    PZ_SyncUndoKeys ActiveSheet
End Sub

Private Sub Workbook_Deactivate()
    On Error Resume Next
    modUndo.PZ_UnbindKeys
End Sub

Private Sub Workbook_SheetActivate(ByVal Sh As Object)
    On Error Resume Next
    PZ_SyncUndoKeys Sh
End Sub

Private Sub Workbook_BeforeClose(Cancel As Boolean)
    On Error Resume Next
    modUndo.PZ_UnbindKeys
End Sub

Private Sub PZ_SyncUndoKeys(ByVal Sh As Object)
    On Error Resume Next
    Select Case Sh.CodeName
        Case "Sheet1", "Sheet2", "Sheet8", "Sheet10"   'ConduitIndex / FillIndex / Ref Docs / Project Description
            modUndo.PZ_BindKeys
        Case Else
            modUndo.PZ_UnbindKeys
    End Select
End Sub

