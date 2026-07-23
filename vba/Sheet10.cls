VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet10"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

'Data area (rows 3..PD_LAST_ROW, columns A..H). Mirrors Ref Documents & Deviations:
'the cells are kept as Text so a value like "00E016" is stored verbatim instead of being
'turned into scientific notation by Excel's "General" number handling.
Private Const PD_FIRST_DATA As Long = 3
Private Const PD_LAST_ROW As Long = 100

'Multi-level Ctrl+Z / Ctrl+Y (mirrors ConduitIndex). Snapshot the data area and drive our
'own stacks so the sheet's own Change handling doesn't leave undo dead.
Private Const PD_UNDO_RANGE As String = "A3:H100"
Private Const PD_UNDO_MAX As Long = 50
Private mCommitted As Variant
Private mUndo As Collection
Private mRedo As Collection

Public Sub PD_EnsureHeaders()
    On Error Resume Next
    If Len(Trim$(CStr(Me.Cells(1, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 8).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    'Row 1 title band -- restore text AND its look (white bold on dark blue). Ctrl+A + Delete
    'wipes the header text; without re-applying the fill/font the band read as blank, so we
    'rebuild the whole thing, not just the text.
    With Me.Range("A1:H1")
        .UnMerge
        .Merge
        .Interior.Color = RGB(21, 58, 138)
        .Font.Color = RGB(255, 255, 255)
        .Font.Bold = True
    End With
    Me.Cells(1, 1).Value = "PROJECT DESCRIPTION"
    Me.Cells(2, 1).Value = "Owner"
    Me.Cells(2, 2).Value = "Job Title"
    Me.Cells(2, 3).Value = "Content"
    Me.Cells(2, 4).Value = "Proj No."
    Me.Cells(2, 5).Value = "Status"
    Me.Cells(2, 6).Value = "Date"
    Me.Cells(2, 7).Value = "Engineer"
    Me.Cells(2, 8).Value = "Drafter"
    'Row 2 column labels -- black bold on light blue with a thin box border.
    With Me.Range("A2:H2")
        .Interior.Color = RGB(218, 233, 248)
        .Font.Color = RGB(0, 0, 0)
        .Font.Bold = True
        .Borders.LineStyle = xlContinuous
        .Borders.Weight = xlThin
    End With
    Application.EnableEvents = True
End Sub

'Keep the data area formatted as Text (like Ref Documents & Deviations), so values such
'as "00E016" stay exactly as typed rather than becoming scientific notation. Applied
'proactively (on activate / open) so the cell is already Text before anything is typed.
'Also auto-fit the columns so they lengthen to their content, like the other sheets.
Public Sub PD_EnsureTextFormat()
    On Error Resume Next
    Me.Range("A" & PD_FIRST_DATA & ":H" & PD_LAST_ROW).NumberFormat = "@"
    Dim c As Long
    For c = 1 To 8
        If Not Me.Columns(c).Hidden Then Me.Columns(c).AutoFit
    Next c
End Sub

Private Sub Worksheet_SelectionChange(ByVal Target As Range)
    'Keep the custom Ctrl+Z / Ctrl+Y bindings live (see modUndo.PZ_BindKeys) and capture the
    'initial committed state so the first edit is undoable.
    On Error Resume Next
    modUndo.PZ_BindKeys
    If IsEmpty(mCommitted) Then mCommitted = Me.Range(PD_UNDO_RANGE).Formula
    On Error GoTo 0
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call PD_EnsureHeaders
    Dim inData As Boolean
    inData = Not (Intersect(Target, Me.Range(PD_UNDO_RANGE)) Is Nothing)
    'Record the pre-change state on the undo stack (data-area edits only).
    If inData Then Call PD_RecordChange
    If inData Then
        Application.EnableEvents = False
        Call PD_EnsureTextFormat
        Application.EnableEvents = True
        'Snapshot the committed state for the next undo.
        On Error Resume Next
        mCommitted = Me.Range(PD_UNDO_RANGE).Formula
        On Error GoTo 0
    End If
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    modUndo.PZ_BindKeys
    Call PD_EnsureHeaders
    Call PD_EnsureTextFormat
End Sub


'================= Multi-level undo / redo (mirrors ConduitIndex) =================

Private Sub PD_RecordChange()
    On Error Resume Next
    If mUndo Is Nothing Then Set mUndo = New Collection
    If mRedo Is Nothing Then Set mRedo = New Collection
    If Not IsEmpty(mCommitted) Then
        mUndo.Add mCommitted
        Do While mUndo.Count > PD_UNDO_MAX
            mUndo.Remove 1
        Loop
        Set mRedo = New Collection
    End If
    On Error GoTo 0
End Sub

'Ctrl+Z on Project Description. Public so the modUndo key shim can reach it.
Public Sub PD_DoUndo()
    On Error Resume Next
    If mUndo Is Nothing Then Exit Sub
    If mUndo.Count = 0 Then Exit Sub
    If mRedo Is Nothing Then Set mRedo = New Collection
    Dim snap As Variant
    snap = mUndo(mUndo.Count)
    mUndo.Remove mUndo.Count
    If Not IsEmpty(mCommitted) Then mRedo.Add mCommitted
    Call PD_RestoreSnapshot(snap)
End Sub

'Ctrl+Y on Project Description.
Public Sub PD_DoRedo()
    On Error Resume Next
    If mRedo Is Nothing Then Exit Sub
    If mRedo.Count = 0 Then Exit Sub
    If mUndo Is Nothing Then Set mUndo = New Collection
    Dim snap As Variant
    snap = mRedo(mRedo.Count)
    mRedo.Remove mRedo.Count
    If Not IsEmpty(mCommitted) Then mUndo.Add mCommitted
    Call PD_RestoreSnapshot(snap)
End Sub

Private Sub PD_RestoreSnapshot(ByVal snap As Variant)
    If IsEmpty(snap) Then Exit Sub
    On Error GoTo done
    Application.EnableEvents = False
    Application.ScreenUpdating = False
    Me.Range(PD_UNDO_RANGE).Formula = snap
    Call PD_EnsureTextFormat
    mCommitted = Me.Range(PD_UNDO_RANGE).Formula
done:
    Application.ScreenUpdating = True
    Application.EnableEvents = True
End Sub


