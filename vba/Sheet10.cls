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

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call PD_EnsureHeaders
    If Not Intersect(Target, Me.Range("A" & PD_FIRST_DATA & ":H" & PD_LAST_ROW)) Is Nothing Then
        Application.EnableEvents = False
        Call PD_EnsureTextFormat
        Application.EnableEvents = True
    End If
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    Call PD_EnsureHeaders
    Call PD_EnsureTextFormat
End Sub


