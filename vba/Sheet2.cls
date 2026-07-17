VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet2"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

    Private Const PZ_MARKER_COL As String = "BI"
Private Const PZ_AUTO_MARKER As String = "PZ_AUTO_INSTRUMENT_ROW"
Private Const SSYM As String = "E"   'S Symbol column (now before Color 1-4)

'Grow-by-one row pool: data rows 3..PZ_LAST_ROW (100 rows). Only filled rows
'plus one blank stay visible; the rest are hidden each rebuild.
    Private Const PZ_LAST_ROW As Long = 500
    Private Const PZ_CHUNK As Long = 25   'ready-row buffer / auto-grow chunk
Private mProcTo As Long
Private mMaxProc As Long   'session high-water of processed rows; bounds the pool reformat for speed
Private mHdr As Object
Private mBlockAttrs As Object   'cache: BlockName(normalized) -> dict of its attribute tags (from BlockIndex)

    Private Const CLR_RANGE As String = "E3:E500"
Private Const CLR_DISP_SHEET As String = "Document Manager"
Private Const CLR_DISP_START As String = "XFC3"
Private Const CLR_DISP_NAME As String = "ColorDropdownDisplay"
Private Const CLR_SEP As String = ", "
Private mClrPrevAddr As String
Private mClrPrevVal As String

'--- Per-term "Hide from Generation" toggle ----------------------------------
'Hidden CSV store ("S1,D3" �) lives at BK(63), OUTSIDE the A3:BJ watch range so
'writing it never re-triggers Worksheet_Change. Term cells keep their real value;
'a hidden term is flagged (amber + comment) and its dropdown shows a check mark.
    Private Const PZ_HIDDENTERMS_COL As Long = 65
Private Const PZ_TERM_OPT As String = "Hide from Generation"
Private Const PZ_TERM_NOTE As String = "This Term is Hidden From Generation"
Private Const PZ_TAG_NOTE As String = "This Tag is Hidden From Generation"
Private mTermPrevAddr As String
Private mTermPrevVal As String

'Fill for "not applicable" cells: medium grey. Font matches the fill so any
'residual text stays hidden.
Private Const PZ_GREY_R As Long = 128
Private Const PZ_GREY_G As Long = 128
Private Const PZ_GREY_B As Long = 128

'--- Multi-level custom undo / redo ------------------------------------------
'Running VBA on Worksheet_Change wipes Excel's native undo stack, so Ctrl+Z is
'dead. We keep our own undo/redo stacks of data-range snapshots and bind Ctrl+Z
'/ Ctrl+Y (via Application.OnKey in modUndo) to walk them. Each snapshot is the
'.Formula of the data range at a committed state.
    Private Const PZ_UNDO_RANGE As String = "A3:BM200"
    Private Const PZ_UNDO_MAX As Long = 20   'how many steps back you can go
Private mCommitted As Variant            'state as of the last completed rebuild
Private mUndo As Collection              'undo stack (older states, oldest first)
Private mRedo As Collection              'redo stack

Public Sub PZ_EnsureTopHeaders()
    '--- task 3: self-heal the row-1 section banners + the Max Wire Label Length box.
    On Error Resume Next
    Dim okBanners As Boolean, okBox As Boolean
    okBanners = (CStr(Me.Cells(1, 1).Value) = "WIRE INFO") _
        And (CStr(Me.Cells(1, 5).Value) = "SOURCE INFO") _
        And (CStr(Me.Cells(1, 29).Value) = "DESTINATION INFO") _
        And (CStr(Me.Cells(1, 53).Value) = "WIRE LABEL")
    okBox = (InStr(1, CStr(Me.Cells(1, 66).Value), "Max Wire Label Length", vbTextCompare) > 0)
    If okBanners And okBox Then Exit Sub
    Application.EnableEvents = False
    Application.DisplayAlerts = False
    If Not okBanners Then
        Me.Range("A1:BD1").UnMerge
        Me.Range("A1:BD1").ClearContents
        Me.Rows(1).RowHeight = 26
        PZ_MakeBanner "A1:D1", "WIRE INFO", RGB(21, 84, 184)
        PZ_MakeBanner "E1:AB1", "SOURCE INFO", RGB(46, 106, 160)
        PZ_MakeBanner "AC1:AZ1", "DESTINATION INFO", RGB(81, 126, 179)
        PZ_MakeBanner "BA1:BD1", "WIRE LABEL", RGB(54, 74, 99)
    End If
    If Not okBox Then
        Me.Columns("BN:BO").EntireColumn.Hidden = False
        With Me.Cells(1, 66)
            .Value = "Max Wire Label Length:"
            .Font.Bold = True
            .Font.ColorIndex = xlAutomatic
            .Font.Size = 11
            .Interior.Color = RGB(238, 242, 246)
            .HorizontalAlignment = xlRight
        End With
        With Me.Cells(1, 67)
            If Len(Trim$(CStr(.Value))) = 0 Or Not IsNumeric(.Value) Then .Value = 50
            .Font.Bold = True
            .Font.ColorIndex = xlAutomatic
            .Font.Size = 11
            .Interior.Color = RGB(255, 255, 0)
            .HorizontalAlignment = xlCenter
        End With
        With Me.Range(Me.Cells(1, 66), Me.Cells(1, 67))
            .Borders.LineStyle = xlContinuous
            .Borders.Weight = xlThin
        End With
        Me.Columns(66).ColumnWidth = 24
        Me.Columns(67).ColumnWidth = 8
    End If
    Me.Parent.Names.Add Name:="WireLabelMaxLen", RefersTo:="=FillIndex!$BO$1"
    Call PZ_EnsureHeaderBorders   'restore the header box border after banners are (re)built
    Application.DisplayAlerts = True
    Application.EnableEvents = True
End Sub

Private Sub PZ_MakeBanner(ByVal addr As String, ByVal txt As String, ByVal clr As Long)
    With Me.Range(addr)
        .ClearContents
        .MergeCells = True
        .Cells(1, 1).Value = txt
        .Interior.Color = clr
        .Font.Color = RGB(255, 255, 255)
        .Font.Bold = True
        .Font.Size = 16
        .HorizontalAlignment = xlCenter
        .VerticalAlignment = xlCenter
    End With
End Sub

'Self-heal the header box border. The two header rows (row 1 section banners + row 2 column
'labels, columns A..BD) carry a thin grid; a Ctrl+A + Delete used to trigger a rebuild that
'STRIPPED it. This restores it and is called from every rebuild / header-heal.
Private Sub PZ_EnsureHeaderBorders()
    On Error Resume Next
    With Me.Range("A1:BD2")
        .Borders(xlEdgeLeft).LineStyle = xlContinuous
        .Borders(xlEdgeLeft).Weight = xlThin
        .Borders(xlEdgeTop).LineStyle = xlContinuous
        .Borders(xlEdgeTop).Weight = xlThin
        .Borders(xlEdgeBottom).LineStyle = xlContinuous
        .Borders(xlEdgeBottom).Weight = xlThin
        .Borders(xlEdgeRight).LineStyle = xlContinuous
        .Borders(xlEdgeRight).Weight = xlThin
        .Borders(xlInsideVertical).LineStyle = xlContinuous
        .Borders(xlInsideVertical).Weight = xlThin
        .Borders(xlInsideHorizontal).LineStyle = xlContinuous
        .Borders(xlInsideHorizontal).Weight = xlThin
    End With
    On Error GoTo 0
End Sub



Public Sub PZ_HideCtrlCols()
    On Error Resume Next
    Me.Columns(23).EntireColumn.Hidden = True
    Me.Columns(30).EntireColumn.Hidden = True
    Me.Columns(31).EntireColumn.Hidden = True
    Me.Columns(33).EntireColumn.Hidden = True
    Me.Columns(34).EntireColumn.Hidden = True
    Me.Columns(47).EntireColumn.Hidden = True
    Me.Columns(57).EntireColumn.Hidden = True
    Me.Columns(58).EntireColumn.Hidden = True
    Me.Columns(59).EntireColumn.Hidden = True
    Me.Columns(60).EntireColumn.Hidden = True
    Me.Columns(61).EntireColumn.Hidden = True
    Me.Columns(62).EntireColumn.Hidden = True
    Me.Columns(63).EntireColumn.Hidden = True
    Me.Columns(64).EntireColumn.Hidden = True
    Me.Columns(65).EntireColumn.Hidden = True
End Sub














