"""run_stratford_full.py — full pipeline test: Excel conduit list + PLC wiring PDF
+ this project's own finished-IDP DWGs, through the complete gated/anatomy/
project-naming/provenance pipeline (mirrors the control panel worker)."""
import os
import idp_ingest, idp_write, lisa_contract, idp_anatomy, idp_project, logic_store, kb_expand

BASE = "../73.1163 Stratford/IDP/"
SOURCES = [
    BASE + "73.1163 - IDP - CONDUIT LIST 20251121.xlsx",
    BASE + "73.1163_IDP_ToSunKPO_20251111/PLC Cabinet/73.1163_EDC_PLC_Panel_R00_20250923.pdf",
]
TEMPLATE = "../IDP_Builder/resources/template/IDP_Workbook_CurrentWIP_3.xlsm"
OUT_DIR = "../Excel template/Filled"


def main():
    print(logic_store.apply())
    all_recs = []
    for p in SOURCES:
        recs, method = idp_ingest.extract_source(p)
        print(f"{os.path.basename(p):<55} -> {len(recs):>3} conduits via {method}")
        all_recs += recs
    all_recs = idp_ingest.merge_records(all_recs)
    print(f"merged: {len(all_recs)} conduits, {sum(len(r['fill']) for r in all_recs)} fill rows")

    binds = idp_ingest.collect_wiring_bindings(SOURCES)
    nterm, ex = idp_ingest.apply_wiring_terms(all_recs, binds)
    print(f"wiring: {len(binds)} bindings -> {nterm} conduits term-backfilled: {ex}")

    nsym, root, ndwg = idp_ingest.apply_project_dwg_symbols(all_recs, SOURCES)
    print(f"project DWGs: {ndwg} found under {root}\n  -> {nsym} symbols confirmed against this project's own blocks")

    before = lisa_contract.check_records(all_recs)
    arche_before = idp_anatomy.check_archetypes(all_recs)
    print(f"pre-write: {len(before)} LISA-contract issues, {len(arche_before)} archetype notes")

    project = idp_project.detect_project_name(SOURCES)
    out = idp_write.versioned_path(os.path.join(OUT_DIR, f"{project}_IDP_FILLED.xlsm"))
    idp_write.write_workbook(all_recs, TEMPLATE, out)   # gate + anatomy run inside
    print(f"wrote {os.path.basename(out)}")

    after = lisa_contract.check_records(all_recs)
    arche_after = idp_anatomy.check_archetypes(all_recs)
    print(f"post-write: {len(after)} LISA-contract issues remaining")
    print(f"archetype notes ({len(arche_after)}):")
    for a in arche_after[:20]:
        print(f"   [{a['conduit']}] {a['note']}")
    if len(arche_after) > 20:
        print(f"   ... +{len(arche_after) - 20} more")

    prov = idp_project.build_provenance(all_recs)
    print(f"\nprovenance rows: {len(prov)}")
    by_src = {}
    for r in prov:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    for src, n in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>5}  {src}")

    print("\n" + kb_expand.expand_from_records(all_recs))


if __name__ == "__main__":
    main()
