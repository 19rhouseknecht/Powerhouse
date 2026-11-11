# Cooper New Items Only Upgrade

The Pricing Sheet now provides two Cooper actions:

- **Get new item prices only** sends active Cooper-linked items whose current price is $0.
- **Update all prices** sends every active Cooper-linked item.

Only one check may be queued or running. Both workflows use the office listener and require review before prices are applied.

## Safe PythonAnywhere installation

```bash
cd ~
cp powerhouse-app/instance/app.db "powerhouse-app/instance/app-before-cooper-scope-$(date +%Y%m%d-%H%M%S).db"
unzip -o powerhouse-flow-cooper-new-items-only-2026-09-03.zip
```

Reload the website from the PythonAnywhere Web tab. The update adds one database column automatically and does not replace the database, uploads, or virtual environment.
