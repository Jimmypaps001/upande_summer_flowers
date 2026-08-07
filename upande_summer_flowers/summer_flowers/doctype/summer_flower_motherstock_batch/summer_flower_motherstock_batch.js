// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt
//
// The two overrides are the only fields on this form the server does not own. Both
// restore themselves the moment they are switched off, rather than leaving the last
// tweaked figure sitting in a field that is no longer being honoured -- a number
// that looks live but is about to be overwritten on save is worse than no number.

frappe.ui.form.on("Summer Flower Motherstock Batch", {
	override_tc_plants(frm) {
		if (!frm.doc.override_tc_plants && frm.doc.tc_plants_calculated) {
			frm.set_value("tc_plants_required", frm.doc.tc_plants_calculated);
		}
	},

	override_tc_order_date(frm) {
		if (!frm.doc.override_tc_order_date && frm.doc.tc_order_date_calculated) {
			frm.set_value("tc_order_date", frm.doc.tc_order_date_calculated);
			// The sticking week followed the override out; it has to come back with it.
			if (frm.doc.sticking_week_required) {
				frm.set_value("first_sticking_date", frm.doc.sticking_week_required);
			}
		}
	},

	refresh(frm) {
		if (frm.doc.override_tc_plants || frm.doc.override_tc_order_date) {
			frm.dashboard.set_headline_alert(
				__("This batch is not following the calculation. See <b>{0}</b>.",
				   [__("What This Choice Costs")]),
				"orange"
			);
		}
	},
});
