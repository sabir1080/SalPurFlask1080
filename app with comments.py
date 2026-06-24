# File ka Maqsad:
# Ye file ek Flask-based web application hai jo ke inventory management system ke liye banayi gayi hai, jise "SalPurFlask" kaha jata hai. 
# Iska maqsad hai ke users ko suppliers, customers, items, purchases, aur sales ka record manage karne ki sahoolat di jaye. 
# Is application mein user authentication (signup, signin, email verification, password reset), data management (CRUD operations), aur reporting features shamil hain.
#
# ### Is File Mein Kya Kya Use Hua Hai:
# - **Flask**: Web framework jo ke routes, templates, aur server handling ke liye use hota hai.
# - **Flask-SQLAlchemy**: Database operations ke liye, SQLite database ke sath kaam karta hai.
# - **Flask-Login**: User authentication aur session management ke liye.
# - **passlib**: Password hashing ke liye (Argon2 algorithm).
# - **smtplib**: Email sending ke liye (e.g., verification aur password reset emails).
# - **itsdangerous**: Secure token generation ke liye (email verification aur password reset ke liye).
# - **Flask-Paginate**: Data tables mein pagination ke liye.
# - **python-dotenv**: Environment variables load karne ke liye.
# - **SQLAlchemy**: Database ke sath ORM (Object-Relational Mapping) ke liye.
# - **CSV**: Data export ke liye CSV files generate karne ke liye.
#
# ### Ye Application Kya Kya Karti Hai:
# 1. **User Authentication**:
#    - Signup with email verification.
#    - Signin with password hashing.
#    - Password reset via email.
#    - Email verification resend option.
#    - Signout functionality.
# 2. **Inventory Management**:
#    - Suppliers, customers, aur items ka CRUD (Create, Read, Update, Delete).
#    - Purchases aur sales ka record rakhna.
#    - Stock management (items ke stock ko update karna).
# 3. **Reports**:
#    - Date-wise purchase aur sale reports.
#    - Reorder level ke items ki report.
#    - Profit analysis by date, item, aur customer.
#    - CSV export for all reports.
# 4. **Security**:
#    - Password hashing with Argon2.
#    - Email verification for secure access.
#    - Session management with Flask-Login.
#
# ### File Structure aur Functionality:
# - **Models**: User, Supplier, Customer, Item, Purchase, aur Sale ke database models define kiye gaye hain.
# - **Routes**: Authentication, dashboard, CRUD operations, aur reporting ke liye routes banaye gaye hain.
# - **Utilities**: Email sending, token generation, aur verification ke helper functions.
# - **Decorators**: `@login_required` aur `@verified_required` ka use secure routes ke liye.

# File: app.py
# Maqsad: Yeh main Flask application file hai jo ke inventory system ke core logic ko handle karta hai.
# Is mein user authentication (signup, signin, etc.), suppliers, customers, aur database operations shamil hain.

# File: app.py (Continuation)
# Maqsad: Yeh code pehle wale app.py ka hissa hai aur items, purchases, sales, aur reports ke routes define karta hai.
# Is mein inventory management ke liye zaroori CRUD operations aur reporting features shamil hain.

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session, abort
# Flask ke zaroori components import kiye: routing, templates, requests, redirects, flash messages, aur file serving ke liye.

from flask_sqlalchemy import SQLAlchemy
# Database operations ke liye SQLAlchemy ORM.

from flask_paginate import Pagination, get_page_args
# Data tables mein pagination ke liye.

from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
# User authentication aur session management ke liye.

from passlib.context import CryptContext
# Password hashing ke liye Argon2 algorithm.

from datetime import datetime, timedelta
# Date aur time operations ke liye.

from functools import wraps
# Custom decorators ke liye.

import csv
# CSV files generate aur export karne ke liye.

import os
# File paths aur environment variables ke liye.

import secrets
# Secure random tokens ke liye (password reset).

from sqlalchemy.exc import IntegrityError
# Database errors (jaise duplicate entries) handle karne ke liye.

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
# Email sending ke liye.

from itsdangerous import URLSafeTimedSerializer
# Secure tokens generate aur verify karne ke liye.

from dotenv import load_dotenv
# Environment variables load karne ke liye.

from sqlalchemy.sql import func
# SQLAlchemy ke aggregate functions (jaise sum) ke liye.

# Flask app initialize karna (pehle wale code se continuation)
app = Flask(__name__)

# Environment variables load karna
load_dotenv()
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
app.config["SECURITY_PASSWORD_SALT"] = os.getenv("SECURITY_PASSWORD_SALT", "your_salt")
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "zeshanlook@gmail.com")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "rsyq cbds cmgy hxxb")

