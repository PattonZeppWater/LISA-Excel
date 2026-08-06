==============================================================
  LISA  v1.0.00  -  IDP Generation build (for testing)
==============================================================

WHAT THIS IS
  LISA is a desktop app that generates AutoCAD electrical IDP
  drawings from the included Excel workbook. This is a shared
  build for you to continue testing the IDP Generation tool.

------------------------------------------------------------
IMPORTANT - IF YOU DOWNLOADED / UNZIPPED THIS  (read first)
------------------------------------------------------------
  Windows tags anything that arrives by email / download / zip
  as "from the internet." That makes Excel open the workbook in
  PROTECTED VIEW (read-only - so Ctrl+V, Ctrl+Z and even typing
  are disabled) and BLOCKS its macros. If the workbook's keyboard
  shortcuts or self-fixing behavior "don't work," this is why.
  Do ONE of these BEFORE opening the workbook:

    EASIEST: put this whole folder in   C:\trusted
             (a trusted location - Excel then opens it fully
              editable, with macros on, and no warnings).

    OR: right-click the .zip BEFORE unzipping -> Properties ->
        tick "Unblock" -> OK, then unzip (clears the mark from
        every file inside).

    OR: in Excel click "Enable Editing" on the yellow PROTECTED
        VIEW bar, and "Enable Content" if a macro bar appears.

------------------------------------------------------------
BEFORE YOU START  (one-time prerequisites)
------------------------------------------------------------
  1. Windows 10 or 11.
  2. Python 3.12 - you do NOT need to install this yourself.
       "SETUP - Run First.bat" checks for it and, if it's not
       already on your PC, downloads and installs it for you
       automatically (per-user, no admin needed). Just make sure
       you have an internet connection when you run SETUP.
  3. AutoCAD (full AutoCAD, not just a viewer) installed and
       OPEN. LISA drives AutoCAD to draw the DWGs, so AutoCAD
       must be running when you click Generate.
  4. Microsoft Edge WebView2 Runtime - already on almost every
       Win10/11 PC. If the LISA window is blank, install it from
       https://developer.microsoft.com/microsoft-edge/webview2/

------------------------------------------------------------
HOW TO INSTALL  (about 5 minutes, needs internet)
------------------------------------------------------------
  1. Unzip this folder anywhere (e.g. your Desktop).
  2. Double-click   "SETUP - Run First.bat"
       - if Python 3.12 isn't already installed, it downloads
         and installs it automatically, then builds the Python
         environment and downloads the libraries LISA needs.
         Wait for "Setup complete!".
  3. Double-click   "LAUNCH LISA.bat"
       - the LISA window opens. First launch takes ~15-20s.

  (After the one-time setup, you only ever use "LAUNCH LISA.bat".)

------------------------------------------------------------
USING IT
------------------------------------------------------------
  - In LISA, go to the IDP Generation tab.
  - Load the workbook from the "Workbook" folder next to this
    file:  IDP_Workbook.xlsm
  - Pick a conduit and Generate. Make sure AutoCAD is open first.
  - Generated DWGs go to the output folder you choose in LISA.

------------------------------------------------------------
NOTES
------------------------------------------------------------
  - The TimeSheets tab will not work in this build (its
    credentials were intentionally removed before sharing).
    IDP Generation - the part you're testing - is unaffected.
  - If Generate fails with "AutoCAD not accessible", make sure
    AutoCAD is open and not showing a dialog, then try again.
  - Post-generation validation is skipped in this build (it
    needs an internal dev folder); the DWG still generates fine.

Questions? Ask Patton.
