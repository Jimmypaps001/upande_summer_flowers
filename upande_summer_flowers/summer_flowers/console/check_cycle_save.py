"""Can a summer flower crop cycle actually be saved? Rolls back."""

import frappe
from frappe.utils import cint, nowdate


def run():
	try:
		_run()
	except Exception:
		print("RAISED:")
		print(frappe.get_traceback()[-1200:])
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	ver = frappe.db.get_value("Crop Protocol Version",
	                          {"name": "Aster Pink Flash-Karen-v17"},
	                          ["name", "variety", "farm"], as_dict=True)
	if not ver:
		ver = frappe.db.get_value("Crop Protocol Version",
		                          {"version_status": "Active"},
		                          ["name", "variety", "farm"], as_dict=True)
	blk = frappe.db.get_value("Block",
	                          {"custom_is_summer_flower_block": 1,
	                           "farm": ver.farm},
	                          ["name", "farm", "greenhouse"], as_dict=True)
	if not blk:
		blk = frappe.db.get_value("Block", {"custom_is_summer_flower_block": 1},
		                          ["name", "farm", "greenhouse"], as_dict=True)
	print("version %s (%s at %s)" % (ver.name, ver.variety, ver.farm))
	print("block   %s (farm %s, house %s)" % (blk.name, blk.farm, blk.greenhouse))

	d = frappe.new_doc("Crop Cycle")
	d.variety = ver.variety
	d.custom_block = blk.name
	d.custom_crop_protocol_version = ver.name
	d.custom_planting_date = nowdate()
	d.planting_date = nowdate()
	d.custom_area_planted_sqm = 500
	d.custom_live_plant_count = 10000
	print("\nbefore save: scope=%r farm=%r greenhouse=%r"
	      % (d.get("custom_cycle_scope"), d.get("farm"), d.get("greenhouse")))
	d.flags.ignore_permissions = True
	d.insert()
	print("saved as %s" % d.name)
	print("   scope           %s" % d.custom_cycle_scope)
	print("   farm            %s   (read-only, filled from the block)" % d.farm)
	print("   greenhouse      %r" % d.greenhouse)
	print("   custom_greenhouse %s" % d.custom_greenhouse)
	print("   block area      %s m2" % d.custom_block_area_sqm)
	print("   density         %s" % d.custom_planting_density_per_sqm)
	print("   summer flower   %s" % d.custom_is_summer_flower_cycle)

	print("\nsaving again:")
	d.save()
	print("   still fine, scope=%s farm=%s" % (d.custom_cycle_scope, d.farm))
