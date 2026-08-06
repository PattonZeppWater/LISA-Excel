"""
fill_56_1125.py — build the 56.1125 (San Rafael, Third Street Pump Station)
ConduitIndex + FillIndex from Sheet E05 "CONDUIT AND CABLE SCHEDULE"
(Plans (Marked-Up).pdf, page 18 of 25).

The schedule is CAD-drawn graphics — pdfplumber's lattice table extraction
mangles it into unusable garbage (confirmed: this exact page is the source of
the garbled '= - 27 - = -' text that corrupted an earlier extraction) — so it
was transcribed visually, same approach used for Crows Landing (73.1188).

Columns: NO. | SIZE | CABLE SIZE AND QUANTITY | FROM | TO | REMARKS
General note 2: conduits are PVC-coated rigid galvanized steel (PVC-GRS)
unless shown otherwise -> normalized to our 'RMC-PVC' Conduit Type, flagged
since it's a blanket note, not stated per-row.
"""
import os
import re
import idp_extract, idp_write, idp_ingest, lisa_contract, idp_anatomy, logic_store, kb_expand, idp_project

_GROUND_TOKEN_RE = re.compile(r"#\s*\d+\s*G\b")   # e.g. '#8G', '#10G', '#14G'

# (no, size, cable_desc, frm, to, remarks)
ROWS = [
    ("SER", '3"',    "PULLTAPE",
     "(EX) PG&E TRANSFORMER", "METERING ENCLOSURE",
     "208V SERVICE, CONDUCTORS BY PG&E"),
    ("E1", '1 1/2"', "3-#4, #8G",
     "METERING ENCLOSURE", "PCP - MTS (NORMAL)",
     "208V UTILITY POWER"),
    ("E2", '1 1/2"', "6-#10, 4-#14, #10G",
     "PCP - MOTOR STARTERS", "HANDHOLE HH-E1",
     "2 SETS OF 208V POWER, AND TS, MS"),
    ("E3", '1 1/2"', "MANU CABLE (POWER, TS, MS) PUMP 1",
     "WETWELL - PUMP 1", "HANDHOLE HH-E1, EYS FITTING",
     "208V PUMP POWER, AND TS, MS"),
    ("E4", '1 1/2"', "MANU CABLE (POWER, TS, MS) PUMP 2",
     "WETWELL - PUMP 2", "HANDHOLE HH-E1, EYS FITTING",
     "208V PUMP POWER, AND TS, MS"),
    ("E5", '1"',     "PULL TAPE",
     "PCP - PUMP SECTION", "STUB OUT BEHIND PCP",
     "CAP AT 5' BEYOND PCP"),
    ("S1", '2"',     "5 MANU CABLES (OFF, LEAD, LAG, HIGH, LT)",
     "WETWELL - FLOATS AND LT", "HANDHOLE HH-S1, EYS FITTING",
     "MANU CABLES, VIA EYS TO WET WELL"),
    ("S2", '2"',     "5 MANU CABLES (OFF, LEAD, LAG, HIGH, LT)",
     "HANDHOLE HH-S1", "PCP - CONTROL PANEL SECTION",
     "NO SPLICES AT HANDHOLE"),
    ("S3", '1"',     "MANU CABLE (FLOOD), #16 TWSP, #14G",
     "VALVE VAULT J-BOX", "HANDHOLE HH-S1, EYS FITTING",
     "VIA EYS TO WET WELL"),
    ("S4", '1"',     "MANU CABLE (FLOOD), #16 TWSP, #14G",
     "HANDHOLE HH-S1", "PCP - CONTROL PANEL SECTION",
     "NO SPLICES AT HANDHOLE"),
    ("S5", '1 1/2"', "ANTENNA CABLE, #14G",
     "PCP - CONTROL PANEL SECTION", "ANTENNA MAST, WEATHERHEAD",
     "WATERPROOF SPLICE KIT AT ANTENNA"),
    ("S6", '1"',     "PULL TAPE",
     "PCP - CONTROL PANEL SECTION", "STUB OUT BEHIND PCP",
     "CAP AT 5' BEYOND PCP"),
]

DEFAULT_CTYPE = "RMC-PVC"   # general note 2: PVC-coated RGS unless shown otherwise
SRC_PDF = "Plans (Marked-Up).pdf (Sheet E05, pg 18 of 25)"


