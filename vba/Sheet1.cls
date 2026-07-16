VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Sheet1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

    Private Const DROPDOWN_RANGE As String = "J2:J101"

'Grow-by-one row pool for ConduitIndex: data rows 2..CI_LAST_ROW.
Private Const CI_LAST_ROW As Long = 101

Private Const LIST_SHEET As String = "Ref Documents & Deviations"

Private Const LIST_RANGE As String = "A3:A20"

Private Const DISPLAY_SHEET As String = "Document Manager"
Private Const DISPLAY_START_CELL As String = "XFD3"
Private Const DISPLAY_NAME As String = "RefDocDropdownDisplay"

Private Const SEP As String = ", "

'--- Deviations Notes multi-select (col K), mirrors Ref Docs but NO self-order ---
Private Const DEV_DROPDOWN_RANGE As String = "K2:K101"
Private Const DEV_LIST_RANGE As String = "E3:E20"      'Ref Documents col E = "Deviations Notes #"
Private Const DEV_DISPLAY_START_CELL As String = "XFC3"
Private Const DEV_DISPLAY_NAME As String = "DevNotesDropdownDisplay"

Private mPreviousCellAddress As String
Private mPreviousCellValue As String

'--- Multi-level custom undo / redo (mirrors FillIndex / Sheet2) --------------
'The Worksheet_Change rebuild wipes Excel's native undo, so we keep our own
'undo/redo stacks of data-range snapshots. Ctrl+Z / Ctrl+Y are bound (in modUndo)
'while this sheet is active and routed here by codename.
    Private Const CI_UNDO_RANGE As String = "A2:K101"
Private Const CI_UNDO_MAX As Long = 50
Private mCommitted As Variant     'state as of the last completed refresh
Private mUndo As Collection       'undo stack (older states, oldest first)
Private mRedo As Collection       'redo stack

Public Sub CI_EnsureValidations()
    On Error Resume Next
    'Conduit Type dropdown (col I) -- sized to whatever list Document Manager col B
    'actually holds right now, so adding/removing types never goes stale again.
    Dim dmLast As Long
    dmLast = ThisWorkbook.Worksheets("Document Manager").Cells(ThisWorkbook.Worksheets("Document Manager").Rows.Count, 2).End(xlUp).Row
    If dmLast < 2 Then dmLast = 2
    With Me.Range("I2:I" & CI_LAST_ROW).Validation
        .Delete
        .Add Type:=xlValidateList, AlertStyle:=xlValidAlertStop, Operator:=xlBetween, Formula1:="='Document Manager'!$B$2:$B$" & dmLast
        .IgnoreBlank = True
        .InCellDropdown = True
        .ShowError = False
    End With
    'Ref Documents multi-select dropdown (col J) via the existing helper
    ApplyDropdownValidation
    'Deviations Notes multi-select dropdown (col K)
    DevApplyDropdownValidation
    'Keep the data area in Text format so entries never auto-convert
    Me.Range("A2:K" & CI_LAST_ROW).NumberFormat = "@"
End Sub


Public Sub CI_EnsureHeaders()
    On Error Resume Next
    If Len(Trim$(CStr(Me.Cells(1, 1).Value))) > 0 And Len(Trim$(CStr(Me.Cells(1, 10).Value))) > 0 Then Exit Sub
    Application.EnableEvents = False
    Me.Cells(1, 1).Value = "Conduit Name"

    Me.Cells(1, 2).Value = "Source  Name 1 "
    Me.Cells(1, 3).Value = "Source Name 2"
    Me.Cells(1, 4).Value = "Source Name 3"
    Me.Cells(1, 5).Value = "Destination Name 1"
    Me.Cells(1, 6).Value = "Destination Name 2"
    Me.Cells(1, 7).Value = "Destination Name 3"
    Me.Cells(1, 8).Value = "Conduit Size"
    Me.Cells(1, 9).Value = "Conduit Type"
    Me.Cells(1, 10).Value = "Ref Documents"
    Me.Cells(1, 11).Value = "Deviations Notes"
    Application.EnableEvents = True
End Sub


Private Function SelectedMark() As String
    SelectedMark = ChrW(10003) & " "
End Function

