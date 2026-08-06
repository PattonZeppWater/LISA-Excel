# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('Handoff/lisa_symbols.json', 'Handoff'), ('Handoff/template_dictionary.json', 'Handoff'), ('Handoff/idp_anatomy.json', 'Handoff'), ('symbol_library_catalog.json', '.'), ('../IDP_Builder/resources/idp_rules.json', '.'), ('webui', 'webui')]
binaries = []
# idp_control_panel kept as a hidden import so `--classic` still launches the full Tkinter UI (no lost functionality)
hiddenimports = ['idp_control_panel', 'idp_router', 'idp_settings', 'idp_layouts', 'idp_vision_assist', 'mapping_table', 'symbol_infer', 'idp_extract', 'idp_write', 'logic_store', 'kb_expand', 'wire_legend', 'idp_dwg_extract', 'lisa_contract', 'template_dict', 'idp_anatomy', 'idp_excel', 'idp_ingest', 'idp_idp_pdf', 'idp_vision', 'idp_terms', 'idp_panelboard', 'idp_edc', 'idp_edc_symbols', 'idp_vision_schedule', 'idp_wiring', 'idp_project', 'idp_project_symbols', 'idp_dwg_scan', 'idp_training', 'idp_escalate', 'idp_schedule', 'idp_ocr_schedule', 'idp_cable_schedule', 'win32com', 'win32com.client', 'pythoncom', 'pywintypes']
tmp_ret = collect_all('pdfplumber')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pdfminer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# offline OCR engine for reading vector conduit-schedule sheets (models + configs)
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# LISA-look web UI: pywebview window + Flask server (mirrors LISA's architecture)
for _pkg in ('webview', 'flask', 'jinja2', 'werkzeug', 'clr_loader', 'proxy_tools', 'bottle'):
    try:
        tmp_ret = collect_all(_pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception:
        pass
hiddenimports += ['webview.platforms.edgechromium', 'webview.platforms.winforms',
                  'webview.platforms.mshtml', 'clr']


a = Analysis(
    ['idp_web_panel.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='IDP_ControlPanel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
