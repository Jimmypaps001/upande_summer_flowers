# Upande Summer Flowers

Demand-driven production planning, budgeting and motherstock scheduling for summer
flowers, on Frappe/ERPNext v16.

Market demand is captured progressively on a rolling three-year horizon. A
production plan is generated from it using the farm's own growing protocol; on
approval the plan produces a monthly budget and writes the matching records into
Accounts. Blocks, plantings and motherstock generations are tracked alongside.

## Why protocols are per farm

The same variety behaves differently at different farms, because climate changes the
flush curve and the cycle length. `Summer Flower Protocol` is therefore named
`{variety}-{farm}` — `Aster Pink Flash-Karen` and `Aster Pink Flash-Naivasha` are
separate records, each carrying its own flush schedule, geometry and lead times, with
a note recording what makes them differ.

## Two ideas the model rests on

**A planting is locked into a small set of harvest weeks.** Where the flush interval
divides the year (52 ÷ 13 = 4), a planting harvests in four fixed weeks of the year,
every year, until it is uprooted. You do not schedule an individual harvest — you
choose which family of weeks to join and accept the rest. The plan reports the family
on every planting.

**Motherstock is sized by the largest single sticking week, never the annual total.**
Cuttings cannot be banked, so a mother must exist for every cutting stuck in the peak
week. Spreading the stick over more weeks divides the motherstock, the bench and the
tissue-culture order by the same factor, at the cost of smearing the harvest across
those weeks.

## The chain

```
Summer Flower Protocol        per farm: flush curve, geometry, propagation, grades
Summer Flower Block           physical register; coverage derived from plantings
Summer Flower Settings        TC price bands, pot holding rate, lab turnaround
        │
        ▼
Summer Flower Market Demand   living weekly register, extended to stay 3 years ahead
        │  Create Production Plan
        ▼
Summer Flower Production Plan weekly + monthly grid, proposed plantings
        │  workflow: Draft → Pending Approval → Approved
        ├──▶ Summer Flower Budget ──▶ Monthly Distribution + Budget (Accounts)
        └──▶ Summer Flower Planting ──▶ block coverage, projected flushes
                    │
                    ▼
        Summer Flower Motherstock Batch   sizing, TC order dates, 52-week renewal
```

A dashboard at `/summer-flowers` covers demand horizon, monthly production against
demand, block coverage, what is on the ground, motherstock renewals, and a
side-by-side comparison of one variety's protocols across farms.

## How the plan is generated

1. Projected production from plantings already standing is laid onto the week grid.
2. The grid is walked forward. At the first week still short of demand, a planting is
   proposed backwards from that week using the protocol's planting-to-harvest offset.
3. That proposal's entire flush series is folded into the grid before moving on, so
   later weeks in its family are credited and not planted for twice.
4. Proposals are allocated to blocks with free beds.
5. Weeks are rolled up to months.

Rows are flagged where the beds required fall below the protocol's minimum block, the
planting week has already passed, or no block had room — rather than being hidden.

## Requirements

- Frappe v16, ERPNext
- `upande_core` (supplies `Farm`)

The Accounts integration targets a `Budget` doctype with a `Farm` option on
`budget_against` and a `budget_distribution` child table. Sites running stock ERPNext
Budget will need `post_to_accounts` adjusted.

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/Jimmypaps001/upande_summer_flowers --branch version-16
bench --site <site> install-app upande_summer_flowers
```

After installing, set up `Summer Flower Settings` (TC price bands, pot holding rate,
lab turnaround) before creating motherstock batches.

## Contributing

This app uses `pre-commit` for formatting and linting:

```bash
cd apps/upande_summer_flowers
pre-commit install
```

Configured tools: ruff, eslint, prettier, pyupgrade.

## License

MIT
