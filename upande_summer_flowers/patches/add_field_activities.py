"""Give every summer flower protocol its field-activity table.

Timeline (planting to uproot) used to be a single weeks-to-pinch figure, which
only describes a pinched crop. It is now a table of what the plant does in the
field. Seeds the Field Activity list, then fills each protocol's table from the
figures it already holds -- written straight to the child table so no protocol
is re-saved, re-versioned or pushed back through its workflow.
"""

import frappe
from frappe.utils import cint

ACTIVITIES = (("Planting", "Planting"), ("Pinching", "Pinching"), ("Bending", ""),
              ("Disbudding", ""), ("Harvesting", "Harvesting"), ("Uprooting", "Uprooting"))


def execute():
	frappe.reload_doc("summer_flowers", "doctype", "field_activity")
	frappe.reload_doc("summer_flowers", "doctype", "crop_field_activity")
	for name, role in ACTIVITIES:
		if not frappe.db.exists("Field Activity", name):
			frappe.get_doc({"doctype": "Field Activity", "activity_name": name,
			                "role": role}).insert(ignore_permissions=True)

	if not frappe.db.has_column("Crop Protocol", "custom_is_summer_flower"):
		return
	have = set(frappe.get_all("Crop Field Activity", filters={"parenttype": "Crop Protocol"},
	                          pluck="parent", distinct=True))
	for p in frappe.get_all("Crop Protocol", filters={"custom_is_summer_flower": 1},
	                        fields=["name", "weeks_to_pinch", "total_weeks_in_ground",
	                                "custom_sf_first_harvest_offset_weeks as first_cut"]):
		if p.name in have:
			continue
		rows = [("Planting", 0)]
		if cint(p.weeks_to_pinch):
			rows.append(("Pinching", cint(p.weeks_to_pinch)))
		rows += [("Harvesting", cint(p.first_cut)), ("Uprooting", cint(p.total_weeks_in_ground))]
		rank = {"Planting": 0, "Uprooting": 2}
		rows.sort(key=lambda r: (r[1], rank.get(r[0], 1)))
		for idx, (role, weeks) in enumerate(rows, start=1):
			frappe.get_doc({
				"doctype": "Crop Field Activity", "parent": p.name,
				"parenttype": "Crop Protocol", "parentfield": "custom_sf_field_activities",
				"idx": idx, "activity": role, "role": role, "weeks_from_planting": weeks,
			}).db_insert()
