// Copy to Clipboard on another site gives you the whole document: the version
// it was approved as, who approved it and when, and links into that site's
// masters. Pasting that raw fails on all of them at once. This takes the
// agronomy and leaves the paperwork behind.
frappe.listview_settings["Crop Protocol"] = Object.assign(
	frappe.listview_settings["Crop Protocol"] || {},
	{
		onload(listview) {
			listview.page.add_inner_button(__("Paste from another site"), () => {
				paste_protocol_dialog();
			});
		},
	}
);

function paste_protocol_dialog() {
	const d = new frappe.ui.Dialog({
		title: __("Paste a protocol from another site"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options:
					`<p class="text-muted" style="font-size:12px;margin:0 0 8px">${__(
						"On the other site, open the Crop Protocol and use Copy to " +
						"Clipboard. Paste it here. The version, the approver and the " +
						"approval date are left behind — a protocol pasted here is a " +
						"draft here, and approving it is what writes its first version."
					)}</p>`,
			},
			{ fieldname: "farm", label: __("Farm here"), fieldtype: "Link",
			  options: "Farm", reqd: 1 },
			{ fieldname: "variety", label: __("Variety here"), fieldtype: "Link",
			  options: "Item",
			  description: __("Leave empty to use the variety named in the paste.") },
			{ fieldname: "payload", label: __("The pasted document"),
			  fieldtype: "Code", options: "JSON", reqd: 1 },
		],
		primary_action_label: __("Check it"),
		primary_action(v) {
			frappe.call({
				method: "upande_summer_flowers.summer_flowers.crop_protocol"
					+ ".paste_protocol",
				args: { ...v, dry_run: 1 },
				freeze: true,
			}).then((r) => {
				if (!r.message) return;
				const m = r.message;
				const lines = [
					`<b>${__("Left behind")}:</b> ${(m.dropped || []).join(", ") || __("nothing")}`,
					`<b>${__("Would create")}:</b> ${(m.created || []).join(", ") || __("nothing")}`,
				];
				frappe.confirm(lines.join("<br>") + `<br><br>${__("Paste it?")}`, () => {
					frappe.call({
						method: "upande_summer_flowers.summer_flowers.crop_protocol"
							+ ".paste_protocol",
						args: v,
						freeze: true,
						freeze_message: __("Pasting..."),
					}).then((res) => {
						if (!res.message) return;
						d.hide();
						frappe.show_alert({
							message: __("Pasted as {0} — a draft here.", [res.message.name]),
							indicator: "green",
						});
						frappe.set_route("Form", "Crop Protocol", res.message.name);
					});
				});
			});
		},
	});
	d.show();
}
