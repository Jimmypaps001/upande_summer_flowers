// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// The routes, one card each: Route 1, Route 2, ... Each card holds what the way
// is (share, supplier lead, plants per unit, price), its timeline from arrival to
// planting, and the lifts its consignments are handed over in. Who propagates it
// is decided per plan, on the procurement plan and the planning board.
//
// Frappe cannot put a table inside a table row, so the cards are drawn here and
// every edit is written straight into the child tables underneath
// (custom_sf_ways, custom_sf_way_stages, custom_sf_lifts),
// which stay hidden.
// The server rebuilds the timeline on save (route_ways.py); the cards redraw.

frappe.provide("upande_summer_flowers.ways");

frappe.ui.form.on("Crop Protocol", {
	refresh(frm) {
		upande_summer_flowers.ways.render(frm);
	},
	crop_type(frm) {
		upande_summer_flowers.ways.render(frm);
	},
});

(function (ways) {
	const WAY = "custom_sf_ways";
	const STAGES = "custom_sf_way_stages";
	const LIFTS = "custom_sf_lifts";
	const info_cache = {};

	ways.info = async function (route) {
		if (!info_cache[route]) {
			info_cache[route] = await frappe.xcall(
				"upande_summer_flowers.summer_flowers.route_ways.route_info",
				{ route }
			);
		}
		return info_cache[route];
	};

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const key = (r) => [r.route, r.kind, r.stage, r.step || ""].join("|");

	ways.render = async function (frm) {
		const field = frm.fields_dict.custom_sf_routes_html;
		if (!field) return;
		const $w = field.$wrapper.empty();
		if (frm.doc.crop_type !== "Summer Flowers") return;

		const list = frm.doc[WAY] || [];
		const infos = {};
		for (const w of list) {
			if (w.route) infos[w.route] = await ways.info(w.route);
		}

		const read_only = !frm.is_new() && !frappe.model.can_write(frm.doctype, frm.docname)
			? true : frm.doc.docstatus > 0;
		const $root = $(`<div class="sf-routes"></div>`).appendTo($w);
		if (list.length > 1) {
			const total = list.reduce((a, w) => a + flt(w.share_pct), 0);
			$root.append(`<div class="text-muted small mb-2">${__("All routes are used at the same time. Shares")}: <b class="${Math.abs(total - 100) > 0.01 ? "text-danger" : ""}">${format_number(total, null, 2)}%</b> ${__("of 100%")}</div>`);
		}
		list.forEach((w, i) => $root.append(ways.card(frm, w, i, infos[w.route] || {}, read_only)));
		if (!list.length) {
			$root.append(`<div class="text-muted mb-2">${__("No route yet. Add how the plant material comes in.")}</div>`);
		}
		if (!read_only) {
			$(`<button class="btn btn-default btn-sm">${__("Add route")}</button>`)
				.appendTo($root)
				.on("click", () => ways.add_route(frm));
		}
		ways.bind(frm, $root);
	};

	ways.card = function (frm, w, i, info, ro) {
		const dis = (cond) => (ro || cond ? "disabled" : "");
		const num = (dt, row, f, val, cond) =>
			`<input type="number" class="form-control input-xs sf-in" data-dt="${dt}" data-name="${row}" data-f="${f}" value="${esc(val)}" ${dis(cond)}>`;
		const txt = (dt, row, f, val) =>
			`<input type="text" class="form-control input-xs sf-in" data-dt="${dt}" data-name="${row}" data-f="${f}" value="${esc(val)}" ${dis(false)}>`;

		const stages = (frm.doc[STAGES] || []).filter((r) => r.route === w.route);
		const lifts = (frm.doc[LIFTS] || []).filter((r) => r.route === w.route)
			.sort((a, b) => cint(a.lift_no) - cint(b.lift_no));
		const not_bought = info.bought === 0;

		let week = 0;
		const stage_rows = stages.map((r) => {
			week += cint(r.weeks);
			return `<tr>
				<td>${esc(r.activity)}</td>
				<td style="width:110px">${r.worked_out ? `<span class="text-muted">—</span>` : num(r.doctype, r.name, "weeks", cint(r.weeks), false)}</td>
				<td style="width:80px" class="text-right">${week}</td>
				<td>${r.kind === "Planting" ? `<span class="text-muted">${__("= week 0 in the field")}</span>` : txt(r.doctype, r.name, "notes", r.notes)}</td>
			</tr>`;
		}).join("");

		const lift_rows = lifts.map((l) => `<tr>
			<td style="width:70px">${num(l.doctype, l.name, "lift_no", cint(l.lift_no), false)}</td>
			<td class="text-muted" style="width:120px">${esc(l.form || info.lift_form || "")}</td>
			<td>${num(l.doctype, l.name, "trough_weeks", cint(l.trough_weeks), false)}</td>
			<td>${num(l.doctype, l.name, "rooting_weeks", cint(l.rooting_weeks), false)}</td>
			<td class="text-right">${cint(l.trough_weeks) + cint(l.rooting_weeks)}</td>
			<td>${num(l.doctype, l.name, "share_pct", flt(l.share_pct), false)}</td>
			<td>${txt(l.doctype, l.name, "notes", l.notes)}</td>
			<td style="width:30px">${ro ? "" : `<a class="sf-del-lift text-muted" data-name="${l.name}" title="${__("Remove lift")}">✕</a>`}</td>
		</tr>`).join("");

		const lifts_block = info.lifts
			? `<div class="mt-3"><b>${__("Lifts")}</b> <span class="text-muted small">${__("only if a consignment is handed over in parts; shares add up to 100%")}</span>
				${lifts.length ? `<table class="table table-sm table-bordered mt-1 mb-1"><thead><tr>
					<th>${__("Lift")}</th><th>${__("Hands over")}</th>
					<th>${__("Weeks on trough")}</th><th>${__("Rooting weeks")}</th>
					<th class="text-right">${__("Delivered (week)")}</th><th>${__("Share %")}</th><th>${__("Notes")}</th><th></th>
				</tr></thead><tbody>${lift_rows}</tbody></table>` : `<div class="text-muted small">${__("No lifts: the consignment comes in whole.")}</div>`}
				${ro ? "" : `<button class="btn btn-xs btn-default sf-add-lift" data-route="${esc(w.route)}" data-form="${esc(info.lift_form || "")}">${__("Add lift")}</button>`}
			</div>`
			: "";

		return `<div class="sf-route-card border rounded p-3 mb-3" data-way="${w.name}">
			<div class="d-flex justify-content-between align-items-center mb-2">
				<h5 class="m-0">${__("Route {0}", [i + 1])}: ${esc(w.route)}</h5>
				${ro ? "" : `<a class="sf-del-way text-muted small" data-name="${w.name}">${__("Remove route")}</a>`}
			</div>
			<div class="row mb-3 row-cols-4">
				<div class="col"><label class="small text-muted">${__("Share %")}</label>${num(w.doctype, w.name, "share_pct", flt(w.share_pct), false)}</div>
				<div class="col"><label class="small text-muted">${__("Supplier lead (weeks)")}</label>${num(w.doctype, w.name, "lead_weeks", cint(w.lead_weeks), not_bought)}</div>
				<div class="col"><label class="small text-muted">${__("Plants per unit")}</label>${num(w.doctype, w.name, "plants_per_unit", flt(w.plants_per_unit) || 1, false)}</div>
				<div class="col"><label class="small text-muted">${__("Cost per unit (item valuation)")}</label><div class="form-control input-xs" style="background:transparent">${not_bought ? "—" : format_currency(flt(w.rate))}</div></div>
			</div>
			<b>${__("Propagation (arrival to planting)")}</b>
			<table class="table table-sm table-bordered mt-1 mb-0"><thead><tr>
				<th>${__("Activity")}</th><th>${__("Weeks")}</th><th class="text-right">${__("Week")}</th><th>${__("Notes")}</th>
			</tr></thead><tbody>${stage_rows}</tbody></table>
			${lifts_block}
		</div>`;
	};

	ways.bind = function (frm, $root) {
		$root.find(".sf-in").on("change", async function () {
			const $i = $(this);
			const f = $i.data("f");
			const v = $i.attr("type") === "number" ? flt($i.val()) : $i.val();
			await frappe.model.set_value($i.data("dt"), $i.data("name"), f, v);
			if (f === "weeks" || f === "trough_weeks" || f === "rooting_weeks" || f === "share_pct") {
				ways.render(frm);
			}
		});
		$root.find(".sf-del-way").on("click", function () {
			const w = (frm.doc[WAY] || []).find((r) => r.name === $(this).data("name"));
			if (!w) return;
			frappe.confirm(__("Remove {0} with its timeline and lifts?", [w.route]), () => {
				ways.remove_rows(frm, WAY, (r) => r.name === w.name);
				ways.remove_rows(frm, STAGES, (r) => r.route === w.route);
				ways.remove_rows(frm, LIFTS, (r) => r.route === w.route);
				frm.dirty();
				ways.render(frm);
			});
		});
		$root.find(".sf-add-lift").on("click", function () {
			const route = $(this).data("route");
			const mine = (frm.doc[LIFTS] || []).filter((r) => r.route === route);
			const next = mine.reduce((a, r) => Math.max(a, cint(r.lift_no)), 0) + 1;
			frm.add_child(LIFTS, { route, lift_no: next, form: $(this).data("form") || "",
			                       share_pct: mine.length ? 0 : 100 });
			frm.dirty();
			ways.render(frm);
		});
		$root.find(".sf-del-lift").on("click", function () {
			const name = $(this).data("name");
			ways.remove_rows(frm, LIFTS, (r) => r.name === name);
			frm.dirty();
			ways.render(frm);
		});
	};

	ways.remove_rows = function (frm, table, test) {
		const keep = (frm.doc[table] || []).filter((r) => !test(r));
		frm.doc[table] = keep;
		keep.forEach((r, i) => (r.idx = i + 1));
	};

	ways.add_route = function (frm) {
		const used = (frm.doc[WAY] || []).map((w) => w.route);
		const d = new frappe.ui.Dialog({
			title: __("Add route"),
			fields: [
				{
					fieldname: "route", fieldtype: "Link", options: "Material Route", reqd: 1,
					label: __("Route"),
					get_query: () => ({ filters: { name: ["not in", used.length ? used : [""]] } }),
				},
				{ fieldname: "share_pct", fieldtype: "Percent", label: __("Share %"),
				  default: used.length ? 0 : 100 },
				{ fieldname: "lead_weeks", fieldtype: "Int", label: __("Supplier lead (weeks)") },
				{ fieldname: "plants_per_unit", fieldtype: "Float", label: __("Plants per unit"), default: 1 },
			],
			primary_action_label: __("Add"),
			primary_action: async (v) => {
				const info = await ways.info(v.route);
				frm.add_child(WAY, {
					route: v.route, bought: info.bought, share_pct: v.share_pct,
					lead_weeks: info.bought ? v.lead_weeks : 0,
					plants_per_unit: v.plants_per_unit || 1,
				});
				const kept = {};
				(frm.doc[STAGES] || []).forEach((r) => (kept[key(r)] = r));
				info.rows.forEach((r) => {
					const old = kept[key(r)];
					frm.add_child(STAGES, Object.assign({}, r, {
						weeks: old ? old.weeks : r.field ? frm.doc[r.field] || 0 : 0,
					}));
				});
				frm.dirty();
				d.hide();
				ways.render(frm);
			},
		});
		d.show();
	};
})(upande_summer_flowers.ways);
