"""Losses move from Summer Flower Settings to the protocols' routes.

The settings held one loss for tissue culture and one for roots, for every crop.
Each route on a protocol now states its own. Every route delivered as tissue
culture or roots is given the figure the settings held, so nothing moves until
somebody edits a protocol. Written straight to the rows: no protocol is re-saved.

Since superseded. A loss typed against a way was a second opinion on what the
Grades & Losses tab already said, and only the tab ever sized an order, so the
column is gone and route_loss reads the route's own stages. The patch stays for
sites that ran it before that, and does nothing where the column is absent.
"""

import frappe
from frappe.utils import flt


def execute():
	frappe.reload_doc("summer_flowers", "doctype", "crop_protocol_way")
	from upande_summer_flowers.patches._sf_customisations import ensure

	if not ensure("Crop Protocol", "custom_sf_ways"):
		return
	if not frappe.db.table_exists("Crop Protocol Way"):
		return
	# The column this patch fills no longer exists on a site built since; losses
	# are the protocol's Grades & Losses figures and are read off the route.
	if not frappe.db.has_column("Crop Protocol Way", "loss_pct"):
		return
	from upande_summer_flowers.summer_flowers import stages
	from upande_summer_flowers.summer_flowers.crop_routes import ROUTES

	def setting(field):
		try:
			return flt(frappe.db.get_single_value("Summer Flower Settings", field))
		except Exception:
			return 0.0

	fallback = setting("propagation_loss_pct")
	loss = {"Tissue Culture": setting("tc_loss_pct") or fallback,
	        "Roots": setting("roots_loss_pct") or fallback}
	for w in frappe.get_all("Crop Protocol Way", filters={"parenttype": "Crop Protocol"},
	                        fields=["name", "route", "loss_pct"]):
		if flt(w.loss_pct):
			continue
		form = stages.lift_form((ROUTES.get(w.route) or [None])[0])
		if loss.get(form):
			frappe.db.set_value("Crop Protocol Way", w.name, "loss_pct", loss[form],
			                    update_modified=False)
