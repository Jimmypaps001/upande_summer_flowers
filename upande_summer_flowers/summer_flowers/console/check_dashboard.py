"""Does the dashboard get what it now asks for?"""

import frappe


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()


def _run():
	from upande_summer_flowers.summer_flowers import operations_api, planning_api

	plan = None
	for p in frappe.get_all("Summer Flower Production Plan",
	                        filters={"docstatus": 0},
	                        fields=["name", "variety", "farm", "protocol",
	                                "new_plants_required"],
	                        order_by="modified desc", limit=40):
		if not (p.protocol and p.new_plants_required):
			continue
		life = frappe.db.get_value("Crop Protocol Version", p.protocol,
		                           "motherstock_life_weeks")
		if life:
			plan = p
			break
		plan = plan or p
	if not plan:
		print("no draft plan")
		return

	t = planning_api.tc_purchase(plan=plan.name)
	print("tc_purchase for %s (%s at %s)" % (plan.name, plan.variety, plan.farm))
	for k in ("build_up_cycles", "line_life_weeks", "cycle_cost_weeks",
	          "cutting_weeks", "chosen_tc", "chosen_pool", "multiplication_factor"):
		print("   %-24s %s" % (k, t.get(k)))
	print("\n   cycles  factor  plantlets  cutting weeks")
	for r in t.get("cutting_weeks_by_cycles") or []:
		print("   %-7s ×%-6s %-10s %s"
		      % (r["cycles"], r["factor"], f"{r['tc']:,}",
		         r["weeks"] or "NONE"))

	c = operations_api.chain_status(variety=plan.variety, farm=plan.farm,
	                                plan=plan.name)
	print("\nchain_status steps:")
	if c and c.get("steps"):
		for i, st in enumerate(c["steps"], 1):
			print("   %d. %-18s %-22s %s"
			      % (i, st["label"], st.get("name") or "—",
			         (st.get("note") or st.get("state") or "")[:70]))
	else:
		print("\n(chain function not found by name)")
