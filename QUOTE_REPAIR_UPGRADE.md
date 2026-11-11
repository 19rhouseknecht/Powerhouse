# Quote missing-pricing repair upgrade

This additive update turns pending quote lines into an editable repair workflow.

## Included

- Missing Pricing section below the six-option draft.
- Groups repeated missing lines and shows which tiers use them.
- Suggests close-looking services and Pricing Sheet items first.
- Links a missing line to an existing complete service.
- Explicit service multiplier for quantities such as four bulbs at a one-bulb example price.
- Prices simple work from an existing Pricing Sheet item and quantity.
- Creates a new centralized Pricing Sheet item, unit price, unit, and optional Cooper part number directly from the quote.
- Opens the complete service builder with the missing name prefilled when labor, materials, tools, and a reusable scope are needed.
- Returns from service creation to the same quote repair workflow.
- Prevents confirmation while prices are still missing.
- Adds a Rework quote with Caden action after repairs.
- Gives Caden both the Service Library and Pricing Sheet library.
- Lets Caden select exact services with multipliers or exact pricing items with quantities.
- Locks both service and direct-item prices when a quote is confirmed.
- Preserves existing quote, service, and pricing data.

## Safe installation

Upload `powerhouse-app-quote-repair-upgrade.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-quote-repair.db
unzip -o powerhouse-app-quote-repair-upgrade.zip
```

Press **Reload** on the PythonAnywhere Web tab. The new quote-line pricing-item
link is added automatically. The ZIP contains no database, virtual environment,
or uploaded customer files.
