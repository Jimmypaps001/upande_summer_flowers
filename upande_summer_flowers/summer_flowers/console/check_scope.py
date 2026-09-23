"""What the dashboard says it is showing, against what it is showing."""

import frappe


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())


def _run():
	from upande_summer_flowers.summer_flowers import operations_api, planning_api

	print("=== scope_of() with nothing selected ===")
	sc = planning_api.scope_of()
	for k in ("plan", "variety", "farm", "implicit", "plans_in_scope", "asked"):
		print("   %-16s %s" % (k, sc[k]))

	print("\n=== scope_of() with a variety chosen ===")
	sc2 = planning_api.scope_of(variety=sc["variety"])
	print("   plan %s | implicit %s | plans_in_scope %s"
	      % (sc2["plan"], sc2["implicit"], sc2["plans_in_scope"]))

	print("\n=== the chain, with procurement in it ===")
	ov = operations_api.process_overview(plan=sc["plan"])
	for i, st in enumerate(ov["stages"], 1):
		flag = "OK " if st.get("done") else ("!! " if st.get("blocked") else "   ")
		print("  %2d %s %-18s %-22s %s" % (i, flag, st["label"],
		      (st.get("value") or st.get("state") or "")[:22],
		      (st.get("blocked") or st.get("detail") or "")[:56]))
	print("\n   first thing to deal with: %s" % ov.get("stuck"))
