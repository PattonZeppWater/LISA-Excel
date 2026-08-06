"""
IDP Extractor - GUI front end.

Double-click the .exe:
  1. Add one or more IDP PDF files.
  2. Choose the IDP workbook template (.xlsm).
  3. Choose where to save the filled workbook.
  4. Click Run.
"""

import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from idp_extract import parse_pdf, extract_conduits
from idp_write import write_workbook


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IDP PDF -> Workbook Extractor")
        self.geometry("720x520")
        self.pdfs = []
        self.template = tk.StringVar()
        self.output = tk.StringVar()
        self.log_q = queue.Queue()
        self._build()
        self.after(100, self._drain_log)

    def _build(self):
        pad = {"padx": 8, "pady": 4}

        # --- PDFs ---
        f1 = ttk.LabelFrame(self, text="1. PDF files")
        f1.pack(fill="both", expand=False, **pad)
        self.pdf_list = tk.Listbox(f1, height=6)
        self.pdf_list.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        b1 = ttk.Frame(f1); b1.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(b1, text="Add PDFs...", command=self.add_pdfs).pack(fill="x")
        ttk.Button(b1, text="Remove", command=self.remove_pdf).pack(fill="x", pady=4)
        ttk.Button(b1, text="Clear", command=self.clear_pdfs).pack(fill="x")

        # --- template ---
        f2 = ttk.LabelFrame(self, text="2. Template workbook (.xlsm)")
        f2.pack(fill="x", **pad)
        ttk.Entry(f2, textvariable=self.template).pack(side="left", fill="x",
                                                       expand=True, padx=6, pady=6)
        ttk.Button(f2, text="Browse...", command=self.pick_template).pack(
            side="right", padx=6, pady=6)

        # --- output ---
        f3 = ttk.LabelFrame(self, text="3. Save filled workbook as")
        f3.pack(fill="x", **pad)
        ttk.Entry(f3, textvariable=self.output).pack(side="left", fill="x",
                                                     expand=True, padx=6, pady=6)
        ttk.Button(f3, text="Browse...", command=self.pick_output).pack(
            side="right", padx=6, pady=6)

        # --- run + log ---
        self.run_btn = ttk.Button(self, text="Run", command=self.run)
        self.run_btn.pack(**pad)
        lf = ttk.LabelFrame(self, text="Log")
        lf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lf, height=10, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- file pickers ----
    def add_pdfs(self):
        files = filedialog.askopenfilenames(
            title="Select IDP PDF files",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        for f in files:
            if f not in self.pdfs:
                self.pdfs.append(f)
                self.pdf_list.insert("end", f)

    def remove_pdf(self):
        for i in reversed(self.pdf_list.curselection()):
            self.pdf_list.delete(i)
            del self.pdfs[i]

    def clear_pdfs(self):
        self.pdf_list.delete(0, "end")
        self.pdfs = []

    def pick_template(self):
        f = filedialog.askopenfilename(
            title="Select template workbook",
            filetypes=[("Excel macro workbook", "*.xlsm"),
                       ("Excel workbook", "*.xlsx"), ("All files", "*.*")])
        if f:
            self.template.set(f)
            if not self.output.get():
                base = os.path.splitext(f)[0]
                ext = os.path.splitext(f)[1]
                self.output.set(base + "_FILLED" + ext)

    def pick_output(self):
        tmpl = self.template.get()
        ext = os.path.splitext(tmpl)[1] or ".xlsm"
        f = filedialog.asksaveasfilename(
            title="Save filled workbook as", defaultextension=ext,
            filetypes=[("Excel macro workbook", "*.xlsm"),
                       ("Excel workbook", "*.xlsx"), ("All files", "*.*")])
        if f:
            self.output.set(f)

    # ---- logging ----
    def logmsg(self, msg):
        self.log_q.put(msg)

    def _drain_log(self):
        while not self.log_q.empty():
            msg = self.log_q.get()
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain_log)

    # ---- run ----
    def run(self):
        if not self.pdfs:
            messagebox.showwarning("Missing input", "Add at least one PDF file.")
            return
        if not self.template.get() or not os.path.isfile(self.template.get()):
            messagebox.showwarning("Missing template", "Select a valid template workbook.")
            return
        if not self.output.get():
            messagebox.showwarning("Missing output", "Choose where to save the result.")
            return
        self.run_btn.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            all_recs = []
            for pdf in self.pdfs:
                self.logmsg(f"Reading {os.path.basename(pdf)} ...")
                recs = parse_pdf(pdf)
                if recs:
                    self.logmsg(f"  -> {len(recs)} drawing pages found")
                else:
                    # No AIC IDP drawings — try a conduit schedule, then fall back
                    # to deriving conduits from the cable schedule's routing column.
                    recs, method = extract_conduits(pdf)
                    if recs:
                        flagged = sum(1 for r in recs if r.get("flags"))
                        how = ("schedule tables" if method == "conduit_schedule"
                               else "cable-schedule routing (derived)")
                        msg = f"  -> no IDP drawings; {len(recs)} conduits from {how}"
                        if flagged:
                            msg += f" ({flagged} flagged for review)"
                        self.logmsg(msg)
                    else:
                        self.logmsg("  -> 0 drawings, 0 schedule/derived conduits found")
                all_recs.extend(recs)
            self.logmsg(f"Writing {len(all_recs)} conduits into workbook ...")
            out = write_workbook(all_recs, self.template.get(), self.output.get())
            self.logmsg(f"Done. Saved to:\n{out}")
            messagebox.showinfo("Complete", f"Saved:\n{out}")
        except Exception as e:
            self.logmsg("ERROR: " + str(e))
            self.logmsg(traceback.format_exc())
            messagebox.showerror("Error", str(e))
        finally:
            self.run_btn.configure(state="normal")


if __name__ == "__main__":
    App().mainloop()
