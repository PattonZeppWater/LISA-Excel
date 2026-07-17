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

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call PD_EnsureHeaders
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    Call PD_EnsureHeaders
End Sub


