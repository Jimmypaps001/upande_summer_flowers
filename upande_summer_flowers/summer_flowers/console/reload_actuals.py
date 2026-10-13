import frappe, traceback

DOCTYPES = [
	("summer_flowers", "doctype", "summer_flower_propagation_actual"),
	("summer_flowers", "doctype", "summer_flower_propagation_intake"),
	("summer_flowers", "doctype", "summer_flower_propagation_week"),
	("summer_flowers", "doctype", "summer_flower_propagation_plan"),
]

def main():
	try:
		for mod, kind, name in DOCTYPES:
			frappe.reload_doc(mod, kind, name, force=True)
			print("reloaded", name)
		frappe.db.commit()
		print("done")
	except Exception:
		print(traceback.format_exc())
