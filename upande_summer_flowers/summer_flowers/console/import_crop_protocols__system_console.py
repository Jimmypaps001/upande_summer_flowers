# Load the "Crop Protocal.xlsx" summer flower protocols.
#
# Awesome Bar -> System Console -> paste -> Execute. Dry run first; set
# DRY_RUN = False to write. Idempotent: an unchanged protocol is reported and
# left alone, so re-running after a partial run is safe.
#
# Source sheet: Crop Protocal.xlsx, tab TC_MS_PLANTS_PERENNIAL DISTINCT, 12 rows.
# The tab name is the route and the cycle: TC -> Motherstock -> Plants, perennial
# flushing in distinct cycles. Both are written, so crop_routes.classify() has
# nothing left to do for these twelve.
#
# Only INPUTS are set. Croplife, lifetime flushes, lifetime stems/plant and total
# weeks to establishment are derived by the controller, so the sheet's values for
# those four are treated as CHECKS: the script sets the inputs, lets the
# derivation run, then compares. A mismatch is reported per field rather than
# written over, because a protocol that disagrees with its own sheet is the one
# thing worth stopping for.
#
# Not in this sheet, and therefore left at zero: pots_per_sqm, hardening_weeks,
# cycle_time_weeks, multiplication_factor_per_cycle, rooting_success_pct,
# field_establishment_pct. plants_per_sqm_bench cannot be computed without
# pots_per_sqm, so motherstock bench space stays unavailable for these twelve.
#
# Written for safe_exec: no imports, no str.format.

DRY_RUN = True          # set False to write
APPROVE = False         # True also approves, which is what writes a version
CREATE_MISSING_ITEMS = False

CROP_TYPE = "Summer Flowers"
CYCLE     = "Perennial - distinct flushes"
ROUTE     = ["TC", "Motherstock", "Plants"]

# The sheet uses "Carzan KS"; the sites use "Carzan Ks".
FARM_ALIAS = {"Carzan KS": "Carzan Ks", "Carzan ST": "Carzan St"}

# Bed geometry is NOT in this sheet, and the derivation refuses to run without
# it: Crop Protocol Version.set_geometry throws "Net m2 per bed is required",
# because plants per bed -- and from it plants per block and the minimum
# planting in beds -- is worked out from it. These two are carried over from
# Aster Pink Flash-Karen, which is what the twelve on karenroses already hold.
# 50 x 20 plants/m2 gives 1,000 plants per bed. CONFIRM PER FARM before
# approving: it is the one number here that nobody at Carzan or Kariki Juja has
# supplied. Override a farm that differs in SQM_NET_BY_FARM.
SQM_NET_PER_BED = 50.0
BEDS_PER_BLOCK  = 40
SQM_NET_BY_FARM = {}          # e.g. {"Kariki Juja": 45.0}
BEDS_BY_FARM    = {}

