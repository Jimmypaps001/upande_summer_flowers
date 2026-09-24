// The crop cycle stands on a planting, and the planting is what consumed the
// plants. Offered here because this is the document a grower opens when the
// crop is in the ground -- the calendar entry behind it is rarely the one they
// went looking for.
frappe.ui.form.on("Crop Cycle", {
	refresh(frm) {
		const cal = frm.doc.custom_planting_calendar;
		if (!cal || frm.doc.docstatus === 2) return;
		frappe.db.get_value("Planting Calendar", cal,
			["stock_entry", "actual_planting_date", "plants", "variety"])
			.then((r) => {
				const d = (r && r.message) || {};
				if (d.stock_entry) {
					frm.dashboard.add_comment(
						__("{0} took {1} {2} out of stock for this cycle.",
							[d.stock_entry, format_number(d.plants || 0, null, 0),
							 d.variety || ""]),
						"green", true);
					return;
				}
				if (!d.actual_planting_date) return;
				frm.add_custom_button(__("Issue Plants From Stock"), () => {
					frappe.call({
						method: "upande_summer_flowers.summer_flowers.plant_issue.issue_plants",
						args: { planting: cal },
						freeze: true,
						freeze_message: __("Taking the plants out of stock..."),
					}).then(() => frm.reload_doc());
				});
			});
	},
});
