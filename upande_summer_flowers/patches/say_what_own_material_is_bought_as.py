"""Each own-crop stage gets the stage it is bought at when the farm runs short."""

import frappe


def execute():
	frappe.reload_doc("summer_flowers", "doctype", "material_stage")
	from upande_summer_flowers.summer_flowers import stages

	for name, info in (stages.all_stages() or {}).items():
		if info.get("kind") != stages.TAKEN or info.get("bought_as"):
			continue
		if not name.endswith(" (Own)"):
			continue
		plain = name[: -len(" (Own)")].strip()
		if stages.kind(plain) != stages.BOUGHT:
			continue
		if frappe.db.exists("Material Stage", name):
			frappe.db.set_value("Material Stage", name, "bought_as", plain,
			                    update_modified=False)
	stages.clear_cache()
