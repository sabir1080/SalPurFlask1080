from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, default=0)
    reorder_level = db.Column(db.Integer, default=10)

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    id_supplier = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    id_item = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    id_supplier_rel = db.relationship('Supplier', backref='purchases')
    id_item_rel = db.relationship('Item', backref='purchases')

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    id_customer = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    id_item = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    sale_price = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    id_customer_rel = db.relationship('Customer', backref='sales')
    id_item_rel = db.relationship('Item', backref='sales')