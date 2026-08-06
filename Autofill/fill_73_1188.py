"""
fill_73_1188.py — build the 73.1188 (Crows Landing) ConduitIndex + FillIndex from
Sheet E-3 "386 CONDUIT & CABLE SCHEDULE" (page 37 of the project set).

The schedule is drawn graphics (no text layer) so it was transcribed visually.
Columns: ID# | FROM | TO | CONDUIT(qty,size,type) | CONDUCTOR(qty,size,gnd) | CABLE(qty,type) | NOTES
"""
import os
import idp_extract, idp_write, logic_store, kb_expand

# (id, from, to, cdt_qty, size, ctype, cond_qty, cond_size, gnd, cable_qty, cable_type, notes)
ROWS = [
 ("P001","(E) T.I.D. POLE","NEW T.I.D. XFMER",1,'4"',"PVC40",0,"","",0,"","PRIMARY PER T.I.D."),
 ("H001","NEW T.I.D. XFMER","UGPS",3,'5"',"PVC40",12,"#750 MCM AL","",0,"","T.I.D. 1200A SECONDARY"),
 ("H002","UGPS","1200A MSB",3,'4"',"PVC40",12,"#400 MCM CU","",0,"","CABLES 1000A MLO"),
 ("H003","1000A MSB","ATS 1000A",3,'4"',"RMC",12,"#400 MCM","#2/o",0,"","MSB TB TO ATS"),
 ("H004","ATS","MCC1",3,'4"',"RMC",12,"#400 MCM","#2/o",0,"","1000A"),
 ("H005","ATS","EG1",3,'4"',"RMC",12,"#400 MCM","#2/o",0,"","1000A"),
 ("H006","MCC1-SEC. 1","EG1",1,'3/4"',"RMC",3,"#12","#12",0,"","6000W BLOCK HEATER @ EG1"),
 ("H007","MCC1-SEC. 1","TX-L",1,'1 1/2"',"RMC",3,"#2","#8",0,"","75kVA XFMR PRIMARY FEEDER"),
 ("H008","MCC1-SEC. 2","PMP-P-01",1,'4"',"RMC",3,"#500","#2",0,"","200HP 400A FEEDER"),
 ("H009","MCC1-SEC. 3","PMP-P-02",1,'4"',"RMC",3,"#500","#2",0,"","200HP 400A FEEDER"),
 ("H010","MCC1-SEC. 4","PMP-P-03",1,'4"',"RMC",3,"#500","#2",0,"","200HP 400A FEEDER"),
 ("H011","MCC1-SEC. 5","PMP-P-04",1,'2"',"RMC",3,"1/o","#6",0,"","75HP 150A FEEDER"),
 ("H012","MCC1-SEC. 6","PMP-P-05",1,'2"',"RMC",3,"1/o","#6",0,"","75HP 150A FEEDER"),
 ("H013","MCC1-SEC. 7","FUTURE PMP-P-06",1,'2"',"RMC",3,"1/o","#6",0,"","(F) 75HP 150A FEEDER"),
 ("H014","MCC1-SEC. 8","DISC-COMP-01",1,'3/4"',"RMC",3,"#12","#12",0,"","COMP-01 5HP COMPRESSOR"),
 ("H015","MCC1-SEC. 8","TANKLESS WATER HEATER @ CHEM BLDG.",1,'1 1/2"',"RMC",3,"#2","#8",0,"","TANKLESS WATER HEATER 480V-3PH-54kW"),
 ("L001","TX-L","PANEL L",1,'2 1/2"',"RMC",4,"#3/o","#6",0,"","200A-120/208V-3PH-4W"),
 ("L002","PANEL L","EG1",1,'3/4"',"RMC",6,"#12","#12",0,"","L1-2,4,6"),
 ("L003","PANEL L","POWER J BOX @ COMPRESSOR SHELTER",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-39-41 COMPRESSOR SHELTER"),
 ("L005A","PANEL L","CHEMICAL BLDG. CONTROL PANEL",1,'1"',"RMC",3,"#6","#10",0,"","L1-39-41 CHEMICAL BLDG STUB UP"),
 ("L005B","CHEMICAL BLDG. CONTROL PANEL","MIXER CABINET @ STORAGE TANK",1,'3/4"',"RMC",2,"#10","#10",0,"","120V PWR FROM CHEM BLDG TO MIXER"),
 ("L006","PANEL L",'3\'x5\' ELECT. VAULT "P1"',1,'1"',"RMC",10,"#10","#10",0,"","L1-1,3,5,11,13"),
 ("L007",'3\'x5\' ELECT. VAULT "P1"',"CHEMICAL BLDG. RECPS/LT.",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-5 RMC RISER @ CHEMICAL BLDG"),
 ("L008A",'3\'x5\' ELECT. VAULT "P1"','3\'x5\' ELECT. VAULT "P2"',1,'3/4"',"RMC",2,"#10","#10",0,"","L1-3 CKT FOR FUTURE MOTORIZED GATE"),
 ("L008B",'3\'x5\' ELECT. VAULT "P2"',"N16 PULL BOX @ FUTURE MOTORIZED GATE",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-3 CKT FUTURE MOTORIZED GATE"),
 ("L009A",'3\'x5\' ELECT. VAULT "P1"',"SITE POLE LIGHT",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-1"),
 ("L009B","SITE POLE LIGHT","SITE POLE LIGHT",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-1"),
 ("L010",'3\'x5\' ELECT. VAULT "P1"',"ENCLOSURE @ TANK",1,'3/4"',"RMC",4,"#10","#10",0,"","L1-11,13 TANK HATCH LOCK/ALARM"),
 ("L011A","PANEL L",'3\'x5\' ELECT. VAULT "P1"',1,'1"',"RMC",8,"#10","#10",0,"","L1-15,17,19,21"),
 ("L011B",'3\'x5\' ELECT. VAULT "P1"','3\'x5\' ELECT. VAULT "P2"',1,'1"',"RMC",8,"#10","#10",0,"","L1-15,17,19,21"),
 ("L012A",'3\'x5\' ELECT. VAULT "P2"',"SITE POLE LIGHT",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-15"),
 ("L012B","SITE POLE LIGHT","SITE POLE LIGHT",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-15"),
 ("L013",'3\'x5\' ELECT. VAULT "P2"',"WP POWER J BOX @ HYDRO TANK #1",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-17"),
 ("L014",'3\'x5\' ELECT. VAULT "P2"',"WP POWER J BOX @ HYDRO TANK #2",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-19"),
 ("L015",'3\'x5\' ELECT. VAULT "P2"',"WP POWER J BOX @ PRESSURE REDUCING STATION",1,'3/4"',"RMC",2,"#10","#10",0,"","L1-21"),
 ("C001","PLC","EG1",1,'1"',"RMC",12,"#14","#14",1,"CAT-6 SHLD","SIGNAL/CONTROLS"),
 ("C002","PLC","CHEMICAL BLDG. CONTROL PANEL",1,'1 1/2"',"RMC",0,"","",1,"PULLROPE","SIGNAL/CONTROLS VIA S1"),
 ("C003","PLC","N16 PULL BOX @ FUTURE MOTORIZED GATE",1,'1"',"RMC",0,"","",1,"PULLROPE","SIGNAL/CONTROLS VIA S1 & S2"),
 ("C004A","PLC","ENCLOSURE @ STORAGE TANK",1,'1"',"RMC",0,"","",1,"2C/16STP","SIGNAL/CONTROLS"),
 ("C004B","ENCLOSURE @ STORAGE TANK","LE-01-A & LE-01-B",1,'1"',"RMC",0,"","",2,"2C/16STP","LEVEL SENSORS SIGNAL/CONTROLS"),
 ("C004C","ENCLOSURE @ STORAGE TANK","ZS-01-A",1,'1"',"RMC",4,"#14","#14",0,"","TANK LADDER"),
 ("C004D","ENCLOSURE @ STORAGE TANK","ZS-01-B",1,'1"',"RMC",4,"#14","#14",0,"","MFG. CABLE ROOF HATCH / TANK LADDER"),
 ("C005","PLC","WP SIGNAL J BOX @ HYDROPNEUMATIC TANK #1",1,'1"',"RMC",0,"","",1,"PULLROPE","SIGNAL/CONTROLS VIA S1 & S2"),
 ("C006","PLC","WP SIGNAL J BOX @ HYDROPNEUMATIC TANK #2",1,'1"',"RMC",0,"","",1,"PULLROPE","SIGNAL/CONTROLS VIA S1 & S2"),
 ("C007","PLC","WP SIGNAL J BOX @ PRESSURE REDUCING STATION",1,'1"',"RMC",0,"","",1,"PULLROPE","SIGNAL/CONTROLS VIA S1 & S2"),
 ("C008","MCC1-SEC. 2","PSHL-P-01",1,'1"',"RMC",4,"#14","#14",0,"","SIGNAL/CONTROLS"),
 ("C009","MCC1-SEC. 3","PSHL-P-02",1,'1"',"RMC",4,"#14","#14",0,"","SIGNAL/CONTROLS"),
 ("C010","MCC1-SEC. 4","PSHL-P-03",1,'1"',"RMC",4,"#14","#14",0,"","SIGNAL/CONTROLS"),
 ("C011","MCC1-SEC. 5","PSHL-P-04",1,'1"',"RMC",4,"#14","#14",0,"","SIGNAL/CONTROLS"),
 ("C012","MCC1-SEC. 6","PSHL-P-05",1,'1"',"RMC",4,"#14","#14",0,"","SIGNAL/CONTROLS"),
 ("C013","MCC1-SEC. 7","FUTURE PSHL-P-06",1,'1"',"RMC",4,"#14","#14",0,"","SIGNAL/CONTROLS"),
 ("C014","PLC","SIGNAL J BOX @ FE 01-01",1,'1"',"RMC",0,"","",1,"MFR CABLE","SIGNAL/CONTROLS"),
 ("C015","PLC","SIGNAL J BOX @ COMPRESSOR SHELTER",1,'1"',"RMC",0,"","",1,"PULLROPE","SIGNAL/CONTROLS COMP SHELTER"),
 ("C016","PLC","SIGNAL J BOX @ COMP-01",1,'1"',"RMC",0,"","",1,"MFR CABLE","SIGNAL/CONTROLS COMP-01"),
 ("C017","PLC","WP SIGNAL J BOX @ FE P-04",1,'1"',"RMC",0,"","",1,"MFR CABLE","SIGNAL/CONTROLS"),
 ("C018","PLC","WP SIGNAL J BOX @ FE P-05",1,'1"',"RMC",0,"","",1,"MFR CABLE","SIGNAL/CONTROLS"),
 ("C019","PLC","WP SIGNAL J BOX @ FUTURE FE P-06",1,'1"',"RMC",0,"","",1,"MFR CABLE","SIGNAL/CONTROLS"),
 ("C020","PLC","ANTENNA POLE",1,'1 1/2"',"RMC",0,"","",0,"","SIGNAL/CONTROLS"),
 ("C021","PLC","FS-01 @ EYE WASH",1,'1"',"RMC",2,"#14","#14",0,"","SIGNAL/CONTROLS FLOW SWITCH VIA S1"),
 ("XC001","PLC",'3\'x5\' ELECT. VAULT "S1"',2,'1 1/2"',"RMC",0,"","",2,"PULLROPES","SPARE SIGNAL RACEWAYS"),
 ("XH001","MCC1-SEC. 8",'3\'x5\' ELECT. VAULT "P1"',2,'1 1/2"',"RMC",0,"","",2,"PULLROPES","SPARE POWER RACEWAYS"),
]

CABLE_TYPE = {"CAT-6 SHLD": "CAT-6", "PULLROPE": "PULL_ROPE", "PULLROPES": "PULL_ROPE",
              "2C/16STP": "TSP", "MFR CABLE": "MFG_CABLE"}


def build_records():
    recs = []
    for (cid, frm, to, cq, size, ctype, condq, condsz, gnd, cabq, cabt, notes) in ROWS:
        fill = []
        if condq:
            kind = "POWER" if cid[0] in "PHL" else "CONTROL"
            fill.append({"type": kind, "gauge": condsz, "colors": [], "count": condq})
        if cabq:
            fill.append({"type": CABLE_TYPE.get(cabt, "MFG_CABLE"), "gauge": "", "colors": [], "count": cabq})
        idp_extract._attach_symbols(fill, frm, to)
        recs.append({"name": cid, "source": [frm], "dest": [to],
                     "size": size, "ctype": idp_extract._norm_ctype(ctype),
                     "docs": [], "wires": [], "fill": fill,
                     "deviations": notes, "flags": ["from_drawing_schedule"]})
    return recs


if __name__ == "__main__":
    print(logic_store.apply())
    recs = build_records()
    print("conduits:", len(recs))
    print(kb_expand.expand_from_records(recs))
    tmpl = r"C:/Users/cole.mclaughlin/OneDrive - Lyles Group/Desktop/Claude Files/IDP_Builder/resources/template/IDP_Workbook_CurrentWIP_3.xlsm"
    out = idp_write.versioned_path(   # never overwrite a prior result
        r"C:/Users/cole.mclaughlin/OneDrive - Lyles Group/Desktop/Claude Files/Excel template/Filled/73.1188_CrowsLanding_FILLED.xlsm")
    idp_write.write_workbook(recs, tmpl, out, clear_rows=True, add_flags=True)
    ng = idp_write.versioned_path(os.path.splitext(out)[0] + "_NoGrey.xlsm")
    n = idp_write.degrey(out, ng)
    print("wrote", os.path.basename(out), "| de-greyed", n)
