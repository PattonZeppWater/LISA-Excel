VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet3"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

Public Sub PL_EnsureHeaders()
    On Error Resume Next
    If Len(Trim$(CStr(Me.Cells(1, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(1, 11).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    Me.Cells(1, 1).Value = "HeadUnit_L_W1"
    Me.Cells(1, 2).Value = "HeadUnit_R_W1"
    Me.Cells(1, 3).Value = "HeadUnit_L_W2"
    Me.Cells(1, 4).Value = "HeadUnit_R_W2"
    Me.Cells(1, 5).Value = "HeadUnit_L_W3"
    Me.Cells(1, 6).Value = "HeadUnit_R_W3"
    Me.Cells(1, 7).Value = "HeadUnit_L_W4"
    Me.Cells(1, 8).Value = "HeadUnit_R_W4"
    Me.Cells(1, 10).Value = "FillType"
    Me.Cells(1, 11).Value = "WireColor"
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call PL_EnsureHeaders
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    Call PL_EnsureHeaders
End Sub