Private Function StripSelectedMark(ByVal textValue As String) As String
    textValue = Trim(CStr(textValue))

    If Left$(textValue, Len(SelectedMark())) = SelectedMark() Then
        textValue = Mid$(textValue, Len(SelectedMark()) + 1)
    End If

    StripSelectedMark = Trim(textValue)
End Function

Private Function CleanSelectedText(ByVal selectedText As String) As String

    Dim items As Variant
    Dim item As Variant
    Dim cleanItem As String
    Dim resultText As String

    selectedText = Trim(CStr(selectedText))

    If Len(selectedText) = 0 Then Exit Function

    items = Split(selectedText, SEP)

    For Each item In items

        cleanItem = StripSelectedMark(CStr(item))

        If Len(cleanItem) > 0 Then
            If Len(resultText) = 0 Then
                resultText = cleanItem
            Else
                resultText = resultText & SEP & cleanItem
            End If
        End If

    Next item

    CleanSelectedText = resultText

End Function

Private Function ValueAlreadySelected(ByVal selectedText As String, ByVal checkValue As String) As Boolean

    Dim items As Variant
    Dim item As Variant
    Dim cleanItem As String

    selectedText = Trim(CStr(selectedText))
    checkValue = StripSelectedMark(CStr(checkValue))

    If Len(selectedText) = 0 Then Exit Function
    If Len(checkValue) = 0 Then Exit Function

    items = Split(selectedText, SEP)

    For Each item In items

        cleanItem = StripSelectedMark(CStr(item))

        If StrComp(cleanItem, checkValue, vbTextCompare) = 0 Then
            ValueAlreadySelected = True
            Exit Function
        End If

    Next item

End Function

Private Function RemoveSelection(ByVal selectedText As String, ByVal removeValue As String) As String

    Dim items As Variant
    Dim item As Variant
    Dim resultText As String
    Dim cleanItem As String

    selectedText = Trim(CStr(selectedText))
    removeValue = StripSelectedMark(CStr(removeValue))

    If Len(selectedText) = 0 Then Exit Function
    If Len(removeValue) = 0 Then
        RemoveSelection = selectedText
        Exit Function
    End If

    items = Split(selectedText, SEP)

    For Each item In items

        cleanItem = StripSelectedMark(CStr(item))

        If Len(cleanItem) > 0 Then
            If StrComp(cleanItem, removeValue, vbTextCompare) <> 0 Then
                If Len(resultText) = 0 Then
                    resultText = cleanItem
                Else
                    resultText = resultText & SEP & cleanItem
                End If
            End If
        End If

    Next item

    RemoveSelection = resultText

End Function

Private Function OrderSelectionsByMainList(ByVal selectedText As String) As String

    Dim wsList As Worksheet
    Dim sourceRng As Range
    Dim cell As Range
    Dim rawValue As String
    Dim resultText As String

    selectedText = CleanSelectedText(selectedText)

    If Len(selectedText) = 0 Then Exit Function

    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set sourceRng = wsList.Range(LIST_RANGE)

    For Each cell In sourceRng.Cells

        rawValue = Trim(CStr(cell.Value))

        If Len(rawValue) > 0 Then
            If ValueAlreadySelected(selectedText, rawValue) Then
                If Len(resultText) = 0 Then
                    resultText = rawValue
                Else
                    resultText = resultText & SEP & rawValue
                End If
            End If
        End If

    Next cell

    OrderSelectionsByMainList = resultText

End Function

Private Function ValueExistsInMainList(ByVal checkValue As String) As Boolean

    Dim wsList As Worksheet
    Dim sourceRng As Range
    Dim cell As Range

    checkValue = StripSelectedMark(checkValue)

    If Len(checkValue) = 0 Then Exit Function

    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set sourceRng = wsList.Range(LIST_RANGE)

    For Each cell In sourceRng.Cells
        If StrComp(Trim(CStr(cell.Value)), checkValue, vbTextCompare) = 0 Then
            ValueExistsInMainList = True
            Exit Function
        End If
    Next cell

End Function

Private Function GetDisplayRange() As Range

    Dim wsList As Worksheet
    Dim wsDisplay As Worksheet
    Dim sourceRng As Range

    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set wsDisplay = ThisWorkbook.Worksheets(DISPLAY_SHEET)
    Set sourceRng = wsList.Range(LIST_RANGE)

    Set GetDisplayRange = wsDisplay.Range(DISPLAY_START_CELL).Resize(sourceRng.Rows.Count, 1)

