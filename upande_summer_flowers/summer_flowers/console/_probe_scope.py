import frappe


def run():
	d = frappe.new_doc("Crop Cycle")
	d.variety = "1212100405"
	d.farm = "Chepsito"
	print("class:", type(d).__mro__[:3])
	print("has settle_scope:", hasattr(d, "settle_scope"))
	print("has summer_flower_crop:", hasattr(d, "summer_flower_crop"))
	if hasattr(d, "summer_flower_crop"):
		print("variety on doc:", repr(d.get("variety")))
		print("exists protocol:",
		      frappe.db.exists("Crop Protocol",
		                       {"variety": d.get("variety"),
		                        "custom_is_summer_flower": 1}))
		print("summer_flower_crop():", d.summer_flower_crop())
		d.settle_scope()
		print("scope after settle:", d.custom_cycle_scope)
