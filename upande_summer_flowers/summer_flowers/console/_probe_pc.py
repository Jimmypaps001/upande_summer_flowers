import frappe


def run(user="james@upande.com"):
	print("total Planting Calendar rows:", frappe.db.count("Planting Calendar"))
	print("by farm:", frappe.db.sql(
		"select farm, count(*) n from `tabPlanting Calendar` group by farm",
		as_dict=True))
	if not frappe.db.exists("User", user):
		print("no such user:", user)
		users = frappe.get_all("User", filters={"enabled": 1},
		                       pluck="name", limit=8)
		print("users here:", users)
		return
	print("\n--- as %s ---" % user)
	perms = frappe.get_all("User Permission", filters={"user": user},
	                       fields=["allow", "for_value", "applicable_for"])
	print("user permissions:", perms or "none")
	print("roles:", sorted(frappe.get_roles(user))[:14])
	frappe.set_user(user)
	try:
		rows = frappe.get_list("Planting Calendar", limit_page_length=0)
		print("get_list sees:", len(rows))
	except Exception as e:
		print("get_list raised:", str(e)[:140])
	m = frappe.get_meta("Planting Calendar")
	print("\ndoctype perms:", [(p.role, p.read, p.write, p.create)
	                           for p in m.permissions])
	frappe.set_user("Administrator")
