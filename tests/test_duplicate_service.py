"""Duplicate a service (an overhead 200A service into an underground one): the copy has everything the original has
(details, labor, materials linked to the same Pricing Sheet items, tools, steps, the drawing and its marks) with its own
copies of the pictures, and the original never changes when the copy is edited."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import CSRF, check, done, make_site, sign_in  # noqa: E402

m = make_site()
app, db = m.app, m.db
folder = os.path.join(app.static_folder, "uploads", "services")
os.makedirs(folder, exist_ok=True)
for name in ("aaaaaaaaaaaaaaaa-drawing.png", "bbbbbbbbbbbbbbbb-step-mast.png"):
    with open(os.path.join(folder, name), "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + name.encode())
with app.app_context():
    meter = m.PriceItem(name="200A meter base", lookup_key="meter", unit="ea", price=120, labor_hours_per_unit=1)
    wire = m.PriceItem(name="2/0 aluminum SER", lookup_key="ser", unit="ft", price=4.1)
    db.session.add_all([meter, wire]); db.session.flush()
    service = m.Service(name="200A Service — Overhead", category="Service Upgrades", description="New overhead service.",
                        example_quantity="1 service", estimated_labor_hours=10, has_steps=True,
                        drawing_filename="aaaaaaaaaaaaaaaa-drawing.png", drawing_spots_json=json.dumps({"p%d" % meter.id: [{"x": 10, "y": 20}]}))
    db.session.add(service); db.session.flush()
    for position, (item, qty) in enumerate(((meter, 1), (wire, 25))):
        row = m.ServiceItem(service_id=service.id, name=item.name, quantity=qty, unit=item.unit, unit_price=item.price, position=position)
        row.pricing_link = m.ServiceItemPricingLink(price_item=item)
        db.session.add(row)
    db.session.add(m.ServiceItem(service_id=service.id, name="Weatherhead", quantity=1, unit="ea", unit_price=18, position=2))
    db.session.add(m.ServiceTool(service_id=service.id, name="Bucket truck", tool_type="Rental", rental_cost=150, is_critical=True))
    db.session.add(m.ServiceStep(service_id=service.id, position=0, title="Set the mast", body="Mount the mast.", image_filename="bbbbbbbbbbbbbbbb-step-mast.png"))
    db.session.commit()
    source_id, ids = service.id, {"meter": meter.id, "wire": wire.id}
owner = sign_in(m)

page = owner.get("/quoting/services/%d" % source_id).get_data(as_text=True)
check("⧉ Duplicate" in page, "a service's page has a Duplicate button")
r = owner.post("/quoting/services/%d/duplicate" % source_id, data=CSRF)
with app.app_context():
    copy = m.Service.query.filter(m.Service.id != source_id).one()
    check(r.status_code == 302 and r.headers["Location"].endswith("/quoting/services/%d/edit" % copy.id), "it opens the copy, ready to rename and change")
    check(copy.name == "200A Service — Overhead (copy)" and copy.category == "Service Upgrades" and copy.description == "New overhead service."
          and copy.example_quantity == "1 service" and copy.estimated_labor_hours == 10, "the copy has the same details and labor")
    check([(i.current_name, i.quantity, i.catalog_item.id if i.catalog_item else None) for i in copy.items]
          == [("200A meter base", 1, ids["meter"]), ("2/0 aluminum SER", 25, ids["wire"]), ("Weatherhead", 1, None)],
          "the same materials and quantities, linked to the same Pricing Sheet items (so prices stay in step)")
    check(abs(copy.example_total - db.session.get(m.Service, source_id).example_total) < 0.001, "so it prices the same until it's changed")
    check([(t.name, t.rental_cost, t.is_critical) for t in copy.tools] == [("Bucket truck", 150, True)], "the same tools")
    step = copy.steps[0]
    check(copy.has_steps and step.title == "Set the mast" and step.image_filename and step.image_filename != "bbbbbbbbbbbbbbbb-step-mast.png"
          and os.path.isfile(os.path.join(folder, step.image_filename)), "the steps, each with its own copy of the picture")
    check(copy.drawing_filename and copy.drawing_filename != "aaaaaaaaaaaaaaaa-drawing.png" and copy.drawing_filename.endswith("-drawing.png")
          and os.path.isfile(os.path.join(folder, copy.drawing_filename)) and copy.drawing_spots == db.session.get(m.Service, source_id).drawing_spots,
          "the drawing (its own copy) and where the parts are marked on it")
    copy_id = copy.id
owner.post("/quoting/services/%d/duplicate" % source_id, data=CSRF)
with app.app_context():
    check(m.Service.query.filter_by(name="200A Service — Overhead (copy 2)").count() == 1, "a second copy gets its own name")
    # editing the copy never touches the original
    copy = db.session.get(m.Service, copy_id)
    copy.name = "200A Service — Underground"; copy.items[1].quantity = 80; copy.steps[0].title = "Dig the trench"
    db.session.commit()
    source = db.session.get(m.Service, source_id)
    check(source.name == "200A Service — Overhead" and source.items[1].quantity == 25 and source.steps[0].title == "Set the mast"
          and os.path.isfile(os.path.join(folder, source.drawing_filename)), "the original stays exactly as it was")
done()
