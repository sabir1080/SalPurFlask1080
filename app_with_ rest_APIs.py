from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# =========================
# MODELS
# =========================

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, default=0)
    purchase_price = db.Column(db.Float)
    sale_price = db.Column(db.Float)

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact = db.Column(db.String(20))
    address = db.Column(db.String(200))

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact = db.Column(db.String(20))
    address = db.Column(db.String(200))

# =========================
# ITEM APIs
# =========================

@app.route("/")
def home():
    return {
        "message": "API Server is running",
        "available_endpoints": [
            "/api/items",
            "/api/suppliers",
            "/api/customers"
        ]
    }


@app.route('/api/items', methods=['GET'])
def get_items():
    items = Item.query.all()
    return jsonify([
        {
            "id": i.id,
            "name": i.name,
            "stock": i.stock,
            "purchase_price": i.purchase_price,
            "sale_price": i.sale_price
        } for i in items
    ])

@app.route('/api/items/<int:id>', methods=['GET'])
def get_item(id):
    item = db.session.get(Item, id) or abort(404)
    return jsonify({
        "id": item.id,
        "name": item.name,
        "stock": item.stock,
        "purchase_price": item.purchase_price,
        "sale_price": item.sale_price
    })

@app.route('/api/items', methods=['POST'])
def create_item():
    data = request.get_json()
    item = Item(
        name=data.get('name'),
        stock=data.get('stock', 0),
        purchase_price=data.get('purchase_price'),
        sale_price=data.get('sale_price')
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"message": "Item created"}), 201

@app.route('/api/items/<int:id>', methods=['PUT'])
def update_item(id):
    item = db.session.get(Item, id) or abort(404)
    data = request.get_json()

    item.name = data.get('name', item.name)
    item.stock = data.get('stock', item.stock)
    item.purchase_price = data.get('purchase_price', item.purchase_price)
    item.sale_price = data.get('sale_price', item.sale_price)

    db.session.commit()
    return jsonify({"message": "Item updated"})

@app.route('/api/items/<int:id>', methods=['DELETE'])
def delete_item(id):
    item = db.session.get(Item, id) or abort(404)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"message": "Item deleted"})

# =========================
# SUPPLIER APIs
# =========================

@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    suppliers = Supplier.query.all()
    return jsonify([
        {
            "id": s.id,
            "name": s.name,
            "contact": s.contact,
            "address": s.address
        } for s in suppliers
    ])

@app.route('/api/suppliers', methods=['POST'])
def create_supplier():
    data = request.get_json()
    supplier = Supplier(
        name=data.get('name'),
        contact=data.get('contact'),
        address=data.get('address')
    )
    db.session.add(supplier)
    db.session.commit()
    return jsonify({"message": "Supplier created"}), 201

# =========================
# CUSTOMER APIs
# =========================

@app.route('/api/customers', methods=['GET'])
def get_customers():
    customers = Customer.query.all()
    return jsonify([
        {
            "id": c.id,
            "name": c.name,
            "contact": c.contact,
            "address": c.address
        } for c in customers
    ])

@app.route('/api/customers', methods=['POST'])
def create_customer():
    data = request.get_json()
    customer = Customer(
        name=data.get('name'),
        contact=data.get('contact'),
        address=data.get('address')
    )
    db.session.add(customer)
    db.session.commit()
    return jsonify({"message": "Customer created"}), 201

# =========================
# RUN
# =========================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=7000)
