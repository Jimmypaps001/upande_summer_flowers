import frappe


def run():
	props = frappe.get_all("Summer Flower Propagation Plan",
	                       filters={"docstatus": ["<", 2]},
	                       fields=["name", "variety", "farm", "production_plan",
	                               "status"])
	print("propagation plans: %d" % len(props))
	print("%-20s %-28s %-18s %-9s %s"
	      % ("plan", "variety", "production plan", "procured", "calendars"))
	for p in props:
		cals = frappe.db.count("Planting Calendar",
		                       {"variety": p.variety, "farm": p.farm})
		proc = frappe.db.get_value("Summer Flower Procurement Plan",
		                           {"production_plan": p.production_plan,
		                            "docstatus": ["<", 2]},
		                           ["name", "docstatus"], as_dict=True)
		blocks = 0
		if p.production_plan:
			blocks = frappe.db.count(
				"Summer Flower Plan Block",
				{"parent": p.production_plan, "is_new_planting": 1,
				 "block": ["is", "set"]})
		print("%-20s %-28s %-18s %-9s %s (%s cohorts have a block)"
		      % (p.name, (p.variety or "")[:28], p.production_plan or "-",
		         ("%s d%s" % (proc.name[-5:], proc.docstatus)) if proc else "none",
		         cals, blocks))
