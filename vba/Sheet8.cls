VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet8"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

'Ref Documents self-heal -- mirrors ConduitIndex (Sheet1).  Restores the column-
'label header row if cleared and keeps the data area tidy (grow-by-one, hide the
'empty rows).  The TOP TWO rows are headers and are always preserved: row 1 = title
'band, row 2 = column labels.  Data lives in rows 3..RD_LAST_ROW.

Private Const RD_HEADER_ROW As Long = 2
Private Const RD_FIRST_DATA As Long = 3
Private Const RD_LAST_ROW As Long = 100

Public Sub RD_EnsureHeaders()
    On Error Resume Next
    'Only restore the row-2 labels if they've been cleared; row 1 is never touched.
    If Len(Trim$(CStr(Me.Cells(RD_HEADER_ROW, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(RD_HEADER_ROW, 5).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    Me.Cells(RD_HEADER_ROW, 1).Value = "Ref Document"
    Me.Cells(RD_HEADER_ROW, 2).Value = "DWG#"
    Me.Cells(RD_HEADER_ROW, 3).Value = "Description"
    Me.Cells(RD_HEADER_ROW, 4).Value = "Manufacturer"
    Me.Cells(RD_HEADER_ROW, 5).Value = "Deviations Notes #"
    Me.Cells(RD_HEADER_ROW, 6).Value = "Deviations Notes"
    Application.EnableEvents = True
End Sub

Private Function RD_LastDataRow() As Long
    Dim r As Long, c As Long, last As Long
    last = RD_HEADER_ROW
    For r = RD_FIRST_DATA To RD_LAST_ROW
        For c = 1 To 6
            If Trim$(CStr(Me.Cells(r, c).Value)) <> "" Then
                last = r
                Exit For
            End If
        Next c
    Next r
    RD_LastDataRow = last
End Function

Public Sub RD_RefreshRows()
    'Show the ENTIRE data area -- NO row hiding (same reason as ConduitIndex: hidden rows
    'made Enter/arrow keys jump past them into a far dead row). Every row stays visible so
    'navigation into the blank rows below the data is normal.
    Dim c As Long
    On Error Resume Next
    Me.Rows(RD_FIRST_DATA & ":" & RD_LAST_ROW).EntireRow.Hidden = False
    'Keep the data area Text so entries never auto-convert.
    Me.Range("A" & RD_FIRST_DATA & ":H" & RD_LAST_ROW).NumberFormat = "@"
    For c = 1 To 6
        If Not Me.Columns(c).Hidden Then Me.Columns(c).AutoFit
    Next c
    On Error GoTo 0
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call RD_EnsureHeaders
    If Not Intersect(Target, Me.Range("A" & RD_FIRST_DATA & ":H" & RD_LAST_ROW)) Is Nothing Then
        Application.EnableEvents = False
        Call RD_RefreshRows
        Application.EnableEvents = True
    End If
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    Call RD_EnsureHeaders
    Call RD_RefreshRows
End Sub




