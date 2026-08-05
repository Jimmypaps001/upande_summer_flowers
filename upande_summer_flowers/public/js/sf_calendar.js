// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt
//
// A calendar for child table rows.
//
// Frappe's calendar view is a list view: it plots documents, and a child row is not
// a document. Demand weeks, plan weeks, plantings and sticking weeks all carry dates
// and none of them could be seen on a calendar -- you read them as a grid of numbers
// and worked out for yourself which month a week fell in, which is exactly the thing
// a calendar is for.
//
// One renderer, used by every form that has dated rows. Weekly figures land on their
// Monday; single-date events (planting, pinch, first harvest, uproot) land on the day
// itself as a marker, so a week can carry both.

window.sf_calendar = (function () {
	const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
	const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
		"August", "September", "October", "November", "December"];

	const iso = (d) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1)
		.padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
	const parse = (s) => {
		if (!s) return null;
		const [y, m, d] = String(s).slice(0, 10).split("-").map(Number);
		return y ? new Date(Date.UTC(y, m - 1, d)) : null;
	};
	const num = (n) => (n || n === 0
		? (window.frappe ? frappe.format(Math.round(n), { fieldtype: "Int" })
			: String(Math.round(n)))
		: "");
	const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
		(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

	// Monday of the week a date falls in. Everything weekly in this app is keyed to
	// the ISO Monday, including which season a week belongs to, so the calendar has
	// to agree with that or a week would appear in the wrong month.
	function monday(d) {
		const out = new Date(d.getTime());
		const shift = (out.getUTCDay() + 6) % 7;
		out.setUTCDate(out.getUTCDate() - shift);
		return out;
	}

	function isoWeek(d) {
		const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
		t.setUTCDate(t.getUTCDate() + 4 - ((t.getUTCDay() + 6) % 7 + 1));
		const first = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
		return Math.ceil(((t - first) / 86400000 + 1) / 7);
	}

	// A scale that survives one enormous week. Rank rather than magnitude: the point
	// of the shading is which weeks are heavier than which, and a single 10x week
	// would otherwise flatten everything else to the same pale colour.
	function shader(values, negativeIsBad) {
		const seen = values.filter((v) => v !== null && v !== undefined && v !== 0);
		const sorted = [...seen].sort((a, b) => a - b);
		return (v) => {
			if (v === null || v === undefined || v === 0) return "";
			if (negativeIsBad && v < 0) {
				const worst = Math.min(...seen.filter((x) => x < 0), -1);
				const a = 0.12 + 0.5 * Math.min(1, Math.abs(v) / Math.abs(worst));
				return `background:rgba(192,57,43,${a.toFixed(2)})`;
			}
			if (negativeIsBad) return "background:rgba(39,174,96,0.18)";
			const rank = sorted.indexOf(v) / Math.max(1, sorted.length - 1);
			const a = 0.10 + 0.45 * rank;
			return `background:rgba(41,128,185,${a.toFixed(2)})`;
		};
	}

	function render(wrapper, opts) {
		const weeks = (opts.weeks || []).map((w) => ({ ...w, d: parse(w.date) }))
			.filter((w) => w.d);
		const events = (opts.events || []).map((e) => ({ ...e, d: parse(e.date) }))
			.filter((e) => e.d);
		if (!weeks.length && !events.length) {
			wrapper.innerHTML = `<p class="text-muted">${
				esc(opts.empty || "Nothing dated to show yet.")}</p>`;
			return;
		}

		const byMonday = {};
		weeks.forEach((w) => { byMonday[iso(monday(w.d))] = w; });
		const byDay = {};
		events.forEach((e) => {
			const k = iso(e.d);
			(byDay[k] = byDay[k] || []).push(e);
		});

		const all = [...weeks.map((w) => w.d), ...events.map((e) => e.d)];
		const from = new Date(Math.min(...all)), to = new Date(Math.max(...all));
		const shade = shader(weeks.map((w) => w.value), !!opts.signed);

		let html = "";
		if (opts.legend) {
			html += `<div style="margin:0 0 8px;font-size:11px" class="text-muted">${
				opts.legend.map((l) => `<span style="margin-right:14px">${
					l.dot ? `<span style="display:inline-block;width:8px;height:8px;
						border-radius:50%;background:${l.dot};margin-right:4px"></span>` : ""
				}${esc(l.label)}</span>`).join("")}</div>`;
		}
		html += `<div style="display:flex;flex-wrap:wrap;gap:14px">`;

		let cur = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), 1));
		const end = new Date(Date.UTC(to.getUTCFullYear(), to.getUTCMonth(), 1));
		while (cur <= end) {
			const y = cur.getUTCFullYear(), m = cur.getUTCMonth();
			html += `<div style="min-width:236px">
				<div style="font-weight:600;font-size:12px;margin-bottom:3px">${
					MONTHS[m]} ${y}</div>
				<table style="border-collapse:collapse;font-size:10px;table-layout:fixed;width:236px">
				<thead><tr>${DAYS.map((d) => `<th style="width:28px;padding:2px;
					text-align:center;color:#888;font-weight:500">${d[0]}</th>`).join("")}
					<th style="width:40px;padding:2px;text-align:right;color:#888;
					font-weight:500">wk</th></tr></thead><tbody>`;

			// Whole weeks, Monday first, so a week's figure sits on one row even where
			// the week straddles two months.
			let day = monday(new Date(Date.UTC(y, m, 1)));
			const lastOfMonth = new Date(Date.UTC(y, m + 1, 0));
			while (day <= lastOfMonth) {
				const wk = byMonday[iso(day)];
				const tint = wk ? shade(wk.value) : "";
				html += `<tr style="${tint}">`;
				for (let i = 0; i < 7; i++) {
					const cell = new Date(day.getTime());
					cell.setUTCDate(cell.getUTCDate() + i);
					const outside = cell.getUTCMonth() !== m;
					const evs = byDay[iso(cell)] || [];
					const dots = evs.map((e) => `<span title="${esc(e.title || "")}"
						style="display:inline-block;width:6px;height:6px;border-radius:50%;
						background:${e.colour || "#8e44ad"}"></span>`).join("");
					html += `<td style="padding:2px;text-align:center;border:1px solid #eee;
						color:${outside ? "#ccc" : "#333"}">${cell.getUTCDate()}
						<div style="height:7px;line-height:7px">${dots}</div></td>`;
				}
				const label = wk
					? `<span title="${esc(wk.title || "")}"><b>${
						opts.signed && wk.value > 0 ? "+" : ""}${num(wk.value)}</b></span>`
					: "";
				html += `<td style="padding:2px;text-align:right;border:1px solid #eee;
					white-space:nowrap"><span style="color:#999">${
					isoWeek(day)}</span>${label ? "<br>" + label : ""}</td></tr>`;
				day.setUTCDate(day.getUTCDate() + 7);
			}
			html += `</tbody></table></div>`;
			cur = new Date(Date.UTC(y, m + 1, 1));
		}
		wrapper.innerHTML = html + `</div>`;
	}

	return function sf_calendar(field, opts) {
		const wrapper = field && field.$wrapper ? field.$wrapper.get(0) : field;
		if (!wrapper) return;
		render(wrapper, opts || {});
	};
})();
