import frappe, traceback

def main():
	try:
		for n in ("summer_flower_protocol_lift", "crop_protocol_version",
		          "summer_flower_settings"):
			frappe.reload_doc("summer_flowers", "doctype", n, force=True)
			print("reloaded", n)
		frappe.db.commit()
		print("done")
	except Exception:
		print(traceback.format_exc())
