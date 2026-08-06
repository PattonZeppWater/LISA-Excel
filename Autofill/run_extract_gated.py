"""
run_extract_gated.py — run the extractor through the full new architecture:
  build records -> apply remembered logic -> LISA-readiness gate (snap + report)
  -> grow KB -> write a VERSIONED (never-overwrite) workbook.

Demonstrates the end-to-end pipeline on 73.1188 (Crows Landing).
"""
import os
import fill_73_1188
import idp_write, logic_store, kb_expand, lisa_contract

TMPL = r"C:/Users/cole.mclaughlin/OneDrive - Lyles Group/Desktop/Claude Files/IDP_Builder/resources/template/IDP_Workbook_CurrentWIP_3.xlsm"
OUT_BASE = r"C:/Users/cole.mclaughlin/OneDrive - Lyles Group/Desktop/Claude Files/Excel template/Filled/73.1188_CrowsLanding_FILLED.xlsm"


def main():
    print("logic:", logic_store.apply())
    recs = fill_73_1188.build_records()
    n_rows = sum(len(r["fill"]) for r in recs)
    print(f"conduits: {len(recs)}  fill rows: {n_rows}")

    before = lisa_contract.check_records(recs)
    remodels = lisa_contract.normalize_connections(recs)
    after = lisa_contract.check_records(recs)
    print(f"\nLISA-readiness gate: {len(before)} initial violation(s) -> "
          f"{len(remodels)} row(s) remodeled -> {len(after)} remaining")
    print("connection remodels:")
    for r in remodels[:40]:
        print(f"  [{r['conduit']:<7}] {r['note']}")
    if len(remodels) > 40:
        print(f"  ... +{len(remodels) - 40} more")
    if after:
        print("STILL FAILING:")
        for iss in after:
            print(f"  row {iss['row']:>3}  {iss['field']:<9} {iss['value']!r:<24} {iss['problem']}")

    print("\n" + kb_expand.expand_from_records(recs))

    out = idp_write.versioned_path(OUT_BASE)
    idp_write.write_workbook(recs, TMPL, out, clear_rows=True, add_flags=True)
    ng = idp_write.versioned_path(os.path.splitext(out)[0] + "_NoGrey.xlsm")
    n = idp_write.degrey(out, ng)
    print(f"\nwrote {os.path.basename(out)}  (de-greyed {n} cells -> {os.path.basename(ng)})")
    print("previous results preserved (no overwrite).")


if __name__ == "__main__":
    main()
