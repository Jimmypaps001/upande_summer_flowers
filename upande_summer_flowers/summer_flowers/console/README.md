# System Console scripts

Not importable module code. Each file here is pasted whole into
**Awesome Bar → System Console → Execute**, which is the only way to load data
onto a site this bench cannot reach — a Frappe Cloud site has no `bench console`.
They therefore run under `safe_exec`: no imports, no `str.format`, no file access,
which is why the sheet figures are inlined rather than read from the workbook.

Every script:

- defaults to `DRY_RUN = True` and reports exactly what it would do
- is idempotent, so a re-run after a partial load repeats nothing
- checks the source figures against their own stated totals **before** writing,
  and refuses to write if they disagree

| script | loads | source |
|---|---|---|
| `import_crop_protocols__system_console.py` | 12 Aster protocols, per variety and farm | `Crop Protocal.xlsx`, tab `TC_MS_PLANTS_PERENNIAL DISTINCT` |
| `import_market_demand__system_console.py` | 8 Aster demand records, 52 weeks each | the farm × variety × grade × week demand sheet |

## What they will not do for you

`import_crop_protocols` sets inputs only. Croplife, lifetime flushes, lifetime
stems per plant and weeks to establishment are derived by the controller, so the
sheet's values for those four are used as **checks** — a mismatch is reported per
field and never written over.

Bed geometry is in neither sheet, and `Crop Protocol Version.set_geometry` refuses
to derive without it, so `SQM_NET_PER_BED` and `BEDS_PER_BLOCK` sit at the top of
the protocol script carried over from `Aster Pink Flash-Karen`. **Nobody at Carzan
or Kariki Juja has confirmed those.** Override per farm in `SQM_NET_BY_FARM`
before anyone approves a protocol that depends on them.

The demand sheet carries no year — `SEASON_START_YEAR` decides which season its
W27→W26 columns land in.
