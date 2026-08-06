"""
IDP Extractor — Control Panel   (LISA-themed)
=============================================
A GUI front end that exposes ALL scanning options in one place, then extracts
conduits/fills from one or more PDFs and writes a filled IDP workbook.

The look matches LISA (dark navy #0d1a28, blue/teal accents, Segoe UI).

Options exposed:
  * PDF list (add files / add whole folder)
  * Template workbook (.xlsm)  + output path
  * Extraction mode: Auto | IDP drawings | Conduit schedule | Derive from cables
  * Infer S/D symbols from the library      (on/off)
  * Flag uncertain cells (amber + comment)  (on/off)
  * Clear existing data rows first          (on/off)
  * Also save a de-greyed copy              (on/off)
  * Default Conduit Type for unknowns       (XXX / PER SPEC / RMC / ...)

Run it:  python idp_control_panel.py     (or double-click the built .exe)
"""
import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from idp_extract import extract_conduits
from idp_ingest import (extract_source, merge_records, collect_wiring_bindings,
                        apply_wiring_terms, apply_project_dwg_symbols,
                        derive_teach_candidates, learn_from_finished_idps)
from idp_write import write_workbook, degrey, versioned_path, check_template_sane
import idp_project
import idp_training
import idp_escalate
import idp_schedule
import logic_store
import kb_expand

# ── LISA theme palette ──────────────────────────────────────────────────────
BG      = "#0d1a28"   # window background (dark navy) — LISA .main-content
PANEL   = "#16263d"   # panel / group background — LISA .drop-zone
FIELD   = "#1e3a5f"   # input field background — LISA .btn-primary
BORDER  = "#2e3a5a"   # LISA tree line
TEXT    = "#c8d8e8"   # primary text
MUTED   = "#8a9bb5"   # secondary text — LISA .status-msg
CATLBL  = "#7a80a0"   # section-label grey — LISA .tree-category-label
ACCENT  = "#4a9fd4"   # blue accent — LISA .drop-zone.dragging
ACCENT2 = "#00e6b3"   # teal (primary action) — LISA hero gradient
CYAN    = "#0099dd"   # cyan eyebrow — LISA .hero-label (#09d)
HERO    = "#06101e"   # hero band — LISA .home-hero
SIDEBAR = "#1a1a2e"   # LISA .sidebar
BTN     = "#1e3a5f"   # LISA .btn-primary bg
BTN_BD  = "#2a5a8a"   # LISA .btn-primary border
BTN_HOV = "#2a4e7a"   # LISA .btn-primary:hover
WARN    = "#e6a817"
DANGER  = "#c0392b"
FONT    = "Segoe UI"
MONO    = "Courier New"

DEFAULT_TEMPLATE_NAMES = ["IDP_Workbook_CurrentWIP_3.xlsm"]
CONDUIT_TYPES = ["XXX", "PER SPEC", "RMC", "PVC", "RGS", "PVC/RGS", "FLEX", "PCS", "RMC-PVC"]
MODES = [("Auto (drawings → schedule → cables)", "auto"),
         ("IDP drawings only", "drawings"),
         ("Conduit schedule only", "schedule"),
         ("Derive from cable schedule", "cables")]