End Function

Private Function SheetRangeRef(ByVal ws As Worksheet, ByVal rng As Range) As String
    SheetRangeRef = "='" & Replace(ws.Name, "'", "''") & "'!" & rng.Address
End Function

Private Sub HideDisplayColumn()

    Dim wsDisplay As Worksheet
    Dim displayRng As Range

    On Error Resume Next

    Set wsDisplay = ThisWorkbook.Worksheets(DISPLAY_SHEET)
    Set displayRng = GetDisplayRange()

    wsDisplay.Columns(displayRng.Column).Hidden = True

    On Error GoTo 0

End Sub

Private Sub BuildDropdownDisplayList(ByVal selectedText As String)

    Dim wsList As Worksheet
    Dim sourceRng As Range
    Dim displayRng As Range
    Dim i As Long
    Dim rawValue As String

    selectedText = CleanSelectedText(selectedText)

    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set sourceRng = wsList.Range(LIST_RANGE)
    Set displayRng = GetDisplayRange()

    displayRng.ClearContents

    For i = 1 To sourceRng.Rows.Count

        rawValue = Trim(CStr(sourceRng.Cells(i, 1).Value))

        If Len(rawValue) > 0 Then
            If ValueAlreadySelected(selectedText, rawValue) Then
                displayRng.Cells(i, 1).Value = SelectedMark() & rawValue
            Else
                displayRng.Cells(i, 1).Value = rawValue
            End If
        End If

    Next i

    HideDisplayColumn

End Sub

Private Sub ApplyDropdownValidation()

    Dim wsDisplay As Worksheet
    Dim displayRng As Range

    Set wsDisplay = ThisWorkbook.Worksheets(DISPLAY_SHEET)
    Set displayRng = GetDisplayRange()

    On Error Resume Next
    ThisWorkbook.Names(DISPLAY_NAME).Delete
    On Error GoTo 0

    ThisWorkbook.Names.Add _
        Name:=DISPLAY_NAME, _
        RefersTo:=SheetRangeRef(wsDisplay, displayRng)

    With Me.Range(DROPDOWN_RANGE).Validation
        .Delete
    End With

    Me.Range(DROPDOWN_RANGE).Validation.Add _
        Type:=xlValidateList, _
        AlertStyle:=xlValidAlertStop, _
        Operator:=xlBetween, _
        Formula1:="=" & DISPLAY_NAME

    With Me.Range(DROPDOWN_RANGE).Validation
        .IgnoreBlank = True
        .InCellDropdown = True
        .ShowError = False
    End With

    HideDisplayColumn

End Sub

Private Sub Worksheet_SelectionChange(ByVal Target As Range)

    'One-time: capture the initial committed state so the first edit is undoable.
    If IsEmpty(mCommitted) Then
        On Error Resume Next
        mCommitted = Me.Range(CI_UNDO_RANGE).Formula
        On Error GoTo 0
    End If

    On Error GoTo SafeExit

    If Target.CountLarge > 1 Then Exit Sub

    If Not Intersect(Target, Me.Range(DROPDOWN_RANGE)) Is Nothing Then
        mPreviousCellAddress = Target.Address(False, False)
        mPreviousCellValue = CleanSelectedText(CStr(Target.Value))
        Application.EnableEvents = False
        BuildDropdownDisplayList mPreviousCellValue
        ApplyDropdownValidation
    ElseIf Not Intersect(Target, Me.Range(DEV_DROPDOWN_RANGE)) Is Nothing Then
        mPreviousCellAddress = Target.Address(False, False)
        mPreviousCellValue = CleanSelectedText(CStr(Target.Value))
        Application.EnableEvents = False
        DevBuildDropdownDisplayList mPreviousCellValue
        DevApplyDropdownValidation
    End If

SafeExit:
    Application.EnableEvents = True

End Sub

