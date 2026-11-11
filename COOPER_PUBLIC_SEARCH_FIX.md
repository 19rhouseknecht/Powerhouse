# Cooper public-search fix

This update changes the Pricing Sheet price checker to use Cooper Electric's
public product-search URL directly. It no longer signs in or depends on finding
the header search field.

For each Cooper part number, the app now opens:

```text
https://www.cooper-electric.com/product/search?q=PART_NUMBER
```

The browser still accepts Cooper's cookie notice when it appears. Prices are
collected only for exact Cooper Electric part-number matches and remain in the
preview until an authorized user applies them.

The parser reads Cooper's exact `data-product-id` and structured
`data-analytics-price` fields. If a Pricing Sheet item is set to **per foot**
and Cooper lists the price per 1,000 feet, the preview divides the source price
by 1,000 and displays the conversion before it can be applied.

The expanded activity window reports the current URL and page title, cookie
status, product-card count, exact matched ID, price-loading wait, raw supplier
price and unit, any per-foot conversion, and the final preview status. The bot
waits up to 20 seconds for results and another 20 seconds for the exact card's
asynchronously loaded price instead of reading the card too early.

## Install on PythonAnywhere

Upload `powerhouse-app-cooper-public-search-fix.zip`, then run:

```bash
cd ~
cp powerhouse-app/instance/app.db powerhouse-app/instance/app-before-cooper-public-search.db
unzip -o powerhouse-app-cooper-public-search-fix.zip
```

Then open the PythonAnywhere **Web** tab and press **Reload**.

The ZIP intentionally excludes the database, virtual environment, uploads,
API keys, and supplier credential-encryption key.
