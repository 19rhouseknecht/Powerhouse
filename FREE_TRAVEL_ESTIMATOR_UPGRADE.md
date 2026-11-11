# Free Travel Estimator Upgrade

This replaces the Google Routes integration. No Google account, billing, or API key is required.

## Calculation

1. The free U.S. Census geocoder converts the dispatch and job addresses into coordinates.
2. Powerhouse Flow calculates straight-line miles.
3. Straight-line miles are multiplied by the road-distance factor (default `1.25`).
4. Estimated road miles are divided by average speed (default `45 mph`).
5. If the estimated one-way trip exceeds 60 minutes, the complete one-way time is multiplied by the company labor rate and added once to every quote option.

Settings contains the dispatch address, road factor, and average speed. Every job also has an optional manual one-way-minute override. A manual time always wins over the automatic estimate.

## Safe installation

Upload `powerhouse-flow-free-travel-estimator-2026-09-11.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db "powerhouse-app/instance/app-before-free-travel-$(date +%Y%m%d-%H%M%S).db"
unzip -o powerhouse-flow-free-travel-estimator-2026-09-11.zip
```

Reload the website from the PythonAnywhere Web tab. The update does not contain or replace the database, uploads, or virtual environment.

You may remove `GOOGLE_MAPS_API_KEY` from the WSGI configuration; this version does not read it. Previously confirmed quotes keep their locked totals. Existing draft travel calculations are automatically cleared and rebuilt with the free estimator.

If the Census service cannot locate an address, enter the known one-way time in the job's **Manual one-way time** field.