Private Sub CI_HandleRefDocChange(ByVal Target As Range)

    Dim oldVal As String
    Dim newVal As String
    Dim combinedVal As String
    Dim validationType As Long

    On Error GoTo SafeExit

    If Target.CountLarge > 1 Then Exit Sub
    If Intersect(Target, Me.Range(DROPDOWN_RANGE)) Is Nothing Then Exit Sub

    On Error Resume Next
    validationType = Target.Validation.Type

    If Err.Number <> 0 Then
        Err.Clear
        Exit Sub
    End If

    On Error GoTo SafeExit

    If validationType <> xlValidateList Then Exit Sub

    newVal = StripSelectedMark(CStr(Target.Value))

    Application.EnableEvents = False

    If mPreviousCellAddress = Target.Address(False, False) Then
        oldVal = mPreviousCellValue
    Else
        oldVal = ""
    End If

    If Len(newVal) = 0 Then
        Target.Value = ""
        mPreviousCellAddress = Target.Address(False, False)
        mPreviousCellValue = ""
        BuildDropdownDisplayList ""
        ApplyDropdownValidation
        GoTo SafeExit
    End If

    If ValueExistsInMainList(newVal) = False Then
        Target.Value = oldVal
        BuildDropdownDisplayList oldVal
        ApplyDropdownValidation
        GoTo SafeExit
    End If

    If Len(Trim(oldVal)) = 0 Then

        ' Nothing was selected before, so add the new value
        combinedVal = newVal

    ElseIf ValueAlreadySelected(oldVal, newVal) Then

        ' Value was already selected, so remove it
        combinedVal = RemoveSelection(oldVal, newVal)

    Else

        ' Value was not selected yet, so add it
        combinedVal = oldVal & SEP & newVal

    End If

    Target.Value = OrderSelectionsByMainList(combinedVal)

    mPreviousCellAddress = Target.Address(False, False)
    mPreviousCellValue = CStr(Target.Value)

    BuildDropdownDisplayList CStr(Target.Value)
    ApplyDropdownValidation

SafeExit:
    Application.EnableEvents = True

End Sub


Private Function DevGetDisplayRange() As Range
    Dim wsList As Worksheet
    Dim wsDisplay As Worksheet
    Dim sourceRng As Range
    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set wsDisplay = ThisWorkbook.Worksheets(DISPLAY_SHEET)
    Set sourceRng = wsList.Range(DEV_LIST_RANGE)
    Set DevGetDisplayRange = wsDisplay.Range(DEV_DISPLAY_START_CELL).Resize(sourceRng.Rows.Count, 1)
End Function

Private Sub DevHideDisplayColumn()
    Dim wsDisplay As Worksheet
    Dim displayRng As Range
    On Error Resume Next
    Set wsDisplay = ThisWorkbook.Worksheets(DISPLAY_SHEET)
    Set displayRng = DevGetDisplayRange()
    wsDisplay.Columns(displayRng.Column).Hidden = True
    On Error GoTo 0
End Sub

Private Function DevValueExistsInMainList(ByVal checkValue As String) As Boolean
    Dim wsList As Worksheet
    Dim sourceRng As Range
    Dim cell As Range
    checkValue = StripSelectedMark(checkValue)
    If Len(checkValue) = 0 Then Exit Function
    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set sourceRng = wsList.Range(DEV_LIST_RANGE)
    For Each cell In sourceRng.Cells
        If StrComp(Trim(CStr(cell.Value)), checkValue, vbTextCompare) = 0 Then
            DevValueExistsInMainList = True
            Exit Function
        End If
    Next cell
End Function

Private Sub DevBuildDropdownDisplayList(ByVal selectedText As String)
    Dim wsList As Worksheet
    Dim sourceRng As Range
    Dim displayRng As Range
    Dim i As Long
    Dim rawValue As String
    selectedText = CleanSelectedText(selectedText)
    Set wsList = ThisWorkbook.Worksheets(LIST_SHEET)
    Set sourceRng = wsList.Range(DEV_LIST_RANGE)
    Set displayRng = DevGetDisplayRange()
    displayRng.ClearContents
    For i = 1 To sourceRng.Rows.Count
        rawValue = Trim(CStr(sourceRng.Cells(i, 1).Value))
        If Len(rawValue) > 0 Then
            If ValueAlreadySelected(selectedText, rawValue) Then
                displayRng.Cells(i, 1).Value = SelectedMark() & rawValue
            Else
                displayRng.Cells(i, 1).Value = rawValue
            End If
        End If
    Next i
    DevHideDisplayColumn
