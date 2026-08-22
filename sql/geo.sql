-- ZIP-level aggregates for the mixed-beverage choropleth.
--
-- Leading columns mirror idx_mb_upper_city_date so the planner can serve the
-- existing top-N query from this index too; location_zip trails so GROUP BY
-- location_zip reads from the index instead of the table. Same expression-index
-- requirement as the city predicate: upper(location_city) needs an index on
-- that exact expression, a bare-column index cannot serve it.
CREATE INDEX IF NOT EXISTS idx_mb_upper_city_date_zip
    ON mixed_beverage (upper(location_city), obligation_end_date, location_zip);

