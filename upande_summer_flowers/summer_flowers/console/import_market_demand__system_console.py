# Load the Aster market demand sheet: farm x variety x grade x 52 weeks.
#
# Awesome Bar -> System Console -> paste -> Execute. Dry run first; set
# DRY_RUN = False to write. Idempotent: an existing demand record is rebuilt
# from these figures rather than duplicated.
#
# The sheet gives demand per grade per week. Those are strict proportions of the
# variety's weekly total in every one of the 52 weeks -- 9.09/27.27/31.82/31.82
# for the real products and 30/35/35 for the two MPdum placeholders -- so the
# weekly table carries the total and the split is held once as percentages, in
# the Summer Flower Demand Grade table. That is the shape the app already used:
# the orphaned rows for Aster Pink Flash-Karen carry exactly these percentages.
#
# Only three distinct figures appear per grade across the season: a base, a peak
# and a trough. They are written out per variety below as (base, peak, trough)
# of the variety total, with the grade split beside them, and every row is
# checked against the sheet's own stated Total before anything is written.
#
# Written for safe_exec: no imports, no str.format.

DRY_RUN = True
SEASON_START_YEAR = 2027       # the sheet is W27 -> W26 with no year on it
COMPANY = None                 # left as-is when None; the farms' protocols say Kaitet Group

# The season, in order: 26 weeks of one year then 26 of the next.
WEEKS = []
for w in range(27, 53):
    WEEKS.append([SEASON_START_YEAR, w])
for w in range(1, 27):
    WEEKS.append([SEASON_START_YEAR + 1, w])

# Which of base/peak/trough each of the 52 weeks takes.
#   W27-W34 base, W35-W43 peak, W44-W47 base, W48-W2 trough, W3-W4 base,
#   W5-W21 peak, W22-W26 base
BANDS = (["b"] * 8) + (["p"] * 9) + (["b"] * 4) + (["t"] * 7) + \
        (["b"] * 2) + (["p"] * 17) + (["b"] * 5)

# farm, variety, VBN, product group, [base, peak, trough] of the WEEKLY TOTAL,
# grade split as [grade, base_stems], and the sheet's stated season Total.
ROWS = [
 ["Carzan St", "Aster (Novi-belgii Grp) Double Date Pink", "129108", "Aster",
  [72600, 108900, 36300],
  [["56cm", 6600], ["60cm", 19800], ["70cm", 23100], ["80cm", 23100]], 4464900],

 ["Carzan Ks", "Aster (Novi Belgii Grp) Dark Milka", "15557", "Aster",
  [114950, 172424, 57474],
  [["56cm", 10450], ["60cm", 31350], ["70cm", 36575], ["80cm", 36575]], 7069392],

 ["Carzan Ks", "Aster (Novi Belgii Grp) Double Date White", "MPdum0002", "Aster",
  [121000, 145200, 96800],
  [["60cm", 36300], ["70cm", 42350], ["80cm", 42350]], 6751800],

 ["Carzan Ks", "Aster (Novi Belgii Grp) Teeny Tiny Blue", "129342", "Aster",
  [48398, 72600, 24198],
  [["56cm", 4400], ["60cm", 13200], ["70cm", 15399], ["80cm", 15399]], 2976548],

 ["Carzan Ks", "Aster (Novi-belgii Grp) Double Date Milka", "129109", "Aster",
  [72600, 108900, 36300],
  [["56cm", 6600], ["60cm", 19800], ["70cm", 23100], ["80cm", 23100]], 4464900],

 ["Carzan Ks", "Aster Teeny Tiny White", "MPdum0003", "Aster",
  [87998, 105600, 70400],
  [["60cm", 26400], ["70cm", 30799], ["80cm", 30799]], 4910362],

 ["Kariki Juja", "Aster (Novi belgii Grp) Teeny Tiny Pink", "129231", "Aster",
  [48398, 72600, 24198],
  [["56cm", 4400], ["60cm", 13200], ["70cm", 15399], ["80cm", 15399]], 2976548],

 ["Kariki Juja", "Aster (Novi-belgii Grp) 'Flash'", "103243", "Aster",
  [60500, 90748, 30250],
  [["56cm", 5500], ["60cm", 16500], ["70cm", 19250], ["80cm", 19250]], 3720698],
]

# The sheet's own per-farm Total row, as an independent check on the above.
FARM_TOTALS = {"Carzan St": 4464900, "Carzan Ks": 26173002, "Kariki Juja": 6697246}

log = []
def say(line):
    log.append(line)