End Sub

Private Sub DevApplyDropdownValidation()
    Dim wsDisplay As Worksheet
    Dim displayRng As Range
    Set wsDisplay = ThisWorkbook.Worksheets(DISPLAY_SHEET)
    Set displayRng = DevGetDisplayRange()
    On Error Resume Next
    ThisWorkbook.Names(DEV_DISPLAY_NAME).Delete
    On Error GoTo 0
    ThisWorkbook.Names.Add _
        Name:=DEV_DISPLAY_NAME, _
        RefersTo:=SheetRangeRef(wsDisplay, displayRng)
    With Me.Range(DEV_DROPDOWN_RANGE).Validation
        .Delete
    End With
    Me.Range(DEV_DROPDOWN_RANGE).Validation.Add _
        Type:=xlValidateList, _
        AlertStyle:=xlValidAlertStop, _
        Operator:=xlBetween, _
        Formula1:="=" & DEV_DISPLAY_NAME
    With Me.Range(DEV_DROPDOWN_RANGE).Validation
        .IgnoreBlank = True
        .InCellDropdown = True
        .ShowError = False
    End With
    DevHideDisplayColumn
End Sub

Private Sub CI_HandleDevChange(ByVal Target As Range)
    Dim oldVal As String
    Dim newVal As String
    Dim combinedVal As String
    Dim validationType As Long
    On Error GoTo SafeExit
    If Target.CountLarge > 1 Then Exit Sub
    If Intersect(Target, Me.Range(DEV_DROPDOWN_RANGE)) Is Nothing Then Exit Sub
    On Error Resume Next
    validationType = Target.Validation.Type
    If Err.Number <> 0 Then
        Err.Clear
        Exit Sub
    End If
    On Error GoTo SafeExit
    If validationType <> xlValidateList Then Exit Sub
    newVal = StripSelectedMark(CStr(Target.Value))
    Application.EnableEvents = False
    If mPreviousCellAddress = Target.Address(False, False) Then
        oldVal = mPreviousCellValue
    Else
        oldVal = ""
    End If
    If Len(newVal) = 0 Then
        Target.Value = ""
        mPreviousCellAddress = Target.Address(False, False)
        mPreviousCellValue = ""
        DevBuildDropdownDisplayList ""
        DevApplyDropdownValidation
        GoTo SafeExit
    End If
    If DevValueExistsInMainList(newVal) = False Then
        Target.Value = oldVal
        DevBuildDropdownDisplayList oldVal
        DevApplyDropdownValidation
        GoTo SafeExit
    End If
    If Len(Trim(oldVal)) = 0 Then
        combinedVal = newVal
    ElseIf ValueAlreadySelected(oldVal, newVal) Then
        combinedVal = RemoveSelection(oldVal, newVal)
    Else
        combinedVal = oldVal & SEP & newVal
    End If
    ' NO self-order: keep the order the user picked (just strip the checkmarks)
    Target.Value = CleanSelectedText(combinedVal)
    mPreviousCellAddress = Target.Address(False, False)
    mPreviousCellValue = CStr(Target.Value)
    DevBuildDropdownDisplayList CStr(Target.Value)
    DevApplyDropdownValidation
SafeExit:
    Application.EnableEvents = True
End Sub


'================= Grow-by-one rows + auto-width (added) =================

Private Sub Worksheet_Change(ByVal Target As Range)

    Dim inData As Boolean

    On Error GoTo SafeExit
    Call CI_EnsureHeaders   'self-heal row-1 headers if deleted

        inData = Not (Intersect(Target, Me.Range("A2:K" & CI_LAST_ROW)) Is Nothing)

    'Record the pre-change state on the undo stack (data-area edits only).
    If inData Then Call CI_RecordChange

    'Ref Documents multi-select handling
    If Not Intersect(Target, Me.Range(DROPDOWN_RANGE)) Is Nothing Then
        If Target.CountLarge = 1 Then Call CI_HandleRefDocChange(Target)
    End If

    'Deviations Notes multi-select handling
    If Not Intersect(Target, Me.Range(DEV_DROPDOWN_RANGE)) Is Nothing Then
        If Target.CountLarge = 1 Then Call CI_HandleDevChange(Target)
    End If

    'Grow-by-one for any edit in the data area
    If inData Then
        Application.EnableEvents = False
        Call CI_RefreshRows
        Call CI_EnsureValidations   'self-heal Type + Ref Doc dropdowns
        Application.EnableEvents = True
        'Snapshot the committed state for the next undo.
        On Error Resume Next
        mCommitted = Me.Range(CI_UNDO_RANGE).Formula
        On Error GoTo 0
    End If