# SQLAlchemy aur LoginManager initialize karna
db = SQLAlchemy(app)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "signin"

# Utility Functions (pehle wale code se)
def generate_verification_token(email):
    # Email verification ke liye secure token banata hai.
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
    return serializer.dumps(email, salt=app.config["SECURITY_PASSWORD_SALT"])

def verify_token(token, expiration=3600):
    # Token verify karta hai (1 ghanta expiry).
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
    try:
        email = serializer.loads(token, salt=app.config["SECURITY_PASSWORD_SALT"], max_age=expiration)
        return email
    except Exception:
        return None

def send_email(to_email, subject, body):
    # Email bhejne ka function jo verification aur password reset ke liye use hota hai.
    try:
        msg = MIMEMultipart()
        msg["From"] = app.config["MAIL_USERNAME"]
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"])
        server.starttls()
        server.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {str(e)}")
        flash(f"Failed to send email: {str(e)}", "danger")
        return False

def verified_required(f):
    # Custom decorator jo check karta hai ke user verified hai.
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.verified:
            flash("Please verify your email to access this page.", "danger")
            return redirect(url_for("signin"))
        return f(*args, **kwargs)
    return decorated_function

# Database Models (pehle wale code se)
class User(db.Model, UserMixin):
    # User ka data store karne ka model (authentication ke liye).
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    verified = db.Column(db.Boolean, default=False, nullable=False)
    reset_token = db.Column(db.String(120), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"User('{self.name}', '{self.email}')"

class Supplier(db.Model):
    # Supplier ka data store karne ka model.
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact = db.Column(db.String(15), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    purchases = db.relationship("Purchase", backref="id_supplier", lazy=True)

class Customer(db.Model):
    # Customer ka data store karne ka model.
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact = db.Column(db.String(15), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    sales = db.relationship("Sale", backref="id_customer", lazy=True)

class Item(db.Model):
    # Item ka data store karne ka model.
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    reorder_level = db.Column(db.Integer, nullable=False, default=50)
    purchase_price = db.Column(db.Float, nullable=True)
    sale_price = db.Column(db.Float, nullable=True)
    purchases = db.relationship("Purchase", backref="id_item", lazy=True)
    sales = db.relationship("Sale", backref="id_item", lazy=True)

class Purchase(db.Model):
    # Purchase ka data store karne ka model.
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Sale(db.Model):
    # Sale ka data store karne ka model.
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    sale_price = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

# Database create karna
with app.app_context():
    db.create_all()

# Flask-Login ke liye user loader
@login_manager.user_loader
def load_user(user_id):
    # User ID ke base pe user ko load karta hai.
    return db.session.get(User, int(user_id))

# Pagination helper function
def get_paginated_results(query, per_page=10):
    # Data ko paginate karne ka function jo results aur pagination object return karta hai.
    page, _, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    results = query.offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework="bootstrap5")
    return results, pagination

# Authentication Routes (pehle wale code se)
@app.route("/signup", methods=["GET", "POST"])
def signup():
    # User signup ka route jo naye users ko register karta hai.
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        print(f"Signup attempt: name={name}, email={email}, password={password}")
        if not name or not email or not password:
            flash("All fields are required!", "danger")
            return render_template("signup.html")
        if User.query.filter_by(email=email).first():
            flash("Email already registered!", "danger")
            return render_template("signup.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "danger")
            return render_template("signup.html")
        hashed_password = pwd_context.hash(password)
        print(f"Hashed password: {hashed_password}")
        user = User(name=name, email=email, password=hashed_password, verified=False)
        try:
            db.session.add(user)
            db.session.commit()
            print(f"User saved: {user.email}, verified={user.verified}")
        except Exception as e:
            db.session.rollback()
            print(f"Database error: {str(e)}")
            flash("Failed to register user. Please try again.", "danger")
            return render_template("signup.html")
        token = generate_verification_token(email)
        verification_url = url_for("verify_email", token=token, _external=True)
        body = f"""
        Hello {name},

        Thank you for registering with SalPurFlask. Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        SalPurFlask Team
        """
        if send_email(email, "Verify Your Email - SalPurFlask", body):
            flash("A verification email has been sent. Please check your inbox.", "success")
        else:
            db.session.delete(user)
            db.session.commit()
            return render_template("signup.html")
        return redirect(url_for("signin"))
    return render_template("signup.html")

@app.route("/signin", methods=["GET", "POST"])
def signin():
    # User signin ka route jo users ko login karne deta hai.
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        print(f"Signin attempt: email={email}, password={password}")
        user = User.query.filter_by(email=email).first()
        if user:
            print(f"Single User Found: {user.id}, {user.email}, {user.name}")
        else:
            print("User not found with this email.")
        if user:
            print(f"User found: email={user.email}, verified={user.verified}, hash={user.password}")
            try:
                if pwd_context.verify(password, user.password):
                    if not user.verified:
                        flash("Please verify your email before signing in. Check your inbox.", "danger")
                        return render_template("signin.html")
                    login_user(user)
                    session["user_id"] = user.id
                    print(f"Signin successful: {user.email}")
                    flash("Signed in successfully!", "success")
                    return redirect(url_for("index"))
                else:
                    print("Password verification failed")
                    flash("Invalid email or password!", "danger")
            except Exception as e:
                print(f"Password verification error: {str(e)}")
                flash("Invalid email or password!", "danger")
        else:
            print("User not found")
            flash("Invalid email or password!", "danger")
    return render_template("signin.html")

@app.route("/signout")
@login_required
def signout():
    # User signout ka route jo session khatam karta hai.
    logout_user()
    session.pop("user_id", None)
    flash("Signed out successfully!", "success")
    return redirect(url_for("signin"))

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    # Password reset request ka route jo reset link bhejta hai.
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Email not found!", "danger")
        else:
            token = secrets.token_urlsafe(32)
            expiry = datetime.utcnow() + timedelta(hours=1)
            user.reset_token = token
            user.reset_token_expiry = expiry
            db.session.commit()
            reset_url = url_for("reset_password", token=token, _external=True)
            body = f"""
            Dear User,

            To reset your password, please click the following link:
            {reset_url}

            This link will expire in 1 hour. If you did not request a password reset, please ignore this email.

            Regards,
            SalPurFlask Team
            """
            if send_email(email, "Password Reset Request - SalPurFlask", body):
                flash("Password reset link sent to your email!", "success")
                return redirect(url_for("signin"))
        return render_template("forgot_password.html")
    return render_template("forgot_password.html")

@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    # Password reset karne ka route jo naya password set karta hai.
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    user = User.query.filter_by(reset_token=token).first()
    if not user or user.reset_token_expiry < datetime.utcnow():
        flash("Invalid or expired reset link!", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "danger")
        else:
            user.password = pwd_context.hash(password)
            user.reset_token = None
            user.reset_token_expiry = None
            db.session.commit()
            flash("Password reset successfully! Please sign in.", "success")
            return redirect(url_for("signin"))
    return render_template("reset_password.html", token=token)

@app.route("/verify_email/<token>")
def verify_email(token):
    # Email verification ka route jo user ko verified mark karta hai.
    email = verify_token(token)
    if not email:
        flash("Invalid or expired verification link!", "danger")
        return redirect(url_for("signup"))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash("User not found!", "danger")
        return redirect(url_for("signup"))
    if user.verified:
        flash("Email already verified! Please sign in.", "success")
        return redirect(url_for("signin"))
    try:
        user.verified = True
        db.session.commit()
        print(f"User verified: {user.email}")
        flash("Email verified successfully! You can now sign in.", "success")
    except Exception as e:
        db.session.rollback()
        print(f"Verification error: {str(e)}")
        flash("Failed to verify email. Please try again.", "danger")
    return redirect(url_for("signin"))

@app.route("/resend_verification", methods=["GET", "POST"])
def resend_verification():
    # Verification email dobara bhejne ka route.
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Email not found!", "danger")
            return render_template("resend_verification.html")
        if user.verified:
            flash("Email already verified! Please sign in.", "success")
            return redirect(url_for("signin"))
        token = generate_verification_token(email)
        verification_url = url_for("verify_email", token=token, _external=True)
        body = f"""
        Hello {user.name},

        Please click the link below to verify your email address:

        {verification_url}

        This link will expire in 1 hour.

        Regards,
        SalPurFlask Team
        """
        if send_email(email, "Verify Your Email - SalPurFlask", body):
            flash("A new verification email has been sent. Please check your inbox.", "success")
        return redirect(url_for("signin"))
    return render_template("resend_verification.html")

# Main Routes (pehle wale code se)
@app.route("/")
def index():
    # Homepage ka route jo authenticated users ko index page dikhata hai.
    if current_user.is_authenticated:
        return render_template("index.html")
    return redirect(url_for("signin"))

@app.route('/dashboard')
@login_required
def dashboard():
    # Dashboard route jo inventory ke stats dikhata hai.
    items = Item.query.all()
    purchases = Purchase.query.order_by(Purchase.date.desc()).limit(5).all()
    sales = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    total_purchase_cost = db.session.query(func.sum(Purchase.quantity * Purchase.purchase_price)).scalar() or 0.0
    total_sale_revenue = db.session.query(func.sum(Sale.quantity * Sale.sale_price)).scalar() or 0.0
    return render_template(
        'dashboard.html',
        items=items,
        purchases=purchases,
        sales=sales,
        total_purchase_cost=total_purchase_cost,
        total_sale_revenue=total_sale_revenue,
    )

@app.route("/supplier", methods=["GET", "POST"])
@verified_required
def supplier():
    # Supplier management ka route jo suppliers ko add, list, aur search karne deta hai.
    search = request.args.get("search", "")
    query = Supplier.query.filter(Supplier.name.ilike(f"%{search}%")) if search else Supplier.query
    suppliers, pagination = get_paginated_results(query)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        address = request.form.get("address", "").strip()
        if not name or not contact or not address:
            flash("All fields are required!", "danger")
        elif not contact.isdigit() or len(contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            supplier = Supplier(name=name, contact=contact, address=address)
            db.session.add(supplier)
            db.session.commit()
            flash("Supplier added successfully!", "success")
            return redirect(url_for("supplier"))
    return render_template("supplier.html", suppliers=suppliers, pagination=pagination, search=search)

@app.route("/supplier/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_supplier(id):
    # Supplier edit karne ka route.
    supplier = db.session.get(Supplier, id) or abort(404)
    if request.method == "POST":
        supplier.name = request.form.get("name", "").strip()
        supplier.contact = request.form.get("contact", "").strip()
        supplier.address = request.form.get("address", "").strip()
        if not supplier.name or not supplier.contact or not supplier.address:
            flash("All fields are required!", "danger")
        elif not supplier.contact.isdigit() or len(supplier.contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            db.session.commit()
            flash("Supplier updated successfully!", "success")
            return redirect(url_for("supplier"))
    return render_template("edit_supplier.html", supplier=supplier)

@app.route("/supplier/delete/<int:id>", methods=["POST"])
@verified_required
def delete_supplier(id):
    # Supplier delete karne ka route.
    supplier = db.session.get(Supplier, id) or abort(404)
    if supplier.purchases:
        flash("Cannot delete supplier with associated purchases!", "danger")
    else:
        db.session.delete(supplier)
        db.session.commit()
        flash("Supplier deleted successfully!", "success")
    return redirect(url_for("supplier"))

@app.route("/export_suppliers")
@verified_required
def export_suppliers():
    # Suppliers ko CSV mein export karne ka route.
    suppliers = Supplier.query.all()
    with open("static/suppliers.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Name", "Contact", "Address"])
        for s in suppliers:
            writer.writerow([s.id, s.name, s.contact, s.address])
    return send_from_directory("static", "suppliers.csv")

@app.route("/customer", methods=["GET", "POST"])
@verified_required
def customer():
    # Customer management ka route jo customers ko add, list, aur search karne deta hai.
    search = request.args.get("search", "")
    query = Customer.query.filter(Customer.name.ilike(f"%{search}%")) if search else Customer.query
    customers, pagination = get_paginated_results(query)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        address = request.form.get("address", "").strip()
        if not name or not contact or not address:
            flash("All fields are required!", "danger")
        elif not contact.isdigit() or len(contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            customer = Customer(name=name, contact=contact, address=address)
            db.session.add(customer)
            db.session.commit()
            flash("Customer added successfully!", "success")
            return redirect(url_for("customer"))
    return render_template("customer.html", customers=customers, pagination=pagination, search=search)

@app.route("/customer/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_customer(id):
    # Customer edit karne ka route.
    customer = db.session.get(Customer, id) or abort(404)
    if request.method == "POST":
        customer.name = request.form.get("name", "").strip()
        customer.contact = request.form.get("contact", "").strip()
        customer.address = request.form.get("address", "").strip()
        if not customer.name or not customer.contact or not customer.address:
            flash("All fields are required!", "danger")
        elif not customer.contact.isdigit() or len(customer.contact) < 10:
            flash("Contact must be a valid phone number!", "danger")
        else:
            db.session.commit()
            flash("Customer updated successfully!", "success")
            return redirect(url_for("customer"))
    return render_template("edit_customer.html", customer=customer)

@app.route("/customer/delete/<int:id>", methods=["POST"])
@verified_required
def delete_customer(id):
    # Customer delete karne ka route.
    customer = db.session.get(Customer, id) or abort(404)
    if customer.sales:
        flash("Cannot delete customer with associated sales!", "danger")
    else:
        db.session.delete(customer)
        db.session.commit()
        flash("Customer deleted successfully!", "success")
    return redirect(url_for("customer"))

@app.route("/export_customers")
@verified_required
def export_customers():
    # Customers ko CSV mein export karne ka route.
    customers = Customer.query.all()
    with open("static/customers.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Name", "Contact", "Address"])
        for c in customers:
            writer.writerow([c.id, c.name, c.contact, c.address])
    return send_from_directory("static", "customers.csv")

# Item Management Routes
@app.route("/item", methods=["GET", "POST"])
@verified_required
def item():
    # Item management ka route jo items ko add, list, aur search karne deta hai.
    search = request.args.get("search", "").strip()
    query = Item.query.filter(Item.name.ilike(f"%{search}%")) if search else Item.query
    items, pagination = get_paginated_results(query)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        stock = request.form.get("stock", "").strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        # Input validation
        if not name or not stock or not reorder_level:
            flash("Name, Stock, and Reorder Level are required!", "danger")
        elif not stock.isdigit() or not reorder_level.isdigit():
            flash("Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        else:
            # Naya item create aur save karna
            item = Item(
                name=name,
                stock=int(stock),
                reorder_level=int(reorder_level),
                purchase_price=float(purchase_price) if purchase_price else None,
                sale_price=float(sale_price) if sale_price else None,
            )
            db.session.add(item)
            db.session.commit()
            flash("Item added successfully!", "success")
            return redirect(url_for("item"))
    return render_template("item.html", items=items, pagination=pagination, search=search)

@app.route("/item/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_item(id):
    # Item edit karne ka route.
    item = db.session.get(Item, id) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        stock = request.form.get("stock", "").strip()
        reorder_level = request.form.get("reorder_level", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        # Input validation
        if not name or not stock or not reorder_level:
            flash("Name, Stock, and Reorder Level are required!", "danger")
        elif not stock.isdigit() or not reorder_level.isdigit():
            flash("Stock and Reorder Level must be numbers!", "danger")
        elif purchase_price and (not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0):
            flash("Purchase price must be a non-negative number!", "danger")
        elif sale_price and (not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0):
            flash("Sale price must be a non-negative number!", "danger")
        else:
            # Item update karna
            item.name = name
            item.stock = int(stock)
            item.reorder_level = int(reorder_level)
            item.purchase_price = float(purchase_price) if purchase_price else None
            item.sale_price = float(sale_price) if sale_price else None
            db.session.commit()
            flash("Item updated successfully!", "success")
            return redirect(url_for("item"))
    return render_template("edit_item.html", item=item)

@app.route("/item/delete/<int:id>", methods=["POST"])
@verified_required
def delete_item(id):
    # Item delete karne ka route.
    item = db.session.get(Item, id) or abort(404)
    if item.purchases or item.sales:
        flash("Cannot delete item with associated purchases or sales!", "danger")
    else:
        db.session.delete(item)
        db.session.commit()
        flash("Item deleted successfully!", "success")
    return redirect(url_for("item"))

@app.route("/api/item/<int:id>")
@verified_required
def get_item(id):
    # Item ke purchase aur sale price ko API ke zariye return karta hai.
    item = db.session.get(Item, id) or abort(404)
    return {"purchase_price": item.purchase_price, "sale_price": item.sale_price}

# Purchase Management Routes
@app.route("/purchase", methods=["GET", "POST"])
@verified_required
def purchase():
    # Purchase management ka route jo purchases ko add, list, aur search karne deta hai.
    search = request.args.get("search", "").strip()
    query = Purchase.query
    if search:
        # Supplier ya item ke naam se search karna
        query = (
            query.join(Supplier)
            .join(Item)
            .filter((Supplier.name.ilike(f"%{search}%")) | (Item.name.ilike(f"%{search}%")))
        )
    purchases, pagination = get_paginated_results(query)
    suppliers = Supplier.query.all()
    items = Item.query.all()
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "").strip()
        item_id = request.form.get("item_id", "").strip()
        quantity = request.form.get("quantity", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        date = request.form.get("date", "").strip()
        # Input validation
        if not supplier_id or not item_id or not quantity or not purchase_price or not date:
            flash("All fields are required!", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
            )
        if not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
            )
        if not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0:
            flash("Purchase price must be a non-negative number!", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
            )
        try:
            # Item stock update aur purchase record create karna
            item = db.session.get(Item, item_id) or abort(404)
            item.stock += int(quantity)
            purchase = Purchase(
                supplier_id=supplier_id,
                item_id=item_id,
                quantity=int(quantity),
                purchase_price=float(purchase_price),
                date=datetime.strptime(date, "%Y-%m-%d"),
            )
            db.session.add(purchase)
            db.session.commit()
            flash("Purchase added successfully!", "success")
            return redirect(url_for("purchase"))
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
            return render_template(
                "purchase.html",
                suppliers=suppliers,
                items=items,
                purchases=purchases,
                pagination=pagination,
                search=search,
            )
    return render_template(
        "purchase.html",
        suppliers=suppliers,
        items=items,
        purchases=purchases,
        pagination=pagination,
        search=search,
    )

@app.route("/purchase/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_purchase(id):
    # Purchase edit karne ka route.
    purchase = db.session.get(Purchase, id) or abort(404)
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id", "")
        item_id = request.form.get("item_id", "")
        quantity = request.form.get("quantity", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        date = request.form.get("date", "")
        # Input validation
        if not supplier_id or not item_id or not quantity or not purchase_price or not date:
            flash("All fields are required!", "danger")
        elif not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
        elif not purchase_price.replace(".", "", 1).isdigit() or float(purchase_price) < 0:
            flash("Purchase price must be a non-negative number!", "danger")
        else:
            try:
                # Item stock update aur purchase record update karna
                item = db.session.get(Item, item_id) or abort(404)
                old_quantity = purchase.quantity
                if purchase.item_id == int(item_id):
                    item.stock = item.stock - old_quantity + int(quantity)
                else:
                    old_item = db.session.get(Item, purchase.item_id) or abort(404)
                    old_item.stock -= old_quantity
                    item.stock += int(quantity)
                purchase.supplier_id = supplier_id
                purchase.item_id = item_id
                purchase.quantity = int(quantity)
                purchase.purchase_price = float(purchase_price)
                purchase.date = datetime.strptime(date, "%Y-%m-%d")
                db.session.commit()
                flash("Purchase updated successfully!", "success")
                return redirect(url_for("purchase"))
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    suppliers = Supplier.query.all()
    items = Item.query.all()
    return render_template("edit_purchase.html", purchase=purchase, suppliers=suppliers, items=items)

@app.route("/purchase/delete/<int:id>", methods=["POST"])
@verified_required
def delete_purchase(id):
    # Purchase delete karne ka route jo stock ko bhi update karta hai.
    purchase = db.session.get(Purchase, id) or abort(404)
    item = db.session.get(Item, purchase.item_id) or abort(404)
    item.stock -= purchase.quantity
    db.session.delete(purchase)
    db.session.commit()
    flash("Purchase deleted successfully!", "success")
    return redirect(url_for("purchase"))

# Sale Management Routes
@app.route("/sale", methods=["GET", "POST"])
@verified_required
def sale():
    # Sale management ka route jo sales ko add, list, aur search karne deta hai.
    search = request.args.get("search", "").strip()
    query = Sale.query
    if search:
        # Customer ya item ke naam se search karna
        query = (
            query.join(Customer)
            .join(Item)
            .filter((Customer.name.ilike(f"%{search}%")) | (Item.name.ilike(f"%{search}%")))
        )
    sales, pagination = get_paginated_results(query)
    customers = Customer.query.all()
    items = Item.query.all()
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip()
        item_id = request.form.get("item_id", "").strip()
        quantity = request.form.get("quantity", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        date = request.form.get("date", "").strip()
        # Input validation
        if not customer_id or not item_id or not quantity or not sale_price or not date:
            flash("All fields are required!", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
            )
        if not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
            )
        if not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0:
            flash("Sale price must be a non-negative number!", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
            )
        try:
            # Item stock check aur sale record create karna
            item = db.session.get(Item, item_id) or abort(404)
            if item.stock < int(quantity):
                flash("Insufficient stock!", "danger")
                return render_template(
                    "sale.html",
                    customers=customers,
                    items=items,
                    sales=sales,
                    pagination=pagination,
                    search=search,
                )
            item.stock -= int(quantity)
            sale = Sale(
                customer_id=customer_id,
                item_id=item_id,
                quantity=int(quantity),
                sale_price=float(sale_price),
                date=datetime.strptime(date, "%Y-%m-%d"),
            )
            db.session.add(sale)
            db.session.commit()
            flash("Sale recorded successfully!", "success")
            return redirect(url_for("sale"))
        except ValueError:
            flash("Invalid date format! Use YYYY-MM-DD.", "danger")
            return render_template(
                "sale.html",
                customers=customers,
                items=items,
                sales=sales,
                pagination=pagination,
                search=search,
            )
    return render_template(
        "sale.html",
        customers=customers,
        items=items,
        sales=sales,
        pagination=pagination,
        search=search,
    )

@app.route("/sale/edit/<int:id>", methods=["GET", "POST"])
@verified_required
def edit_sale(id):
    # Sale edit karne ka route jo stock ko bhi update karta hai.
    sale = db.session.get(Sale, id) or abort(404)
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "")
        item_id = request.form.get("item_id", "")
        quantity = request.form.get("quantity", "").strip()
        sale_price = request.form.get("sale_price", "").strip()
        date = request.form.get("date", "")
        # Input validation
        if not customer_id or not item_id or not quantity or not sale_price or not date:
            flash("All fields are required!", "danger")
        elif not quantity.isdigit() or int(quantity) <= 0:
            flash("Quantity must be a positive number!", "danger")
        elif not sale_price.replace(".", "", 1).isdigit() or float(sale_price) < 0:
            flash("Sale price must be a non-negative number!", "danger")
        else:
            try:
                # Item stock update aur sale record update karna
                item = db.session.get(Item, item_id) or abort(404)
                old_quantity = sale.quantity
                stock_valid = True
                if sale.item_id == int(item_id):
                    if item.stock + old_quantity < int(quantity):
                        flash("Insufficient stock for updated quantity!", "danger")
                        stock_valid = False
                    else:
                        item.stock = item.stock + old_quantity - int(quantity)
                else:
                    old_item = db.session.get(Item, sale.item_id) or abort(404)
                    old_item.stock += old_quantity
                    if item.stock < int(quantity):
                        flash("Insufficient stock for new item!", "danger")
                        stock_valid = False
                    else:
                        item.stock -= int(quantity)
                if stock_valid:
                    sale.customer_id = customer_id
                    sale.item_id = item_id
                    sale.quantity = int(quantity)
                    sale.sale_price = float(sale_price)
                    sale.date = datetime.strptime(date, "%Y-%m-%d")
                    db.session.commit()
                    flash("Sale updated successfully!", "success")
                    return redirect(url_for("sale"))
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    customers = Customer.query.all()
    items = Item.query.all()
    return render_template("edit_sale.html", sale=sale, customers=customers, items=items)

@app.route("/sale/delete/<int:id>", methods=["POST"])
@verified_required
def delete_sale(id):
    # Sale delete karne ka route jo stock ko wapas update karta hai.
    sale = db.session.get(Sale, id) or abort(404)
    item = db.session.get(Item, sale.item_id) or abort(404)
    item.stock += sale.quantity
    db.session.delete(sale)
    db.session.commit()
    flash("Sale deleted successfully!", "success")
    return redirect(url_for("sale"))

# Reports Routes
@app.route("/reports", methods=["GET", "POST"])
@verified_required
def reports():
    # Reports generate karne ka route jo purchases, sales, aur profits ke reports dikhata hai.
    purchase_report = sale_report = reorder_report = date_profit_report = item_profit = customer_profit = []
    total_sale_amt = total_profit_amt = 0
    start_date_str = end_date_str = ""
    if request.method == "POST":
        start_date_str = request.form.get("start_date", "")
        end_date_str = request.form.get("end_date", "")
        if not start_date_str or not end_date_str:
            flash("Both dates are required!", "danger")
        else:
            try:
                # Date range ke base pe reports generate karna
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                purchase_report = Purchase.query.filter(Purchase.date.between(start_date, end_date)).all()
                sale_report = Sale.query.filter(Sale.date.between(start_date, end_date)).all()
                reorder_report = Item.query.filter(Item.stock <= Item.reorder_level).all()
                # Date-wise profit report
                date_profit_report = (
                    db.session.query(
                        db.func.date(Sale.date).label("sale_date"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Purchase, Sale.item_id == Purchase.item_id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(db.func.date(Sale.date))
                    .order_by(db.func.date(Sale.date))
                    .all()
                )
                # Item-wise profit report
                item_profit = (
                    db.session.query(
                        Item.name.label("name"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Sale, Sale.item_id == Item.id)
                    .join(Purchase, Purchase.item_id == Item.id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(Item.name)
                    .order_by(Item.name)
                    .all()
                )
                # Customer-wise profit report
                customer_profit = (
                    db.session.query(
                        Customer.name.label("name"),
                        db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                        db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("profit_amt"),
                    )
                    .join(Sale, Sale.customer_id == Customer.id)
                    .join(Purchase, Sale.item_id == Purchase.item_id)
                    .filter(Sale.date.between(start_date, end_date))
                    .group_by(Customer.name)
                    .order_by(Customer.name)
                    .all()
                )
                # Total sale aur profit calculate karna
                totals = (
                    db.session.query(
                        db.func.sum(Sale.quantity * Sale.sale_price).label("total_sale_amt"),
                        db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("total_profit_amt"),
                    )
                    .join(Purchase, Sale.item_id == Purchase.item_id)
                    .filter(Sale.date.between(start_date, end_date))
                    .first()
                )
                total_sale_amt = totals.total_sale_amt or 0.0
                total_profit_amt = totals.total_profit_amt or 0.0
            except ValueError:
                flash("Invalid date format! Use YYYY-MM-DD.", "danger")
    return render_template(
        "reports.html",
        purchase_report=purchase_report,
        sale_report=sale_report,
        reorder_report=reorder_report,
        date_profit_report=date_profit_report,
        item_profit=item_profit,
        customer_profit=customer_profit,
        total_sale_amt=total_sale_amt,
        total_profit_amt=total_profit_amt,
        start_date=start_date_str,
        end_date=end_date_str,
    )

@app.route("/export_purchase_report", methods=["POST"])
@verified_required
def export_purchase_report():
    # Purchase report ko CSV mein export karne ka route.
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        purchases = (
            Purchase.query.join(Supplier)
            .join(Item)
            .filter(Purchase.date.between(start_date, end_date))
            .all()
        )
        with open("static/purchase_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Supplier", "Item", "Quantity", "Purchase Price", "Total", "Date"])
            for purchase in purchases:
                writer.writerow(
                    [
                        purchase.id,
                        purchase.id_supplier.name,
                        purchase.id_item.name,
                        purchase.quantity,
                        round(purchase.purchase_price, 2),
                        round(purchase.quantity * purchase.purchase_price, 2),
                        purchase.date.strftime("%Y-%m-%d"),
                    ]
                )
        return send_from_directory("static", "purchase_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_sale_report", methods=["POST"])
@verified_required
def export_sale_report():
    # Sale report ko CSV mein export karne ka route.
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        sales = (
            Sale.query.join(Customer)
            .join(Item)
            .filter(Sale.date.between(start_date, end_date))
            .all()
        )
        with open("static/sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Customer", "Item", "Quantity", "Sale Price", "Total", "Date"])
            for sale in sales:
                writer.writerow(
                    [
                        sale.id,
                        sale.id_customer.name,
                        sale.id_item.name,
                        sale.quantity,
                        round(sale.sale_price, 2),
                        round(sale.quantity * sale.sale_price, 2),
                        sale.date.strftime("%Y-%m-%d"),
                    ]
                )
        return send_from_directory("static", "sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_date_sale_report", methods=["POST"])
@verified_required
def export_date_sale_report():
    # Date-wise sale aur profit report ko CSV mein export karne ka route.
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        date_sale_report = (
            db.session.query(
                db.func.date(Sale.date).label("sale_date"),
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Purchase, Sale.item_id == Purchase.item_id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(db.func.date(Sale.date))
            .order_by(db.func.date(Sale.date))
            .all()
        )
        with open("static/date_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Date", "Sale Amount", "Profit Amount"])
            for row in date_sale_report:
                writer.writerow([row.sale_date, round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "date_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_item_sale_report", methods=["POST"])
@verified_required
def export_item_sale_report():
    # Item-wise sale aur profit report ko CSV mein export karne ka route.
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        item_sale = (
            db.session.query(
                Item.name.label("name"),
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Sale, Sale.item_id == Item.id)
            .join(Purchase, Purchase.item_id == Item.id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Item.name)
            .order_by(Item.name)
            .all()
        )
        with open("static/item_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Item", "Sale Amount", "Profit Amount"])
            for row in item_sale:
                writer.writerow([row.name, round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "item_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

@app.route("/export_customer_sale_report", methods=["POST"])
@verified_required
def export_customer_sale_report():
    # Customer-wise sale aur profit report ko CSV mein export karne ka route.
    start_date_str = request.form.get("start_date", "")
    end_date_str = request.form.get("end_date", "")
    if not start_date_str or not end_date_str:
        flash("Both dates are required!", "danger")
        return redirect(url_for("reports"))
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        customer_sale = (
            db.session.query(
                Customer.name.label("name"),
                db.func.sum(Sale.quantity * Sale.sale_price).label("sale_amt"),
                db.func.sum((Sale.sale_price - Purchase.purchase_price) * Sale.quantity).label("profit_amt"),
            )
            .join(Sale, Sale.customer_id == Customer.id)
            .join(Purchase, Sale.item_id == Purchase.item_id)
            .filter(Sale.date.between(start_date, end_date))
            .group_by(Customer.name)
            .order_by(Customer.name)
            .all()
        )
        with open("static/customer_sale_report.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Customer", "Sale Amount", "Profit Amount"])
            for row in customer_sale:
                writer.writerow([row.name, round(row.sale_amt, 2), round(row.profit_amt, 2)])
        return send_from_directory("static", "customer_sale_report.csv")
    except ValueError:
        flash("Invalid date format! Use YYYY-MM-DD.", "danger")
        return redirect(url_for("reports"))

# App run karna
if __name__ == "__main__":
    # Flask server ko debug mode mein run karta hai local machine pe port 5172 par.
    app.run(debug=True, host="127.0.0.1", port=5172)