def _find_default_template():
    # 1) the DESIGNATED template the user chose (remembered, independent of any project)
    try:
        import idp_settings
        remembered = idp_settings.get_template_path()
        if remembered:
            return remembered
    except Exception:
        pass
    # 2) otherwise fall back to a template shipped next to the app
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (os.path.join(here, "..", "IDP_Builder", "resources", "template"), here):
        for name in DEFAULT_TEMPLATE_NAMES:
            p = os.path.join(rel, name)
            if os.path.exists(p):
                return os.path.abspath(p)
    return ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LISA · IDP Extractor")
        self.geometry("860x820")
        self.configure(bg=BG)
        self._init_style()
        self.pdfs = []
        self.template = tk.StringVar(value=_find_default_template())
        self.output = tk.StringVar()
        self.mode = tk.StringVar(value="auto")
        # Scan-settings options all default ON; the bottom-left/global toggles default OFF
        # except High-accuracy OCR (parity with the web UI, per Master Cole).
        self.infer_symbols = tk.BooleanVar(value=True)
        self.add_flags = tk.BooleanVar(value=True)
        self.clear_rows = tk.BooleanVar(value=True)
        self.save_nogrey = tk.BooleanVar(value=True)
        self.learn_logic = tk.BooleanVar(value=True)
        self.ocr_hi_accuracy = tk.BooleanVar(value=True)    # 2-pass schedule OCR (on by default)
        self.clear_deviations = tk.BooleanVar(value=False)  # clean deviation-notes column (off)
        self.unknown_type = tk.StringVar(value="XXX")
        self.log_q = queue.Queue()
        self.prov_q = queue.Queue()
        self._build()
        self._load_ui_state()          # restore previous uploads / paths
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log)
        self.after(150, self._drain_prov)

    # ── persisted UI state (remember previous uploads across launches) ───────
    def _state_path(self):
        base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                or os.path.expanduser("~"))
        d = os.path.join(base, "AIC_IDP_Extractor")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(d, "ui_state.json")

    def _persist(self):
        """Save the current upload lists + template/output so they survive a
        restart. Safe/no-op on failure."""
        import json
        state = {
            "pdfs": list(getattr(self, "pdfs", [])),
            "train_plans": list(getattr(self, "train_plans", [])),
            "train_finished": list(getattr(self, "train_finished", [])),
            "train_generated": list(getattr(self, "train_generated", [])),
            "template": self.template.get(),
            "output": self.output.get(),
        }
        try:
            with open(self._state_path(), "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
        except OSError:
            pass

    def _load_ui_state(self):
        """Repopulate upload lists + paths from the last session (called after
        the UI is built, so every listbox exists)."""
        import json
        try:
            with open(self._state_path(), encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            return

        def _folder_label(p):
            return p + ("  (folder)" if os.path.isdir(p) else "")

        for p in state.get("pdfs", []):
            if p not in self.pdfs:
                self.pdfs.append(p)
                self.pdf_list.insert("end", _folder_label(p) if os.path.isdir(p) else p)
        for key, store, lb in (("train_plans", self.train_plans, self.train_plans_list),
                               ("train_finished", self.train_finished, self.train_finished_list),
                               ("train_generated", self.train_generated, self.train_generated_list)):
            for p in state.get(key, []):
                if p not in store:
                    store.append(p)
                    lb.insert("end", _folder_label(p))
        if state.get("template"):
            self.template.set(state["template"])
        if state.get("output"):
            self.output.set(state["output"])

    def _on_close(self):
        self._persist()
        self.destroy()

    def _init_style(self):
        st = ttk.Style(self)
        st.theme_use("clam")   # honors color config on Windows
        st.configure(".", background=PANEL, foreground=TEXT,
                     fieldbackground=FIELD, font=(FONT, 10), borderwidth=0)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        st.configure("TLabelframe", background=PANEL, bordercolor=BORDER,
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT2,
                     font=(FONT, 10, "bold"))
        st.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        st.map("TCheckbutton", background=[("active", PANEL)],
               foreground=[("active", ACCENT)])
        st.configure("TRadiobutton", background=PANEL, foreground=TEXT)
        st.map("TRadiobutton", background=[("active", PANEL)],
               foreground=[("active", ACCENT)])
        st.configure("TEntry", fieldbackground=FIELD, foreground=TEXT,
                     insertcolor=TEXT, bordercolor=BORDER, borderwidth=1, padding=4)
        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                     foreground=TEXT, arrowcolor=ACCENT, bordercolor=BORDER, padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)],
               foreground=[("readonly", TEXT)])
        # buttons — LISA .btn-primary (navy fill, blue border, lighter hover)
        st.configure("TButton", background=BTN, foreground=TEXT, bordercolor=BTN_BD,
                     lightcolor=BTN_BD, darkcolor=BTN_BD, borderwidth=1, focusthickness=0,
                     relief="flat", padding=(11, 6), font=(FONT, 10))
        st.map("TButton", background=[("active", BTN_HOV), ("pressed", BTN_HOV)],
               bordercolor=[("active", ACCENT)], foreground=[("active", "#ffffff")])
        # Run — LISA's primary CTA: teal, echoing the hero gradient
        st.configure("Run.TButton", background=ACCENT2, foreground="#08131f", bordercolor=ACCENT2,
                     lightcolor=ACCENT2, darkcolor=ACCENT2, borderwidth=1,
                     font=(FONT, 11, "bold"), padding=(18, 9))
        st.map("Run.TButton", background=[("active", "#33efc4"), ("pressed", "#00c9a0")])
        # notebook (tabs)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                     padding=(16, 7), font=(FONT, 10, "bold"))
        st.map("TNotebook.Tab", background=[("selected", FIELD)],
               foreground=[("selected", ACCENT2)])
        # treeview (logic table)
        st.configure("Treeview", background=FIELD, fieldbackground=FIELD,
                     foreground=TEXT, borderwidth=0, rowheight=24)
        st.configure("Treeview.Heading", background="#22405f", foreground=TEXT,
                     font=(FONT, 9, "bold"))
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#08131f")])

        # scrollbars — dark, chunky, DRAG-only (no arrow buttons to click through).
        # Strip the arrow elements out of the layout so only the trough + thumb show.
        st.layout("IDP.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})]})])
        st.layout("IDP.Horizontal.TScrollbar", [
            ("Horizontal.Scrollbar.trough", {"sticky": "we", "children": [
                ("Horizontal.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})]})])
        for sb in ("IDP.Vertical.TScrollbar", "IDP.Horizontal.TScrollbar"):
            st.configure(sb, troughcolor=PANEL, background="#3a5a82",
                         bordercolor=PANEL, arrowcolor=PANEL, relief="flat",
                         borderwidth=0, width=14)
            st.map(sb, background=[("active", ACCENT), ("pressed", ACCENT2)])

    def _build(self):
        pad = {"padx": 10, "pady": 5}
        # ── hero header (echoes LISA's .home-hero: dark band, cyan mono eyebrow,
        #    big title with a teal accent, italic tagline, run options on the right) ──
        hero = tk.Frame(self, bg=HERO)
        hero.pack(fill="x")
        inner = tk.Frame(hero, bg=HERO)
        inner.pack(fill="x", padx=22, pady=(15, 13))
        left = tk.Frame(inner, bg=HERO); left.pack(side="left", anchor="w")
        tk.Label(left, text="E L E C T R I C A L   I D P   A U T O M A T I O N",
                 bg=HERO, fg=CYAN, font=(MONO, 8, "bold")).pack(anchor="w")
        title = tk.Frame(left, bg=HERO); title.pack(anchor="w", pady=(2, 0))
        tk.Label(title, text="IDP EXTRACTOR", bg=HERO, fg="#ffffff",
                 font=(FONT, 22, "bold")).pack(side="left")
        tk.Label(title, text="  control panel", bg=HERO, fg=ACCENT2,
                 font=(FONT, 12)).pack(side="left", pady=(9, 0))
        tk.Label(left, text="Extract conduits, fill & terminations into the IDP workbook LISA reads.",
                 bg=HERO, fg="#7a9bb5", font=(FONT, 9, "italic")).pack(anchor="w", pady=(3, 0))
        opts = tk.Frame(inner, bg=HERO); opts.pack(side="right", anchor="ne", pady=(2, 0))
        for _t, _v in (("High-accuracy schedule OCR (slower)", self.ocr_hi_accuracy),
                       ("Clear deviation notes", self.clear_deviations)):
            tk.Checkbutton(opts, text=_t, variable=_v, bg=HERO, fg=TEXT, selectcolor=FIELD,
                           activebackground=HERO, activeforeground=ACCENT2,
                           font=(FONT, 9)).pack(anchor="e")
        # layered accent underline (teal over blue) — echoes the hero gradient sweep
        tk.Frame(self, bg=ACCENT2, height=2).pack(fill="x")
        tk.Frame(self, bg=ACCENT, height=1).pack(fill="x", pady=(0, 4))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        scan = ttk.Frame(nb, style="TFrame")
        logic = ttk.Frame(nb, style="TFrame")
        sources = ttk.Frame(nb, style="TFrame")
        training = ttk.Frame(nb, style="TFrame")
        schedule = ttk.Frame(nb, style="TFrame")
        nb.add(scan, text="  Scan  ")
        nb.add(schedule, text="  Conduit Schedule  ")
        nb.add(logic, text="  Remembered Logic  ")
        nb.add(sources, text="  Sources  ")
        nb.add(training, text="  Training  ")
        self._build_scan(scan, pad)
        self._build_schedule(schedule, pad)
        self._build_logic(logic, pad)
        self._build_sources(sources, pad)
        self._build_training(training, pad)

    def _build_scan(self, scan, pad):
        f1 = ttk.LabelFrame(scan, text="1 · Source files to scan (PDF or Excel)")
        f1.pack(fill="both", expand=False, **pad)
        self.pdf_list = tk.Listbox(f1, height=7, bg=FIELD, fg=TEXT, bd=0,
                                   highlightthickness=1, highlightbackground=BORDER,
                                   selectbackground=ACCENT, selectforeground="#08131f",
                                   font=(FONT, 9), activestyle="none")
        self.pdf_list.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        b = ttk.Frame(f1, style="Panel.TFrame"); b.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(b, text="Add PDFs…", command=self.add_pdfs).pack(fill="x")
        ttk.Button(b, text="Add Excel…", command=self.add_excel).pack(fill="x", pady=(4, 0))
        ttk.Button(b, text="Add Folder…", command=self.add_folder).pack(fill="x", pady=4)
        ttk.Button(b, text="Remove", command=self.remove_pdf).pack(fill="x")
        ttk.Button(b, text="Clear", command=self.clear_pdfs).pack(fill="x", pady=4)

        f2 = ttk.LabelFrame(scan, text="2 · Template workbook (.xlsm)")
        f2.pack(fill="x", **pad)
        ttk.Entry(f2, textvariable=self.template).pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(f2, text="Browse…", command=self.pick_template).pack(side="right", padx=6, pady=6)

        f3 = ttk.LabelFrame(scan, text="3 · Save filled workbook as")
        f3.pack(fill="x", **pad)
        ttk.Entry(f3, textvariable=self.output).pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(f3, text="Save folder…", command=self.pick_output).pack(side="right", padx=6, pady=6)

        f4 = ttk.LabelFrame(scan, text="4 · Scanning options")
        f4.pack(fill="x", **pad)
        mf = ttk.Frame(f4, style="Panel.TFrame"); mf.pack(fill="x", padx=6, pady=4)
        ttk.Label(mf, text="Extraction mode:", style="Muted.TLabel").pack(side="left")
        for label, val in MODES:
            ttk.Radiobutton(mf, text=label, value=val, variable=self.mode).pack(side="left", padx=5)
        cf = ttk.Frame(f4, style="Panel.TFrame"); cf.pack(fill="x", padx=6, pady=4)
        ttk.Checkbutton(cf, text="Infer S/D symbols", variable=self.infer_symbols).pack(side="left", padx=5)
        ttk.Checkbutton(cf, text="Flag uncertain cells", variable=self.add_flags).pack(side="left", padx=5)
        ttk.Checkbutton(cf, text="Clear existing rows first", variable=self.clear_rows).pack(side="left", padx=5)
        ttk.Checkbutton(cf, text="Also save de-greyed copy", variable=self.save_nogrey).pack(side="left", padx=5)
        ttk.Checkbutton(cf, text="Learn: flag new logic to teach", variable=self.learn_logic).pack(side="left", padx=5)
        tf = ttk.Frame(f4, style="Panel.TFrame"); tf.pack(fill="x", padx=6, pady=4)
        ttk.Label(tf, text="Default Conduit Type for unknowns:", style="Muted.TLabel").pack(side="left")
        ttk.Combobox(tf, textvariable=self.unknown_type, values=CONDUIT_TYPES,
                     width=12, state="readonly").pack(side="left", padx=5)

        self.run_btn = ttk.Button(scan, text="▶  Run scan", command=self.run, style="Run.TButton")
        self.run_btn.pack(pady=8)

        lf = ttk.LabelFrame(scan, text="Log")
        lf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lf, height=11, wrap="word", state="disabled", bd=0,
                           bg="#0b1622", fg=TEXT, insertbackground=TEXT,
                           highlightthickness=1, highlightbackground=BORDER,
                           font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    # ── Conduit Schedule tab (vector sheets → enter directly in the exe) ─────
    _SCHED_COLS = ["Name", "From", "To", "Cdt Size", "Cdt Type",
                   "Cond Qty", "Cond Gauge", "Gnd Size",
                   "Cable Qty", "Cable Type", "Notes"]
    _SCHED_WIDTHS = [64, 150, 150, 66, 64, 62, 90, 74, 62, 84, 200]

    def _build_schedule(self, sched, pad):
        # A vector conduit-schedule sheet has no text to read. The exe renders it
        # for you on the left; you enter the rows on the right and write the
        # workbook directly — no Excel, no OCR, no API.
        bar = ttk.Frame(sched, style="TFrame"); bar.pack(fill="x", **pad)
        ttk.Button(bar, text="Render PDF page…", command=self._sched_render).pack(side="left")
        self.sched_page = tk.IntVar(value=1)
        ttk.Label(bar, text="Page").pack(side="left", padx=(10, 2))
        ttk.Spinbox(bar, from_=1, to=9999, width=6, textvariable=self.sched_page,
                    command=self._sched_render_current).pack(side="left")
        ttk.Button(bar, text="Add row", command=self._sched_add_row).pack(side="left", padx=(12, 2))
        ttk.Button(bar, text="Delete row", command=self._sched_del_row).pack(side="left", padx=2)
        ttk.Button(bar, text="Clear", command=self._sched_clear).pack(side="left", padx=2)
        tk.Label(bar, text="  Vector schedules are read automatically on Run scan; this tab is for "
                           "review / manual tweaks.", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self._sched_render_dpi = 140
        self._sched_sel = None          # (x0,y0,x1,y1) in rendered-image px
        self._sched_sel_rect = None     # canvas rectangle id

        body = ttk.Panedwindow(sched, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        # left: rendered page image (scrollable)
        left = ttk.LabelFrame(body, text="Schedule sheet (read from here)")
        cv_wrap = ttk.Frame(left, style="Panel.TFrame"); cv_wrap.pack(fill="both", expand=True)
        self.sched_canvas = tk.Canvas(cv_wrap, bg=PANEL, highlightthickness=0)
        vsb = ttk.Scrollbar(cv_wrap, orient="vertical", command=self.sched_canvas.yview,
                            style="IDP.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(cv_wrap, orient="horizontal", command=self.sched_canvas.xview,
                            style="IDP.Horizontal.TScrollbar")
        self.sched_canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y"); hsb.pack(side="bottom", fill="x")
        self.sched_canvas.pack(side="left", fill="both", expand=True)
        self.sched_canvas.bind("<ButtonPress-1>", self._sched_sel_press)
        self.sched_canvas.bind("<B1-Motion>", self._sched_sel_drag)
        self.sched_canvas.bind("<ButtonRelease-1>", self._sched_sel_release)
        self._sched_pdf = None
        body.add(left, weight=3)

        # right: editable grid
        right = ttk.LabelFrame(body, text="Conduit rows (double-click a cell to edit)")
        tv_wrap = ttk.Frame(right, style="Panel.TFrame"); tv_wrap.pack(fill="both", expand=True)
        self.sched_tree = ttk.Treeview(tv_wrap, columns=self._SCHED_COLS, show="headings",
                                       selectmode="browse")
        for c, w in zip(self._SCHED_COLS, self._SCHED_WIDTHS):
            self.sched_tree.heading(c, text=c)
            self.sched_tree.column(c, width=w, anchor="w", stretch=(c == "Notes"))
        tsb = ttk.Scrollbar(tv_wrap, orient="vertical", command=self.sched_tree.yview,
                            style="IDP.Vertical.TScrollbar")
        thb = ttk.Scrollbar(tv_wrap, orient="horizontal", command=self.sched_tree.xview,
                            style="IDP.Horizontal.TScrollbar")
        self.sched_tree.configure(yscrollcommand=tsb.set, xscrollcommand=thb.set)
        self.sched_tree.tag_configure("lowconf", background="#5a4a1e")   # amber = verify
        tsb.pack(side="right", fill="y"); thb.pack(side="bottom", fill="x")
        self.sched_tree.pack(side="left", fill="both", expand=True)
        self.sched_tree.bind("<Double-1>", self._sched_edit_cell)
        body.add(right, weight=2)

        # actions
        act = ttk.Frame(sched, style="TFrame"); act.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(act, text="▶  Write workbook from these rows", command=self._sched_write,
                   style="Run.TButton").pack(side="left")
        ttk.Button(act, text="➕ Include in Scan run (attach EDC terms)",
                   command=self._sched_include).pack(side="left", padx=10)
        tk.Label(act, text="Columns match the E-3 schedule. Ground goes in its own row automatically.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=8)

    def _sched_render(self):
        f = filedialog.askopenfilename(title="Select the conduit-schedule PDF",
                                       filetypes=[("PDF", "*.pdf")])
        if not f:
            return
        self._sched_pdf = f
        self._sched_render_current()

    def _sched_render_current(self):
        if not self._sched_pdf:
            return
        try:
            import fitz
            doc = fitz.open(self._sched_pdf)
            idx = max(0, min(self.sched_page.get() - 1, doc.page_count - 1))
            pix = doc[idx].get_pixmap(dpi=140)
            tmp = os.path.join(os.environ.get("TEMP", "."), "idp_sched_page.png")
            pix.save(tmp)
            self._sched_img = tk.PhotoImage(file=tmp)   # keep a reference (no GC)
            self.sched_canvas.delete("all")
            self.sched_canvas.create_image(0, 0, anchor="nw", image=self._sched_img)
            self.sched_canvas.configure(scrollregion=(0, 0, self._sched_img.width(),
                                                      self._sched_img.height()))
        except Exception as e:
            messagebox.showerror("Render failed", str(e))

    # ---- rubber-band region select on the rendered page ----
    def _sched_sel_press(self, e):
        x, y = self.sched_canvas.canvasx(e.x), self.sched_canvas.canvasy(e.y)
        self._sched_sel = [x, y, x, y]
        if self._sched_sel_rect:
            self.sched_canvas.delete(self._sched_sel_rect)
        self._sched_sel_rect = self.sched_canvas.create_rectangle(
            x, y, x, y, outline="#00e6b3", width=2)

    def _sched_sel_drag(self, e):
        if not self._sched_sel:
            return
        x, y = self.sched_canvas.canvasx(e.x), self.sched_canvas.canvasy(e.y)
        self._sched_sel[2], self._sched_sel[3] = x, y
        self.sched_canvas.coords(self._sched_sel_rect, *self._sched_sel)

    def _sched_sel_release(self, e):
        if self._sched_sel:
            x0, y0, x1, y1 = self._sched_sel
            if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
                self._sched_sel = None          # too small — treat as a click

    def _sched_ocr(self):
        if not self._sched_pdf:
            messagebox.showwarning("No page", "Open a PDF page first (Render PDF page…)."); return
        clip = None
        if self._sched_sel:
            s = self._sched_render_dpi / 72.0
            x0, y0, x1, y1 = self._sched_sel
            clip = (min(x0, x1) / s, min(y0, y1) / s, max(x0, x1) / s, max(y0, y1) / s)
        self.sched_ocr_btn.configure(state="disabled")
        threading.Thread(target=self._sched_ocr_worker,
                         args=(self._sched_pdf, self.sched_page.get() - 1, clip), daemon=True).start()

    def _sched_ocr_worker(self, pdf, page_idx, clip_tuple):
        try:
            import idp_ocr_schedule as O
            import fitz
            clip = fitz.Rect(*clip_tuple) if clip_tuple else None
            self.logmsg("Reading schedule from the page via OCR (offline)…")
            rows, meta = O.read_schedule(pdf, page_idx, clip=clip, log=self.logmsg)
            self.after(0, lambda: self._sched_populate(rows, meta))
        except Exception as e:
            self.logmsg("OCR ERROR: " + str(e))
            self.after(0, lambda msg=str(e): messagebox.showerror("OCR failed", msg))
        finally:
            self.after(0, lambda: self.sched_ocr_btn.configure(state="normal"))

    def _sched_populate(self, rows, meta):
        """Fill the review grid from OCR results; flag low-confidence rows amber."""
        self._sched_clear()
        thr = meta.get("low_conf_threshold", 0.8)
        key = ["name", "src", "dst", "size", "ctype", "cdt_qty", "cond_gauge",
               "gnd", "cable_qty", "cable_type", "notes"]
        for r in rows:
            # grid columns: Name,From,To,CdtSize,CdtType,CondQty,CondGauge,GndSize,CableQty,CableType,Notes
            vals = [r.get("name", ""), r.get("src", ""), r.get("dst", ""), r.get("size", ""),
                    r.get("ctype", ""), r.get("cond_qty", ""), r.get("cond_gauge", ""),
                    r.get("gnd", ""), r.get("cable_qty", ""), r.get("cable_type", ""),
                    r.get("notes", "")]
            conf = r.get("_conf", {})
            low = any(conf.get(k, 1.0) < thr and str(r.get(k, "")).strip()
                      for k in ("name", "src", "dst", "size", "ctype", "cond_gauge", "gnd"))
            self.sched_tree.insert("", "end", values=vals, tags=("lowconf",) if low else ())
        self.logmsg(f"OCR read {len(rows)} conduit rows into the review grid "
                    f"({meta.get('low_conf_cells', 0)} low-confidence cells flagged amber). "
                    f"Verify the amber rows against the page, then Write workbook.")
        messagebox.showinfo("Schedule read",
                            f"Read {len(rows)} conduits.\n{meta.get('low_conf_cells', 0)} "
                            f"low-confidence cells flagged amber — verify those against the "
                            f"page, fix any, then click Write workbook.")

    def _sched_add_row(self):
        self.sched_tree.insert("", "end", values=[""] * len(self._SCHED_COLS))

    def _sched_del_row(self):
        for iid in self.sched_tree.selection():
            self.sched_tree.delete(iid)

    def _sched_clear(self):
        for iid in self.sched_tree.get_children():
            self.sched_tree.delete(iid)

    def _sched_paste(self):
        try:
            data = self.clipboard_get()
        except tk.TclError:
            return
        for line in data.splitlines():
            if not line.strip():
                continue
            cells = line.split("\t")
            cells = (cells + [""] * len(self._SCHED_COLS))[:len(self._SCHED_COLS)]
            self.sched_tree.insert("", "end", values=cells)

    def _sched_edit_cell(self, event):
        tree = self.sched_tree
        if tree.identify("region", event.x, event.y) != "cell":
            return
        rowid = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not rowid or not col:
            return
        cidx = int(col[1:]) - 1
        x, y, w, h = tree.bbox(rowid, col)
        cur = tree.set(rowid, self._SCHED_COLS[cidx])
        e = tk.Entry(tree)
        e.place(x=x, y=y, width=w, height=h)
        e.insert(0, cur); e.focus_set(); e.select_range(0, "end")

        def commit(_=None):
            tree.set(rowid, self._SCHED_COLS[cidx], e.get())
            e.destroy()
        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        e.bind("<Escape>", lambda _: e.destroy())

    def _sched_collect(self):
        rows = []
        for iid in self.sched_tree.get_children():
            v = [self.sched_tree.set(iid, c) for c in self._SCHED_COLS]
            rows.append(dict(name=v[0], src=v[1], dst=v[2], size=v[3], ctype=v[4],
                             cond_qty=v[5], cond_gauge=v[6], gnd=v[7],
                             cable_qty=v[8], cable_type=v[9], notes=v[10]))
        return rows

    def _sched_include(self):
        rows = [r for r in self._sched_collect() if r["name"].strip()]
        if not rows:
            messagebox.showwarning("No rows", "Enter at least one conduit row first."); return
        self._pending_schedule = rows
        self.logmsg(f"Conduit Schedule: {len(rows)} conduit(s) queued for the next Scan run "
                    f"— add your EDC folder on the Scan tab, then Run to attach terminals.")

    def _sched_write(self):
        rows = [r for r in self._sched_collect() if r["name"].strip()]
        if not rows:
            messagebox.showwarning("No rows", "Enter at least one conduit row first."); return
        if not os.path.isfile(self.template.get()):
            messagebox.showwarning("Missing template", "Select a valid template workbook on the Scan tab."); return
        if not self.output.get():
            messagebox.showwarning("Missing output", "Choose a save location on the Scan tab."); return
        threading.Thread(target=self._sched_write_worker, args=(rows,), daemon=True).start()

    def _sched_write_worker(self, rows):
        try:
            self.logmsg(logic_store.apply())
            recs = idp_schedule.rows_to_records(rows)
            kb_expand.expand_from_records(recs)
            target = versioned_path(self.output.get())
            self.logmsg(f"Conduit Schedule → writing {len(recs)} conduits → "
                        f"{os.path.basename(target)} …")
            out = write_workbook(recs, self.template.get(), target,
                                 clear_rows=self.clear_rows.get(), add_flags=self.add_flags.get())
            self.logmsg(f"Saved: {out}")
            if self.save_nogrey.get():
                ng = versioned_path(os.path.splitext(out)[0] + "_NoGrey.xlsm")
                n = degrey(out, ng)
                self.logmsg(f"De-greyed copy: {os.path.basename(ng)}  ({n} cells)")
            self.after(0, lambda o=out: messagebox.showinfo("Complete", f"Saved:\n{o}"))
        except Exception as e:
            self.logmsg("ERROR: " + str(e))
            self.logmsg(traceback.format_exc())
            self.after(0, lambda msg=str(e): messagebox.showerror("Error", msg))

    # ── Training tab (plans / finished / generated → learn + ask Claude) ─────
    def _build_training(self, training, pad):
        self.train_plans, self.train_finished, self.train_generated = [], [], []
        top = ttk.Frame(training, style="TFrame"); top.pack(fill="x", **pad)
        ttk.Label(top, text="Teach the tool by example: upload a project's plans, its "
                            "finished IDPs, and the IDP generated from our workbook. "
                            "The extractor compares them, learns the gaps, and feeds them "
                            "back into the Claude skills.", style="Muted.TLabel",
                  background=BG, wraplength=820).pack(side="left")

        specs = [("1 · Plans (source docs — PDF/Excel)", self.train_plans, "train_plans_list",
                  [("PDF/Excel", "*.pdf *.xlsx *.xlsm *.xls")]),
                 ("2 · Finished IDPs (approved drawings — DWG/folder)", self.train_finished,
                  "train_finished_list", [("DWG", "*.dwg")]),
                 ("3 · Generated IDP (our workbook, or LISA's DWGs)", self.train_generated,
                  "train_generated_list", [("Workbook/DWG", "*.xlsm *.xlsx *.dwg")])]
        for title, store, attr, filetypes in specs:
            lf = ttk.LabelFrame(training, text=title); lf.pack(fill="x", **pad)
            lb = tk.Listbox(lf, height=3, bg=FIELD, fg=TEXT, bd=0, highlightthickness=1,
                            highlightbackground=BORDER, selectbackground=ACCENT,
                            selectforeground="#08131f", font=(FONT, 9), activestyle="none")
            lb.pack(side="left", fill="both", expand=True, padx=6, pady=6)
            setattr(self, attr, lb)
            bb = ttk.Frame(lf, style="Panel.TFrame"); bb.pack(side="right", fill="y", padx=6, pady=6)
            ttk.Button(bb, text="Add files…",
                       command=lambda s=store, l=lb, ft=filetypes: self._train_add_files(s, l, ft)
                       ).pack(fill="x")
            ttk.Button(bb, text="Add folder…",
                       command=lambda s=store, l=lb: self._train_add_folder(s, l)
                       ).pack(fill="x", pady=4)
            ttk.Button(bb, text="Clear",
                       command=lambda s=store, l=lb: (s.clear(), l.delete(0, "end"), self._persist())
                       ).pack(fill="x")

        bf = ttk.Frame(training, style="TFrame"); bf.pack(fill="x", **pad)
        self.train_btn = ttk.Button(bf, text="▶  Compare & Learn", command=self._run_training,
                                    style="Run.TButton")
        self.train_btn.pack(side="left")
        ttk.Button(bf, text="Ask Claude about uncertainties", command=self._ask_claude).pack(side="left", padx=8)

        lf = ttk.LabelFrame(training, text="Training log")
        lf.pack(fill="both", expand=True, **pad)
        self.train_log = tk.Text(lf, height=10, wrap="word", state="disabled", bd=0,
                                 bg="#0b1622", fg=TEXT, insertbackground=TEXT,
                                 highlightthickness=1, highlightbackground=BORDER,
                                 font=("Consolas", 9))
        self.train_log.pack(fill="both", expand=True, padx=6, pady=6)
        self._last_training = None   # last run's report, for Ask Claude

    def _train_add_files(self, store, listbox, filetypes):
        for f in filedialog.askopenfilenames(title="Select files",
                                              filetypes=list(filetypes) + [("All", "*.*")]):
            if f not in store:
                store.append(f); listbox.insert("end", f)
        self._persist()

    def _train_add_folder(self, store, listbox):
        d = filedialog.askdirectory(title="Select a folder (scanned recursively)")
        if not d:
            return
        if d not in store:
            store.append(d); listbox.insert("end", d + "  (folder)")
        self._persist()

    def _tlog(self, m):
        self.train_log.configure(state="normal")
        self.train_log.insert("end", m + "\n"); self.train_log.see("end")
        self.train_log.configure(state="disabled")

    def _run_training(self):
        if not self.train_finished or not self.train_generated:
            messagebox.showwarning("Need inputs", "Add at least the Finished IDPs and the "
                                   "Generated IDP to compare.")
            return
        self.train_btn.configure(state="disabled")
        threading.Thread(target=self._training_worker, daemon=True).start()

    def _training_worker(self):
        try:
            self.after(0, lambda: self._tlog("Comparing finished IDPs against our output …"))
            rep = idp_training.run_training(
                plans=list(self.train_plans), finished=list(self.train_finished),
                generated=list(self.train_generated), learn=True,
                log=lambda m: self.after(0, lambda mm=m: self._tlog(mm)))
            self._last_training = rep
            def done():
                self._tlog(rep["summary"])
                for g in rep["gaps"][:40]:
                    self._tlog(f"  gap [{g['conduit']}] {g['field']}: "
                               f"finished={g['ground']!r} ours={g['ours']!r}")
                if len(rep["gaps"]) > 40:
                    self._tlog(f"  … +{len(rep['gaps']) - 40} more gaps")
                if rep["learned"]:
                    self._tlog(f"Learned {len(rep['learned'])} rule(s) → Remembered Logic "
                               f"+ skill references updated.")
                    self._reload_logic()
                if rep["uncertain"]:
                    self._tlog(f"{len(rep['uncertain'])} item(s) need judgment — click "
                               f"'Ask Claude about uncertainties'.")
            self.after(0, done)
        except Exception as e:
            self.after(0, lambda err=str(e): self._tlog("ERROR: " + err))
        finally:
            self.after(0, lambda: self.train_btn.configure(state="normal"))

    def _ask_claude(self):
        # prefer the last training report's uncertainties; else the last scan's records
        items = []
        if self._last_training:
            items = idp_escalate.from_training_report(self._last_training)
        if not items and getattr(self, "_last_records", None):
            items = idp_escalate.collect_uncertain(self._last_records)
        if not items:
            messagebox.showinfo("Nothing to ask", "Run a scan or a Compare & Learn first — "
                                "there are no open uncertainties to escalate.")
            return
        project = idp_project.detect_project_name(
            list(self.train_finished) + list(self.train_generated) + list(self.pdfs))
        path = idp_escalate.build_packet(items, project=project)
        self._tlog(f"Wrote {len(items)} question(s) for Claude → {path}")
        # try a live API call if a key is configured
        reply = None
        try:
            with open(path, encoding="utf-8") as fh:
                reply = idp_escalate.ask_claude_api(fh.read())
        except Exception:
            reply = None
        if reply:
            added = idp_escalate.apply_rule_lines(reply)
            self._tlog(f"Claude answered (API): {added} rule(s) applied to Remembered Logic.")
            self._reload_logic()
            messagebox.showinfo("Claude answered",
                                f"Applied {added} rule(s) from Claude's reply.\nPacket: {path}")
        else:
            messagebox.showinfo("Ask Claude",
                                f"Wrote {len(items)} question(s) to:\n{path}\n\n"
                                "Your attached Claude Code chat can resolve these directly — "
                                "just tell it \"resolve the extractor's open questions\" and it "
                                "will read this packet, answer using the IDP skills, and apply "
                                "the rules to Remembered Logic (no copy-paste). "
                                "Set ANTHROPIC_API_KEY to have the exe do it fully automatically.")

    # ── Sources tab (per-cell provenance) ───────────────────────────────────
    def _build_sources(self, sources, pad):
        top = ttk.Frame(sources, style="TFrame"); top.pack(fill="x", **pad)
        ttk.Label(top, text="Where each ConduitIndex / FillIndex value was found — the "
                            "exact file path per cell. Populated after a scan runs.",
                  style="Muted.TLabel", background=BG).pack(side="left")

        ff = ttk.Frame(sources, style="TFrame"); ff.pack(fill="x", padx=10)
        ttk.Label(ff, text="Filter by conduit:", style="Muted.TLabel", background=BG).pack(side="left")
        self.src_filter = tk.StringVar()
        e = ttk.Entry(ff, textvariable=self.src_filter, width=20)
        e.pack(side="left", padx=6)
        e.bind("<KeyRelease>", lambda ev: self._refresh_sources_view())
        ttk.Button(ff, text="Export CSV…", command=self._export_sources_csv).pack(side="right")

        tf = ttk.LabelFrame(sources, text="Cell provenance")
        tf.pack(fill="both", expand=True, **pad)
        cols = ("conduit", "sheet", "field", "value", "source")
        self.src_tree = ttk.Treeview(tf, columns=cols, show="headings", height=18)
        widths = {"conduit": 80, "sheet": 130, "field": 130, "value": 160, "source": 380}
        for c in cols:
            self.src_tree.heading(c, text=c.capitalize())
            self.src_tree.column(c, width=widths[c], anchor="w", stretch=False)
        vsb = ttk.Scrollbar(tf, orient="vertical", style="IDP.Vertical.TScrollbar",
                            command=self.src_tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", style="IDP.Horizontal.TScrollbar",
                            command=self.src_tree.xview)
        self.src_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.src_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns", padx=(2, 0))
        hsb.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.src_tree.bind("<Shift-MouseWheel>",
                           lambda e: self.src_tree.xview_scroll(int(-e.delta / 120), "units"))

        self.src_status = ttk.Label(sources, text="No scan run yet.", style="Muted.TLabel", background=BG)
        self.src_status.pack(anchor="w", padx=12, pady=(0, 6))

        self._provenance = []   # full unfiltered rows from the last run

    def set_provenance(self, rows):
        self._provenance = rows or []
        self._refresh_sources_view()
        n_files = len(set(r["source"] for r in self._provenance if not r["source"].startswith("derived")))
        self.src_status.configure(
            text=f"{len(self._provenance)} cell(s) traced across {n_files} source file(s).")

    def _refresh_sources_view(self):
        self.src_tree.delete(*self.src_tree.get_children())
        needle = self.src_filter.get().strip().upper()
        for row in self._provenance:
            if needle and needle not in row["conduit"].upper():
                continue
            self.src_tree.insert("", "end", values=(row["conduit"], row["sheet"],
                                 row["field"], row["value"], row["source"]))

    def _export_sources_csv(self):
        if not self._provenance:
            messagebox.showinfo("Nothing to export", "Run a scan first.")
            return
        f = filedialog.asksaveasfilename(title="Export provenance", defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if not f:
            return
        import csv
        with open(f, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["conduit", "sheet", "field", "value", "source"])
            w.writeheader()
            w.writerows(self._provenance)
        messagebox.showinfo("Exported", f"Wrote {len(self._provenance)} rows to:\n{f}")

    # ── Remembered Logic tab ────────────────────────────────────────────────
    def _build_logic(self, logic, pad):
        top = ttk.Frame(logic, style="TFrame"); top.pack(fill="x", **pad)
        ttk.Label(top, text="Extraction rules the tool remembers and applies on every scan. "
                            "Edit anytime.", style="Muted.TLabel",
                  background=BG).pack(side="left")

        tf = ttk.LabelFrame(logic, text="Rules")
        tf.pack(fill="both", expand=True, **pad)
        # buttons on the right first, then the tree+scrollbars fill the rest
        rb = ttk.Frame(tf, style="Panel.TFrame"); rb.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(rb, text="Add…", command=lambda: self._rule_dialog()).pack(fill="x")
        ttk.Button(rb, text="Edit…", command=self._edit_rule).pack(fill="x", pady=4)
        ttk.Button(rb, text="Remove", command=self._remove_rule).pack(fill="x")
        ttk.Button(rb, text="Restore defaults", command=self._load_defaults).pack(fill="x", pady=(16, 0))
        ttk.Button(rb, text="Learn from IDP folder…", command=self._learn_from_folder).pack(fill="x", pady=(16, 0))

        # tree + BOTH scrollbars in a grid container so the wide columns
        # (context/note) are reachable by horizontal scroll
        tw = ttk.Frame(tf, style="Panel.TFrame")
        tw.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        cols = ("type", "match", "result", "context", "note")
        self.tree = ttk.Treeview(tw, columns=cols, show="headings", height=9)
        for c, w in zip(cols, (110, 200, 170, 90, 240)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w", stretch=False)
        lvsb = ttk.Scrollbar(tw, orient="vertical", style="IDP.Vertical.TScrollbar",
                             command=self.tree.yview)
        lhsb = ttk.Scrollbar(tw, orient="horizontal", style="IDP.Horizontal.TScrollbar",
                             command=self.tree.xview)
        self.tree.configure(yscrollcommand=lvsb.set, xscrollcommand=lhsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        lvsb.grid(row=0, column=1, sticky="ns", padx=(2, 0))
        lhsb.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        tw.rowconfigure(0, weight=1)
        tw.columnconfigure(0, weight=1)
        # shift+wheel scrolls horizontally
        self.tree.bind("<Shift-MouseWheel>",
                       lambda e: self.tree.xview_scroll(int(-e.delta / 120), "units"))

        nf = ttk.LabelFrame(logic, text="Notes — where to look for tags / terms, reminders")
        nf.pack(fill="both", expand=False, **pad)
        self.notes = tk.Text(nf, height=6, wrap="word", bd=0, bg=FIELD, fg=TEXT,
                             insertbackground=TEXT, highlightthickness=1,
                             highlightbackground=BORDER, font=(FONT, 9))
        self.notes.pack(fill="both", expand=True, padx=6, pady=6)

        ttk.Button(logic, text="💾  Save & Apply logic", command=self._save_logic,
                   style="Run.TButton").pack(pady=6)

        self._reload_logic()

    def _reload_logic(self):
        self.logic_data = logic_store.load()
        # first run (empty store) → show the built-in new logic so the page isn't blank
        if not self.logic_data.get("rules") and not self.logic_data.get("notes"):
            self.logic_data = logic_store.defaults()
        self._fill_logic(self.logic_data)

    def _fill_logic(self, data):
        self.tree.delete(*self.tree.get_children())
        for r in data.get("rules", []):
            self.tree.insert("", "end", values=(r.get("type", ""), r.get("match", ""),
                             r.get("result", ""), r.get("context", ""), r.get("note", "")))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", data.get("notes", ""))

    def _load_defaults(self):
        if messagebox.askyesno(
                "Restore defaults",
                "Load the built-in extraction logic (LISA contract + drawing "
                "conventions)?\n\nThis replaces the rules and notes shown here. "
                "Nothing is saved until you click Save & Apply."):
            self._fill_logic(logic_store.defaults())

    def _learn_from_folder(self):
        """Point at ANY folder of finished IDPs (a past job's CAD folder, not
        necessarily today's project) and harvest its real device->symbol
        pairs into Remembered Logic — independent of running an extraction."""
        d = filedialog.askdirectory(title="Select a folder containing FINISHED IDP drawings (.dwg)")
        if not d:
            return
        self.logmsg(f"Learning from finished IDPs under {d} …")
        threading.Thread(target=self._learn_worker, args=(d,), daemon=True).start()

    def _learn_worker(self, folder):
        try:
            added, ndwg = learn_from_finished_idps([folder])
        except Exception as e:
            self.logmsg(f"Learn failed: {e}")
            self.after(0, lambda: messagebox.showerror("Learn failed", str(e)))
            return
        self.logmsg(f"Scanned {ndwg} DWG(s) under {folder} -> {added} new rule(s) learned.")
        if added:
            self.after(0, self._reload_logic)
        self.after(0, lambda: messagebox.showinfo(
            "Learned from finished IDPs",
            f"Scanned {ndwg} DWG(s) under:\n{folder}\n\n"
            f"{added} new keyword rule(s) added to Remembered Logic."
            + ("" if ndwg else "\n\n(No .dwg files found under that folder.)")))

    def _rule_dialog(self, preset=None, item=None):
        d = tk.Toplevel(self); d.title("Rule"); d.configure(bg=BG); d.transient(self); d.grab_set()
        vals = preset or {"type": "header_alias", "match": "", "result": "", "context": "", "note": ""}
        fields = {}
        rows = [("type", logic_store.RULE_TYPES), ("match", None), ("result", None),
                ("context", None), ("note", None)]
        hints = {"type": "", "match": "raw header / value / keyword",
                 "result": "canonical field / value / device token",
                 "context": "conduit | cable | field name (optional)", "note": "optional"}
        for i, (name, choices) in enumerate(rows):
            tk.Label(d, text=name.capitalize(), bg=BG, fg=TEXT, font=(FONT, 10)).grid(
                row=i, column=0, sticky="e", padx=8, pady=5)
            if choices:
                var = tk.StringVar(value=vals.get(name, choices[0]))
                ttk.Combobox(d, textvariable=var, values=choices, state="readonly",
                             width=34).grid(row=i, column=1, padx=8, pady=5)
            else:
                var = tk.StringVar(value=vals.get(name, ""))
                ttk.Entry(d, textvariable=var, width=36).grid(row=i, column=1, padx=8, pady=5)
            tk.Label(d, text=hints[name], bg=BG, fg=MUTED, font=(FONT, 8)).grid(
                row=i, column=2, sticky="w", padx=4)
            fields[name] = var

        def ok():
            rec = {k: v.get().strip() for k, v in fields.items()}
            if item is not None:
                self.tree.item(item, values=(rec["type"], rec["match"], rec["result"],
                                             rec["context"], rec["note"]))
            else:
                self.tree.insert("", "end", values=(rec["type"], rec["match"], rec["result"],
                                                     rec["context"], rec["note"]))
            d.destroy()
        bf = tk.Frame(d, bg=BG); bf.grid(row=len(rows), column=0, columnspan=3, pady=10)
        ttk.Button(bf, text="OK", command=ok, style="Run.TButton").pack(side="left", padx=6)
        ttk.Button(bf, text="Cancel", command=d.destroy).pack(side="left", padx=6)

    def _edit_rule(self):
        sel = self.tree.selection()
        if not sel:
            return
        v = self.tree.item(sel[0], "values")
        preset = dict(zip(("type", "match", "result", "context", "note"), v))
        self._rule_dialog(preset=preset, item=sel[0])

    def _remove_rule(self):
        for s in self.tree.selection():
            self.tree.delete(s)

    def _save_logic(self):
        rules = []
        for iid in self.tree.get_children():
            t, m, r, c, n = self.tree.item(iid, "values")
            rules.append({"type": t, "match": m, "result": r, "context": c, "note": n})
        data = {"rules": rules, "notes": self.notes.get("1.0", "end").strip()}
        logic_store.save(data)
        summary = logic_store.apply(data)
        self.logic_data = data
        messagebox.showinfo("Saved", "Remembered logic saved & applied.\n" + summary)

    # ---- pickers ----
    def _default_out_dir(self):
        """A real, absolute save folder — the current output's folder if the user set
        one, else the DICTATED output folder (never a source/project folder, so filled
        workbooks all land in one designated place)."""
        cur = os.path.dirname(self.output.get())
        if cur and os.path.isdir(cur):
            return os.path.abspath(cur)
        try:
            import idp_settings
            return idp_settings.get_output_dir()
        except Exception:
            return os.path.abspath(os.path.join(os.path.expanduser("~"), "Desktop"))

    def _suggest_output(self):
        """Auto-name the output after the detected project (e.g. '73.1163_
        Stratford_FILLED.xlsm') so results are self-labeling. Only overwrites the
        field while it still looks auto-generated (never clobbers a name the user
        typed themselves); the worker re-derives this at run time too."""
        site = idp_project.detect_site_name(self.pdfs)
        if not site:
            return
        cur = self.output.get()
        # recognize both the new (_FILLED) and legacy (_IDP_FILLED) auto names
        looks_auto = (not cur) or os.path.basename(cur).upper().endswith(
            ("_FILLED.XLSM", "_IDP_FILLED.XLSM"))
        if not looks_auto:
            return
        out_dir = os.path.dirname(cur) or self._default_out_dir()
        self.output.set(os.path.join(out_dir, f"{site}_FILLED.xlsm"))

    def add_pdfs(self):
        for f in filedialog.askopenfilenames(title="Select PDFs",
                                             filetypes=[("PDF", "*.pdf"), ("All", "*.*")]):
            if f not in self.pdfs:
                self.pdfs.append(f); self.pdf_list.insert("end", f)
        self._suggest_output(); self._persist()

    def add_excel(self):
        for f in filedialog.askopenfilenames(
                title="Select Excel sources (IDP workbook or conduit list)",
                filetypes=[("Excel", "*.xlsx *.xlsm *.xls"), ("All", "*.*")]):
            if f not in self.pdfs:
                self.pdfs.append(f); self.pdf_list.insert("end", f)
        self._suggest_output(); self._persist()

    def add_folder(self):
        d = filedialog.askdirectory(title="Select a folder of source files")
        if not d:
            return
        try:
            import idp_settings
            idp_settings.add_recent_project(d)   # remember this project folder
        except Exception:
            pass
        added = 0
        for dirpath, _dirnames, filenames in os.walk(d):
            for name in sorted(filenames):
                if name.lower().endswith((".pdf", ".xlsx", ".xlsm", ".xls")):
                    f = os.path.join(dirpath, name)
                    if f not in self.pdfs:
                        self.pdfs.append(f); self.pdf_list.insert("end", f)
                        added += 1
        self._suggest_output(); self._persist()
        self.logmsg(f"Added {added} file(s) from {d} (including subfolders).")

    def remove_pdf(self):
        for i in reversed(self.pdf_list.curselection()):
            self.pdf_list.delete(i); del self.pdfs[i]
        self._persist()

    def clear_pdfs(self):
        self.pdf_list.delete(0, "end"); self.pdfs = []
        self._persist()

    def pick_template(self):
        f = filedialog.askopenfilename(title="Template workbook",
                                       filetypes=[("Excel macro workbook", "*.xlsm"), ("All", "*.*")])
        if not f:
            return
        try:
            check_template_sane(f)
        except ValueError as e:
            messagebox.showerror("Bad template", str(e))
            return
        self.template.set(f)
        try:
            import idp_settings
            idp_settings.set_template_path(f)   # remember as the designated template
        except Exception:
            pass
        if not self.output.get():
            base, ext = os.path.splitext(f)
            self.output.set(base + "_FILLED" + ext)
        self._suggest_output(); self._persist()

    def pick_output(self):
        """Choose a new save FOLDER; keep the workbook name (just update the
        directory). Name defaults to the detected project if none set yet."""
        d = filedialog.askdirectory(title="Choose save folder (workbook name is kept)")
        if not d:
            return
        name = os.path.basename(self.output.get())
        if not name:
            site = idp_project.detect_site_name(self.pdfs)
            name = f"{site}_FILLED.xlsm" if site else "IDP_FILLED.xlsm"
        self.output.set(os.path.join(os.path.abspath(d), name))
        try:
            import idp_settings
            idp_settings.set_output_dir(d)   # remember as the dictated output folder
        except Exception:
            pass
        self._persist()

    # ---- logging ----
    def logmsg(self, m):
        self.log_q.put(m)

    def _drain_log(self):
        while not self.log_q.empty():
            self.log.configure(state="normal")
            self.log.insert("end", self.log_q.get() + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain_log)

    def _drain_prov(self):
        while not self.prov_q.empty():
            self.set_provenance(self.prov_q.get())
        self.after(150, self._drain_prov)

    # ---- run ----
    def run(self):
        if not self.pdfs and not getattr(self, "_pending_schedule", None):
            messagebox.showwarning("Missing input", "Add at least one source file (PDF or Excel), "
                                   "or enter a conduit schedule and click ‘Include in Scan run’."); return
        if not os.path.isfile(self.template.get()):
            messagebox.showwarning("Missing template", "Select a valid template workbook."); return
        if not self.output.get():
            messagebox.showwarning("Missing output", "Choose where to save the result."); return
        try:
            check_template_sane(self.template.get())
        except ValueError as e:
            messagebox.showerror("Bad template", str(e)); return
        self.run_btn.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        import time
        _t0 = time.time()
        try:
            self.logmsg(logic_store.apply())   # apply remembered logic first
            mode = self.mode.get()
            infer = self.infer_symbols.get()
            unk = self.unknown_type.get()
            all_recs = []
            # manually-entered conduit schedule (vector sheets, entered in-exe) —
            # seed these so EDC terminals/symbols attach to them during the scan
            if getattr(self, "_pending_schedule", None):
                srecs = idp_schedule.rows_to_records(self._pending_schedule)
                all_recs.extend(srecs)
                self.logmsg(f"Conduit Schedule: {len(srecs)} manually-entered conduit(s) included "
                            f"in this run.")
            # smart, EARLY-STOPPING targeting: inspect files in priority order (ENGINEERING
            # first) by name, stop once we hold the conduit schedule + a terminations source,
            # and extract ONLY those — never the whole project. Fully offline; no Claude.
            _conduit_srcs, _edc_srcs = self.pdfs, []
            try:
                import idp_router
                _manifest = idp_router.discover_sources(self.pdfs, log=self.logmsg)
                for _ln in idp_router.routing_report(_manifest).split("\n"):
                    self.logmsg(_ln)
                _conduit_srcs = idp_router.conduit_sources(_manifest) or []
                _edc_srcs = idp_router.edc_sources(_manifest) or []
                if not _conduit_srcs:
                    _conduit_srcs = [p for role, items in _manifest.items()
                                     if role not in ("skip", "cover_letter", "cut_sheet", "other")
                                     for (p, _r) in items] or self.pdfs
                _conduit_srcs = _conduit_srcs[:3]
                _edc_srcs = idp_router.scope_edc_to_conduit(_conduit_srcs, _edc_srcs)
            except Exception as e:
                self.logmsg(f"   (routing skipped: {e})")
                _conduit_srcs, _edc_srcs = self.pdfs, []
            for src in _conduit_srcs:
                name = os.path.basename(src)
                self.logmsg(f"Scanning {name} …")
                try:
                    recs, method = extract_source(src, mode=mode, infer_symbols=infer,
                                                  ocr_refine=self.ocr_hi_accuracy.get(),
                                                  log=self.logmsg)
                except Exception as e:
                    self.logmsg(f"   ! error: {e}")
                    continue
                if unk != "XXX":
                    for r in recs:
                        if str(r.get("ctype", "")).strip() in ("", "XXX"):
                            r["ctype"] = unk
                flagged = sum(1 for r in recs if r.get("flags"))
                if recs:
                    self.logmsg(f"   → {len(recs)} conduits via {method}"
                                + (f"  ({flagged} flagged)" if flagged else ""))
                elif method == "cover-letter":
                    self.logmsg("   → submittal cover letter (no conduit data) — skipped.")
                elif method == "edc-source":
                    self.logmsg("   → EDC drawing package (terminals, not a conduit "
                                "schedule). Its S/D terms will attach to the conduits "
                                "from your conduit-schedule source during this scan.")
                elif method == "ocr-schedule":
                    lc = sum(1 for r in recs if "ocr_low_confidence" in r.get("flags", []))
                    self.logmsg(f"   → vector conduit schedule read by offline OCR: "
                                f"{len(recs)} conduits"
                                + (f"  ({lc} flagged amber — verify against the sheet)" if lc else ""))
                elif method == "scanned-needs-vision":
                    import idp_escalate
                    d = idp_escalate._localappdata_dir()
                    self.logmsg("   → conduit schedule is on a SCANNED/vector sheet (no text "
                                "layer). Rendered the page images to:")
                    self.logmsg(f"       {d}")
                    self.logmsg("     To read it: open ASK_CLAUDE_VISION.md there and paste "
                                "the images into your Claude chat (no API key needed).")
                else:
                    self.logmsg("   → nothing extractable")
                all_recs.extend(recs)

            if not all_recs:
                self.logmsg("No CONDUITS found — you scanned only EDC/terminal or scanned "
                            "sheets. Add the source that has the CONDUIT SCHEDULE (or "
                            "transcribe the scanned schedule via the ASK_CLAUDE_VISION packet); "
                            "then the EDC terminals + symbols will populate. Nothing written.")
                return
            all_recs = merge_records(all_recs)   # dedup across sources (richer wins)
            self._last_records = all_recs        # for Training tab → "Ask Claude"
            # YIELD GUARD — never present a near-empty extraction as a finished workbook.
            _fill_n = sum(len(r.get("fill") or []) for r in all_recs)
            self._poor_yield = (len(all_recs) < 3 or _fill_n == 0)
            if self._poor_yield:
                # BOUNDED re-target: OCR only the finder-flagged schedule pages of other
                # candidates so a targeting false-positive self-corrects (Excel read directly).
                try:
                    import idp_router as _R2, idp_layouts as _L2, idp_ingest as _ing2
                    _rel = [p for p in self.pdfs if _L2.folder_relevance(p) != "skip"]
                    _cand, _c2 = _R2._name_preselect(_rel)
                    _better = _ing2.retarget_schedule(_cand, tried=set(_conduit_srcs),
                                                      infer=infer,
                                                      hi_ocr=self.ocr_hi_accuracy.get(),
                                                      log=self.logmsg)
                    if len(_better) > len(all_recs):
                        all_recs = merge_records(_better); self._last_records = all_recs
                        _fill_n = sum(len(r.get("fill") or []) for r in all_recs)
                        self._poor_yield = (len(all_recs) < 3 or _fill_n == 0)
                except Exception as e:
                    self.logmsg(f"   (re-target skipped: {e})")
            if self._poor_yield:
                try:
                    import idp_vision_schedule as _VS
                    vrecs = _VS.read_schedule_via_vision(_conduit_srcs or self.pdfs, log=self.logmsg)
                    if len(vrecs) > len(all_recs):
                        all_recs = merge_records(vrecs); self._last_records = all_recs
                        _fill_n = sum(len(r.get("fill") or []) for r in all_recs)
                        self._poor_yield = (len(all_recs) < 3 or _fill_n == 0)
                except Exception as e:
                    self.logmsg(f"   (vision schedule-read skipped: {e})")
            if self._poor_yield:
                self.logmsg("⚠⚠ COULD NOT READ THE CONDUIT SCHEDULE — this workbook is INCOMPLETE "
                            "and should not be used as-is (schedule likely embedded in a plan "
                            "sheet). Set an API key + enable Vision-assist to auto-read it.")
                try:
                    import idp_vision_schedule as _VS, idp_escalate, os as _os
                    refs = _VS.find_schedule_pages(_conduit_srcs or self.pdfs)
                    if refs:
                        _od = _os.path.join(idp_escalate._localappdata_dir(), "_schedule_pages")
                        _imgs = _VS.render_schedule_pages(refs, _od)
                        self.logmsg(f"   Rendered {len(_imgs)} candidate schedule sheet(s) → {_od}.")
                except Exception:
                    pass
            # backfill S Tag/Term from any wiring-diagram PDFs (matched by device name)
            _term_srcs = _edc_srcs or [p for p in _conduit_srcs if p.lower().endswith(".pdf")]
            try:
                binds = collect_wiring_bindings(_term_srcs)
                if binds:
                    n, ex = apply_wiring_terms(all_recs, binds)
                    self.logmsg(f"Wiring diagrams: {len(binds)} I/O bindings → "
                                f"{n} conduit(s) term-backfilled"
                                + (f"  (e.g. {ex[0]})" if ex else ""))
            except Exception as e:
                self.logmsg(f"   (wiring backfill skipped: {e})")
            # pull FillIndex terminals off AIC EDC drawing sheets (three-line /
            # terminal / PLC I/O / analog) + parse any text-layer panelboard for
            # branch-circuit breaker ratings. Vision via API key if set, else the
            # sheets are rendered and an ASK_CLAUDE_EDC.md packet is written.
            try:
                import idp_ingest as _ing
                _ing.apply_edc_terms_from_paths(all_recs, _term_srcs, log=self.logmsg,
                                                allow_api=False, write_packet=True)
            except Exception as e:
                self.logmsg(f"   (EDC term extraction skipped: {e})")
            # bridge OCR-hard cells (channels/circuits/dense cable lists) to an OFFLINE
            # packet — allow_api=False so a SCAN never contacts Claude (output stays offline)
            try:
                import idp_vision_assist
                idp_vision_assist.assist(all_recs, _conduit_srcs + _edc_srcs,
                                         log=self.logmsg, allow_api=False)
            except Exception as e:
                self.logmsg(f"   (vision-assist skipped: {e})")
            # Symbol confirmation reads the BLOCK-LIBRARY folder (what the blocks look like)
            # and infers — NO AutoCAD scan, ever. Symbols don't change, so it's read once
            # and applied every run.
            if infer:
                try:
                    import idp_edc_symbols, idp_project_symbols as _ps
                    idp_edc_symbols.read_symbols_from_edc(all_recs, _term_srcs,
                                                          _ps.load_symbol_library(),
                                                          log=self.logmsg)
                except Exception as e:
                    self.logmsg(f"   (EDC block symbol read skipped: {e})")
                try:
                    n, src, _ = apply_project_dwg_symbols(all_recs, self.pdfs)
                    if src:
                        self.logmsg(f"Symbols: confirmed {n} against the block library "
                                    f"({os.path.basename(str(src).rstrip('/' + chr(92)))}) — no AutoCAD scan.")
                    else:
                        self.logmsg("Symbols: block library folder not found — inferred from "
                                    "device names/cut sheets only.")
                except Exception as e:
                    self.logmsg(f"   (symbol confirmation skipped: {e})")
                # Vision block-read (runs only if an ANTHROPIC_API_KEY is set): render EDC
                # landings and match graphical blocks to the library for unresolved symbols.
                try:
                    import idp_edc_symbols, idp_project_symbols as _ps
                    idp_edc_symbols.confirm_symbols_via_vision(
                        all_recs, _term_srcs, _ps.load_symbol_library(), log=self.logmsg)
                except Exception as e:
                    self.logmsg(f"   (vision block-read skipped: {e})")
            else:
                self.logmsg("Symbol confirmation skipped (enable 'Infer S/D symbols').")
            self.logmsg(kb_expand.expand_from_records(all_recs))   # grow the KB from this run

            # surface what this run DIDN'T confidently know, as teachable rows in
            # Remembered Logic — one-time-taught fixes apply to every future run
            if self.learn_logic.get():
                try:
                    # dedup against the CURRENT store on disk (not the stale
                    # in-memory self.logic_data), since learn_from_finished_idps
                    # may have just persisted rules earlier in this same run
                    data = logic_store.load()
                    existing = [r.get("match", "") for r in data.get("rules", [])]
                    cands = derive_teach_candidates(all_recs, existing_matches=existing)
                    if cands:
                        data.setdefault("rules", []).extend(cands)
                        logic_store.save(data)
                        self.logmsg(f"Logic: {len(cands)} new item(s) added to Remembered "
                                    f"Logic for you to teach (low-confidence symbols / "
                                    f"unresolved conduit types).")
                        self.after(0, self._reload_logic)   # refresh the tab on the Tk thread
                except Exception as e:
                    self.logmsg(f"   (learn-logic skipped: {e})")

            # ALWAYS save into the dictated folder — never the project/source folder
            site = idp_project.detect_site_name(self.pdfs)
            import idp_settings
            out_path = idp_settings.resolve_output_path(site, self.output.get(), self.pdfs)
            self.logmsg(f"Site '{site or 'IDP'}' → saving to dictated folder: {os.path.dirname(out_path)}")
            target = versioned_path(out_path)   # never overwrite a prior result
            if target != out_path:
                self.logmsg(f"(output exists — writing new version: {os.path.basename(target)})")
            _clr = self.clear_deviations.get()
            if _clr:
                self.logmsg("Deviation-notes column will be written blank (clean sheets) — "
                            "circuit/EDC detection still uses the notes first.")
            self.logmsg(f"Writing {len(all_recs)} conduits → {os.path.basename(target)} …")
            out = write_workbook(all_recs, self.template.get(), target,
                                 clear_rows=self.clear_rows.get(), add_flags=self.add_flags.get(),
                                 clear_deviations=_clr)
            self.logmsg(f"Saved: {out}")
            if self.save_nogrey.get():
                ng = versioned_path(os.path.splitext(out)[0] + "_NoGrey.xlsm")
                n = degrey(out, ng)
                self.logmsg(f"De-greyed copy: {os.path.basename(ng)}  ({n} cells)")

            self.prov_q.put(idp_project.build_provenance(all_recs))

            # ── GENERATION CONFIDENCE — an honest read on how much of the output is
            # high-confidence vs. needs a human check, from the OCR/symbol confidences
            # and the amber flags the writer raised. Not a guarantee — a triage guide. ──
            try:
                _grps = [g for r in all_recs for g in (r.get("fill") or [])]
                _nc = len(all_recs) or 1
                _ng = len(_grps) or 1
                _ocr = sum(1 for r in all_recs if "from_ocr_schedule" in (r.get("flags") or []))
                _ocr_low = sum(1 for r in all_recs if "ocr_low_confidence" in (r.get("flags") or []))
                _low_sym = sum(1 for g in _grps
                               if min(g.get("s_symbol_conf", 1.0), g.get("d_symbol_conf", 1.0)) < 0.6)
                _assumed = sum(1 for g in _grps if g.get("connection_remodel") or g.get("type_note"))
                _mfg = sum(1 for g in _grps if str(g.get("type")) == "MFG_CABLE")
                # grounds SYNTHESIZED by convention (not backed by an authoritative
                # cable/ground schedule) are assumptions — e.g. conduit-schedule-only OCR
                # has no ground column, so a ground gets added to every circuit. Counts
                # only when NOT ground-authoritative, so schedule-backed grounds don't ding.
                _assumed_gnd = sum(1 for r in all_recs if not r.get("ground_authoritative")
                                   for g in (r.get("fill") or []) if g.get("auto_ground"))
                _flags = sum(len(r.get("flags") or []) for r in all_recs)
                # coverage: how much of the fill came from the authoritative CABLE
                # schedule, and how many conduits carry a REAL EDC terminal landing.
                _cable = sum(1 for r in all_recs
                             if "fill_from_cable_schedule" in (r.get("flags") or []))
                _termed = sum(1 for r in all_recs if any(
                    (w.get("src") or ("", "", ""))[2] or (w.get("dst") or ("", "", ""))[2]
                    for w in (r.get("wires") or [])))
                _score = 100.0 - 22 * (_ocr_low / _nc) - 25 * (_low_sym / _ng) \
                    - 10 * (_assumed / _ng) - 8 * (_mfg / _ng) - 12 * (_assumed_gnd / _ng)
                _score = max(0.0, min(100.0, _score))
                # a near-empty extraction is INCOMPLETE, never HIGH
                if getattr(self, "_poor_yield", False) or len(all_recs) < 3 or not _grps:
                    _level, _score = "INCOMPLETE", 0.0
                else:
                    _level = "HIGH" if _score >= 88 else "MEDIUM" if _score >= 70 else "LOW"
                self.logmsg("")
                self.logmsg("── WORKBOOK FILL CONFIDENCE ────────────────────────────")
                self.logmsg(f"   Workbook: {os.path.basename(out)}")
                self.logmsg(f"   Confidence this workbook is correctly filled: {_level}  ({_score:.0f}%)")
                self.logmsg(f"   conduits filled: {len(all_recs)}   fill groups: {len(_grps)}")
                self.logmsg(f"   fill from CABLE schedule (precise types + real grounds): "
                            f"{_cable}/{len(all_recs)}")
                self.logmsg(f"   conduits with EDC terminal landings: {_termed}/{len(all_recs)}")
                self.logmsg(f"   OCR-sourced rows: {_ocr}  (low-confidence: {_ocr_low})")
                self.logmsg(f"   uncertain symbols: {_low_sym}   type assumptions: {_assumed}"
                            f"   whole-cable (MFG_CABLE): {_mfg}   synthesized grounds: {_assumed_gnd}")
                self.logmsg(f"   amber flags to verify: {_flags}")
                _advice = ("Looks clean — spot-check the amber-flagged cells." if _level == "HIGH"
                           else "Usable, but review the flagged rows (OCR/symbols/terms) before issue."
                           if _level == "MEDIUM"
                           else "Verify carefully against the source — many cells are inferred/OCR-low.")
                self.logmsg(f"   → {_advice}")
                self.logmsg("───────────────────────────────────────────────────────")
            except Exception as e:
                self.logmsg(f"(confidence summary skipped: {e})")

            _elapsed_min = (time.time() - _t0) / 60.0
            self.logmsg(f"⏱ Generated the Excel in {_elapsed_min:.1f} min "
                        f"({time.time() - _t0:.0f}s) for {len(all_recs)} conduit(s).")
            self.logmsg("Done. ✔")
            # marshal all Tk touches back to the main thread (this runs on a worker
            # thread; creating dialogs / configuring widgets off-thread is unsafe)
            self.after(0, lambda o=out: messagebox.showinfo("Complete", f"Saved:\n{o}"))
        except Exception as e:
            self.logmsg("ERROR: " + str(e))
            self.logmsg(traceback.format_exc())
            self.after(0, lambda msg=str(e): messagebox.showerror("Error", msg))
        finally:
            self.after(0, lambda: self.run_btn.configure(state="normal"))


if __name__ == "__main__":
    App().mainloop()
