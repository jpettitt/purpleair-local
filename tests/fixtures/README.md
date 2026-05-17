# Test fixtures

Real `/json` responses captured from PA-II sensors running firmware 7.02,
with identifying fields redacted:

- `SensorId` → `00:00:00:00:00:0X`
- `Geo` → `PurpleAir-000X` (the original contains a MAC suffix)
- `lat`, `lon` → `0.0`
- `ssid` → `REDACTED`

Two captures are kept because they exercise different shapes:

- `pa2_indoor_single_laser.json` — PA-II indoor with **one** PMS laser
  (`hardwarediscovered: 2.0+BME280+PMSX003-A`). No `_b` fields. Has the
  `response`/`response_date` Data Processor fields.
- `pa2_outdoor_dual_laser.json` — PA-II outdoor with **both** lasers
  (`hardwarediscovered: 2.0+BME280+PMSX003-B+PMSX003-A`). Full `_b`
  field set. No Data Processor configured (no `response*`, no `status_6`).

Both also exercise:

- `pm2.5_aqi` field names with a literal **dot** (firmware 7.02 quirk;
  community docs say `pm2_5_aqi`).
- `place: "inside"` / `"outside"` (docs say `"indoor"` / `"outdoor"`).
- Undocumented fields: `pa_latency`, `latency`, `status_7`.
