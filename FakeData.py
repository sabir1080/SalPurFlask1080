from app import app, db, Supplier, Customer, Item, Purchase, Sale
from datetime import datetime
with app.app_context():
    db.session.query(Sale).delete()
    db.session.query(Purchase).delete()
    db.session.query(Item).delete()
    db.session.query(Supplier).delete()
    db.session.query(Customer).delete()

    supplier = Supplier(name="Supplier A", contact="1234567890", address="Address A")
    customer = Customer(name="Customer A", contact="9876543210", address="Address B")
    item1 = Item(name="Laptop", stock=10, reorder_level=10, purchase_price=10000, sale_price=12000)
    item2 = Item(name="Computer", stock=10, reorder_level=10, purchase_price=20000, sale_price=22000)
    db.session.add_all([supplier, customer, item1, item2])
    db.session.commit()

    supplier = Supplier(name="Supplier B", contact="1234567890", address="Address A")
    customer = Customer(name="Customer B", contact="9876543210", address="Address B")
    item1 = Item(name="LED", stock=10, reorder_level=10, purchase_price=5000, sale_price=6000)
    item2 = Item(name="Printer", stock=10, reorder_level=10, purchase_price=8000, sale_price=9000)
    db.session.add_all([supplier, customer, item1, item2])
    db.session.commit()
    

    purchase1 = Purchase(supplier_id=supplier.id, item_id=item1.id, quantity=50, purchase_price=10000, date=datetime(2025, 5, 1))
    purchase2 = Purchase(supplier_id=supplier.id, item_id=item2.id, quantity=30, purchase_price=20000, date=datetime(2023, 5, 2))
    sale1 = Sale(customer_id=customer.id, item_id=item1.id, quantity=30, sale_price=12000, date=datetime(2025, 5, 1))
    sale2 = Sale(customer_id=customer.id, item_id=item2.id, quantity=20, sale_price=22000, date=datetime(2025, 5, 2))
    db.session.add_all([purchase1, purchase2, sale1, sale2])
    db.session.commit()

    purchase1 = Purchase(supplier_id=supplier.id, item_id=item1.id, quantity=50, purchase_price=10000, date=datetime(2025, 10, 1))
    purchase2 = Purchase(supplier_id=supplier.id, item_id=item2.id, quantity=30, purchase_price=20000, date=datetime(2025, 10, 1))
    sale1 = Sale(customer_id=customer.id, item_id=item1.id, quantity=30, sale_price=12000, date=datetime(2025, 2, 2))
    sale2 = Sale(customer_id=customer.id, item_id=item2.id, quantity=20, sale_price=22000, date=datetime(2025, 2, 3))
    db.session.add_all([purchase1, purchase2, sale1, sale2])
    db.session.commit()