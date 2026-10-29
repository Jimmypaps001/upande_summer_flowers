"""Material stages become records: what each one is, as the code used to say it."""

import frappe


def execute():
	frappe.reload_doc("summer_flowers", "doctype", "material_stage")
	from upande_summer_flowers.summer_flowers.stages import SEED

	for name, values in SEED.items():
		if not frappe.db.exists("Material Stage", name):
			frappe.get_doc(dict(doctype="Material Stage", stage_name=name, **values)).insert(
				ignore_permissions=True)