# farm, variety, density, min_area, croplife, pinch, interval, flushes, life_stems,
# f1..f8, estab, tray, pot, buildup, rooting, cut_per_plant_wk, mult_cycles,
# ms_life, plants_per_pot, supplier_lead
ROWS = [
 ["Carzan KS","Aster (Novi Belgii Grp) Dark Milka",20,1,103,7,12,8,22.05,
  2.7,2.7,2.7,2.7,2.7,2.7,2.85,3,     15,3,8,4,3, 1.0,1,52,5,0],
 ["Carzan KS","Aster (Novi Belgii Grp) Double Date White",20,1,103,7,12,8,16.0,
  2,2,2,2,2,2,2,2,                    15,3,8,4,3, 0.9,1,52,5,0],
 ["Carzan KS","Aster (Novi Belgii Grp) Teeny Tiny Blue",20,1,103,7,12,8,16.0,
  2,2,2,2,2,2,2,2,                    15,3,8,4,3, 1.1,1,52,5,0],
 ["Kariki Juja","Aster (Novi Belgii Grp) Teeny Tiny Blue",20,1,111,7,13,8,16.0,
  2,2,2,2,2,2,2,2,                    15,3,8,4,3, 1.1,1,52,5,0],
 ["Kariki Juja","Aster (Novi belgii Grp) Teeny Tiny Pink",20,1,111,7,13,8,13.34,
  1.79,2,2,1.89,1.7,1.5,1.23,1.23,    15,3,8,4,3, 0.7,1,52,5,0],
 ["Carzan KS","Aster (Novi-belgii Grp) Double Date Milka",20,1,103,7,12,8,16.0,
  2,2,2,2,2,2,2,2,                    15,3,8,4,3, 0.9,1,52,5,0],
 ["Carzan KS","Aster (Novi-belgii Grp) Double Date Pink",20,1,103,7,12,8,22.3,
  2.6,3.1,3.1,3.1,2.6,2.6,2.6,2.6,    15,3,8,4,3, 1.0,1,52,5,0],
 ["Carzan ST","Aster (Novi-belgii Grp) Double Date Pink",20,1,103,7,12,8,22.3,
  2.6,3.1,3.1,3.1,2.6,2.6,2.6,2.6,    15,3,8,4,3, 1.0,1,52,5,0],
 ["Kariki Juja","Aster (Novi-belgii Grp) Double Date Pink",20,1,111,7,13,8,15.5,
  2.5,2.3,1.1,2,2,1.9,1.9,1.8,        15,3,8,4,3, 1.0,1,52,5,0],
 ["Kariki Juja","Aster (Novi-belgii Grp) 'Flash'",20,1,111,7,13,8,18.8,
  2.7,2.6,2.4,2.3,2.2,2.2,2.2,2.2,    15,3,8,4,3, 1.0,1,52,5,0],
 ["Carzan KS","Aster Teeny Tiny White",20,1,103,7,12,8,16.0,
  2,2,2,2,2,2,2,2,                    15,3,8,4,3, 0.9,1,52,5,0],
 ["Carzan KS","Aster Double Date Dusk",20,1,103,7,12,8,16.0,
  2,2,2,2,2,2,2,2,                    15,3,8,4,3, 0.7,1,52,5,0],
]

# Which doctype holds a summer flower protocol here. karenroses is past the
# split; a site still on the pre-17-Aug build has only Crop Protocol, and
# protocol_split.move() carries these across afterwards by the same name.
DT = "Summer Flower Protocol"
if not frappe.db.exists("DocType", DT):
    DT = "Crop Protocol"

log = []
def say(line):
    log.append(line)

# INPUT fields only. The four sheet columns that the controller derives are
# checked after the save instead, in CHECKS below.
def inputs_for(r, farm):
    d = {}
    d["custom_sf_sqm_net_per_bed"] = SQM_NET_BY_FARM.get(farm, SQM_NET_PER_BED)
    d["custom_sf_beds_per_block"] = BEDS_BY_FARM.get(farm, BEDS_PER_BLOCK)
    d["plants_per_sqm"] = r[2]
    d["custom_sf_min_planting_area_sqm"] = r[3]
    d["weeks_to_pinch"] = r[5]
    d["weeks_between_flushes"] = r[6]
    d["custom_sf_weeks_on_tray"] = r[18]
    d["custom_sf_weeks_on_pot"] = r[19]
    d["custom_sf_ramp_weeks"] = r[20]
    d["custom_sf_sticking_to_planting_weeks"] = r[21]
    d["custom_sf_ramp_profile"] = "25,50,75,100"
    d["custom_sf_cuttings_per_plant_per_week"] = r[22]
    d["custom_sf_max_multiplication_cycles"] = r[23]
    d["custom_sf_motherstock_life_weeks"] = r[24]
    d["custom_sf_plants_per_pot"] = r[25]
    d["custom_sf_supplier_lead_weeks"] = r[26]
    d["custom_sf_establishment_includes_ramp"] = 1
    d["custom_sf_establishment_includes_hardening"] = 0
    d["crop_type"] = CROP_TYPE
    d["custom_is_summer_flower"] = 1
    d["custom_sf_growing_cycle"] = CYCLE
    d["custom_sf_crop_class"] = "Summer Flower"
    return d

# field on the doc  ->  sheet column that has to come back out of the derivation
CHECKS = [("total_weeks_in_ground", 4, 0.5),
          ("total_flushes", 7, 0.5),
          ("total_stems_per_plant_life", 8, 0.06),
          ("custom_sf_establishment_weeks", 17, 0.5)]

created = 0
updated = 0
same = 0
blocked = 0
mismatched = 0

