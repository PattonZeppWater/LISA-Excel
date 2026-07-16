VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet7"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

Private Const BL_ATT_MAX As Long = 200

Public Sub BL_EnsureHeaders()
    On Error Resume Next
    If Len(Trim$(CStr(Me.Cells(1, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(1, 13).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(1, 13 + BL_ATT_MAX).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    Me.Cells(1, 1).Value = "Block_Name"
    Me.Cells(1, 2).Value = "VisibilityState"
    Me.Cells(1, 3).Value = "Block_Category"
    Me.Cells(1, 4).Value = "Block_Manufacturer"
    Me.Cells(1, 5).Value = "Block_PartNumber"
    Me.Cells(1, 6).Value = "Picture"
    Me.Cells(1, 7).Value = "COMPLETE"
    Me.Cells(1, 8).Value = "Dimension_Width"
    Me.Cells(1, 9).Value = "Dimension_Length"
    Me.Cells(1, 10).Value = "Insertion_ShiftX"
    Me.Cells(1, 11).Value = "Insertion_ShiftY"
    Me.Cells(1, 12).Value = "Handle"
    Me.Cells(1, 13).Value = "Att_Count"
    Dim i As Long
    For i = 1 To BL_ATT_MAX
        Me.Cells(1, 13 + i).Value = "Att_" & Format(i, "000") & "_Name"
    Next i
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call BL_EnsureHeaders
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    Call BL_EnsureHeaders
End Sub


