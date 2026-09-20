# Retired scripts

These ran once, did their job, and are kept out of `scripts/` because running
them again would damage the tree rather than no-op.

## geo_panel.sh, geo_wire.sh, geo_move.sh

One-shot code generators for the GeoPanel work (Aug 2026). They create
`web/src/GeoPanel.jsx`, export `ramp()` from `YoYHeatmap.jsx`, add the import to
`App.jsx`, and move the panel's section block.

`geo_wire.sh` and `geo_panel.sh` guard themselves and exit early if already
applied. `geo_move.sh` does not: it rewrites `App.jsx` with `sed` against a
block that no longer looks the way it did, and would mangle the file.

The output of all three is committed. There is nothing to re-run.

## geo_index.sh

Wrote `sql/geo.sql` and applied it to guildenstern by hand. The index
(`idx_mb_upper_city_date_zip`) is live, but nothing in the repo applied the file
— a rebuilt database would have come up without it and `/api/mb/geo` would have
degraded the way `/api/mb/austin/top` once did.

`deploy.sh` now ships and applies every `sql/*.sql`, and `check.sh` fails if a
`.sql` file is applied by nothing.

## geo_ship.sh

build -> commit -> push -> deploy -> verify, for the GeoPanel work.

Superseded by `ship.sh` (branch, commit, test, PR, squash-merge) and
`deploy.sh web` (build locally, ship `web/dist`, verify the served bundle).
Its convention of building locally and deploying `web/dist` is the one
`deploy.sh` now follows — guildenstern needs no node_modules, and a broken
build fails on the machine where you can see it.
