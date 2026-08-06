"""run_learn_all_projects.py — harvest finished-IDP logic from every project
folder under Test Extractions, one at a time (sequential, so AutoCAD's COM
session is never juggled between overlapping scans)."""
import os
import idp_ingest

BASE = r"C:\Users\cole.mclaughlin\OneDrive - Lyles Group\Desktop\Claude Files\Test Extractions"
FOLDERS = [
    "56.1125 SanRafael",
    "56.1128 SFPUC",
    "73.1072 PID",
    "73.1105 PCWA",
    "73.1131 Sweeney Ranch",
    "73.1142 Lennar",
    "73.1154 BickfordRanch",
    "73.1163 Stratford",
]

total_added, total_dwgs = 0, 0
for name in FOLDERS:
    path = os.path.join(BASE, name)
    print(f"=== {name} ===", flush=True)
    try:
        added, ndwg = idp_ingest.learn_from_finished_idps([path])
        print(f"  {ndwg} dwg(s) scanned -> {added} new rule(s) learned", flush=True)
        total_added += added
        total_dwgs += ndwg
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)

print(f"\nTOTAL: {total_dwgs} dwgs across {len(FOLDERS)} folders -> {total_added} new rules learned")
