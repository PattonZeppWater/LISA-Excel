VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet9"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

Public Sub SL_EnsureHeaders()
    On Error Resume Next
    If Len(Trim$(CStr(Me.Cells(1, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(1, 54).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    Me.Cells(1, 1).Value = "CAT6_1_L"
    Me.Cells(1, 2).Value = "CAT6_1_R"
    Me.Cells(1, 3).Value = "CONTROL_1_L"
    Me.Cells(1, 4).Value = "CONTROL_1_R"
    Me.Cells(1, 5).Value = "CONTROL_2_L"
    Me.Cells(1, 6).Value = "CONTROL_2_R"
    Me.Cells(1, 7).Value = "CONTROL_3_L"
    Me.Cells(1, 8).Value = "CONTROL_3_R"
    Me.Cells(1, 9).Value = "CONTROL_4_L"
    Me.Cells(1, 10).Value = "CONTROL_4_R"
    Me.Cells(1, 11).Value = "CONTROL_6_L"
    Me.Cells(1, 12).Value = "CONTROL_6_R"
    Me.Cells(1, 13).Value = "CONTROL_8_L"
    Me.Cells(1, 14).Value = "CONTROL_8_R"
    Me.Cells(1, 15).Value = "FIBER_1_L"
    Me.Cells(1, 16).Value = "FIBER_1_R"
    Me.Cells(1, 17).Value = "MFG_CABLE_1_L"
    Me.Cells(1, 18).Value = "MFG_CABLE_1_R"
    Me.Cells(1, 19).Value = "MFG_CABLE_2_L"
    Me.Cells(1, 20).Value = "MFG_CABLE_2_R"
    Me.Cells(1, 21).Value = "MFG_CABLE_3_L"
    Me.Cells(1, 22).Value = "MFG_CABLE_3_R"
    Me.Cells(1, 23).Value = "MFG_CABLE_4_L"
    Me.Cells(1, 24).Value = "MFG_CABLE_4_R"
    Me.Cells(1, 25).Value = "MFG_CABLE_6_L"
    Me.Cells(1, 26).Value = "MFG_CABLE_6_R"
    Me.Cells(1, 27).Value = "MFG_CABLE_8_L"
    Me.Cells(1, 28).Value = "MFG_CABLE_8_R"
    Me.Cells(1, 29).Value = "POWER_1_L"
    Me.Cells(1, 30).Value = "POWER_1_R"
    Me.Cells(1, 31).Value = "POWER_2_L"
    Me.Cells(1, 32).Value = "POWER_2_R"
    Me.Cells(1, 33).Value = "POWER_3_L"
    Me.Cells(1, 34).Value = "POWER_3_R"
    Me.Cells(1, 35).Value = "POWER_4_L"
    Me.Cells(1, 36).Value = "POWER_4_R"
    Me.Cells(1, 37).Value = "POWER_6_L"
    Me.Cells(1, 38).Value = "POWER_6_R"
    Me.Cells(1, 39).Value = "POWER_8_L"
    Me.Cells(1, 40).Value = "POWER_8_R"
    Me.Cells(1, 41).Value = "PULL_ROPE_1_L"
    Me.Cells(1, 42).Value = "PULL_ROPE_1_R"
    Me.Cells(1, 43).Value = "TSP_1_L"
    Me.Cells(1, 44).Value = "TSP_1_R"
    Me.Cells(1, 45).Value = "TSP_2_L"
    Me.Cells(1, 46).Value = "TSP_2_R"
    Me.Cells(1, 47).Value = "TSP_3_L"
    Me.Cells(1, 48).Value = "TSP_3_R"
    Me.Cells(1, 49).Value = "TSP_4_L"
    Me.Cells(1, 50).Value = "TSP_4_R"
    Me.Cells(1, 51).Value = "TSP_6_L"
    Me.Cells(1, 52).Value = "TSP_6_R"
    Me.Cells(1, 53).Value = "TSP_8_L"
    Me.Cells(1, 54).Value = "TSP_8_R"
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
    On Error GoTo SafeExit
    Call SL_EnsureHeaders
SafeExit:
    Application.EnableEvents = True
End Sub

Private Sub Worksheet_Activate()
    On Error Resume Next
    Call SL_EnsureHeaders
End Sub


