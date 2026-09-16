// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// Crop Protocol is defined by another app, so its form script is attached here
// rather than living beside the doctype.
frappe.ui.form.on("Crop Protocol", {
	refresh(frm) {
		upande_summer_flowers.walkthrough.attach(frm, "Crop Protocol Walkthrough");
	},
});
