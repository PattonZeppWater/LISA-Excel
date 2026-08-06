"""Harvest each Test Extractions project's CURATED finished-IDP CAD set (the
latest revision holding the -NNE IDP sheets), sequentially. Reuses any cached
scan; scans live only where needed. SanRafael skipped — its only CAD is a
metering enclosure, not a finished IDP set."""
import os
import idp_ingest

BASE = r"C:\Users\cole.mclaughlin\OneDrive - Lyles Group\Desktop\Claude Files\Test Extractions"
FINISHED = {
    "56.1128 SFPUC":       r"56.1128 SFPUC\sunkpo\IDP\From SunKPO\SUB_IDP_20251231\CAD",
    "73.1072 PID":         r"73.1072 PID\ToSunKPO\73.1072_IDP_20250630\WWP Reconfig\CAD",
    "73.1105 PCWA":        r"73.1105 PCWA\sunkpo\IDP_FromSunKPO\56.1105_SUB_IDP_R1_20250730\CAD",
    "73.1131 Sweeney Ranch": r"73.1131 Sweeney Ranch\SOW 1-5, Meter, MTS, Starter, FI\CAD",
    "73.1142 Lennar":      r"73.1142 Lennar\SOW#1,2\CAD",
    "73.1154 BickfordRanch": r"73.1154 BickfordRanch\_PRJ_IDP\R01\2. CAD DWG\CAD",
    "73.1163 Stratford":   r"73.1163 Stratford\IDP\73.1163_IDP_ToSunKPO_20260130\CAD",
}

total_added = 0
for proj, rel in FINISHED.items():
    path = os.path.join(BASE, rel)
    print(f"=== {proj} ===", flush=True)
    if not os.path.isdir(path):
        print("  (finished CAD folder not found — skipped)", flush=True)
        continue
    try:
        added, ndwg = idp_ingest.learn_from_finished_idps([path])
        print(f"  {ndwg} dwg(s) -> {added} new rule(s) learned", flush=True)
        total_added += added
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)

print(f"\nTOTAL new rules learned across finished sets: {total_added}")