def _fill_for(no, cable_desc):
    """Return (type, count, gauge, flags) for one row's cable description."""
    d = cable_desc.upper()
    if d in ("PULLTAPE", "PULL TAPE"):
        return "PULL_ROPE", 1, "", []
    if "ANTENNA CABLE" in d:
        return "FIBER", 1, "#14", []          # FIBER bucket carries the ANT symbol
    if "TWSP" in d:                            # shielded twisted pair signal
        return "TSP", 1, "#16", ["ground_folded_note:#14G drain"]
    if no in ("E3", "E4"):                     # combined mfr power+TS+MS cable
        return "MFG_CABLE", 1, "", ["mfg_cable_assumed"]
    if no == "E2":                              # 2 sets of power to motor starters
        return "POWER", 3, "#10", ["aggregate_2_starters"]
    if no == "E1":                              # utility service feed, 3-#4 + gnd
        return "POWER", 3, "#4", []
    if "MANU CABLES" in d and "OFF" in d:        # 5 discrete float-switch signal cables
        return "CONTROL", 4, "#14", ["five_float_cables_grouped"]
    return "CONTROL", 1, "", ["unclassified_cable_desc"]


def build_records():
    recs = []
    for (no, size, cable_desc, frm, to, remarks) in ROWS:
        typ, count, gauge, cflags = _fill_for(no, cable_desc)
        # POWER rows: the schedule's '#nG' token is a real safety ground conductor
        # in the same cable -> set explicit phase + ground colors so the anatomy
        # convention pass (which only fills a BLANK colors list) doesn't need to
        # guess. Non-POWER '#nG' tokens (e.g. TSP's '#14G') are a shield/drain,
        # not a safety ground -> leave colors blank for the normal TSP convention.
        has_ground_token = bool(_GROUND_TOKEN_RE.search(cable_desc.upper()))
        if typ == "POWER" and has_ground_token:
            colors = idp_anatomy.POWER_PHASES[:count] + ["GRN"]
        else:
            colors = []
        # The schedule's REMARKS column is real data we were previously discarding —
        # carry it into D Description 1 (LISA_workbook_mapper.py confirms this is a
        # general free-text field, not gated to any symbol type). Flagged since
        # source-vs-destination attribution of a remark is a judgment call.
        fill = [{"type": typ, "count": count, "gauge": gauge, "colors": colors,
                "d_desc": [remarks],
                "d_desc_note": "Populated from the schedule's REMARKS column; "
                               "verify source vs. destination attribution."}]
        idp_extract._attach_symbols(fill, frm, to)
        rec_flags = ["from_drawing_schedule", "ctype_from_general_note"] + cflags
        recs.append({
            "name": no, "source": [frm], "dest": [to],
            "size": size, "ctype": DEFAULT_CTYPE,
            "docs": [], "wires": [], "fill": fill,
            "deviations": remarks, "flags": rec_flags,
            "_src_conduit": SRC_PDF,
            "_field_src": {"name": SRC_PDF, "source": SRC_PDF, "dest": SRC_PDF,
                          "size": SRC_PDF, "deviations": SRC_PDF},
        })
        fill[0]["_src_fill"] = SRC_PDF
        fill[0]["_field_src"] = {"type": SRC_PDF, "wire_ct": SRC_PDF, "gauge": SRC_PDF}
    return recs


if __name__ == "__main__":
    print(logic_store.apply())
    recs = build_records()
    print(f"conduits: {len(recs)}  fill rows: {sum(len(r['fill']) for r in recs)}")

    before = lisa_contract.check_records(recs)
    n_sym, root, ndwg = idp_ingest.apply_project_dwg_symbols(
        recs, ["../Test Extractions/56.1125 SanRafael/Plans (Marked-Up).pdf"])
    print(f"project DWG symbol scan: {ndwg} dwgs under {root} -> {n_sym} confirmed")
    print(f"pre-write LISA-contract issues: {len(before)}")

    print(kb_expand.expand_from_records(recs))

    tmpl = r"C:/Users/cole.mclaughlin/OneDrive - Lyles Group/Desktop/Claude Files/IDP_Builder/resources/template/IDP_Workbook_CurrentWIP_3.xlsm"
    project = idp_project.detect_project_name(["../Test Extractions/56.1125 SanRafael/Plans (Marked-Up).pdf"])
    out_dir = r"C:/Users/cole.mclaughlin/OneDrive - Lyles Group/Desktop/Claude Files/Test Extractions/Filled Workbooks"
    out = idp_write.versioned_path(os.path.join(out_dir, f"{project}_IDP_FILLED.xlsm"))
    idp_write.write_workbook(recs, tmpl, out, clear_rows=True, add_flags=True)

    after = lisa_contract.check_records(recs)
    arche = idp_anatomy.check_archetypes(recs)
    print(f"post-write LISA-contract issues: {len(after)}")
    print(f"archetype notes ({len(arche)}):")
    for a in arche:
        print(f"   [{a['conduit']}] {a['note']}")

    ng = idp_write.versioned_path(os.path.splitext(out)[0] + "_NoGrey.xlsm")
    n = idp_write.degrey(out, ng)
    print(f"wrote {os.path.basename(out)}  (de-greyed {n} cells -> {os.path.basename(ng)})")

    prov = idp_project.build_provenance(recs)
    print(f"provenance rows: {len(prov)}")