SafeExit:
    Application.EnableEvents = True

End Sub

'Last data row (2..CI_LAST_ROW) with anything in columns A..K. Returns 1 when empty.
Private Function CI_LastDataRow() As Long
    Dim r As Long, c As Long, last As Long
    Dim hasData As Boolean
    last = 1
    For r = 2 To CI_LAST_ROW
        hasData = False
        For c = 1 To 11
            If Trim$(CStr(Me.Cells(r, c).Value)) <> "" Then
                hasData = True
                Exit For
            End If
        Next c
        If hasData Then last = r
    Next r
    CI_LastDataRow = last
End Function

Private Sub CI_RefreshRows()
    'Show the ENTIRE data area -- NO row hiding. Hiding the empty rows created a wall:
    'Enter or the arrow keys, on reaching the last visible row, jumped PAST the hidden
    'block into a far "dead" row. With every row visible the cursor moves normally into
    'the blank rows right below the data (Enter and arrows both behave).
    On Error Resume Next
    Me.Rows("2:" & CI_LAST_ROW).EntireRow.Hidden = False
    On Error GoTo 0
    Call CI_AutoWidth(CI_LAST_ROW)
End Sub

Private Sub CI_AutoWidth(ByVal showTo As Long)
    Dim c As Long
    For c = 1 To 12
        If Not Me.Columns(c).Hidden Then
            Me.Columns(c).AutoFit
        End If
    Next c
End Sub


'================= Multi-level undo / redo =================

'Push the pre-change committed state onto the undo stack; a fresh edit clears redo.
Private Sub CI_RecordChange()
    On Error Resume Next
    If mUndo Is Nothing Then Set mUndo = New Collection
    If mRedo Is Nothing Then Set mRedo = New Collection
    If Not IsEmpty(mCommitted) Then
        mUndo.Add mCommitted
        Do While mUndo.Count > CI_UNDO_MAX
            mUndo.Remove 1
        Loop
        Set mRedo = New Collection
    End If
    On Error GoTo 0
End Sub

'Ctrl+Z on ConduitIndex. Public so the modUndo key shim can reach it.
Public Sub CI_DoUndo()
    On Error Resume Next
    If mUndo Is Nothing Then Exit Sub
    If mUndo.Count = 0 Then Exit Sub
    If mRedo Is Nothing Then Set mRedo = New Collection
    Dim snap As Variant
    snap = mUndo(mUndo.Count)
    mUndo.Remove mUndo.Count
    If Not IsEmpty(mCommitted) Then mRedo.Add mCommitted
    Call CI_RestoreSnapshot(snap)
End Sub

'Ctrl+Y on ConduitIndex.
Public Sub CI_DoRedo()
    On Error Resume Next
    If mRedo Is Nothing Then Exit Sub
    If mRedo.Count = 0 Then Exit Sub
    If mUndo Is Nothing Then Set mUndo = New Collection
    Dim snap As Variant
    snap = mRedo(mRedo.Count)
    mRedo.Remove mRedo.Count
    If Not IsEmpty(mCommitted) Then mUndo.Add mCommitted
    Call CI_RestoreSnapshot(snap)
End Sub

Private Sub CI_RestoreSnapshot(ByVal snap As Variant)
    If IsEmpty(snap) Then Exit Sub
    On Error GoTo done
    Application.EnableEvents = False
    Application.ScreenUpdating = False
    Me.Range(CI_UNDO_RANGE).Formula = snap
    Call CI_RefreshRows
    mCommitted = Me.Range(CI_UNDO_RANGE).Formula
done:
    Application.ScreenUpdating = True
    Application.EnableEvents = True
End Sub






