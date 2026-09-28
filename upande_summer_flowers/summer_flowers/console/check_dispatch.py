"""Delivery dates on the propagation plan, and the move to the block. Rolls back."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback()[-700:])
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers import plant_issue

	name = frappe.db.get_value("Summer Flower Propagation Plan", {"docstatus": 0},
	                           "name")
	d = frappe.get_doc("Summer Flower Propagation Plan", name)
	d.flags.ignore_permissions = True
	d.save()
	d.reload()
	print("%s — TC on farm %s, first sticking %s"
	      % (d.name, d.tc_on_farm_date, d.first_sticking_date))
	print("\n   %-13s %-9s %-13s %s" % ("stuck in wk", "plants", "deliver on",
	                                    "planting weeks"))
	for r in d.weeks[:6]:
		print("   %-13s %-9s %-13s %s"
		      % (r.week_start_date, f"{cint(r.plants_to_stick):,}",
		         r.deliver_on or "—", (r.plant_week or "")[:26]))

	print("\n--- the internal move ---")
	cal = frappe.db.get_value("Planting Calendar",
	                          {"seedling_source": "In-house Propagation",
	                           "block": ["is", "set"]}, "name")
	if not cal:
		cal = frappe.db.get_value("Planting Calendar", {"block": ["is", "set"]},
		                          "name")
	c = frappe.get_doc("Planting Calendar", cal)
	print("%s on %s (%s), greenhouse %r"
	      % (c.name, c.block, c.seedling_source or "—", c.greenhouse))
	print("propagation warehouse setting: %r"
	      % plant_issue.propagation_warehouse(c.farm))
	# name a propagation warehouse and give the block one, for the test
	wh = frappe.db.get_value("Warehouse", {"company": c.company, "is_group": 0},
	                         "name")
	frappe.db.set_single_value("Summer Flower Settings", "propagation_warehouse", wh)
	if not c.greenhouse:
		gh = frappe.db.get_value("Warehouse",
		                         {"company": c.company, "is_group": 0,
		                          "name": ["!=", wh]}, "name")
		c.db_set("greenhouse", gh, update_modified=False)
		c.reload()
	print("   propagation warehouse -> %s, block warehouse -> %s"
	      % (wh, c.greenhouse))
	try:
		out = plant_issue.dispatch_to_block(c.name)
		print("   moved: %s" % out)
	except frappe.ValidationError as e:
		print("   refused: %s" % frappe.utils.strip_html(str(e))[:130])