Private Sub PZ_QuickRow(ByVal r As Long)
    'Single-row reprocess mirroring PZ_RebuildRows3To11's per-row work -- used by the
    'Worksheet_Change fast path for a non-instrument structural edit (Wire Ct/Type/Symbol),
    'so it doesn't reprocess all rows. Instrument rows still take the full rebuild.
    Dim lc As Long, cc As Long, n As Long, rCont As Boolean, dsCol As Long, dsc As Range
    Call PZ_ResetRowFormatting(r)
    Call PZ_ClearNonCustomLabels(r)
    lc = 0
    If IsNumeric(Me.Cells(r, "B").Value) Then lc = CLng(Me.Cells(r, "B").Value)
    If PZ_ValidLoop(lc) Then
        Call PZ_FormatLoopCells(r, lc)
        Call PZ_SetWireLabelFormulas(r)
    End If
    Set dsc = PZ_HeaderCell(2, "D Symbol")
    dsCol = 0
    If Not dsc Is Nothing Then dsCol = dsc.Column
    If Len(Trim(CStr(Me.Cells(r, "C").Value))) > 0 And PZ_ValidLoop(lc) Then
        Call PZ_MakeHeaderVisible(r, "D Symbol")
        rCont = (CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
            Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")
        For cc = 1 To 4
            If (Not rCont) And cc <= lc Then
                Call PZ_MakeHeaderVisible(r, "Color " & cc)
            Else
                Call PZ_MakeHeaderBlackClear(r, "Color " & cc)
            End If
        Next cc
    End If
    If PZ_IsSpare(CStr(Me.Cells(r, SSYM).Value)) Then
        Call PZ_MakeHeaderVisible(r, "S Type")
        Call PZ_MakeHeaderVisible(r, "S Quantity")
        For n = 1 To 4
            Call PZ_MakeHeaderBlack(r, "S Tag" & n)
        Next n
    Else
        Call PZ_MakeHeaderBlack(r, "S Type")
        Call PZ_MakeHeaderBlack(r, "S Quantity")
    End If
    If dsCol > 0 Then
        If PZ_IsSpare(CStr(Me.Cells(r, dsCol).Value)) Then
            Call PZ_MakeHeaderVisible(r, "D Type")
            Call PZ_MakeHeaderVisible(r, "D Quantity")
            For n = 1 To 4
                Call PZ_MakeHeaderBlack(r, "D Tag" & n)
            Next n
        Else
            Call PZ_MakeHeaderBlack(r, "D Type")
            Call PZ_MakeHeaderBlack(r, "D Quantity")
        End If
    End If
    'Descriptions: open with the S/D symbol on a valid-loop (or auto-instrument) row
    Dim qHasS As Boolean, qHasD As Boolean, qValid As Boolean, qAuto As Boolean
    qHasS = Len(Trim(CStr(Me.Cells(r, SSYM).Value))) > 0
    qHasD = PZ_DSymPresent(r)
    qValid = PZ_ValidLoop(lc)
    qAuto = (CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")
    If qHasS And (qValid Or qAuto) Then
        Call PZ_MakeHeaderVisible(r, "S Description1")
        Call PZ_MakeHeaderVisible(r, "S Description2")
        Call PZ_MakeHeaderVisible(r, "S Description3")
    Else
        Call PZ_MakeHeaderBlack(r, "S Description1")
        Call PZ_MakeHeaderBlack(r, "S Description2")
        Call PZ_MakeHeaderBlack(r, "S Description3")
    End If
    If qHasD And (qValid Or qAuto) Then
        Call PZ_MakeHeaderVisible(r, "D Description1")
        Call PZ_MakeHeaderVisible(r, "D Description2")
        Call PZ_MakeHeaderVisible(r, "D Description3")
    Else
        Call PZ_MakeHeaderBlack(r, "D Description1")
        Call PZ_MakeHeaderBlack(r, "D Description2")
        Call PZ_MakeHeaderBlack(r, "D Description3")
    End If
    Call PZ_AutoWidth   'auto-size all columns after a pick so text never overlaps
End Sub
Public Sub PZ_EnsureHeaders()
    On Error Resume Next   'never abort header/hide/border self-heal
    'Headers count as intact only if key cells across the WHOLE row are present
    '(A + S Symbol + several D-side cells), so clearing the D-side alone still triggers a
    'full restore instead of being skipped because A2/S-Symbol happen to survive.
    If Len(Trim$(CStr(Me.Cells(2, 1).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 5).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 35).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 41).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 52).Value))) > 0 _
       And Len(Trim$(CStr(Me.Cells(2, 56).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    Me.Cells(2, 1).Value = "Conduit"
    Me.Cells(2, 2).Value = "Wire Ct"
    Me.Cells(2, 3).Value = "Type"
    Me.Cells(2, 4).Value = "Wire Gauge"
    Me.Cells(2, 5).Value = "S Symbol"
    Me.Cells(2, 6).Value = "Color 1"
    Me.Cells(2, 7).Value = "Color 2"
    Me.Cells(2, 8).Value = "Color 3"
    Me.Cells(2, 9).Value = "Color 4"
    Me.Cells(2, 10).Value = "S Tag 1 "
    Me.Cells(2, 11).Value = "S Tag 2"
    Me.Cells(2, 12).Value = "S Tag 3"
    Me.Cells(2, 13).Value = "S Tag 4"
    Me.Cells(2, 14).Value = "S ISATag_Loop#"
    Me.Cells(2, 15).Value = "S ISATag_ElementID"
    Me.Cells(2, 16).Value = "S ISATag_Element#"
    Me.Cells(2, 17).Value = "S ISATag_FunctionID"
    Me.Cells(2, 18).Value = "S Term 1"
    Me.Cells(2, 19).Value = "S Term 2"
    Me.Cells(2, 20).Value = "S Term 3"
    Me.Cells(2, 21).Value = "S Term 4"
    Me.Cells(2, 22).Value = "S Rating"
    Me.Cells(2, 23).Value = "S Fuse Rating"
    Me.Cells(2, 24).Value = "S Description 1"
    Me.Cells(2, 25).Value = "S Description 2"
    Me.Cells(2, 26).Value = "S Description 3"
    Me.Cells(2, 27).Value = "S Type"
    Me.Cells(2, 28).Value = "S Quantity"
    Me.Cells(2, 29).Value = "D Symbol"
    Me.Cells(2, 32).Value = "D Tag 1 "
    Me.Cells(2, 35).Value = "D Tag 2"
    Me.Cells(2, 36).Value = "D Tag 3"
    Me.Cells(2, 37).Value = "D Tag 4"
    Me.Cells(2, 38).Value = "D ISATag_Loop#"
    Me.Cells(2, 39).Value = "D ISATag_ElementID"
    Me.Cells(2, 40).Value = "D ISATag_Element#"
    Me.Cells(2, 41).Value = "D ISATag_FunctionID"
    Me.Cells(2, 42).Value = "D Term 1"
    Me.Cells(2, 43).Value = "D Term 2"
    Me.Cells(2, 44).Value = "D Term 3"
    Me.Cells(2, 45).Value = "D Term 4"
    Me.Cells(2, 46).Value = "D Rating"
    Me.Cells(2, 47).Value = "D Fuse Rating"
    Me.Cells(2, 48).Value = "D Description 1"
    Me.Cells(2, 49).Value = "D Description 2"
    Me.Cells(2, 50).Value = "D Description 3"
    Me.Cells(2, 51).Value = "D Type"
    Me.Cells(2, 52).Value = "D Quantity"
    Me.Cells(2, 53).Value = "Wire Label1"
    Me.Cells(2, 54).Value = "Wire Label 2"
    Me.Cells(2, 55).Value = "Wire Label 3"
    Me.Cells(2, 56).Value = "Wire Label 4"
    Me.Cells(2, 57).Value = "WL1_Mode"
    Me.Cells(2, 58).Value = "WL2_Mode"
    Me.Cells(2, 59).Value = "WL3_Mode"
    Me.Cells(2, 60).Value = "WL4_Mode"
    Me.Cells(2, 65).Value = "Hidden Terms"
    Application.EnableEvents = True
    Call PZ_EnsureHeaderBorders   'headers keep their thin box border (was being stripped)
End Sub

Private Sub PZ_MakeRowFillable(ByVal rowNum As Long)
    'Empty row -> leave every loop cell OPEN (white) so it reads as a ready fillable row,
    'instead of the grey blackout. Mirror of PZ_BlackOutLoopCells.
    Dim n As Long
    'Reset the S Symbol cell too. A former 4-term instrument continuation row leaves its
    'S Symbol greyed (sync grey); if the whole block is cleared at once its marker is lost,
    'so the main loop's wipe is skipped and only this runs -- without this line the S Symbol
    'keeps a grey patch. (Mirrors PZ_ResetRowFormatting.)
    Call PZ_MakeHeaderVisible(rowNum, "S Symbol")
    Call PZ_MakeHeaderVisible(rowNum, "D Symbol")
    Call PZ_MakeHeaderVisible(rowNum, "S Description1")
    Call PZ_MakeHeaderVisible(rowNum, "S Description2")
    Call PZ_MakeHeaderVisible(rowNum, "S Description3")
    Call PZ_MakeHeaderVisible(rowNum, "D Description1")
    Call PZ_MakeHeaderVisible(rowNum, "D Description2")
    Call PZ_MakeHeaderVisible(rowNum, "D Description3")
    Call PZ_MakeHeaderBlack(rowNum, "S Rating")
    Call PZ_MakeHeaderBlack(rowNum, "D Rating")
    Call PZ_MakeHeaderBlack(rowNum, "S Type")
    Call PZ_MakeHeaderBlack(rowNum, "S Quantity")
    Call PZ_MakeHeaderBlack(rowNum, "D Type")
    Call PZ_MakeHeaderBlack(rowNum, "D Quantity")
    Call PZ_MakeHeaderVisible(rowNum, "S ISATag_Loop#")
    Call PZ_MakeHeaderVisible(rowNum, "S ISATag_ElementID")
    Call PZ_MakeHeaderVisible(rowNum, "S ISATag_Element#")
    Call PZ_MakeHeaderVisible(rowNum, "S ISATag_FunctionID")
    Call PZ_MakeHeaderVisible(rowNum, "D ISATag_Loop#")
    Call PZ_MakeHeaderVisible(rowNum, "D ISATag_ElementID")
    Call PZ_MakeHeaderVisible(rowNum, "D ISATag_Element#")
    Call PZ_MakeHeaderVisible(rowNum, "D ISATag_FunctionID")
    For n = 1 To 4
        Call PZ_MakeHeaderVisible(rowNum, "Color " & n)
        Call PZ_MakeHeaderVisible(rowNum, "S Tag" & n)
        Call PZ_MakeHeaderVisible(rowNum, "S Term" & n)
        Call PZ_MakeHeaderVisible(rowNum, "D Tag" & n)
        Call PZ_MakeHeaderVisible(rowNum, "D Term" & n)
        Call PZ_MakeHeaderVisible(rowNum, "Wire Label" & n)
    Next n
    'MakeRowFillable border heal: restore the grid a cut may have stripped
        With Me.Range("A" & rowNum & ":BI" & rowNum)
        .Borders.LineStyle = xlContinuous
        .Borders.Weight = xlThin
        .Borders.Color = 0
    End With
End Sub

Public Sub PZ_ForceRebuild()
    Application.EnableEvents = False
    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual
    On Error GoTo done
    PZ_RebuildRows3To11
done:
    Application.Calculation = xlCalculationAutomatic
    Application.ScreenUpdating = True
    Application.EnableEvents = True
End Sub


Private Sub Worksheet_Change(ByVal Target As Range)
    PZ_EnsureHeaders
    PZ_EnsureTopHeaders

    Dim watchRange As Range
    Dim oldCalcMode As XlCalculation
    Dim wlmN As Long

    Set watchRange = Me.Range("A3:BL" & PZ_LAST_ROW)

    If Intersect(Target, watchRange) Is Nothing Then Exit Sub

    'Guard the WHOLE handler so any failure (the single-cell handlers below OR the
    'rebuild) still reaches SafeExit and re-enables events. Without this, a paste or
    'a handler error could leave EnableEvents off and the sheet would stop
    'rebuilding / growing new rows entirely until Excel is restarted.
    oldCalcMode = Application.Calculation
    On Error GoTo SafeExit

    If Target.CountLarge = 1 Then
        wlmN = PZ_WireLabelColIndex(Target.Column)
        If wlmN > 0 And Target.Row >= 3 And Target.Row <= PZ_LAST_ROW Then
            Application.EnableEvents = False
            PZ_HandleWireLabelPick Target.Row, wlmN, Target
            Application.EnableEvents = True
            Exit Sub
        End If
    End If

    'Per-term hide toggle is now a RIGHT-CLICK (Worksheet_BeforeRightClick), not a typed /
    'picked value -- see that handler. (The old dropdown's single-item list let Excel 365
    'auto-complete a typed letter like f/g/h straight into "Hide from Generation".)

    'FAST PATH: single-cell edit in a NON-structural column (not Symbol/Wire Ct/Type)
    'changes no formatting/grouping; wire labels are auto-updating formulas -> skip the
    'heavy full-sheet rebuild for speed.
    If Target.CountLarge = 1 And Target.Row >= 3 And Target.Row <= PZ_LAST_ROW Then
        Dim colN As Long, sSymC As Long, dSymC As Long
        colN = Target.Column
        On Error Resume Next
        sSymC = PZ_HeaderCell(2, "S Symbol").Column
        dSymC = PZ_HeaderCell(2, "D Symbol").Column
        On Error GoTo SafeExit
        If colN <> 2 And colN <> 3 And colN <> sSymC And colN <> dSymC Then
            Application.EnableEvents = False
            Application.ScreenUpdating = False
            Application.Calculation = xlCalculationManual
            Call PZ_RecordChange
            Call PZ_SetWireLabelFormulas(Target.Row)
            mCommitted = Me.Range(PZ_UNDO_RANGE).Formula
            'Re-fit the edited column + the wire-label columns (their text length may have
            'changed) so a long value never overflows past the hidden columns as stray
            '"background" text. Recalc the wire-label cells first (we are in manual calc).
            On Error Resume Next
            If Not Me.Columns(colN).Hidden Then Me.Columns(colN).AutoFit
            Call PZ_FitWireLabels
            On Error GoTo SafeExit
            GoTo SafeExit
        Else
            'STRUCTURAL single-cell edit (Wire Ct/Type/Symbol): if this row isn't an
            'instrument or a continuation anchor, reprocess only THIS row (fast).
            Dim rr As Long
            rr = Target.Row
            If InStr(1, CStr(Me.Cells(rr, sSymC).Value), "Inst", vbTextCompare) = 0 _
               And InStr(1, CStr(Me.Cells(rr, dSymC).Value), "Inst", vbTextCompare) = 0 _
               And CStr(Me.Cells(rr, PZ_MARKER_COL).Value) <> PZ_AUTO_MARKER _
               And CStr(Me.Cells(rr + 1, PZ_MARKER_COL).Value) <> PZ_AUTO_MARKER _
               And CStr(Me.Cells(rr + 1, PZ_MARKER_COL).Value) <> "PZ_AUTO_ANALOG_ROW" Then
                Application.EnableEvents = False
                Application.ScreenUpdating = False
                Application.Calculation = xlCalculationManual
                Call PZ_RecordChange
                Call PZ_QuickRow(rr)
                Call PZ_ApplyRowVisibility
                mCommitted = Me.Range(PZ_UNDO_RANGE).Formula
                GoTo SafeExit
            End If
        End If
    End If
    Application.EnableEvents = False
    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual

    'Record the pre-change committed state on the undo stack.
    Call PZ_RecordChange

    'A multi-cell change is almost always a paste / fill. Strip the hidden control
    'columns it carried over (auto-row marker, hidden-terms store, wire-label modes)
    'so the rebuild regenerates them cleanly from the pasted data instead of
    'inheriting stale state from the copied rows.
    If Target.CountLarge > 1 Then Call PZ_SanitizePastedRows(Target)

    Call PZ_RebuildRows3To11

SafeExit:
    Application.Calculation = xlCalculationAutomatic   'always return to AUTO so rewritten wire-label formulas re-evaluate (no F9)
    Application.ScreenUpdating = True
    Application.EnableEvents = True

End Sub


Private Sub Worksheet_SelectionChange(ByVal Target As Range)
    'One-time: capture the initial committed state so the first edit is undoable too.
    If IsEmpty(mCommitted) Then
        On Error Resume Next
        mCommitted = Me.Range(PZ_UNDO_RANGE).Formula
        On Error GoTo 0
    End If

    'Capture a term cell's real value on selection so the hide/unhide toggle can
    'restore it after the dropdown pick overwrites the cell with the option text.
    mTermPrevAddr = ""
    mTermPrevVal = ""
    If Target Is Nothing Then Exit Sub
    If Target.CountLarge <> 1 Then Exit Sub
    If Target.Row < 3 Or Target.Row > PZ_LAST_ROW Then Exit Sub
    If Len(PZ_TermToken(Target.Column)) > 0 Then
        mTermPrevAddr = Target.Address
        mTermPrevVal = CStr(Target.Value)
    End If
End Sub


'Right-click a Term or Tag cell to toggle its "Hide from Generation" flag (matches the
'header hover-notes: "Right-click ... for the toggle"). This replaced the single-item
'dropdown, whose validation list let Excel 365 auto-complete typed letters (f/g/h all
'appear in "Hide from Generation") into the hide option. Term/Tag cells are now free text.
Private Sub Worksheet_BeforeRightClick(ByVal Target As Range, Cancel As Boolean)
    On Error GoTo done
    If Target Is Nothing Then Exit Sub
    If Target.CountLarge <> 1 Then Exit Sub
    If Target.Row < 3 Or Target.Row > PZ_LAST_ROW Then Exit Sub
    Dim tok As String
    tok = PZ_TermToken(Target.Column)
    If Len(tok) = 0 Then Exit Sub
    'Only an OPEN cell (white/fillable, or amber/already-hidden) can be toggled; a greyed
    '"not applicable" cell keeps its normal right-click menu.
    If Not PZ_CellIsOpen(Target) Then Exit Sub
    Cancel = True
    Application.EnableEvents = False
    Call PZ_ToggleTermHide(Target, tok)
done:
    Application.EnableEvents = True   'always re-enable, even if the toggle errored
End Sub

'A term/tag cell is "open" when white (fillable) or amber (already flagged hidden); a
'greyed "not applicable" cell is closed.
Private Function PZ_CellIsOpen(ByVal c As Range) As Boolean
    On Error Resume Next
    If c.Interior.Pattern = xlNone Then PZ_CellIsOpen = True: Exit Function
    If c.Interior.Color = RGB(255, 235, 156) Then PZ_CellIsOpen = True
End Function

'Flip a term/tag's hidden state: keep its real value, flip the CSV token, repaint. The
'value is never overwritten (unlike the old dropdown), so no undo/restore dance is needed.
Private Sub PZ_ToggleTermHide(ByVal c As Range, ByVal tok As String)
    Dim hcell As Range, csv As String, nowHidden As Boolean
    Set hcell = Me.Cells(c.Row, PZ_HIDDENTERMS_COL)
    csv = CStr(hcell.Value)
    If PZ_CsvHas(csv, tok) Then
        hcell.Value = PZ_CsvRemove(csv, tok)
        nowHidden = False
    Else
        hcell.Value = PZ_CsvAdd(csv, tok)
        nowHidden = True
    End If
    Call PZ_ApplyTermHideVisual(c, nowHidden)
End Sub


'Push the pre-change committed state onto the undo stack; a fresh edit clears redo.
Private Sub PZ_RecordChange()
    On Error Resume Next
    If mUndo Is Nothing Then Set mUndo = New Collection
    If mRedo Is Nothing Then Set mRedo = New Collection
    If Not IsEmpty(mCommitted) Then
        mUndo.Add mCommitted
        Do While mUndo.Count > PZ_UNDO_MAX
            mUndo.Remove 1                 'drop the oldest beyond the cap
        Loop
        Set mRedo = New Collection         'a new edit invalidates redo history
    End If
    On Error GoTo 0
End Sub

'Ctrl+Z: step back one state. Public so the modUndo key shim can reach it.
Public Sub PZ_DoUndo()
    On Error Resume Next
    If mUndo Is Nothing Then Exit Sub
    If mUndo.Count = 0 Then Exit Sub
    If mRedo Is Nothing Then Set mRedo = New Collection
    Dim snap As Variant
    snap = mUndo(mUndo.Count)
    mUndo.Remove mUndo.Count
    If Not IsEmpty(mCommitted) Then mRedo.Add mCommitted
    Call PZ_RestoreSnapshot(snap)
End Sub

'Ctrl+Y: step forward one state.
Public Sub PZ_DoRedo()
    On Error Resume Next
    If mRedo Is Nothing Then Exit Sub
    If mRedo.Count = 0 Then Exit Sub
    If mUndo Is Nothing Then Set mUndo = New Collection
    Dim snap As Variant
    snap = mRedo(mRedo.Count)
    mRedo.Remove mRedo.Count
    If Not IsEmpty(mCommitted) Then mUndo.Add mCommitted
    Call PZ_RestoreSnapshot(snap)
End Sub

'Restore a snapshot's formulas, then re-run the rebuild so formatting (grey cells,
'wire labels, hidden-term flags) is reconstituted. Recaptures mCommitted at the end.
Private Sub PZ_RestoreSnapshot(ByVal snap As Variant)
    Dim oldCalc As XlCalculation
    If IsEmpty(snap) Then Exit Sub
    On Error GoTo done
    oldCalc = Application.Calculation
    Application.EnableEvents = False
    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual

    Me.Range(PZ_UNDO_RANGE).Formula = snap
    Call PZ_RebuildRows3To11

done:
    Application.Calculation = xlCalculationAutomatic   'undo path: also leave AUTO so labels recompute
    Application.ScreenUpdating = True
    Application.EnableEvents = True
End Sub


Private Sub PZ_RebuildRows3To11()

    Dim r As Long
    Dim i As Long
    Dim loopCount As Long
    Dim copiesNeeded As Long
    Dim preLast As Long

    'Decide how far down to process: through the last filled row plus a little
    'headroom for instrument auto-rows. Rows beyond that get hidden, not touched.
    preLast = PZ_LastDataRow()
    If preLast < 3 Then
        mProcTo = 3
    Else
        mProcTo = preLast + PZ_CHUNK
        If mProcTo > PZ_LAST_ROW Then mProcTo = PZ_LAST_ROW
    End If

    'First remove old auto-generated instrument rows.
    Call PZ_ClearOldAutoInstrumentRows

    'Reset processed rows first.
    For r = 3 To mProcTo
        Call PZ_ResetRowFormatting(r)
        Call PZ_ClearNonCustomLabels(r)
    Next r

    r = 3

    Do While r <= mProcTo

        'Orphaned continuation row: a marked auto-row the loop actually lands on means
        'no anchor instrument generated it this pass (its anchor's Wire Ct/symbol
        'changed), so clear it. Valid continuation rows are skipped by their anchor.
        If CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
            Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW" Then

            Call PZ_WipeAutoRow(r)

        'Allow conduit-only row.
        ElseIf Trim(CStr(Me.Cells(r, "A").Value)) <> "" _
            And Trim(CStr(Me.Cells(r, "B").Value)) = "" _
            And Trim(CStr(Me.Cells(r, SSYM).Value)) = "" Then

            Call PZ_BlackOutLoopCells(r)
            Call PZ_ClearWireLabels(r)
            Call PZ_ApplyPullRopeLogic(r)

        ElseIf (Trim(CStr(Me.Cells(r, SSYM).Value)) <> "" Or PZ_DSymPresent(r)) _
            And IsNumeric(Me.Cells(r, "B").Value) Then

            loopCount = CLng(Me.Cells(r, "B").Value)

            If PZ_ValidLoop(loopCount) Then

                'Format original row
                Call PZ_FormatLoopCells(r, loopCount)
                Call PZ_ApplyPullRopeLogic(r)
                Call PZ_SetWireLabelFormulas(r)

                'Instruments generate extra rows by terminal count: 4Term->+1,
                '6Term->+2, 8Term->+3 (from either the S or the D symbol).
                copiesNeeded = PZ_RowCopies(r)

                If copiesNeeded > 0 Then

                    Dim dcol As Long, dcc As Range, sInstO As Boolean, dInstO As Boolean
                    dcol = 0
                    Set dcc = PZ_HeaderCell(2, "D Symbol")
                    If Not dcc Is Nothing Then dcol = dcc.Column
                    'Which side carries the instrument? That side is synced + greyed on
                    'the continuation rows; the OTHER side is left for the user to fill.
                    sInstO = PZ_IsInstrumentType(r)
                    dInstO = False
                    If dcol > 0 Then dInstO = (InStr(1, CStr(Me.Cells(r, dcol).Value), "Inst", vbTextCompare) > 0)
                    'A side is "synced + greyed" on continuation rows if it holds an
                    'instrument OR a 4-position TB strip (TB-TB-TB-TB) -- both span rows.
                    Dim sCopy As Boolean, dCopy As Boolean
                    sCopy = sInstO Or PZ_IsBigTB(CStr(Me.Cells(r, SSYM).Value))
                    dCopy = False
                    If dcol > 0 Then dCopy = dInstO Or PZ_IsBigTB(CStr(Me.Cells(r, dcol).Value))

                    For i = 1 To copiesNeeded

                        If r + i <= PZ_LAST_ROW Then

                            Me.Cells(r + i, PZ_MARKER_COL).Value = PZ_AUTO_MARKER

                            'Shared identity incl. Wire Ct (Conduit / Wire Ct / Type / Wire
                            'Gauge) so the continuation row's dropdowns actually resolve.
                            Me.Cells(r + i, "A").Value = Me.Cells(r, "A").Value
                            Me.Cells(r + i, "B").Value = Me.Cells(r, "B").Value
                            Me.Cells(r + i, "C").Value = Me.Cells(r, "C").Value
                            Me.Cells(r + i, "D").Value = Me.Cells(r, "D").Value
                            'Carry ONLY the instrument-side symbol down; never the user side
                            'so a manually entered S (or D) symbol on the copy is preserved.
                            If sCopy Then
                                Me.Cells(r + i, SSYM).Value = Me.Cells(r, SSYM).Value
                                If sInstO Then Call PZ_CopyInstrISA(r, r + i, "S")
                            End If
                            If dCopy And dcol > 0 Then
                                Me.Cells(r + i, dcol).Value = Me.Cells(r, dcol).Value
                                If dInstO Then Call PZ_CopyInstrISA(r, r + i, "D")
                            End If

                            Call PZ_FormatLoopCells(r + i, loopCount)
                            Call PZ_ApplyPullRopeLogic(r + i)
                            Call PZ_SetWireLabelFormulas(r + i)

                            'Grey the synced side (instrument or TB strip) on the copy;
                            'leave the OTHER side editable and ungreyed.
                            If sCopy Then
                                With Me.Cells(r + i, SSYM)
                                    .Interior.Color = RGB(191, 191, 191)
                                    .Font.Color = RGB(89, 89, 89)
                                End With
                            End If
                            If dCopy And dcol > 0 Then
                                With Me.Cells(r + i, dcol)
                                    .Interior.Color = RGB(191, 191, 191)
                                    .Font.Color = RGB(89, 89, 89)
                                End With
                            End If

                        End If

                    Next i

                    r = r + copiesNeeded

                End If

            Else
                Call PZ_BlackOutLoopCells(r)
                Call PZ_ClearWireLabels(r)
                Call PZ_ApplyPullRopeLogic(r)
            End If

        Else
            Call PZ_BlackOutLoopCells(r)
            Call PZ_ClearWireLabels(r)
            Call PZ_ApplyPullRopeLogic(r)
        End If

        r = r + 1

    Loop

    'Final cleanup for descriptions
    Call PZ_ForceDescriptionsBySymbol

    'Spare-type column visibility + early D Symbol / Color opening
    Dim dsCol As Long, dsc As Range, n As Long, lc As Long, cc As Long
    Set dsc = PZ_HeaderCell(2, "D Symbol")
    If dsc Is Nothing Then
        dsCol = 0
    Else
        dsCol = dsc.Column
    End If
    For r = 3 To mProcTo
        'D Symbol + Color columns open as soon as Type (col C) and a valid Wire Ct
        'are set, even before an S Symbol is chosen.
        lc = 0
        If IsNumeric(Me.Cells(r, "B").Value) Then lc = CLng(Me.Cells(r, "B").Value)
        If Len(Trim(CStr(Me.Cells(r, "C").Value))) > 0 And PZ_ValidLoop(lc) Then
            Call PZ_MakeHeaderVisible(r, "D Symbol")
            'Colors only on the anchor row, not on continuation rows.
            Dim rCont As Boolean
            rCont = (CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
                Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")
            For cc = 1 To 4
                If (Not rCont) And cc <= lc Then
                    Call PZ_MakeHeaderVisible(r, "Color " & cc)
                Else
                    Call PZ_MakeHeaderBlackClear(r, "Color " & cc)
                End If
            Next cc
        End If
        'S Type / S Quantity open when S Symbol is a spare; spares have no
        'tags, so black out the S Tag columns
        If PZ_IsSpare(CStr(Me.Cells(r, SSYM).Value)) Then
            Call PZ_MakeHeaderVisible(r, "S Type")
            Call PZ_MakeHeaderVisible(r, "S Quantity")
            For n = 1 To 4
                Call PZ_MakeHeaderBlack(r, "S Tag" & n)
            Next n
        Else
            Call PZ_MakeHeaderBlack(r, "S Type")
            Call PZ_MakeHeaderBlack(r, "S Quantity")
        End If
        'D Type / D Quantity open when D Symbol is a spare; spares have no
        'tags, so black out the D Tag columns
        If dsCol > 0 Then
            If PZ_IsSpare(CStr(Me.Cells(r, dsCol).Value)) Then
                Call PZ_MakeHeaderVisible(r, "D Type")
                Call PZ_MakeHeaderVisible(r, "D Quantity")
                For n = 1 To 4
                    Call PZ_MakeHeaderBlack(r, "D Tag" & n)
                Next n
            Else
                Call PZ_MakeHeaderBlack(r, "D Type")
                Call PZ_MakeHeaderBlack(r, "D Quantity")
            End If
        End If
    Next r

    'Tidy instrument-group rows (has final say over the passes above).
    Call PZ_TidyInstrumentRows

    'Self-heal the symbol dropdowns -- row inserts/moves can scramble the
    'relative references in the data-validation formulas.
    Call PZ_EnsureValidations(mProcTo)

    'Self-heal the wire-label max-length highlight -- a regular (format-carrying) paste
    'into the top of the wire-label columns overwrites the destination cells' conditional
    'formatting with whatever the source had, which shrinks the rule's sqref and leaves
    'the pasted-over rows never highlighting no matter how long their label gets.
    Call PZ_EnsureWireLabelCF

    'Normalize row-move leftovers: a row with NO Conduit but stray content
    '(orphaned Symbol etc. from a drag/cut move) is wiped to a clean fillable row.
    'Auto instrument-continuation rows (no Conduit by design) are spared.
    'How far the pool needs cleaning: the processed extent (mProcTo), but never less
    'than the furthest row processed earlier this session, so a shrink still clears old
    'grey. The FIRST rebuild of a session sweeps the whole pool once; after that only the
    'active region is touched -- this is the speed-up (was always 3..PZ_LAST_ROW).
    Dim cleanTo As Long
    If mMaxProc <= 0 Then
        cleanTo = PZ_LAST_ROW
    ElseIf mProcTo > mMaxProc Then
        cleanTo = mProcTo
    Else
        cleanTo = mMaxProc
    End If

    'One pass over the active region: wipe row-move leftovers (orphaned content with no
    'Conduit) and reformat empties back to a clean fillable row.
    'A row with a valid Wire Ct + an S or D Symbol is legitimate in-progress data (the
    'Conduit is just filled in later, or the paste that populated the row didn't happen
    'to include column A) -- the main loop above already formats it correctly, so it must
    'NOT be wiped here. Without this guard, pasting into a row without including column A
    'looked identical to a cut/drag leftover and the whole row got silently cleared.
    Dim rf As Long, rfLoopCt As Long, rfLooksValid As Boolean
    For rf = 3 To cleanTo
        rfLoopCt = 0
        If IsNumeric(Me.Cells(rf, "B").Value) Then rfLoopCt = CLng(Me.Cells(rf, "B").Value)
        rfLooksValid = PZ_ValidLoop(rfLoopCt) And (Trim$(CStr(Me.Cells(rf, SSYM).Value)) <> "" Or PZ_DSymPresent(rf))
        If Trim$(CStr(Me.Cells(rf, "A").Value)) = "" And Not PZ_RowIsEmpty(rf) And Not PZ_IsAuto(rf) And Not rfLooksValid Then
            Me.Range("A" & rf & ":BM" & rf).ClearContents
        End If
        If PZ_RowIsEmpty(rf) Then Call PZ_MakeRowFillable(rf)
    Next rf

    mMaxProc = mProcTo   'everything past mProcTo is clean now; dirty extent shrinks to it
    Call PZ_ApplyRowVisibility
    Call PZ_AutoWidth
    Call PZ_GroupInstrumentRows

    'Re-apply hidden-term flags (amber + comment + check-mark dropdown); the passes
    'above repaint term cells white, so the visuals are rebuilt from the CSV store.
    Call PZ_RestoreTermHideVisuals

    'Snapshot the now-committed state for the next single-level undo.
    On Error Resume Next
    mCommitted = Me.Range(PZ_UNDO_RANGE).Formula
    On Error GoTo 0

    Call PZ_EnsureHeaderBorders   'restore the header box border every rebuild (Ctrl+A / edit)
    PZ_HideCtrlCols
    Me.Range("A3:AZ" & PZ_LAST_ROW).NumberFormat = "@"   'keep entry cells Text (so 001 stays 001); wire-label formulas at BA+ untouched
End Sub


'For instrument rows the shielded-TB source has 3 terminals, so the 4th term is
'dropped. Continuation rows exist only to hold an extra source block, so the
'anchor keeps the group's 4 wire labels / 3 descriptions / destination instrument
'and the continuation's redundant cells are blacked out.
'Copy an instrument's four ISA-tag cells from the anchor down to a continuation
'row (same instrument -> same Loop#/ElementID/etc.) so its wire labels resolve.
Private Sub PZ_CopyInstrISA(ByVal rFrom As Long, ByVal rTo As Long, ByVal side As String)
    Dim parts As Variant, p As Variant, a As Range, b As Range
    parts = Array("ISATag_Loop#", "ISATag_ElementID", "ISATag_Element#", "ISATag_FunctionID")
    For Each p In parts
        Set a = PZ_HeaderCell(rFrom, side & " " & p)
        Set b = PZ_HeaderCell(rTo, side & " " & p)
        If Not a Is Nothing And Not b Is Nothing Then b.Value = a.Value
    Next p
End Sub

'Re-apply the S/D Symbol list validations on the active rows so a scrambled or
'missing dropdown can't persist. The relative refs ($C3/$B3) resolve per row.
Private Sub PZ_EnsureValidations(ByVal lastRow As Long)
    Dim sc As Range, dc As Range, sL As String, dL As String
    If lastRow < 3 Then lastRow = 3
    lastRow = PZ_LAST_ROW   'always heal the whole pool so Ctrl+A+Del can't leave lower rows dropdown-dead
    Set sc = PZ_HeaderCell(2, "S Symbol")
    Set dc = PZ_HeaderCell(2, "D Symbol")
    If Not sc Is Nothing Then
        sL = Split(sc.Address, "$")(1)
        Call PZ_SetSymVal(sL & "3:" & sL & lastRow, _
        "=INDIRECT(IF(OR($C3="""",$B3=""""),""POWER_1_L"",SUBSTITUTE($C3,""-"","""")&""_""&$B3&""_L""))")
    End If
    If Not dc Is Nothing Then
        dL = Split(dc.Address, "$")(1)
        Call PZ_SetSymVal(dL & "3:" & dL & lastRow, _
        "=INDIRECT(IF(OR($C3="""",$B3=""""),""POWER_1_R"",SUBSTITUTE($C3,""-"","""")&""_""&$B3&""_R""))")
    End If
    'Wire Ct + Type dropdowns -- re-applied range-wide each rebuild so rows a cut/move
    'vacated get them back (Excel strips validation off a cut source).
    Call PZ_SetSymVal("A3:A" & lastRow, "=ConduitIndex!$A$2:$A$100")   'Conduit picker (self-heal)
    Call PZ_SetSymVal("B3:B" & lastRow, "='Document Manager'!$A$2:$A$7")
    Call PZ_SetSymVal("C3:C" & lastRow, "=INDIRECT(""Type_""&IF($B3="""",""1"",$B3))")
    'Self-heal the remaining dropdowns range-wide so Ctrl+A+Del (or a cut/move) can't
    'permanently strip them: Color lists, the Wire-Label mode picker, and the per-
    'attribute "Hide from Generation" toggle -- now incl. Tag cells (#2).
    Dim vc As Variant, hh As Range, ltr As String, n3 As Long
    For n3 = 1 To 4
        Set hh = PZ_HeaderCell(2, "Color " & n3)
        If Not hh Is Nothing Then ltr = Split(hh.Address, "$")(1): Call PZ_SetSymVal(ltr & "3:" & ltr & lastRow, "=Color_" & n3)
        Set hh = PZ_HeaderCell(2, "Wire Label" & n3)
        If Not hh Is Nothing Then ltr = Split(hh.Address, "$")(1): Call PZ_SetSymVal(ltr & "3:" & ltr & lastRow, "Default,Custom,Blank")
    Next n3
    'Term / Tag / Rating cells are FREE TEXT. Clear any leftover "Hide from Generation" list
    'validation from older workbooks -- its single-item list let Excel 365 auto-complete a
    'typed letter (f/g/h all occur in the phrase) into the hide option. The hide toggle is
    'now a right-click (Worksheet_BeforeRightClick).
    For Each vc In Array("S Tag1", "S Tag2", "S Tag3", "S Tag4", "D Tag1", "D Tag2", "D Tag3", "D Tag4", _
                         "S Term1", "S Term2", "S Term3", "S Term4", "D Term1", "D Term2", "D Term3", "D Term4", _
                         "S Rating", "D Rating")
        Set hh = PZ_HeaderCell(2, CStr(vc))
        If Not hh Is Nothing Then
            ltr = Split(hh.Address, "$")(1)
            On Error Resume Next
            Me.Range(ltr & "3:" & ltr & lastRow).Validation.Delete
            On Error GoTo 0
        End If
    Next vc
End Sub

Private Sub PZ_SetSymVal(ByVal addr As String, ByVal f As String)
    On Error Resume Next
    With Me.Range(addr).Validation
        .Delete
        .Add Type:=xlValidateList, AlertStyle:=xlValidAlertStop, _
             Operator:=xlBetween, Formula1:=f
        .IgnoreBlank = True
        .InCellDropdown = True
        .ShowError = False
    End With
    On Error GoTo 0
End Sub

'Rebuild the "wire label too long" conditional format fresh over the whole wire-label
'pool (BA3:BD500), matching Excel's built-in Light-Red-Fill-Dark-Red-Text preset. A
'format-carrying paste over the top of this range can leave the destination cells with
'the SOURCE's formatting (i.e. no rule at all), which shrinks the single rule's sqref --
'deleting and re-adding it here every rebuild makes it immune to that.
Private Sub PZ_EnsureWireLabelCF()
    On Error Resume Next
    Dim rng As Range
    Set rng = Me.Range("BA3:BD" & PZ_LAST_ROW)
    rng.FormatConditions.Delete
    With rng.FormatConditions.Add(Type:=xlExpression, Formula1:="=LEN(BA3)>WireLabelMaxLen")
        .Interior.Color = RGB(255, 199, 206)
        .Font.Color = RGB(156, 0, 6)
        .StopIfTrue = True
    End With
    On Error GoTo 0
End Sub

'Set a Term cell's default value only when it is currently blank, so a value the
'user typed is never overwritten on a later rebuild.
Private Sub PZ_DefaultTerm(ByVal rowNum As Long, ByVal headerName As String, ByVal defVal As String)
    Dim c As Range
    Set c = PZ_HeaderCell(rowNum, headerName)
    If c Is Nothing Then Exit Sub
    If Len(Trim$(CStr(c.Value))) = 0 Then c.Value = defVal
End Sub

Private Sub PZ_ClearDefaultTerm(ByVal rowNum As Long, ByVal headerName As String, ByVal defVal As String)
    'Remove a terminal cell's value ONLY if it still equals the auto-default (so a
    'value the user typed by hand is never cleared).
    Dim c As Range
    Set c = PZ_HeaderCell(rowNum, headerName)
    If c Is Nothing Then Exit Sub
    If UCase$(Trim$(CStr(c.Value))) = UCase$(Trim$(defVal)) Then c.ClearContents
End Sub

Private Sub PZ_TidyInstrumentRows()
    Dim r As Long, z As Long, dcZ As Range
    Dim isCont As Boolean, sInst As Boolean, dInst As Boolean, grpInst As Boolean
    For r = 3 To mProcTo
        isCont = (CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
            Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")
        sInst = PZ_IsInstrumentType(r)
        Set dcZ = PZ_HeaderCell(r, "D Symbol")
        dInst = (Not dcZ Is Nothing) And InStr(1, CStr(dcZ.Value), "Inst", vbTextCompare) > 0
        Dim sBig As Boolean, dBig As Boolean
        sBig = PZ_IsBigTB(CStr(Me.Cells(r, SSYM).Value))
        dBig = (Not dcZ Is Nothing) And PZ_IsBigTB(CStr(dcZ.Value))
        grpInst = sInst Or dInst Or sBig Or dBig
        If Not grpInst Then GoTo NextTidy

        'Every instrument row is one pair of wires: Term 1 & 2 + Wire Label 1 & 2
        'open, Term 3 & 4 + Wire Label 3 & 4 closed (both sides).
        Call PZ_MakeHeaderVisible(r, "S Term1")
        Call PZ_MakeHeaderVisible(r, "S Term2")
        Call PZ_MakeHeaderVisible(r, "D Term1")
        Call PZ_MakeHeaderVisible(r, "D Term2")
        'Line+ (L) / Neutral- (N) auto-defaults apply ONLY to a POWER instrument.
        'For any other type (TSP, CONTROL, ...) leave Term 1/2 for the user, and clear a
        'previously auto-filled L/N so a type change self-corrects (only the exact
        '"L"/"N" defaults are cleared, so a value you typed by hand is preserved).
        If Not isCont Then
            Dim isPwrTerm As Boolean
            isPwrTerm = (UCase$(Trim$(CStr(Me.Cells(r, "C").Value))) = "POWER")
            If sInst Then
                If isPwrTerm Then
                    Call PZ_DefaultTerm(r, "S Term1", "L")
                    Call PZ_DefaultTerm(r, "S Term2", "N")
                Else
                    Call PZ_ClearDefaultTerm(r, "S Term1", "L")
                    Call PZ_ClearDefaultTerm(r, "S Term2", "N")
                End If
            End If
            If dInst Then
                If isPwrTerm Then
                    Call PZ_DefaultTerm(r, "D Term1", "L")
                    Call PZ_DefaultTerm(r, "D Term2", "N")
                Else
                    Call PZ_ClearDefaultTerm(r, "D Term1", "L")
                    Call PZ_ClearDefaultTerm(r, "D Term2", "N")
                End If
            End If
        End If
        'A shielded TB strip on a side keeps its 3rd (shield/drain) Term + Tag open
        'even within an instrument group; everything else closes Term/Tag 3 & 4.
        Dim sShldT As Boolean, dShldT As Boolean
        sShldT = PZ_IsShldTB(CStr(Me.Cells(r, SSYM).Value))
        dShldT = (Not dcZ Is Nothing) And PZ_IsShldTB(CStr(dcZ.Value))
        If sShldT Then
            Call PZ_MakeHeaderVisible(r, "S Term3")
            Call PZ_MakeHeaderVisible(r, "S Tag3")
        Else
            Call PZ_MakeHeaderBlackClear(r, "S Term3")
            Call PZ_MakeHeaderBlackClear(r, "S Tag3")
        End If
        If dShldT Then
            Call PZ_MakeHeaderVisible(r, "D Term3")
            Call PZ_MakeHeaderVisible(r, "D Tag3")
        Else
            Call PZ_MakeHeaderBlackClear(r, "D Term3")
            Call PZ_MakeHeaderBlackClear(r, "D Tag3")
        End If
        Call PZ_MakeHeaderBlackClear(r, "S Term4")
        Call PZ_MakeHeaderBlackClear(r, "D Term4")
        Call PZ_MakeHeaderBlackClear(r, "S Tag4")
        Call PZ_MakeHeaderBlackClear(r, "D Tag4")
        If sBig Then
            Call PZ_MakeHeaderVisible(r, "S Tag1")
            Call PZ_MakeHeaderVisible(r, "S Tag2")
        End If
        If dBig Then
            Call PZ_MakeHeaderVisible(r, "D Tag1")
            Call PZ_MakeHeaderVisible(r, "D Tag2")
        End If
        Call PZ_MakeHeaderVisible(r, "Wire Label1")
        Call PZ_MakeHeaderVisible(r, "Wire Label2")
        Call PZ_MakeHeaderBlack(r, "Wire Label3")
        Call PZ_MakeHeaderBlack(r, "Wire Label4")

        If isCont Then
            'Only the SYNCED side (instrument / big-TB, carried down from the anchor)
            'has its descriptions / rating / type / quantity on the top row -- black
            'just that side and grey its symbol. The OTHER side is a distinct per-row
            'block, so leave its descriptions etc. open to edit on each row.
            If sInst Or sBig Then
                Call PZ_MakeHeaderBlack(r, "S Description1")
                Call PZ_MakeHeaderBlack(r, "S Description2")
                Call PZ_MakeHeaderBlack(r, "S Description3")
                Call PZ_MakeHeaderBlack(r, "S Rating")
                Call PZ_MakeHeaderBlack(r, "S Type")
                Call PZ_MakeHeaderBlack(r, "S Quantity")
                Me.Cells(r, SSYM).Interior.Color = RGB(191, 191, 191)
                Me.Cells(r, SSYM).Font.Color = RGB(89, 89, 89)
            End If
            If dInst Or dBig Then
                Call PZ_MakeHeaderBlack(r, "D Description1")
                Call PZ_MakeHeaderBlack(r, "D Description2")
                Call PZ_MakeHeaderBlack(r, "D Description3")
                Call PZ_MakeHeaderBlack(r, "D Rating")
                Call PZ_MakeHeaderBlack(r, "D Type")
                Call PZ_MakeHeaderBlack(r, "D Quantity")
                If Not dcZ Is Nothing Then
                    dcZ.Interior.Color = RGB(191, 191, 191)
                    dcZ.Font.Color = RGB(89, 89, 89)
                End If
            End If
        End If
NextTidy:
    Next r
End Sub

'True for a shielded terminal-block symbol (Shld-TB-TB / TB-TB-Shld), which has
'three terminals -- its 4th terminal column must stay closed.
Private Function PZ_IsShldTB(ByVal nm As String) As Boolean
    PZ_IsShldTB = (InStr(1, nm, "SHLD", vbTextCompare) > 0 _
        And InStr(1, nm, "TB", vbTextCompare) > 0)
End Function


'True if the given row is an auto-generated instrument continuation row.
Private Function PZ_IsAuto(ByVal rr As Long) As Boolean
    If rr < 3 Or rr > PZ_LAST_ROW Then Exit Function
    PZ_IsAuto = (CStr(Me.Cells(rr, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
        Or CStr(Me.Cells(rr, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")
End Function

'Visually group each instrument's continuation rows (Excel outline) so the loop
'reads as one collapsible block. Re-built each rebuild; a manual collapse resets
'on the next edit (the sheet rebuilds on change).
Private Sub PZ_GroupInstrumentRows()
    'Row grouping/outlining removed by request -- just clear any existing outline.
    On Error Resume Next
    Me.Rows("3:" & PZ_LAST_ROW).ClearOutline
    On Error GoTo 0
End Sub


Private Sub PZ_ClearOldAutoInstrumentRows()

    Dim r As Long

    'No-op: continuation rows keep their marker + content between rebuilds so the
    'user's manual symbols persist. Orphaned continuation rows are cleared in the
    'main rebuild loop (PZ_WipeAutoRow) instead.
End Sub

'Fully clear a continuation row that is no longer part of an instrument group.
Private Sub PZ_WipeAutoRow(ByVal rowNum As Long)
    Me.Range("A" & rowNum & ":BI" & rowNum).ClearContents
    'Drop any stale hidden-term CSV so a recycled auto-row starts clean.
    Me.Cells(rowNum, PZ_HIDDENTERMS_COL).ClearContents
    Call PZ_ResetRowFormatting(rowNum)
    Call PZ_ClearWireLabels(rowNum)
    Call PZ_BlackOutLoopCells(rowNum)
End Sub


Private Sub PZ_ResetRowFormatting(ByVal rowNum As Long)

    With Me.Cells(rowNum, SSYM)
        .Interior.Pattern = xlNone
        .Font.ColorIndex = xlAutomatic
    End With

    'Clear any stale "unsupported symbol" warning; re-applied in PZ_FormatLoopCells.
    Call PZ_SetWarn(Me.Cells(rowNum, SSYM), False)
    Call PZ_SetWarn(PZ_HeaderCell(rowNum, "D Symbol"), False)

    Call PZ_BlackOutLoopCells(rowNum)

End Sub


Private Sub PZ_ApplyPullRopeLogic(ByVal rowNum As Long)

    If PZ_IsPullRope(rowNum) Then

        'Wire Gauge column D
        Me.Cells(rowNum, "D").Value = "N/A"

        'Pullrope gets no wire labels
        Call PZ_ClearWireLabels(rowNum)

    Else

        'If row used to be Pullrope, clear the automatic wire gauge value
        If Trim(CStr(Me.Cells(rowNum, "D").Value)) = "N/A" Then
            Me.Cells(rowNum, "D").ClearContents
        End If

    End If

End Sub


Private Sub PZ_SetWireLabelFormulas(ByVal rowNum As Long)

    Dim n As Long
    Dim wireLabelCell As Range
    Dim sTagCell As Range
    Dim sTermCell As Range
    Dim dTagCell As Range
    Dim dTermCell As Range
    Dim conduitCell As Range
    Dim isInstrument As Boolean
    Dim st As String

    'Pullrope rows should never get wire label formulas.
    If PZ_IsPullRope(rowNum) Then
        Call PZ_ClearWireLabels(rowNum)
        Exit Sub
    End If

    Set conduitCell = Me.Cells(rowNum, "A")

    Dim sInst As Boolean, dInst As Boolean, grpInst As Boolean, isCont As Boolean
    Dim dcZ As Range, termIdx As Long, doSet As Boolean
    sInst = PZ_IsInstrumentType(rowNum)
    Set dcZ = PZ_HeaderCell(rowNum, "D Symbol")
    dInst = (Not dcZ Is Nothing) And InStr(1, CStr(dcZ.Value), "Inst", vbTextCompare) > 0
    grpInst = sInst Or dInst
    isCont = (CStr(Me.Cells(rowNum, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
        Or CStr(Me.Cells(rowNum, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")

    For n = 1 To 4

        Set wireLabelCell = PZ_HeaderCell(rowNum, "Wire Label" & n)
        If wireLabelCell Is Nothing Then GoTo NextN
        '--- task 1: a greyed (inactive) wire-label position stays blank so the
        '--- max-length red highlight never fires behind the grey fill.
        If wireLabelCell.Interior.Color = RGB(PZ_GREY_R, PZ_GREY_G, PZ_GREY_B) Then
            wireLabelCell.ClearContents
            GoTo NextN
        End If

        'Instrument rows use only Wire Label 1 & 2 (each row = one pair of wires);
        'Wire Label 3 & 4 are never used on an instrument row.
        If grpInst And n >= 3 Then
            wireLabelCell.ClearContents
            GoTo NextN
        End If

        st = UCase$(Trim$(CStr(PZ_WLState(rowNum, n))))
        If st = "CUSTOM" Then
            GoTo NextN
        ElseIf st = "BLANK" Then
            wireLabelCell.ClearContents
            GoTo NextN
        End If

        'Each row builds its own labels from its own Term n.  On the instrument side
        'the "tag" is the ISA Element ID and Loop # joined by a hyphen (e.g. AIT - 001);
        'the terminal-block side just uses Tag1.
        Dim sTagExpr As String, dTagExpr As String
        Dim eC As Range, lc As Range, t1 As Range

        If sInst Then
            Set eC = PZ_HeaderCell(rowNum, "S ISATag_FunctionID")
            Set lc = PZ_HeaderCell(rowNum, "S ISATag_Loop#")
            If eC Is Nothing Or lc Is Nothing Then GoTo NextN
            sTagExpr = "TEXTJOIN(""-"",TRUE," & eC.Address(False, False) & "," & lc.Address(False, False) & ")"
        Else
            'A 4-position TB strip spans rows but is ONE block, so every row reads
            'the top (anchor) row's Tag1.
            Dim sTagRow As Long, sTagNum As Long
            sTagRow = rowNum
            If PZ_IsBigTB(CStr(Me.Cells(rowNum, SSYM).Value)) Then sTagRow = PZ_AnchorRow(rowNum)
            'Iterate the tag with the wire (Wire Label n -> Tag n), unless the block has
            'only one tag -- then every wire label uses Tag 1.
            If PZ_MaxTags(CStr(Me.Cells(sTagRow, SSYM).Value)) <= 1 Then
                sTagNum = 1
            Else
                sTagNum = n
            End If
            Set t1 = PZ_HeaderCell(sTagRow, "S Tag" & sTagNum)
            If t1 Is Nothing Then GoTo NextN
            sTagExpr = t1.Address(False, False)
        End If

        If dInst Then
            Set eC = PZ_HeaderCell(rowNum, "D ISATag_FunctionID")
            Set lc = PZ_HeaderCell(rowNum, "D ISATag_Loop#")
            If eC Is Nothing Or lc Is Nothing Then GoTo NextN
            dTagExpr = "TEXTJOIN(""-"",TRUE," & eC.Address(False, False) & "," & lc.Address(False, False) & ")"
        Else
            Dim dTagRow As Long, dTagNum As Long, dSymTagCell As Range, dSymTagVal As String
            dTagRow = rowNum
            If (Not dcZ Is Nothing) Then
                If PZ_IsBigTB(CStr(dcZ.Value)) Then dTagRow = PZ_AnchorRow(rowNum)
            End If
            'Iterate the tag with the wire (Wire Label n -> Tag n), unless the block has
            'only one tag -- then every wire label uses Tag 1.
            dSymTagVal = ""
            Set dSymTagCell = PZ_HeaderCell(dTagRow, "D Symbol")
            If Not dSymTagCell Is Nothing Then dSymTagVal = CStr(dSymTagCell.Value)
            If PZ_MaxTags(dSymTagVal) <= 1 Then
                dTagNum = 1
            Else
                dTagNum = n
            End If
            Set t1 = PZ_HeaderCell(dTagRow, "D Tag" & dTagNum)
            If t1 Is Nothing Then GoTo NextN
            dTagExpr = t1.Address(False, False)
        End If

        Set sTermCell = PZ_HeaderCell(rowNum, "S Term" & n)
        Set dTermCell = PZ_HeaderCell(rowNum, "D Term" & n)

        If Not sTermCell Is Nothing And Not dTermCell Is Nothing Then

            'A term cell set to "Hide from Generation" drops out of the label too.
            Dim sTermExpr As String, dTermExpr As String
            sTermExpr = "IF(" & sTermCell.Address(False, False) & "=""Hide from Generation"",""""," & sTermCell.Address(False, False) & ")"
            dTermExpr = "IF(" & dTermCell.Address(False, False) & "=""Hide from Generation"",""""," & dTermCell.Address(False, False) & ")"

            wireLabelCell.Formula = _
                "=IF(" & conduitCell.Address(False, False) & "="""",""""," & _
                "IFERROR(" & _
                "TEXTJOIN("":"",TRUE," & _
                "IF(INDEX(ConduitIndex!$B:$B,MATCH(" & conduitCell.Address(False, False) & ",ConduitIndex!$A:$A,0))=" & sTagExpr & ",""""," & _
                "INDEX(ConduitIndex!$B:$B,MATCH(" & conduitCell.Address(False, False) & ",ConduitIndex!$A:$A,0)))," & _
                sTagExpr & "," & _
                sTermExpr & ")" & _
                "&""/""&" & _
                "TEXTJOIN("":"",TRUE," & _
                "IF(INDEX(ConduitIndex!$E:$E,MATCH(" & conduitCell.Address(False, False) & ",ConduitIndex!$A:$A,0))=" & dTagExpr & ",""""," & _
                "INDEX(ConduitIndex!$E:$E,MATCH(" & conduitCell.Address(False, False) & ",ConduitIndex!$A:$A,0)))," & _
                dTagExpr & "," & _
                dTermExpr & ")" & _
                ",""""))"

        End If

NextN:
    Next n

End Sub


Private Sub PZ_ClearWireLabels(ByVal rowNum As Long)

    Dim n As Long
    Dim wireLabelCell As Range

    For n = 1 To 4

        Set wireLabelCell = PZ_HeaderCell(rowNum, "Wire Label" & n)

        If Not wireLabelCell Is Nothing Then
            wireLabelCell.ClearContents
        End If

    Next n

End Sub


Private Function PZ_BaseBlockName(ByVal sym As String) As String
    'Strip the " (Visibility)" suffix, normalize (upper, no spaces/_/-) for cache keys.
    Dim s As String, p As Long
    s = Trim$(CStr(sym))
    p = InStr(1, s, "(")
    If p > 0 Then s = Trim$(Left$(s, p - 1))
    PZ_BaseBlockName = PZ_NormalizeHeader(s)
End Function

Private Sub PZ_BuildBlockAttrCache()
    'Read BlockIndex once into { blockName -> { attrTag: True } }. In BlockIndex each
    'attribute is its own NAMED column (Tag1, Term1, CB_Rating, Rating, ISATag_LoopNum ...);
    'a block "has" that attribute when its cell in that column is non-empty. Names are
    'normalized (upper, no spaces/_/-) so lookups match regardless of spelling.
    Dim ws As Worksheet, lastRow As Long, lastCol As Long, r As Long, c As Long
    Set mBlockAttrs = CreateObject("Scripting.Dictionary")
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("BlockIndex")
    On Error GoTo 0
    If ws Is Nothing Then Exit Sub
    lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    lastCol = ws.Cells(1, ws.Columns.Count).End(xlToLeft).Column
    Dim nameCol As Long, firstAttCol As Long, hdr As String
    nameCol = 0: firstAttCol = 0
    For c = 1 To lastCol
        hdr = Trim$(CStr(ws.Cells(1, c).Value))
        If StrComp(hdr, "Block_Name", vbTextCompare) = 0 Then nameCol = c
        If StrComp(hdr, "Att_Count", vbTextCompare) = 0 Then firstAttCol = c + 1
    Next c
    If nameCol = 0 Then Exit Sub
    If firstAttCol = 0 Then firstAttCol = nameCol + 1
    Dim tagOf() As String
    ReDim tagOf(1 To lastCol)
    For c = firstAttCol To lastCol
        tagOf(c) = PZ_NormalizeHeader(CStr(ws.Cells(1, c).Value))
    Next c
    Dim data As Variant, bn As String
    data = ws.Range(ws.Cells(1, 1), ws.Cells(lastRow, lastCol)).Value
    Dim d As Object
    For r = 2 To lastRow
        bn = PZ_NormalizeHeader(CStr(data(r, nameCol)))
        If Len(bn) = 0 Then GoTo NextR
        If mBlockAttrs.Exists(bn) Then
            Set d = mBlockAttrs(bn)
        Else
            Set d = CreateObject("Scripting.Dictionary")
            mBlockAttrs.Add bn, d
        End If
        For c = firstAttCol To lastCol
            If Len(tagOf(c)) > 0 Then
                If Len(Trim$(CStr(data(r, c) & ""))) > 0 Then
                    If Not d.Exists(tagOf(c)) Then d.Add tagOf(c), True
                End If
            End If
        Next c
NextR:
    Next r
End Sub

Private Function PZ_BlockAttrDict(ByVal sym As String) As Object
    If mBlockAttrs Is Nothing Then Call PZ_BuildBlockAttrCache
    Dim bn As String
    bn = PZ_BaseBlockName(sym)
    If mBlockAttrs.Exists(bn) Then
        Set PZ_BlockAttrDict = mBlockAttrs(bn)
    Else
        Set PZ_BlockAttrDict = CreateObject("Scripting.Dictionary")
    End If
End Function

Private Function PZ_BlockHasAttr(ByVal sym As String, ByVal tag As String) As Boolean
    PZ_BlockHasAttr = PZ_BlockAttrDict(sym).Exists(PZ_NormalizeHeader(tag))
End Function

Private Function PZ_BlockHasRating(ByVal sym As String) As Boolean
    'True if the block carries ANY rating attribute (Rating, FU_Rating, CB_Rating,
    'DISC_Rating, Rating1/2, ...). Data-driven -> no hardcoded Blocks_Rating list.
    Dim d As Object, k As Variant
    Set d = PZ_BlockAttrDict(sym)
    For Each k In d.Keys
        If InStr(1, CStr(k), "RATING", vbTextCompare) > 0 Then
            PZ_BlockHasRating = True
            Exit Function
        End If
    Next k
End Function

Private Sub PZ_MakeHeaderOnAttr(ByVal rowNum As Long, ByVal header As String, ByVal sym As String, ByVal tag As String)
    'Open the column if the block has the attribute, else grey it.
    If PZ_BlockHasAttr(sym, tag) Then
        Call PZ_MakeHeaderVisible(rowNum, header)
    Else
        Call PZ_MakeHeaderBlack(rowNum, header)
    End If
End Sub

Private Sub PZ_FormatLoopCells(ByVal rowNum As Long, ByVal loopCount As Long)

    Dim n As Long
    Dim symbolName As String
    Dim isInstrument As Boolean
    Dim dIsInstrument As Boolean
    Dim isPullRope As Boolean
    Dim termsToShow As Long
    Dim dsym As Range

    symbolName = CStr(Me.Cells(rowNum, SSYM).Value)
    isInstrument = PZ_IsInstrumentType(rowNum)
    isPullRope = PZ_IsPullRope(rowNum)

    'Continuation (auto-generated) rows share the anchor row's colors, so they
    'show no Color columns of their own.
    Dim isCont As Boolean
    isCont = (CStr(Me.Cells(rowNum, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
        Or CStr(Me.Cells(rowNum, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW")

    'Is the chosen DESTINATION symbol an instrument? This (not the source) drives
    'the D ISA columns, so picking an instrument Source no longer opens them.
    dIsInstrument = False
    Set dsym = PZ_HeaderCell(rowNum, "D Symbol")
    If Not dsym Is Nothing Then
        dIsInstrument = (InStr(1, CStr(dsym.Value), "Inst", vbTextCompare) > 0)
    End If

    'Each side's attributes open only when that side's symbol is filled.
    Dim sHas As Boolean, dHas As Boolean
    sHas = (Len(Trim$(symbolName)) > 0)
    If Not dsym Is Nothing Then dHas = (Len(Trim$(CStr(dsym.Value))) > 0)

    'Instrument terminal columns by term count: 2Term->1-2, 4Term->1-4,
    '6/8Term (unsupported)->none. Non-instruments use loopCount.
    Dim dTermsShow As Long
    If isInstrument Then
        Select Case PZ_TermCount(symbolName)
            Case 2: termsToShow = 2
            Case 4: termsToShow = 4
            Case Else: termsToShow = 0
        End Select
    Else
        termsToShow = loopCount
    End If
    If termsToShow > 4 Then termsToShow = 4

    'D terminal columns: by the D symbol's term count if it's an instrument,
    'otherwise by loopCount.
    If dIsInstrument Then
        Select Case PZ_TermCount(CStr(dsym.Value))
            Case 2: dTermsShow = 2
            Case 4: dTermsShow = 4
            Case Else: dTermsShow = 0
        End Select
    Else
        dTermsShow = loopCount
    End If
    If dTermsShow > 4 Then dTermsShow = 4

    'Detect shielded TB strips (e.g. "TB TB SHLD"): they carry a 3rd shield/drain
    'terminal, so Term 3 / Tag 3 open even though the wire count is 2.
    Dim sShld As Boolean, dShld As Boolean
    sShld = sHas And (Not isInstrument) And PZ_IsShldTB(symbolName)
    dShld = False
    If Not dsym Is Nothing Then _
        dShld = dHas And (Not dIsInstrument) And PZ_IsShldTB(CStr(dsym.Value))

    'Wire Label spans whichever present side has more terminals (the shield bump
    'below is applied AFTER this so the 3rd wire-label column stays closed).
    Dim wlShow As Long
    wlShow = 0
    If sHas And termsToShow > wlShow Then wlShow = termsToShow
    If dHas And dTermsShow > wlShow Then wlShow = dTermsShow

    If sShld And termsToShow < 3 Then termsToShow = 3
    If dShld And dTermsShow < 3 Then dTermsShow = 3

    '========================================================
    ' Pullrope logic:
    ' Only these should show:
    ' S Symbol, S Description 1-3,
    ' D Symbol, D Description 1-3.
    ' Everything else stays black.
    '========================================================
    If isPullRope Then

        Call PZ_MakeHeaderVisible(rowNum, "D Symbol")

        Call PZ_MakeHeaderBlack(rowNum, "S Rating")
        Call PZ_MakeHeaderBlack(rowNum, "D Rating")

        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_Loop#")
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_ElementID")
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_Element#")
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_FunctionID")

        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_Loop#")
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_ElementID")
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_Element#")
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_FunctionID")

        For n = 1 To 4
            Call PZ_MakeHeaderBlack(rowNum, "Color " & n)
            Call PZ_MakeHeaderBlack(rowNum, "S Tag" & n)
            Call PZ_MakeHeaderBlack(rowNum, "S Term" & n)

            Call PZ_MakeHeaderBlack(rowNum, "D Tag" & n)
            Call PZ_MakeHeaderBlack(rowNum, "D Term" & n)

            Call PZ_MakeHeaderBlack(rowNum, "Wire Label" & n)
        Next n

        Call PZ_ClearWireLabels(rowNum)

        Exit Sub

    End If

    Call PZ_MakeHeaderVisible(rowNum, "D Symbol")

    'S/D Rating open when the block carries ANY rating attribute in BlockIndex (CB_Rating,
    'DISC_Rating, FU_Rating, Rating, Rating1/2, ...). Fully data-driven off the block index --
    'composites (CB-CB_L, DISC-DISC_Fuse_L, ...) and new blocks light up automatically.
    If sHas And PZ_BlockHasRating(symbolName) Then
        Call PZ_MakeHeaderVisible(rowNum, "S Rating")
    Else
        Call PZ_MakeHeaderBlack(rowNum, "S Rating")
    End If
    If dHas And PZ_BlockHasRating(CStr(dsym.Value)) Then
        Call PZ_MakeHeaderVisible(rowNum, "D Rating")
    Else
        Call PZ_MakeHeaderBlack(rowNum, "D Rating")
    End If

    'S ISA columns open only when the SOURCE symbol is an instrument. An instrument
    'WITHOUT "Sensor" in its name opens ONLY Loop# and FunctionID; ElementID and
    'Element# stay greyed for it. "Sensor" instruments open all four (prior behavior).
    If sHas Then
        Call PZ_MakeHeaderOnAttr(rowNum, "S ISATag_Loop#", symbolName, "ISATag_LoopNum")
        Call PZ_MakeHeaderOnAttr(rowNum, "S ISATag_FunctionID", symbolName, "ISATag_FunctIdent")
        Call PZ_MakeHeaderOnAttr(rowNum, "S ISATag_ElementID", symbolName, "ISATag_ElementIdent")
        Call PZ_MakeHeaderOnAttr(rowNum, "S ISATag_Element#", symbolName, "ISATag_ElementNum")
    Else
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_Loop#")
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_ElementID")
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_Element#")
        Call PZ_MakeHeaderBlack(rowNum, "S ISATag_FunctionID")
    End If

    'D ISA columns open only when the DESTINATION symbol is an instrument. An instrument
    'WITHOUT "Sensor" in its name opens ONLY Loop# and FunctionID; ElementID and
    'Element# stay greyed for it. "Sensor" instruments open all four (prior behavior).
    If dHas Then
        Call PZ_MakeHeaderOnAttr(rowNum, "D ISATag_Loop#", CStr(dsym.Value), "ISATag_LoopNum")
        Call PZ_MakeHeaderOnAttr(rowNum, "D ISATag_FunctionID", CStr(dsym.Value), "ISATag_FunctIdent")
        Call PZ_MakeHeaderOnAttr(rowNum, "D ISATag_ElementID", CStr(dsym.Value), "ISATag_ElementIdent")
        Call PZ_MakeHeaderOnAttr(rowNum, "D ISATag_Element#", CStr(dsym.Value), "ISATag_ElementNum")
    Else
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_Loop#")
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_ElementID")
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_Element#")
        Call PZ_MakeHeaderBlack(rowNum, "D ISATag_FunctionID")
    End If

    Dim dSymCell As Range, dMax As Long, sMax As Long

    Set dSymCell = PZ_HeaderCell(rowNum, "D Symbol")
    dMax = 99
    If Not dSymCell Is Nothing Then dMax = PZ_MaxTags(CStr(dSymCell.Value))
    sMax = PZ_MaxTags(symbolName)

    'Tag columns are gated by wire count; a shielded TB opens a 3rd (shield) tag.
    Dim sTagCount As Long, dTagCount As Long
    sTagCount = loopCount
    dTagCount = loopCount
    If sShld Then
        If sTagCount < 3 Then sTagCount = 3
        If sMax < 3 Then sMax = 3
    End If
    If dShld Then
        If dTagCount < 3 Then dTagCount = 3
        If dMax < 3 Then dMax = 3
    End If

    For n = 1 To 4

        'Color N gated by Wire Ct, but only on the anchor row (continuation rows
        'of an instrument share the anchor's colors).
        If (Not isCont) And n <= loopCount Then
            Call PZ_MakeHeaderVisible(rowNum, "Color " & n)
        Else
            Call PZ_MakeHeaderBlackClear(rowNum, "Color " & n)
        End If

        'S Term: only if an S symbol is present, by its terminal count.
        If sHas And n <= termsToShow Then
            Call PZ_MakeHeaderVisible(rowNum, "S Term" & n)
        Else
            Call PZ_MakeHeaderBlackClear(rowNum, "S Term" & n)
        End If

        'Wire Label spans whichever side is present.
        If n <= wlShow Then
            Call PZ_MakeHeaderVisible(rowNum, "Wire Label" & n)
        Else
            Call PZ_MakeHeaderBlackClear(rowNum, "Wire Label" & n)
        End If

        'D Term: only if a D symbol is present, by its terminal count.
        If dHas And n <= dTermsShow Then
            Call PZ_MakeHeaderVisible(rowNum, "D Term" & n)
        Else
            Call PZ_MakeHeaderBlackClear(rowNum, "D Term" & n)
        End If

        'S Tag: needs an S symbol; black for instruments (ISA); else gated by tag count.
        If Not sHas Then
            Call PZ_MakeHeaderBlackClear(rowNum, "S Tag" & n)
        ElseIf isInstrument Then
            Call PZ_MakeHeaderBlack(rowNum, "S Tag" & n)
        ElseIf n <= sTagCount And n <= sMax Then
            Call PZ_MakeHeaderVisible(rowNum, "S Tag" & n)
        ElseIf n <= sTagCount Then
            Call PZ_MakeHeaderBlack(rowNum, "S Tag" & n)
        Else
            Call PZ_MakeHeaderBlackClear(rowNum, "S Tag" & n)
        End If

        'D Tag: needs a D symbol; black for instruments (ISA); else gated by tag count.
        If Not dHas Then
            Call PZ_MakeHeaderBlackClear(rowNum, "D Tag" & n)
        ElseIf dIsInstrument Then
            Call PZ_MakeHeaderBlack(rowNum, "D Tag" & n)
        ElseIf n <= dTagCount And n <= dMax Then
            Call PZ_MakeHeaderVisible(rowNum, "D Tag" & n)
        ElseIf n <= dTagCount Then
            Call PZ_MakeHeaderBlack(rowNum, "D Tag" & n)
        Else
            Call PZ_MakeHeaderBlackClear(rowNum, "D Tag" & n)
        End If

    Next n

    'Unsupported-instrument warning (6/8-Term or unknown-term instruments).
    Dim stc As Long, dtc As Long
    stc = PZ_TermCount(symbolName)
    Call PZ_SetWarn(Me.Cells(rowNum, SSYM), isInstrument And stc <> 2 And stc <> 4)
    If Not dsym Is Nothing Then
        dtc = PZ_TermCount(CStr(dsym.Value))
        Call PZ_SetWarn(dsym, dIsInstrument And dtc <> 2 And dtc <> 4)
    End If

End Sub


'Number of terminals encoded in an instrument block name, e.g.
'"Inst_Sensor_4W_L (Field_2Term)" -> 2. Returns 0 when not present.
Private Function PZ_TermCount(ByVal s As String) As Long
    Dim p As Long, i As Long, ch As String, num As String
    s = CStr(s)
    PZ_TermCount = 0
    p = InStr(1, s, "Term", vbTextCompare)
    If p > 1 Then
        i = p - 1
        Do While i >= 1
            ch = Mid$(s, i, 1)
            If ch >= "0" And ch <= "9" Then
                num = ch & num
                i = i - 1
            Else
                Exit Do
            End If
        Loop
        If Len(num) > 0 Then PZ_TermCount = CLng(num)
    End If
    'Four-position terminal strips (TB-TB-TB-TB_Round/Square...) carry no "nTerm"
    'token; treat them as 4-terminal so they generate a continuation copy row.
    If PZ_TermCount = 0 And InStr(1, s, "TB-TB-TB-TB", vbTextCompare) > 0 Then
        PZ_TermCount = 4
    End If
End Function

'True for a 4-position terminal strip (TB-TB-TB-TB_Round/Square) -- like an
'instrument, it spans rows and is greyed on its continuation copies.
Private Function PZ_IsBigTB(ByVal nm As String) As Boolean
    PZ_IsBigTB = (InStr(1, CStr(nm), "TB-TB-TB-TB", vbTextCompare) > 0)
End Function

'The group's top row: walk up over continuation (auto) rows to the first anchor.
Private Function PZ_AnchorRow(ByVal rowNum As Long) As Long
    Dim r As Long
    r = rowNum
    Do While r > 3
        If CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
            Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW" Then
            r = r - 1
        Else
            Exit Do
        End If
    Loop
    PZ_AnchorRow = r
End Function


Private Sub PZ_BlackOutLoopCells(ByVal rowNum As Long)

    Dim n As Long

    Call PZ_MakeHeaderBlack(rowNum, "D Symbol")

    'S Description 1-3
    Call PZ_MakeHeaderBlack(rowNum, "S Description1")
    Call PZ_MakeHeaderBlack(rowNum, "S Description2")
    Call PZ_MakeHeaderBlack(rowNum, "S Description3")

    'D Description 1-3
    Call PZ_MakeHeaderBlack(rowNum, "D Description1")
    Call PZ_MakeHeaderBlack(rowNum, "D Description2")
    Call PZ_MakeHeaderBlack(rowNum, "D Description3")

    Call PZ_MakeHeaderBlack(rowNum, "S Rating")
    Call PZ_MakeHeaderBlack(rowNum, "D Rating")

    'Black out new S ISA attribute cells
    Call PZ_MakeHeaderBlack(rowNum, "S ISATag_Loop#")
    Call PZ_MakeHeaderBlack(rowNum, "S ISATag_ElementID")
    Call PZ_MakeHeaderBlack(rowNum, "S ISATag_Element#")
    Call PZ_MakeHeaderBlack(rowNum, "S ISATag_FunctionID")

    'Black out new D ISA attribute cells
    Call PZ_MakeHeaderBlack(rowNum, "D ISATag_Loop#")
    Call PZ_MakeHeaderBlack(rowNum, "D ISATag_ElementID")
    Call PZ_MakeHeaderBlack(rowNum, "D ISATag_Element#")
    Call PZ_MakeHeaderBlack(rowNum, "D ISATag_FunctionID")

    For n = 1 To 4

        Call PZ_MakeHeaderBlack(rowNum, "Color " & n)
        Call PZ_MakeHeaderBlack(rowNum, "S Tag" & n)
        Call PZ_MakeHeaderBlack(rowNum, "S Term" & n)

        Call PZ_MakeHeaderBlack(rowNum, "D Tag" & n)
        Call PZ_MakeHeaderBlack(rowNum, "D Term" & n)

        Call PZ_MakeHeaderBlack(rowNum, "Wire Label" & n)

    Next n

    Call PZ_ClearNonCustomLabels(rowNum)   'preserve user CUSTOM labels through the per-row reset; invalid-row branches still hard-clear

End Sub


Private Sub PZ_ForceDescriptionsBySymbol()

    Dim r As Long
    Dim hasSymbol As Boolean
    Dim hasValidLoop As Boolean
    Dim isAutoInstrumentRow As Boolean

    For r = 3 To mProcTo

        hasSymbol = Len(Trim(CStr(Me.Cells(r, SSYM).Value))) > 0
        Dim hasDSym As Boolean
        hasDSym = PZ_DSymPresent(r)
        hasValidLoop = False
        isAutoInstrumentRow = False

        If IsNumeric(Me.Cells(r, "B").Value) Then
            If PZ_ValidLoop(CLng(Me.Cells(r, "B").Value)) Then
                hasValidLoop = True
            End If
        End If

        If CStr(Me.Cells(r, PZ_MARKER_COL).Value) = PZ_AUTO_MARKER _
            Or CStr(Me.Cells(r, PZ_MARKER_COL).Value) = "PZ_AUTO_ANALOG_ROW" Then

            isAutoInstrumentRow = True

        End If

        'Open descriptions for:
        '1. Original rows with S Symbol and valid Wire Loop #
        '2. Auto-generated instrument rows with S Symbol
        '3. Pullrope rows with S Symbol and valid Wire Loop #
        'S Descriptions open with the S symbol; D Descriptions with the D symbol
        '(each needing a valid Wire Ct, or being an auto-generated instrument row).
        If hasSymbol And (hasValidLoop Or isAutoInstrumentRow) Then
            Call PZ_MakeHeaderVisible(r, "S Description1")
            Call PZ_MakeHeaderVisible(r, "S Description2")
            Call PZ_MakeHeaderVisible(r, "S Description3")
        Else
            Call PZ_MakeHeaderBlack(r, "S Description1")
            Call PZ_MakeHeaderBlack(r, "S Description2")
            Call PZ_MakeHeaderBlack(r, "S Description3")
        End If

        If hasDSym And (hasValidLoop Or isAutoInstrumentRow) Then
            Call PZ_MakeHeaderVisible(r, "D Description1")
            Call PZ_MakeHeaderVisible(r, "D Description2")
            Call PZ_MakeHeaderVisible(r, "D Description3")
        Else
            Call PZ_MakeHeaderBlack(r, "D Description1")
            Call PZ_MakeHeaderBlack(r, "D Description2")
            Call PZ_MakeHeaderBlack(r, "D Description3")
        End If

    Next r

End Sub


Private Function PZ_IsInstrumentType(ByVal rowNum As Long) As Boolean

    Dim symbolValue As String

    'Instrument logic is based on S Symbol, not Type.
    'If S Symbol contains "Inst", this row is treated as an instrument row.
    symbolValue = PZ_NormalizeHeader(CStr(Me.Cells(rowNum, SSYM).Value))

    If InStr(1, symbolValue, "INST", vbTextCompare) > 0 Then
        PZ_IsInstrumentType = True
    Else
        PZ_IsInstrumentType = False
    End If

End Function


Private Function PZ_IsPullRope(ByVal rowNum As Long) As Boolean

    Dim typeValue As String

    'Pullrope logic is based on Type column C on this sheet.
    'This catches Pullrope, Pull Rope, Pull_Rope, etc.
    typeValue = PZ_NormalizeHeader(CStr(Me.Cells(rowNum, "C").Value))

    If InStr(1, typeValue, "PULLROPE", vbTextCompare) > 0 Then
        PZ_IsPullRope = True
    Else
        PZ_IsPullRope = False
    End If

End Function


Private Function PZ_SymbolIsInNamedList(ByVal symbolName As String, ByVal listName As String) As Boolean

    Dim rng As Range
    Dim ws As Worksheet
    Dim c As Range
    Dim cleanSymbol As String
    Dim cleanCell As String

    cleanSymbol = PZ_NormalizeHeader(symbolName)

    If cleanSymbol = "" Then
        PZ_SymbolIsInNamedList = False
        Exit Function
    End If

    'Workbook-level name
    On Error Resume Next
    Set rng = ThisWorkbook.Names(listName).RefersToRange
    On Error GoTo 0

    'Sheet-level name
    If rng Is Nothing Then
        For Each ws In ThisWorkbook.Worksheets
            On Error Resume Next
            Set rng = ws.Names(listName).RefersToRange
            On Error GoTo 0

            If Not rng Is Nothing Then Exit For
        Next ws
    End If

    If rng Is Nothing Then
        PZ_SymbolIsInNamedList = False
        Exit Function
    End If

    For Each c In rng.Cells

        cleanCell = PZ_NormalizeHeader(CStr(c.Value))

        If cleanCell = cleanSymbol Then
            PZ_SymbolIsInNamedList = True
            Exit Function
        End If

    Next c

    PZ_SymbolIsInNamedList = False

End Function


Private Function PZ_HeaderCell(ByVal rowNum As Long, ByVal headerText As String) As Range

    Dim wantedHeader As String
    Dim c As Range
    Dim h As String

    wantedHeader = PZ_NormalizeHeader(headerText)

    'Build (once) a normalized-header -> column-number cache. Headers never move
    'at runtime, so this stays valid for the whole session and makes the
    '100-row rebuild fast.
    If mHdr Is Nothing Then
        Set mHdr = CreateObject("Scripting.Dictionary")
        For Each c In Me.Range("A2:BM2")
            h = PZ_NormalizeHeader(CStr(c.Value))
            If Len(h) > 0 Then
                If Not mHdr.Exists(h) Then mHdr.Add h, c.Column
            End If
        Next c
    End If

    If mHdr.Exists(wantedHeader) Then
        Set PZ_HeaderCell = Me.Cells(rowNum, mHdr(wantedHeader))
    Else
        Set PZ_HeaderCell = Nothing
    End If

End Function


Private Function PZ_NormalizeHeader(ByVal headerText As String) As String

    headerText = UCase(headerText)
    headerText = Replace(headerText, " ", "")
    headerText = Replace(headerText, "_", "")
    headerText = Replace(headerText, "-", "")

    PZ_NormalizeHeader = headerText

End Function


Private Sub PZ_MakeHeaderVisible(ByVal rowNum As Long, ByVal headerText As String)

    Dim targetCell As Range

    Set targetCell = PZ_HeaderCell(rowNum, headerText)

    If targetCell Is Nothing Then Exit Sub

    With targetCell
        .Interior.Pattern = xlNone
        .Font.ColorIndex = xlAutomatic
    End With

End Sub


Private Sub PZ_MakeHeaderBlack(ByVal rowNum As Long, ByVal headerText As String)

    Dim targetCell As Range

    Set targetCell = PZ_HeaderCell(rowNum, headerText)

    If targetCell Is Nothing Then Exit Sub

    With targetCell
        .Interior.Color = RGB(PZ_GREY_R, PZ_GREY_G, PZ_GREY_B)
        .Font.Color = RGB(PZ_GREY_R, PZ_GREY_G, PZ_GREY_B)
    End With

End Sub


'================= Color multi-select (added) =====================

Private Function CLR_Mark() As String
    CLR_Mark = ChrW(10003) & " "
End Function

Private Function CLR_StripMark(ByVal t As String) As String
    t = Trim(CStr(t))
    If Left$(t, Len(CLR_Mark())) = CLR_Mark() Then t = Mid$(t, Len(CLR_Mark()) + 1)
    CLR_StripMark = Trim(t)
End Function

Private Function CLR_Clean(ByVal s As String) As String
    Dim items As Variant, it As Variant, ci As String, res As String
    s = Trim(CStr(s))
    If Len(s) = 0 Then Exit Function
    items = Split(s, CLR_SEP)
    For Each it In items
        ci = CLR_StripMark(CStr(it))
        If Len(ci) > 0 Then
            If Len(res) = 0 Then res = ci Else res = res & CLR_SEP & ci
        End If
    Next it
    CLR_Clean = res
End Function

Private Function CLR_Count(ByVal s As String) As Long
    s = CLR_Clean(s)
    If Len(s) = 0 Then
        CLR_Count = 0
    Else
        CLR_Count = UBound(Split(s, CLR_SEP)) + 1
    End If
End Function

Private Function CLR_AlreadySel(ByVal s As String, ByVal chk As String) As Boolean
    Dim items As Variant, it As Variant
    s = Trim(CStr(s))
    chk = CLR_StripMark(CStr(chk))
    If Len(s) = 0 Or Len(chk) = 0 Then Exit Function
    items = Split(s, CLR_SEP)
    For Each it In items
        If StrComp(CLR_StripMark(CStr(it)), chk, vbTextCompare) = 0 Then
            CLR_AlreadySel = True
            Exit Function
        End If
    Next it
End Function

Private Function CLR_RemoveSel(ByVal s As String, ByVal remVal As String) As String
    Dim items As Variant, it As Variant, res As String, ci As String
    s = Trim(CStr(s))
    remVal = CLR_StripMark(CStr(remVal))
    If Len(s) = 0 Then Exit Function
    items = Split(s, CLR_SEP)
    For Each it In items
        ci = CLR_StripMark(CStr(it))
        If Len(ci) > 0 And StrComp(ci, remVal, vbTextCompare) <> 0 Then
            If Len(res) = 0 Then res = ci Else res = res & CLR_SEP & ci
        End If
    Next it
    CLR_RemoveSel = res
End Function

Private Function CLR_LoopCount(ByVal rowNum As Long) As Long
    CLR_LoopCount = 0
    If IsNumeric(Me.Cells(rowNum, "B").Value) Then CLR_LoopCount = CLng(Me.Cells(rowNum, "B").Value)
End Function

Private Function CLR_SourceRange(ByVal rowNum As Long) As Range
    Dim lc As Long, nm As String
    lc = CLR_LoopCount(rowNum)
    Select Case lc
        Case 2: nm = "Color_2"
        Case 3: nm = "Color_3"
        Case 4: nm = "Color_4"
        Case Else: nm = "Color_1"
    End Select
    On Error Resume Next
    Set CLR_SourceRange = ThisWorkbook.Names(nm).RefersToRange
    On Error GoTo 0
End Function

Private Function CLR_InSource(ByVal rowNum As Long, ByVal chk As String) As Boolean
    Dim rng As Range, c As Range
    chk = CLR_StripMark(chk)
    If Len(chk) = 0 Then Exit Function
    Set rng = CLR_SourceRange(rowNum)
    If rng Is Nothing Then Exit Function
    For Each c In rng.Cells
        If Len(Trim(CStr(c.Value))) > 0 Then
            If StrComp(Trim(CStr(c.Value)), chk, vbTextCompare) = 0 Then
                CLR_InSource = True
                Exit Function
            End If
        End If
    Next c
End Function

Private Function CLR_DispRange(ByVal rowNum As Long) As Range
    Dim ws As Worksheet, src As Range
    Set ws = ThisWorkbook.Worksheets(CLR_DISP_SHEET)
    Set src = CLR_SourceRange(rowNum)
    If src Is Nothing Then Exit Function
    Set CLR_DispRange = ws.Range(CLR_DISP_START).Resize(src.Cells.Count, 1)
End Function

Private Sub CLR_BuildDisplay(ByVal rowNum As Long, ByVal selectedText As String)
    Dim ws As Worksheet, src As Range, disp As Range, i As Long, raw As String
    selectedText = CLR_Clean(selectedText)
    Set src = CLR_SourceRange(rowNum)
    If src Is Nothing Then Exit Sub
    Set disp = CLR_DispRange(rowNum)
    Set ws = ThisWorkbook.Worksheets(CLR_DISP_SHEET)
    ws.Range(CLR_DISP_START).Resize(50, 1).ClearContents
    For i = 1 To src.Cells.Count
        raw = Trim(CStr(src.Cells(i, 1).Value))
        If Len(raw) > 0 Then
            If CLR_AlreadySel(selectedText, raw) Then
                disp.Cells(i, 1).Value = CLR_Mark() & raw
            Else
                disp.Cells(i, 1).Value = raw
            End If
        End If
    Next i
    ws.Columns(disp.Column).Hidden = True
End Sub

Private Sub CLR_ApplyValidation(ByVal rowNum As Long, ByVal Target As Range)
    Dim ws As Worksheet, disp As Range
    Set ws = ThisWorkbook.Worksheets(CLR_DISP_SHEET)
    Set disp = CLR_DispRange(rowNum)
    If disp Is Nothing Then Exit Sub
    On Error Resume Next
    ThisWorkbook.Names(CLR_DISP_NAME).Delete
    On Error GoTo 0
    ThisWorkbook.Names.Add Name:=CLR_DISP_NAME, _
        RefersTo:="='" & Replace(ws.Name, "'", "''") & "'!" & disp.Address
    Target.Validation.Delete
    Target.Validation.Add Type:=xlValidateList, AlertStyle:=xlValidAlertStop, _
        Operator:=xlBetween, Formula1:="=" & CLR_DISP_NAME
    With Target.Validation
        .IgnoreBlank = True
        .InCellDropdown = True
        .ShowError = False
    End With
    ws.Columns(disp.Column).Hidden = True
End Sub

'(Color multi-select Worksheet_SelectionChange removed: Color is now 4 single-select columns.)

Private Sub CLR_HandleChange(ByVal Target As Range)
    Dim oldVal As String, newVal As String, combined As String, lc As Long
    On Error GoTo SafeExit
    newVal = CLR_StripMark(CStr(Target.Value))
    Application.EnableEvents = False

    If mClrPrevAddr = Target.Address(False, False) Then
        oldVal = mClrPrevVal
    Else
        oldVal = ""
    End If

    lc = CLR_LoopCount(Target.Row)
    If lc < 1 Then lc = 1

    If Len(newVal) = 0 Then
        Target.Value = ""
        mClrPrevAddr = Target.Address(False, False)
        mClrPrevVal = ""
        CLR_BuildDisplay Target.Row, ""
        CLR_ApplyValidation Target.Row, Target
        GoTo SafeExit
    End If


    If Len(Trim(oldVal)) = 0 Then
        combined = newVal
    ElseIf CLR_AlreadySel(oldVal, newVal) Then
        combined = CLR_RemoveSel(oldVal, newVal)
    Else
        If CLR_Count(oldVal) >= lc Then
            combined = oldVal
        Else
            combined = oldVal & CLR_SEP & newVal
        End If
    End If

    Target.Value = combined
    mClrPrevAddr = Target.Address(False, False)
    mClrPrevVal = CStr(Target.Value)
    CLR_BuildDisplay Target.Row, CStr(Target.Value)
    CLR_ApplyValidation Target.Row, Target

SafeExit:
    Application.EnableEvents = True
End Sub


'================= Wire-label override (added) =================

Private Function PZ_WireLabelModeValue(ByVal rowNum As Long) As String
    Dim c As Range
    Set c = PZ_HeaderCell(rowNum, "Wire Label Mode")
    If c Is Nothing Then
        PZ_WireLabelModeValue = ""
    Else
        PZ_WireLabelModeValue = CStr(c.Value)
    End If
End Function

Private Sub PZ_ApplyWireLabelMode(ByVal rowNum As Long)
    Dim wlm As String, n As Long, c As Range
    wlm = UCase$(Trim$(CStr(PZ_WireLabelModeValue(rowNum))))
    If wlm = "NONE" Then
        Call PZ_ClearWireLabels(rowNum)
    ElseIf wlm = "CUSTOM" Then
        For n = 1 To 4
            Set c = PZ_HeaderCell(rowNum, "Wire Label" & n)
            If Not c Is Nothing Then
                If c.HasFormula Then c.Value = c.Value
            End If
        Next n
    Else
        Call PZ_SetWireLabelFormulas(rowNum)
    End If
End Sub


'================= Per-wire label override (added) =================

Private Function PZ_WLState(ByVal rowNum As Long, ByVal n As Long) As String
    Dim c As Range
    Set c = PZ_HeaderCell(rowNum, "WL" & n & "_Mode")
    If c Is Nothing Then
        PZ_WLState = ""
    Else
        PZ_WLState = CStr(c.Value)
    End If
End Function

Private Sub PZ_SetWLState(ByVal rowNum As Long, ByVal n As Long, ByVal s As String)
    Dim c As Range
    Set c = PZ_HeaderCell(rowNum, "WL" & n & "_Mode")
    If Not c Is Nothing Then c.Value = s
End Sub

Private Function PZ_WireLabelColIndex(ByVal colNum As Long) As Long
    Dim n As Long, c As Range
    PZ_WireLabelColIndex = 0
    For n = 1 To 4
        Set c = PZ_HeaderCell(2, "Wire Label" & n)
        If Not c Is Nothing Then
            If c.Column = colNum Then
                PZ_WireLabelColIndex = n
                Exit Function
            End If
        End If
    Next n
End Function

Private Sub PZ_ClearNonCustomLabels(ByVal rowNum As Long)
    Dim n As Long, c As Range
    For n = 1 To 4
        If UCase$(Trim$(CStr(PZ_WLState(rowNum, n)))) <> "CUSTOM" Then
            Set c = PZ_HeaderCell(rowNum, "Wire Label" & n)
            If Not c Is Nothing Then c.ClearContents
        End If
    Next n
End Sub

Private Sub PZ_HandleWireLabelPick(ByVal rowNum As Long, ByVal n As Long, ByVal tgtCell As Range)
    Dim v As String, vu As String
    v = Trim$(CStr(tgtCell.Value))
    vu = UCase$(v)
    If vu = "DEFAULT" Then
        Call PZ_SetWLState(rowNum, n, "DEFAULT")
        Call PZ_SetWireLabelFormulas(rowNum)
    ElseIf vu = "BLANK" Then
        Call PZ_SetWLState(rowNum, n, "BLANK")
        tgtCell.ClearContents
    ElseIf vu = "CUSTOM" Then
        'Seed the cell with the current DEFAULT label, converted to a static value,
        'so the user has something to edit instead of a blank cell.
        Call PZ_SetWLState(rowNum, n, "DEFAULT")
        Call PZ_SetWireLabelFormulas(rowNum)
        If tgtCell.HasFormula Then tgtCell.Value = tgtCell.Value
        Call PZ_SetWLState(rowNum, n, "CUSTOM")
    Else
        If Len(v) = 0 Then
            If UCase$(Trim$(CStr(PZ_WLState(rowNum, n)))) <> "CUSTOM" Then
                Call PZ_SetWLState(rowNum, n, "BLANK")
            End If
        Else
            Call PZ_SetWLState(rowNum, n, "CUSTOM")
        End If
    End If
End Sub

'================= Per-term "Hide from Generation" =================

'The "checked" dropdown option, built with ChrW so the source stays ASCII-safe
'through the .bas import (avoids a mangled check-mark byte).
Private Function PZ_TermOptOn() As String
    PZ_TermOptOn = ChrW(10003) & " " & PZ_TERM_OPT
End Function

'Column -> hide token if it is a Term or Tag column, else "".
'Term tokens: "S1".."D4".  Tag tokens: "SG1".."DG4" (G = taG).
Private Function PZ_TermToken(ByVal colNum As Long) As String
    Dim n As Long, c As Range
    PZ_TermToken = ""
    For n = 1 To 4
        Set c = PZ_HeaderCell(2, "S Term " & n)
        If Not c Is Nothing Then
            If c.Column = colNum Then PZ_TermToken = "S" & n: Exit Function
        End If
    Next n
    For n = 1 To 4
        Set c = PZ_HeaderCell(2, "D Term " & n)
        If Not c Is Nothing Then
            If c.Column = colNum Then PZ_TermToken = "D" & n: Exit Function
        End If
    Next n
    For n = 1 To 4
        Set c = PZ_HeaderCell(2, "S Tag " & n)
        If Not c Is Nothing Then
            If c.Column = colNum Then PZ_TermToken = "SG" & n: Exit Function
        End If
    Next n
    For n = 1 To 4
        Set c = PZ_HeaderCell(2, "D Tag " & n)
        If Not c Is Nothing Then
            If c.Column = colNum Then PZ_TermToken = "DG" & n: Exit Function
        End If
    Next n
End Function

'Token -> the cell on a given row. "S2"->S Term 2 ; "SG2"->S Tag 2.
Private Function PZ_TermCellFromToken(ByVal rowNum As Long, ByVal tok As String) As Range
    Dim side As String
    If Len(tok) < 2 Then Exit Function
    side = Left$(tok, 1)
    If Mid$(tok, 2, 1) = "G" Then          'tag token, e.g. SG2
        Set PZ_TermCellFromToken = PZ_HeaderCell(rowNum, side & " Tag " & Mid$(tok, 3))
    Else                                   'term token, e.g. S2
        Set PZ_TermCellFromToken = PZ_HeaderCell(rowNum, side & " Term " & Mid$(tok, 2))
    End If
End Function

'True if a hide token refers to a Tag cell (vs a Term cell).
Private Function PZ_TokenIsTag(ByVal tok As String) As Boolean
    PZ_TokenIsTag = (Len(tok) >= 2) And (Mid$(tok, 2, 1) = "G")
End Function

'Toggle a term's hidden state: keep the real value, flip the CSV token, repaint.
Private Sub PZ_HandleTermHide(ByVal tgtCell As Range)
    Dim tok As String, realVal As String, hcell As Range
    Dim csv As String, nowHidden As Boolean

    tok = PZ_TermToken(tgtCell.Column)
    If Len(tok) = 0 Then Exit Sub

    'Restore the term's real value (the dropdown pick overwrote it with the
    'option text). Primary source: the value captured when the cell was selected.
    'Fallback (value typed + hidden in one selection): undo the user's pick.
    If mTermPrevAddr = tgtCell.Address _
       And mTermPrevVal <> PZ_TERM_OPT And mTermPrevVal <> PZ_TermOptOn() Then
        realVal = mTermPrevVal
    Else
        realVal = ""
        On Error Resume Next
        Application.Undo
        realVal = CStr(tgtCell.Value)
        On Error GoTo 0
        If realVal = PZ_TERM_OPT Or realVal = PZ_TermOptOn() Then realVal = ""
    End If
    tgtCell.Value = realVal

    'Flip the token in this row's hidden-terms CSV store.
    Set hcell = Me.Cells(tgtCell.Row, PZ_HIDDENTERMS_COL)
    csv = CStr(hcell.Value)
    If PZ_CsvHas(csv, tok) Then
        hcell.Value = PZ_CsvRemove(csv, tok)
        nowHidden = False
    Else
        hcell.Value = PZ_CsvAdd(csv, tok)
        nowHidden = True
    End If

    Call PZ_ApplyTermHideVisual(tgtCell, nowHidden)
End Sub

'Paint the flag on a term cell: amber fill + note comment + check-mark dropdown
'when hidden; clear all three when not.
Private Sub PZ_ApplyTermHideVisual(ByVal c As Range, ByVal isHidden As Boolean)
    Dim note As String
    On Error Resume Next
    note = PZ_TERM_NOTE
    If PZ_TokenIsTag(PZ_TermToken(c.Column)) Then note = PZ_TAG_NOTE
    If Not c.Comment Is Nothing Then c.Comment.Delete
    If isHidden Then
        c.AddComment note
        If Not c.Comment Is Nothing Then c.Comment.Visible = False
        c.Interior.Color = RGB(255, 235, 156)
    Else
        c.Interior.Pattern = xlNone
    End If
    'No dropdown validation anymore -- the hidden state shows as the amber fill + note; the
    'toggle is right-click. (A single-item list here caused the typed-letter auto-complete.)
    On Error GoTo 0
End Sub

'Rebuild every hidden-term flag from the CSV store (called at end of a rebuild,
'after the formatting passes have repainted the term cells). A row that was
'cleared/deleted has its stale hide tokens + comments wiped.
Private Sub PZ_RestoreTermHideVisuals()
    Dim r As Long, i As Long, csv As String, c As Range
    Dim toks As Variant
    'PASS A: drop any "Hidden From Generation" note on a greyed/closed cell or an
    'empty (no-conduit) row -- even one whose stored token is already gone (orphan).
    Dim cmt As Comment, cc As Range, rr As Long, kill As Collection, k As Long
    Set kill = New Collection
    For Each cmt In Me.Comments
        Set cc = Nothing
        On Error Resume Next
        Set cc = cmt.Parent
        On Error GoTo 0
        If Not cc Is Nothing Then
            rr = cc.Row
            If rr >= 3 And rr <= PZ_LAST_ROW Then
                If InStr(1, cmt.Text, "Hidden From Generation", vbTextCompare) > 0 Then
                    If Trim$(CStr(Me.Cells(rr, "A").Value)) = "" Or cc.Interior.Pattern <> xlNone Then
                        kill.Add cc
                    End If
                End If
            End If
        End If
    Next cmt
    For k = 1 To kill.Count
        On Error Resume Next
        kill(k).Comment.Delete
        On Error GoTo 0
    Next k
    'PASS B: re-apply the hide flag for OPEN cells that still carry a token; drop the
    'token where the cell is now closed or the row is empty.
    toks = Array("S1", "S2", "S3", "S4", "D1", "D2", "D3", "D4", _
                 "SG1", "SG2", "SG3", "SG4", "DG1", "DG2", "DG3", "DG4")
    For r = 3 To PZ_LAST_ROW
        csv = CStr(Me.Cells(r, PZ_HIDDENTERMS_COL).Value)
        If Len(PZ_CsvNorm(csv)) = 0 Then GoTo NextRow
        For i = LBound(toks) To UBound(toks)
            If PZ_CsvHas(csv, CStr(toks(i))) Then
                Set c = PZ_TermCellFromToken(r, CStr(toks(i)))
                If Not c Is Nothing Then
                    If Trim$(CStr(Me.Cells(r, "A").Value)) <> "" And c.Interior.Pattern = xlNone Then
                        Call PZ_ApplyTermHideVisual(c, True)
                    Else
                        csv = PZ_CsvRemove(csv, CStr(toks(i)))
                    End If
                End If
            End If
        Next i
        Me.Cells(r, PZ_HIDDENTERMS_COL).Value = csv
NextRow:
    Next r
End Sub

'Strip hidden control columns from rows touched by a paste/fill so the rebuild
'regenerates them from the visible data (prevents copied auto-markers, stale
'hidden-term flags and wire-label modes from corrupting the layout).
Private Sub PZ_SanitizePastedRows(ByVal Target As Range)
    Dim rr As Range, r As Long, rmin As Long, rmax As Long
    rmin = PZ_LAST_ROW + 1: rmax = 2
    For Each rr In Target.Rows
        r = rr.Row
        If r >= 3 And r <= PZ_LAST_ROW Then
            If r < rmin Then rmin = r
            If r > rmax Then rmax = r
        End If
    Next rr
    If rmax < rmin Then Exit Sub
    For r = rmin To rmax
        Me.Cells(r, PZ_MARKER_COL).ClearContents       'auto-row marker -> re-derived
        Me.Cells(r, PZ_HIDDENTERMS_COL).ClearContents  'hidden-terms store -> reset
    Me.Range("BE" & r & ":BH" & r).ClearContents   'WL1-4 modes -> back to auto
    Next r
End Sub

'A row holds no loop data (cleared/deleted): no conduit, no wire count, no symbol.
Private Function PZ_RowIsEmpty(ByVal r As Long) As Boolean
    PZ_RowIsEmpty = (Trim$(CStr(Me.Cells(r, "A").Value)) = "" _
        And Trim$(CStr(Me.Cells(r, "B").Value)) = "" _
        And Trim$(CStr(Me.Cells(r, SSYM).Value)) = "" _
        And Not PZ_DSymPresent(r))
End Function

'Wipe a row's hidden-cell CSV token store and any "Hidden From Generation"
'comments left on its Term / Tag cells.
Private Sub PZ_ClearRowHideFlags(ByVal r As Long)
    Dim toks As Variant, i As Long, c As Range
    Me.Cells(r, PZ_HIDDENTERMS_COL).ClearContents
    toks = Array("S1", "S2", "S3", "S4", "D1", "D2", "D3", "D4", _
                 "SG1", "SG2", "SG3", "SG4", "DG1", "DG2", "DG3", "DG4")
    For i = LBound(toks) To UBound(toks)
        Set c = PZ_TermCellFromToken(r, CStr(toks(i)))
        If Not c Is Nothing Then
            On Error Resume Next
            If Not c.Comment Is Nothing Then
                If InStr(1, c.Comment.Text, "Hidden From Generation", vbTextCompare) > 0 Then c.Comment.Delete
            End If
            On Error GoTo 0
        End If
    Next i
End Sub

Private Function PZ_CsvNorm(ByVal csv As String) As String
    PZ_CsvNorm = Replace(Trim$(CStr(csv)), " ", "")
End Function

Private Function PZ_CsvHas(ByVal csv As String, ByVal tok As String) As Boolean
    PZ_CsvHas = (InStr(1, "," & PZ_CsvNorm(csv) & ",", "," & tok & ",", vbTextCompare) > 0)
End Function

Private Function PZ_CsvAdd(ByVal csv As String, ByVal tok As String) As String
    Dim base As String
    base = PZ_CsvNorm(csv)
    If PZ_CsvHas(base, tok) Then PZ_CsvAdd = base: Exit Function
    If Len(base) = 0 Then
        PZ_CsvAdd = tok
    Else
        PZ_CsvAdd = base & "," & tok
    End If
End Function

Private Function PZ_CsvRemove(ByVal csv As String, ByVal tok As String) As String
    Dim parts As Variant, i As Long, out As String
    parts = Split(PZ_CsvNorm(csv), ",")
    For i = LBound(parts) To UBound(parts)
        If Len(parts(i)) > 0 And StrComp(CStr(parts(i)), tok, vbTextCompare) <> 0 Then
            If Len(out) = 0 Then out = CStr(parts(i)) Else out = out & "," & CStr(parts(i))
        End If
    Next i
    PZ_CsvRemove = out
End Function


Private Sub PZ_MakeHeaderBlackClear(ByVal rowNum As Long, ByVal headerText As String)
    Dim targetCell As Range
    Set targetCell = PZ_HeaderCell(rowNum, headerText)
    If targetCell Is Nothing Then Exit Sub
    With targetCell
        .ClearContents
        .Interior.Color = RGB(PZ_GREY_R, PZ_GREY_G, PZ_GREY_B)
        .Font.Color = RGB(PZ_GREY_R, PZ_GREY_G, PZ_GREY_B)
    End With
End Sub

Private Function PZ_IsSpare(ByVal s As String) As Boolean
    PZ_IsSpare = (InStr(1, CStr(s), "Spare", vbTextCompare) > 0)
End Function

Private Function PZ_MaxTags(ByVal blockName As String) As Long
    Dim res As Variant
    PZ_MaxTags = 99   'unknown block -> no restriction
    blockName = Trim$(CStr(blockName))
    If Len(blockName) = 0 Then Exit Function
    On Error Resume Next
    res = Application.WorksheetFunction.VLookup(blockName, ThisWorkbook.Names("BlockTagCount").RefersToRange, 2, False)
    On Error GoTo 0
    If IsNumeric(res) Then PZ_MaxTags = CLng(res)
End Function


'================= Grow-by-one rows + auto-width (added) =================

'Last data row (3..PZ_LAST_ROW) that has anything in Conduit (A), Wire Loop (B)
'or S Symbol (F). Returns 2 when nothing is filled.
Private Function PZ_LastDataRow() As Long
    Dim r As Long, last As Long
    last = 2
    For r = 3 To PZ_LAST_ROW
        If Trim$(CStr(Me.Cells(r, "A").Value)) <> "" _
           Or Trim$(CStr(Me.Cells(r, "B").Value)) <> "" _
           Or Trim$(CStr(Me.Cells(r, SSYM).Value)) <> "" Then
            last = r
        End If
    Next r
    PZ_LastDataRow = last
End Function

'Show data rows up to (last filled + 1); hide the rest of the pool.
Private Sub PZ_ApplyRowVisibility()
    Dim postLast As Long, showTo As Long
    postLast = PZ_LastDataRow()
    showTo = postLast + PZ_CHUNK          'always keep a chunk of ready rows below the data
    If showTo < 3 + PZ_CHUNK Then showTo = 3 + PZ_CHUNK
    If showTo > PZ_LAST_ROW Then showTo = PZ_LAST_ROW
    Me.Rows("3:" & showTo).Hidden = False
    If showTo < PZ_LAST_ROW Then Me.Rows((showTo + 1) & ":" & PZ_LAST_ROW).Hidden = True
End Sub

'Auto-fit each visible column to its widest visible cell (header + data),
'standard Excel "AutoFit Column Width".
Public Sub PZ_AutoWidth()
    'Fit each visible column to its content. Wire-label columns (53-56) are sized
    'EXPLICITLY by PZ_FitWireLabels: Columns.AutoFit is unreliable for them (it measures
    'the formula result, which is often empty/stale at autofit time), so a long label
    'would overflow past the hidden control columns and show as stray "background" text.
    Dim c As Long
    For c = 1 To 58
        If (c < 53 Or c > 56) And Not Me.Columns(c).Hidden Then Me.Columns(c).AutoFit
    Next c
    Call PZ_FitWireLabels
End Sub

Private Sub PZ_FitWireLabels()
    'Size each Wire Label column (53-56) to its longest value so a label stays INSIDE
    'its own column instead of spilling across the hidden control columns and showing as
    'stray "background" text further right. Width is set directly from the measured text
    'length (reliable, unlike Columns.AutoFit here). Labels are recalculated first since
    'we are usually in manual-calc during a rebuild/edit.
    Dim c As Long, r As Long, maxLen As Long, s As String, lastR As Long, ldr As Long
    lastR = mMaxProc
    'Also measure through the last row that actually holds data: on a fast-path edit the
    'processed extent (mMaxProc) can lag the row just typed, so its (longer) label was not
    'measured and the column never widened -- the "sometimes it doesn't lengthen" case.
    ldr = PZ_LastDataRow()
    If ldr > lastR Then lastR = ldr
    If lastR < 3 Or lastR > PZ_LAST_ROW Then lastR = PZ_LAST_ROW
    On Error Resume Next
    'Full recalc so the wire-label formulas AND every cell they reference are current
    'before we measure -- recalculating only the label cells left them reading stale
    'precedents (empty) during an edit, so the column never widened.
    Application.Calculate
    On Error GoTo 0
    For c = 53 To 56
        If Not Me.Columns(c).Hidden Then
            maxLen = Len(CStr(Me.Cells(2, c).Value))   'header
            For r = 3 To lastR
                s = CStr(Me.Cells(r, c).Value)
                If Len(s) > maxLen Then maxLen = Len(s)
            Next r
            If maxLen < 9 Then maxLen = 9
            If maxLen > 80 Then maxLen = 80
            Me.Columns(c).ColumnWidth = maxLen + 3
        End If
    Next c
End Sub


'================= Wire Ct / instrument helpers (added) =================

'Valid Wire Ct values: 1,2,3,4,6,8.
Private Function PZ_ValidLoop(ByVal lc As Long) As Boolean
    PZ_ValidLoop = (lc = 1 Or lc = 2 Or lc = 3 Or lc = 4 Or lc = 6 Or lc = 8)
End Function

'Extra auto-rows an instrument generates from its terminal count.
Private Function PZ_ExtraRows(ByVal tc As Long) As Long
    Select Case tc
        Case 4: PZ_ExtraRows = 1
        Case 6: PZ_ExtraRows = 2
        Case 8: PZ_ExtraRows = 3
        Case Else: PZ_ExtraRows = 0
    End Select
End Function

'Extra rows for a row, from whichever of S/D symbol is the larger instrument.
Private Function PZ_RowCopies(ByVal rowNum As Long) As Long
    Dim sc As Long, dc As Long, dsym As Range, capTerm As Long, wireCt As Long, capWire As Long
    sc = PZ_ExtraRows(PZ_TermCount(CStr(Me.Cells(rowNum, SSYM).Value)))
    Set dsym = PZ_HeaderCell(rowNum, "D Symbol")
    If Not dsym Is Nothing Then dc = PZ_ExtraRows(PZ_TermCount(CStr(dsym.Value)))
    If sc >= dc Then capTerm = sc Else capTerm = dc
    'Never generate more instrument rows than the WIRE COUNT needs: each row is one pair
    'of wires (2 terminals), so rows = Ceil(WireCt / 2) and extra = rows - 1. A 4-Term
    'instrument on a Wire Ct 2 conduit therefore gets NO continuation row.
    wireCt = 0
    If IsNumeric(Me.Cells(rowNum, "B").Value) Then wireCt = CLng(Me.Cells(rowNum, "B").Value)
    If wireCt < 1 Then
        capWire = capTerm            'no wire count yet -> fall back to terminal capacity
    Else
        capWire = ((wireCt + 1) \ 2) - 1
        If capWire < 0 Then capWire = 0
    End If
    If capTerm <= capWire Then PZ_RowCopies = capTerm Else PZ_RowCopies = capWire
End Function

'Amber "unsupported symbol" warning with a hover note, app-style.
Private Sub PZ_SetWarn(ByVal cell As Range, ByVal onWarn As Boolean)
    If cell Is Nothing Then Exit Sub
    On Error Resume Next
    cell.ClearComments
    On Error GoTo 0
    If onWarn Then
        cell.Interior.Color = RGB(255, 235, 156)
        cell.Font.Color = RGB(156, 87, 0)
        On Error Resume Next
        cell.AddComment "This symbol isn't supported."
        cell.Comment.Visible = False
        On Error GoTo 0
    End If
End Sub

'True when the D Symbol cell on this row is filled.
Private Function PZ_DSymPresent(ByVal rowNum As Long) As Boolean
    Dim c As Range
    Set c = PZ_HeaderCell(rowNum, "D Symbol")
    If c Is Nothing Then Exit Function
    PZ_DSymPresent = (Len(Trim$(CStr(c.Value))) > 0)
End Function

















