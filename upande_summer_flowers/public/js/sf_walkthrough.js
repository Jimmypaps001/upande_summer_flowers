// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// The "i" icon that runs a form's walkthrough.
//
// A Form Tour on its own is unreachable. Frappe builds frm.tour on every form but
// only ever starts it from an onboarding step, or when the tour happens to be named
// exactly after its doctype. Ours are named for what they explain, so nothing ever
// started them and the walkthrough written for the Crop Protocol had no way in.
// This puts the icon on the form and starts the named tour when it is clicked.
//
// The tours carry the wording that used to sit under every field as a description.
// Shown once, when asked for, instead of occupying the form for good.

frappe.provide("upande_summer_flowers.walkthrough");

upande_summer_flowers.walkthrough.attach = function (frm, tour_name) {
	// refresh fires repeatedly; the icon is added once per form.
	if (!frm || frm.__sf_walkthrough === tour_name) return;
	frm.__sf_walkthrough = tour_name;

	frappe.db.exists("Form Tour", tour_name).then((exists) => {
		// A site without the fixture is not an error. An icon that opens an empty
		// overlay would be, so nothing is added.
		if (!exists) {
			frm.__sf_walkthrough = null;
			return;
		}
		frm.page.add_action_icon(
			"info",
			() => upande_summer_flowers.walkthrough.start(frm, tour_name),
			"sf-walkthrough-btn",
			__("How this form is filled in")
		);
	});
};

upande_summer_flowers.walkthrough.start = function (frm, tour_name) {
	// A tour highlights fields one at a time, so init() opens every collapsed
	// section first -- a step pointing at a field inside a shut section highlights
	// nothing and looks broken.
	Promise.resolve(frm.tour.init({ tour_name: tour_name }))
		.then(() => frm.tour.start())
		.catch((e) => {
			frappe.msgprint({
				title: __("Walkthrough"),
				message: __("The walkthrough could not be opened: {0}", [e.message || e]),
				indicator: "orange",
			});
		});
};
