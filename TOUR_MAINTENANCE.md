# Guided tour maintenance

The top-right **Give a Tour** walkthrough is defined in one file:

```text
static/js/tour.js
```

Whenever a future upgrade adds, removes, renames, or materially changes a page:

1. Add or update its URL in the `tour-routes` block in `templates/base.html`.
2. Add or update the matching entry in the `steps` list in `static/js/tour.js`.
3. Explain the page's purpose, normal workflow, and important safety or approval behavior.
4. Use a stable CSS selector for the element the tour should highlight.
5. Run `node --check static/js/tour.js` and walk through the complete tour.

Tour progress is kept only in the browser's session storage. It does not change
the database, and closing or finishing the tour removes the saved progress.
