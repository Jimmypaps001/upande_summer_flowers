// Copyright (c) 2026, James Kiruga and contributors

// The season plan holds nothing of its own -- every row is read off the Planting
// Calendar it names. So the only thing to do to it is read it again.
frappe.ui.form.on("Summer Flower Season Plan", {
	refresh(frm) {
		frm.add_custom_button(__("Rebuild From Calendars"), () => {
			frm.call({ doc: frm.doc, method: "rebuild", freeze: true,
				freeze_message: __("Reading the calendars...") })
				.then(() => frm.save().then(() => frm.reload_doc()));
		});
		if (frm.doc.production_plan) {
			frm.add_custom_button(__("Production Plan"), () =>
				frappe.set_route("Form", "Summer Flower Production Plan",
					frm.doc.production_plan));
		}
		if (frm.doc.summary) {
			frm.dashboard.add_comment(frm.doc.summary, "blue", true);
		}
	},
});
