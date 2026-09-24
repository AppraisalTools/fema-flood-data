# fema-flood-data

Our own copy of FEMA's flood maps (panel numbers, effective dates and flood zones) for the counties we appraise in.
The New Order Processing workflow in n8n reads these files to fill in the FEMA section of the Valuator Info,
so it doesn't depend on FEMA's servers (which block n8n Cloud).

## How the workflow uses it
1. Finds which FIRM panel contains the subject's lat/lon, using `data/<ST>/panels.json`.
2. Loads that panel's flood zones from `data/<ST>/z/<panel>.json` and checks which zone the point falls in.
   If the point is inside the panel but not in any mapped hazard area, it's **Zone X (minimal)**.
3. If a county isn't loaded here, the workflow falls back to NC FRIS (NC only), then FEMA directly.

## Adding or updating a county (a few times a year, or when FEMA issues new maps)
1. Go to <https://msc.fema.gov/portal/advanceSearch>. In **Jurisdiction Name or FEMA ID**, enter the county's
   FEMA ID (below), then **Search**.
2. Open **Effective Products → NFHL** and download **NFHL Data-County**.
3. Upload the zip with the n8n **FEMA Flood Data Upload** form. n8n attaches it to a temporary GitHub release.
   (Alternative: commit it to `incoming/` with GitHub Desktop.)
4. A GitHub Action processes it automatically, updates `data/`, and deletes the temporary release, so zips never pile up in the repo.
   Check the **Actions** tab for a green check. Re-loading a county replaces only that county's data.

`data/index.json` lists each loaded county, when it was loaded, and the newest panel date in that download.

## FEMA IDs
State + county FIPS + `C` (countywide).

| County | FEMA ID |   | County | FEMA ID |
|---|---|---|---|---|
| York, SC | 45091C | | Durham, NC | 37063C |
| Chester, SC | 45023C | | Wake, NC | 37183C |
| Lancaster, SC | 45057C | | Mecklenburg, NC | 37119C |

Any other county: 37 (NC) or 45 (SC) + the county's 3-digit FIPS code + C.

## Running the build by hand
```
python3 scripts/build.py path/to/45091C_20260902.zip
```
Only the Python standard library is needed.

Always spot-check against the FIRM (or CRS) before signing. This is a convenience copy, not the official map.
