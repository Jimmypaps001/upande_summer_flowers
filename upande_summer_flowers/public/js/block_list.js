// Draw a block over a run of beds.
//
// A block drawn by hand carries no beds, and everything the planner needs off a
// block -- its area, how many plantings it can hold, what is standing on it -- is
// read off the Bed records that belong to it. So the way to make one is to say
// which beds it covers, and the block follows from that.
frappe.listview_settings["Block"] = frappe.listview_settings["Block"] || {};

const _sf_prior_onload = frappe.listview_settings["Block"].onload;

frappe.listview_settings["Block"].onload = function (listview) {
    if (_sf_prior_onload) _sf_prior_onload(listview);

    listview.page.add_inner_button(__("New from beds"), () => sf_block_from_beds());
};

function sf_block_from_beds() {
    const M = "upande_summer_flowers.summer_flowers.block.";

    const d = new frappe.ui.Dialog({
        title: __("Draw a block over beds"),
        fields: [
            {
                fieldname: "greenhouse", label: __("Greenhouse"), fieldtype: "Link",
                options: "Warehouse", reqd: 1,
                description: __("The house the beds are in. The block takes it, and is named after it."),
            },
            { fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm",
              description: __("Left blank, it is read off the beds.") },
            { fieldname: "cb", fieldtype: "Column Break" },
            { fieldname: "block", label: __("Block name"), fieldtype: "Data", reqd: 1,
              description: __("What the farm calls it — 11B, 16C1. The record is named \"<greenhouse> - Block <this>\".") },
            {
                fieldname: "summer_flowers", label: __("Summer flowers"),
                fieldtype: "Check", default: 1,
                description: __("Ticked, the block is planned by the summer flower planner and measured against its protocols."),
            },
            { fieldname: "sb", fieldtype: "Section Break", label: __("Beds") },
            { fieldname: "first", label: __("From bed"), fieldtype: "Int",
              description: __("Blank takes every bed in the house.") },
            { fieldname: "cb2", fieldtype: "Column Break" },
            { fieldname: "last", label: __("To bed"), fieldtype: "Int" },
            { fieldname: "preview", fieldtype: "HTML" },
        ],
        primary_action_label: __("Create block"),
        primary_action(v) {
            frappe.call({
                method: M + "create_from_beds", freeze: true,
                freeze_message: __("Drawing the block…"),
                args: {
                    greenhouse: v.greenhouse, block: v.block, first: v.first || null,
                    last: v.last || null, farm: v.farm || null,
                    summer_flowers: v.summer_flowers ? 1 : 0,
                },
            }).then((r) => {
                const m = r.message;
                if (!m) return;
                d.hide();
                frappe.show_alert({
                    message: __("{0} created over {1} beds, {2} ha.",
                        [m.block, m.beds, m.measured_ha]),
                    indicator: "green",
                }, 7);
                frappe.set_route("Form", "Block", m.block);
            });
        },
    });

    // What it is about to take, before it takes it. A range that is mostly
    // unmeasured, or half of it already in another block, is worth seeing first --
    // the create refuses the second outright.
    const look = frappe.utils.debounce(() => {
        const g = d.get_value("greenhouse");
        if (!g) return;
        frappe.call({
            method: M + "beds_for_range",
            args: { greenhouse: g, first: d.get_value("first") || null,
                    last: d.get_value("last") || null },
        }).then((r) => {
            const f = r.message;
            if (!f) return;
            if (f.farm && !d.get_value("farm")) d.set_value("farm", f.farm);
            const bits = [];
            bits.push(`<b>${f.free}</b> free bed(s), <b>${f.measured_ha}</b> ha measured`);
            if (f.first_free != null)
                bits.push(`beds ${f.first_free}–${f.last_free}`);
            if (f.unmeasured)
                bits.push(`<span class="text-warning">${f.unmeasured} without a credible measurement</span>`);
            if (f.taken.length)
                bits.push(`<span class="text-danger">${f.taken.length} already in another block: ` +
                    frappe.utils.escape_html(f.taken.slice(0, 4)
                        .map((t) => `${t.bed} → ${t.block}`).join(", ")) +
                    (f.taken.length > 4 ? "…" : "") + "</span>");
            if (!f.beds) bits.push(`<span class="text-danger">no beds in this house</span>`);
            d.fields_dict.preview.$wrapper.html(
                `<p class="text-muted small">${bits.join(" · ")}</p>`);
        });
    }, 300);

    ["greenhouse", "first", "last"].forEach((f) => {
        d.fields_dict[f].df.onchange = look;
    });
    d.show();
}
