import frappe


def run():
	print("Planting Calendar by status:", frappe.db.sql(
		"select calendar_status s, count(*) n from `tabPlanting Calendar` group by s",
		as_dict=True))
	print("with an actual planting date:",
	      frappe.db.count("Planting Calendar",
	                      {"actual_planting_date": ["is", "set"]}))
	print("\nBlock Bed rows by bed_status:", frappe.db.sql(
		"select ifnull(bed_status,'(blank)') s, count(*) n from `tabBlock Bed` "
		"group by s", as_dict=True))
	if frappe.get_meta("Bed").has_field("custom_bed_status"):
		print("Bed records by custom_bed_status:", frappe.db.sql(
			"select ifnull(custom_bed_status,'(blank)') s, count(*) n "
			"from `tabBed` group by s order by n desc limit 6", as_dict=True))
	pcb = "Planting Calendar Bed"
	if frappe.db.exists("DocType", pcb):
		print("\n%s by bed_status:" % pcb, frappe.db.sql(
			"select ifnull(bed_status,'(blank)') s, count(*) n from `tab%s` "
			"group by s" % pcb, as_dict=True))
	print("\nCrop Cycle sf status:", frappe.db.sql(
		"select ifnull(custom_sf_cycle_status,'(blank)') s, count(*) n "
		"from `tabCrop Cycle` group by s", as_dict=True))
