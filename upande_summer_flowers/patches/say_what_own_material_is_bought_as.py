"""Each own-crop stage gets the stage it is bought at when the farm runs short.

Backfilled from the name, which is how every site spells them today: Tubers
(Own) is bought as Tubers. A stage whose plain name is not itself bought --
cuttings, which nobody sells unrooted -- is left blank on purpose, and the
procurement plan reports that gap instead of filling it.
"""

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
