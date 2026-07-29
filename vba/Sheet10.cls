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

'VERTICAL Project Description form:
'   Row 1      A1:B1  "PROJECT DESCRIPTION" band (white bold on dark blue)
'   Rows 2..9  col A  field labels (Owner..Drafter), col B  the value for each field
'The label ORDER top-to-bottom maps POSITIONALLY to the title-block lines LINE1..LINE8, so it
'must stay Owner, Job Title, Content, Proj No., Status, Date, Engineer, Drafter. Values are
'kept as Text (like Ref Documents & Deviations) so "00E016" stays verbatim, not scientific.
Private Const PD_FIRST_FIELD As Long = 2       'first field row (Owner)
Private Const PD_LAST_FIELD As Long = 9        'last field row (Drafter)
Private Const PD_LABEL_COL As Long = 1         'column A = labels
Private Const PD_VALUE_COL As Long = 2         'column B = values
Private Const PD_VALUE_MINWIDTH As Double = 40 'generous floor; column still auto-lengthens
Private Const PD_UNDO_RANGE As String = "B2:B9"
Private Const PD_UNDO_MAX As Long = 50
Private mCommitted As Variant
Private mUndo As Collection
Private mRedo As Collection

Public Sub PD_EnsureHeaders()
    On Error Resume Next
    'Already the vertical form? (title band + first & last labels in column A.)
    If Len(Trim$(CStr(Me.Cells(1, 1).Value))) > 0 _
       And LCase$(Trim$(CStr(Me.Cells(PD_FIRST_FIELD, PD_LABEL_COL).Value))) = "owner" _
       And LCase$(Trim$(CStr(Me.Cells(PD_LAST_FIELD, PD_LABEL_COL).Value))) = "drafter" Then Exit Sub
    Application.EnableEvents = False

    'Rebuild from a clean slate -- clears a wiped form AND any leftover HORIZONTAL layout
    'remnants (old labels across row 2 / values on row 3). Only reached when the form is
    'missing/broken; a populated vertical form passes the check above and is never cleared.
    Me.Range("A1:H1").UnMerge
    Me.Range("A1:H9").Clear

    'Row 1 title band across the two form columns -- white bold on dark blue, centered.
    With Me.Range("A1:B1")
        .Merge
        .Interior.Color = RGB(21, 58, 138)
        .Font.Color = RGB(255, 255, 255)
        .Font.Bold = True
        .Font.Size = 16
        .HorizontalAlignment = xlCenter
        .VerticalAlignment = xlCenter
    End With
    Me.Cells(1, 1).Value = "PROJECT DESCRIPTION"

    'Field labels down column A (order = title-block LINE order -- do NOT reorder).
    Me.Cells(2, PD_LABEL_COL).Value = "Owner"
    Me.Cells(3, PD_LABEL_COL).Value = "Job Title"
    Me.Cells(4, PD_LABEL_COL).Value = "Content"
    Me.Cells(5, PD_LABEL_COL).Value = "Proj No."
    Me.Cells(6, PD_LABEL_COL).Value = "Status"
    Me.Cells(7, PD_LABEL_COL).Value = "Date"
    Me.Cells(8, PD_LABEL_COL).Value = "Engineer"
    Me.Cells(9, PD_LABEL_COL).Value = "Drafter"

    'Labels -- black bold on light blue, boxed.
    With Me.Range(Me.Cells(PD_FIRST_FIELD, PD_LABEL_COL), Me.Cells(PD_LAST_FIELD, PD_LABEL_COL))
        .Interior.Color = RGB(218, 233, 248)
        .Font.Color = RGB(0, 0, 0)
        .Font.Bold = True
        .Borders.LineStyle = xlContinuous
        .Borders.Weight = xlThin
        .HorizontalAlignment = xlCenter
        .VerticalAlignment = xlCenter
    End With
    'Value cells -- plain, boxed.
    With Me.Range(Me.Cells(PD_FIRST_FIELD, PD_VALUE_COL), Me.Cells(PD_LAST_FIELD, PD_VALUE_COL))
        .Font.Bold = False
        .Borders.LineStyle = xlContinuous
        .Borders.Weight = xlThin
    End With
    Me.Columns(PD_LABEL_COL).ColumnWidth = 14
    Call PD_EnsureTextFormat
    Application.EnableEvents = True
End Sub

'Keep the value cells as Text so values such as "00E016" stay exactly as typed. The value
'column gets a GENEROUS FIXED FLOOR that can still auto-lengthen: AutoFit first (a long value
'widens it), then clamp back up to the floor so an empty/short form never collapses to a
'squished width. Applied proactively (activate / open / change).
Public Sub PD_EnsureTextFormat()
    On Error Resume Next
    Me.Range(Me.Cells(PD_FIRST_FIELD, PD_VALUE_COL), Me.Cells(PD_LAST_FIELD, PD_VALUE_COL)).NumberFormat = "@"
    Me.Columns(PD_VALUE_COL).AutoFit
    If Me.Columns(PD_VALUE_COL).ColumnWidth < PD_VALUE_MINWIDTH Then
        Me.Columns(PD_VALUE_COL).ColumnWidth = PD_VALUE_MINWIDTH
    End If
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
    'Record the pre-change state on the undo stack (value-cell edits only).
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