for r in ROWS:
    farm = FARM_ALIAS.get(r[0], r[0])
    variety = r[1].strip()
    name = variety + "-" + farm

    if not frappe.db.exists("Farm", farm):
        say("BLOCKED  " + name + " : no Farm called '" + farm + "'")
        blocked = blocked + 1
        continue
    if not frappe.db.exists("Item", variety):
        if CREATE_MISSING_ITEMS and not DRY_RUN:
            it = frappe.new_doc("Item")
            it.item_code = variety
            it.item_name = variety
            it.item_group = CROP_TYPE
            it.stock_uom = "Nos"
            it.is_stock_item = 0
            it.insert(ignore_permissions=True)
            say("item     " + variety + " : created")
        else:
            say("BLOCKED  " + name + " : no Item called '" + variety + "'"
                + (" (set CREATE_MISSING_ITEMS = True)" if not CREATE_MISSING_ITEMS else ""))
            blocked = blocked + 1
            continue

    exists = frappe.db.exists(DT, name)
    want = inputs_for(r, farm)

    # what would change
    changes = []
    if exists:
        doc = frappe.get_doc(DT, name)
        for k in want:
            have = doc.get(k)
            if k == "custom_sf_ramp_profile" or k == "crop_type" or \
               k == "custom_sf_growing_cycle" or k == "custom_sf_crop_class":
                if str(have or "").replace(" ", "") != str(want[k]).replace(" ", ""):
                    changes.append(k + ": " + str(have) + " -> " + str(want[k]))
            else:
                if abs(float(have or 0) - float(want[k] or 0)) > 0.0011:
                    changes.append(k + ": " + str(have) + " -> " + str(want[k]))
        # flush rows
        have_f = []
        rows_sorted = sorted(doc.get("custom_sf_flush_schedule") or [],
                             key=lambda x: x.flush_number or 0)
        for fr in rows_sorted:
            have_f.append(round(float(fr.stems_per_plant or 0), 4))
        want_f = []
        for i in range(9, 17):
            want_f.append(round(float(r[i]), 4))
        if have_f != want_f:
            changes.append("flush_schedule: " + str(have_f) + " -> " + str(want_f))
        # route
        have_r = []
        for rr in (doc.get("custom_sf_material_route") or []):
            if rr.stage:
                have_r.append(rr.stage)
        if have_r != ROUTE:
            changes.append("material_route: " + str(have_r) + " -> " + str(ROUTE))
    else:
        doc = None

    if exists and not changes:
        say("unchanged " + name)
        same = same + 1
        continue

    if not exists:
        say("CREATE   " + name)
    else:
        say("UPDATE   " + name)
        for c in changes:
            say("             " + c)

    if DRY_RUN:
        if exists:
            updated = updated + 1
        else:
            created = created + 1
        continue

    if not exists:
        doc = frappe.new_doc(DT)
        doc.variety = variety
        doc.variety_item = variety
        doc.farm = farm
        doc.custom_sf_protocol_status = "Draft"

    for k in want:
        doc.set(k, want[k])
    doc.custom_sf_change_reason = ("Loaded from Crop Protocal.xlsx, tab "
        "TC_MS_PLANTS_PERENNIAL DISTINCT. Bed geometry is not in that sheet: "
        "confirm it per farm before approval.")

    doc.set("custom_sf_flush_schedule", [])
    n = 1
    for i in range(9, 17):
        doc.append("custom_sf_flush_schedule", {
            "flush_number": n,
            "weeks_from_pinch": r[6] * n,
            "stems_per_plant": r[i]})
        n = n + 1

    doc.set("custom_sf_material_route", [])
    for stage in ROUTE:
        doc.append("custom_sf_material_route", {"stage": stage})

    if exists:
        doc.save(ignore_permissions=True)
        updated = updated + 1
    else:
        doc.insert(ignore_permissions=True)
        created = created + 1

    # the four the controller works out: compare, never overwrite
    doc.reload()
    for pair in CHECKS:
        got = float(doc.get(pair[0]) or 0)
        expect = float(r[pair[1]])
        if abs(got - expect) > pair[2]:
            say("    MISMATCH " + pair[0] + " derived " + str(got)
                + ", sheet says " + str(expect))
            mismatched = mismatched + 1

    if APPROVE:
        doc.custom_sf_protocol_status = "Approved"
        doc.save(ignore_permissions=True)

say("")
say("doctype              : " + DT)
say("created              : " + str(created))
say("updated              : " + str(updated))
say("already correct      : " + str(same))
say("blocked              : " + str(blocked))
say("derived mismatches   : " + str(mismatched))
if DRY_RUN:
    say("")
    say("DRY RUN. Nothing written. Set DRY_RUN = False and run again to apply.")

frappe.msgprint("<pre>" + "\n".join(log) + "</pre>")
