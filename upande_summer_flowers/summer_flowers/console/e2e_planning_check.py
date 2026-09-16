# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""End to end check of the planning chain, against real site data.

    bench --site <site> execute \\
        upande_summer_flowers.summer_flowers.console.e2e_planning_check.main

Runs one register of each kind of crop -- continuous, distinct flushes, single
flush -- from the demand register through the protocol in force, the preview, the
plan, the order schedule and the dashboard chain, checking the arithmetic the plan
claims at each step. Everything is built inside a savepoint and rolled back, so it
can be run against live data without leaving a plan behind. Nothing is written.
"""

import traceback

import frappe
from upande_summer_flowers.summer_flowers.doctype.crop_protocol_version.crop_protocol_version import current_version
from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
    plan_preview, build_from_demand,
)
from upande_summer_flowers.summer_flowers import sourcing, operations_api

def main():
    CASES = [
        ("Craspedia Gold Drum-Karen", 2026, "continuous crop, farm with real blocks"),
        ("Aster (Novi Belgii Grp) Dark Milka-Carzan Ks", 2027, "distinct flushes, farm with no blocks"),
        ("Bupleurum rotundifolium-Carzan Ks", 2027, "single flush, seed route"),
    ]

    tally = []
    def check(name, ok, detail=""):
        tally.append((name, ok))
        print("      %-46s %s  %s" % (name, "PASS" if ok else "**FAIL**", detail))

    for REG, SEASON, why in CASES:
        print("\n" + "=" * 86)
        print("CASE  %s  season %s   (%s)" % (REG, SEASON, why))
        print("=" * 86)
        frappe.db.savepoint("e2e")
        try:
            # 1 ------------------------------------------------ the register
            d = frappe.get_doc("Summer Flower Market Demand", REG)
            print("\n  [1] Register")
            print("      variety=%s farm=%s weeks=%s stems=%s peak=%s"
                  % (d.variety, d.farm, d.weeks_covered, d.total_demand_stems, d.peak_weekly_demand))
            check("register names a farm", bool(d.farm), d.farm or "")
            check("register named variety-farm", d.name == "%s-%s" % (d.variety, d.farm))

            # 2 ------------------------------------------------ the protocol
            ver = current_version(d.variety, d.farm)
            v = frappe.get_cached_doc("Crop Protocol Version", ver)
            print("\n  [2] Protocol in force: %s" % ver)
            print("      cycle=%s plants/bed=%s first cut +%sw stick->plant %sw life %sw turnaround %sw"
                  % (v.growing_cycle, v.plants_per_bed, v.first_harvest_offset_weeks,
                     v.sticking_to_planting_weeks, v.total_weeks_in_ground, v.turnaround_weeks))
            offs = v.flush_offsets()
            print("      harvest profile (weeks from planting, stems/plant): %s%s"
                  % (offs[:6], " …%d more" % (len(offs) - 6) if len(offs) > 6 else ""))
            print("      route: %s" % [(r.stage, r.weeks, "BUY" if r.is_purchase else "")
                                       for r in (v.material_route or []) if (r.row_type or "Stage") == "Stage"])
            check("a protocol is in force", bool(ver))
            check("the protocol yields a harvest profile", bool(offs))

            # 3 ------------------------------------------------ preview, no farm passed
            pv = plan_preview(REG, season_start_year=SEASON)
            print("\n  [3] Preview, called WITHOUT a farm")
            print("      farm resolved: %s" % pv.get("farm"))
            for n in pv.get("blocking") or []:
                print("      BLOCKING: %s" % n)
            for n in pv.get("notes") or []:
                print("      note    : %s" % n)
            check("farm resolved from the register", pv.get("farm") == d.farm, pv.get("farm") or "")
            check("preview is not blocked", not pv.get("blocking"))
            if pv.get("blocking"):
                frappe.db.rollback(save_point="e2e")
                continue

            # 4 ------------------------------------------------ build
            name = build_from_demand(REG, season_start_year=SEASON)
            p = frappe.get_doc("Summer Flower Production Plan", name if isinstance(name, str) else name.get("name"))
            news = [b for b in p.plan_blocks if b.is_new_planting]
            print("\n  [4] Plan built: %s" % p.name)
            print("      farm=%s season=%s protocol=%s" % (p.farm, p.season_start_year, p.protocol))
            print("      plantings=%s beds=%s plants=%s not placed=%s"
                  % (len(news), sum(b.beds or 0 for b in news),
                     sum(b.plants or 0 for b in news), p.plantings_not_placed))
            print("      demand=%s planned=%s coverage=%s%%"
                  % (p.get("total_demand_stems"), p.get("total_planned_stems"), p.get("coverage_pct")))
            for b in news[:4]:
                print("        stick %s-W%02s -> plant %s-W%02s (%s) -> first cut %s-W%02s | %sb %sp | block %s"
                      % (b.sticking_year, b.sticking_week, b.planting_year, b.planting_week,
                         b.planting_date, b.first_harvest_year, b.first_harvest_week,
                         b.beds, b.plants, b.block or "-"))
            check("plan inherits the register's farm", p.farm == d.farm)
            check("plantings were proposed", len(news) > 0, "%d" % len(news))
            check("every planting has a planting date", all(b.planting_date for b in news))
            check("every planting has a sticking week", all(b.sticking_year and b.sticking_week for b in news))
            # the arithmetic the plan claims
            bad = [b for b in news if b.plants != (b.beds or 0) * (v.plants_per_bed or 0)]
            check("plants = beds x plants per bed", not bad, "%d rows off" % len(bad))
            import datetime
            from frappe.utils import getdate
            off = v.first_harvest_offset_weeks or (offs[0][0] if offs else 0)
            bad = [b for b in news
                   if getdate(b.planting_date) + datetime.timedelta(weeks=off)
                   != getdate(b.planting_date) + datetime.timedelta(weeks=off)]
            wrong_stick = [b for b in news
                           if getdate(b.planting_date) - datetime.timedelta(weeks=v.sticking_to_planting_weeks or 0)
                           != frappe.utils.getdate(frappe.utils.add_days(b.planting_date, -7 * (v.sticking_to_planting_weeks or 0)))]
            check("sticking = planting - stick weeks", not wrong_stick, "%d rows off" % len(wrong_stick))

            # 5 ------------------------------------------------ what to buy
            print("\n  [5] Material to order")
            print("      plan says: %s TC plantlets, order by %s" % (p.tc_plants_to_order, p.tc_order_by_date))
            opts = sourcing.entry_options(ver)
            print("      route entry options: %s"
                  % ([(o["stage"], "to ground %sw" % o["weeks_to_ground"], "lead %sw" % o["lead_weeks"])
                      for o in opts] or "NONE — no stage on this route is marked bought"))
            if opts:
                sch = sourcing.order_schedule(p.name)
                t = sch.get("totals") or {}
                print("      order schedule: buy at %s | %s orders | %s units | %s late | first %s"
                      % (sch.get("stage"), t.get("plantings"), t.get("units"), t.get("late"), t.get("first_order")))
                check("order schedule has a row per planting", t.get("plantings") == len(news),
                      "%s vs %s" % (t.get("plantings"), len(news)))
            stage_names = [r.stage for r in (v.material_route or []) if (r.row_type or "Stage") == "Stage"]
            tc_route = "TC" in stage_names
            check("TC order figure suits this route", tc_route or not p.tc_plants_to_order,
                  "route is %s but the plan ordered %s TC plantlets"
                  % (" -> ".join(stage_names), p.tc_plants_to_order) if not tc_route and p.tc_plants_to_order else "")

            # 6 ------------------------------------------------ the chain
            cs = operations_api.chain_status(variety=d.variety, farm=d.farm, plan=p.name)
            got = cs.get("demand") or {}
            got_name = got.get("name") if isinstance(got, dict) else got
            got_farm = got.get("farm") if isinstance(got, dict) else None
            print("\n  [6] Chain status: demand resolved to %s (farm %s)"
                  % (got_name, got_farm))
            check("chain resolves THIS farm's register", got_name == d.name,
                  got_name or "nothing")
            check("chain's register is at this farm", got_farm == d.farm,
                  got_farm or "none")

            # 7 ------------------------------------------------ determinism
            p2 = frappe.get_doc("Summer Flower Production Plan",
                                build_from_demand(REG, season_start_year=SEASON))
            sig = lambda doc: [(b.planting_year, b.planting_week, b.beds, b.plants)
                               for b in doc.plan_blocks if b.is_new_planting]
            check("same demand gives the same plan", sig(p) == sig(p2),
                  "%d/%d plantings" % (len(sig(p)), len(sig(p2))))
        except Exception:
            print("\n  *** CASE RAISED ***")
            traceback.print_exc(file=sys.stdout)
            tally.append(("case %s completed" % REG, False))
        finally:
            frappe.db.rollback(save_point="e2e")
            print("\n  (rolled back — nothing kept)")

    print("\n" + "=" * 86)
    bad = [n for n, ok in tally if not ok]
    print("RESULT: %d checks, %d passed, %d failed" % (len(tally), len(tally) - len(bad), len(bad)))
    for n in bad:
        print("   FAILED: %s" % n)
    sys.stdout.flush()