def weekly(fig):
    out = []
    i = 0
    while i < len(BANDS):
        band = BANDS[i]
        if band == "b":
            out.append(fig[0])
        elif band == "p":
            out.append(fig[1])
        else:
            out.append(fig[2])
        i = i + 1
    return out

# ------------------------------------------------------------------ checks
say("CHECKS")
bad = 0
farm_seen = {}
for r in ROWS:
    farm, variety, vbn, group, fig, grades, stated = r
    total = 0
    for v in weekly(fig):
        total = total + v
    if total != stated:
        say("  MISMATCH " + variety + ": weeks sum to " + str(total)
            + ", sheet says " + str(stated))
        bad = bad + 1
    gsum = 0
    for g in grades:
        gsum = gsum + g[1]
    if gsum != fig[0]:
        say("  MISMATCH " + variety + ": grades sum to " + str(gsum)
            + ", weekly base is " + str(fig[0]))
        bad = bad + 1
    farm_seen[farm] = farm_seen.get(farm, 0) + stated
for farm in FARM_TOTALS:
    if farm_seen.get(farm, 0) != FARM_TOTALS[farm]:
        say("  MISMATCH " + farm + " total: " + str(farm_seen.get(farm, 0))
            + " vs sheet " + str(FARM_TOTALS[farm]))
        bad = bad + 1
if bad:
    say("  " + str(bad) + " mismatches -- refusing to write")
else:
    say("  all 8 variety totals, 8 grade splits and 3 farm totals agree with the sheet")

# --------------------------------------------------------------- existing
# Naming carries the farm now, so records written before that need renaming
# before these are inserted -- and renaming re-adopts the grade rows that were
# orphaned when the farm was dropped from the name.
say("")
say("EXISTING RECORDS")
for d in frappe.get_all("Summer Flower Market Demand", fields=["name", "variety", "farm"]):
    want = str(d.variety) + "-" + str(d.farm) if d.farm else None
    if not d.farm:
        say("  " + d.name + " : has no farm, cannot be renamed -- set one first")
    elif d.name != want:
        say("  " + d.name + " -> " + want)
        if not DRY_RUN:
            frappe.rename_doc("Summer Flower Market Demand", d.name, want,
                              force=True, show_alert=False)
    else:
        say("  " + d.name + " : already named for its farm")

# ----------------------------------------------------------------- import
say("")
say("IMPORT")
created = 0
updated = 0
skipped = 0
if bad:
    say("  skipped, see CHECKS above")
else:
    for r in ROWS:
        farm, variety, vbn, group, fig, grades, stated = r
        name = variety + "-" + farm
        if not frappe.db.exists("Farm", farm):
            say("  BLOCKED " + name + " : no Farm called " + farm)
            skipped = skipped + 1
            continue
        if not frappe.db.exists("Item", variety):
            say("  BLOCKED " + name + " : no Item called " + variety)
            skipped = skipped + 1
            continue
        exists = frappe.db.exists("Summer Flower Market Demand", name)
        say(("  UPDATE " if exists else "  CREATE ") + name
            + "  total " + str(stated))
        if DRY_RUN:
            if exists:
                updated = updated + 1
            else:
                created = created + 1
            continue

        if exists:
            doc = frappe.get_doc("Summer Flower Market Demand", name)
        else:
            doc = frappe.new_doc("Summer Flower Market Demand")
            doc.variety = variety
            doc.farm = farm
        doc.vbn_code = vbn
        doc.product_group = group
        doc.target_years_ahead = 1
        if COMPANY:
            doc.company = COMPANY

        doc.set("demand_weeks", [])
        vals = weekly(fig)
        i = 0
        while i < len(WEEKS):
            doc.append("demand_weeks", {
                "year": WEEKS[i][0],
                "week_no": WEEKS[i][1],
                "demand_stems": vals[i],
                "is_firm": 0,
            })
            i = i + 1

        doc.set("grade_allocation", [])
        for g in grades:
            doc.append("grade_allocation", {
                "grade": g[0],
                "allocation_pct": round(100.0 * g[1] / fig[0], 4),
            })

        if exists:
            doc.save(ignore_permissions=True)
            updated = updated + 1
        else:
            doc.insert(ignore_permissions=True)
            created = created + 1

say("")
say("season               : " + str(SEASON_START_YEAR) + "-W27 to "
    + str(SEASON_START_YEAR + 1) + "-W26, " + str(len(WEEKS)) + " weeks")
say("created              : " + str(created))
say("updated              : " + str(updated))
say("blocked              : " + str(skipped))
if DRY_RUN:
    say("")
    say("DRY RUN. Nothing written. Set DRY_RUN = False and run again to apply.")

frappe.msgprint("<pre>" + "\n".join(log) + "</pre>")
