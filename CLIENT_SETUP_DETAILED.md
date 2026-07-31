# SalPurFlask/TradeFlow - Detailed Setup Guide with Examples

Complete step-by-step guide for client setup with real examples.

---

## PHASE 1: INSTALLATION & CONFIGURATION

### Step 1.1: Download & Prepare

```bash
# Download project from GitHub
git clone https://github.com/sabir1080/SalPurFlask1080.git
cd SalPurFlask1080

# Check Python version (must be 3.8+)
python --version
# Output: Python 3.10.5 (or higher)
```

### Step 1.2: Create .env File

**Option A: Windows (Command Prompt)**
```bash
# Copy example file
copy .env.example .env

# Open in Notepad
notepad .env
```

**Option B: Mac/Linux**
```bash
# Copy example file
cp .env.example .env

# Open in editor
nano .env
```

### Step 1.3: Configure .env with Real Values

**BEFORE (with placeholders):**
```
SECRET_KEY=your_secret_key
SECURITY_PASSWORD_SALT=your_salt
MAIL_USERNAME=zeshanlook@gmail.com
MAIL_PASSWORD=wfcg hiem mzza psla
```

**AFTER (with real values for Example Company "ABC Traders"):**

```
# Secret Keys - MUST BE RANDOM!
# Generate using: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0
SECURITY_PASSWORD_SALT=x9y8z7w6v5u4t3s2r1q0p9o8n7m6l5k4j3i2h1g0f9e8d7c6b5a4z3y2x1

# Email Configuration (Gmail App Password - NOT regular password!)
# Get from: https://myaccount.google.com/apppasswords
MAIL_USERNAME=abc.traders.pk@gmail.com
MAIL_PASSWORD=hkjd kmlp qprs tuvw

# Company Information
COMPANY_NAME=ABC Traders
APP_NAME=TradeFlow
COMPANY_TAGLINE=Quality Products at Best Prices
DESIGNED_DEVELOPED=Sabir Shah

# Timezone
APP_TIMEZONE=Asia/Karachi

# Currency
CURRENCY=Rs

# Fiscal Year Start Month
FISCAL_YEAR_START_MONTH=7

# Signup Control
ALLOW_SIGNUP=false

# AI Features (Optional)
# ANTHROPIC_API_KEY=sk-ant-api03-xxxxx (leave blank if not using)

# Database (LEAVE EMPTY for local SQLite)
# DATABASE_URL= 
```

**⚠️ Important Notes:**
- SECRET_KEY: Generate random 64-character string
- MAIL_PASSWORD: Use Gmail App Password, NOT your regular password
- ALLOW_SIGNUP: Keep FALSE for security
- DATABASE_URL: Leave empty for local testing

---

## PHASE 2: PYTHON & DEPENDENCIES

### Step 2.1: Create Virtual Environment

**Windows:**
```bash
# Create virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate

# You should see (venv) in your terminal prompt
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
# You should see (venv) in your terminal
```

### Step 2.2: Install Dependencies

```bash
# Ensure pip is latest
python -m pip install --upgrade pip

# Install all required packages
pip install -r requirements.txt

# This will install: Flask, SQLAlchemy, Flask-Login, Passlib, etc.
# Takes 2-3 minutes...
```

**Expected Output:**
```
Collecting flask==2.3.2
...
Successfully installed flask-2.3.2 sqlalchemy-2.0.19 ...
```

### Step 2.3: Verify Installation

```bash
# Check if Flask installed correctly
python -c "import flask; print(flask.__version__)"
# Output: 2.3.2 (or similar)

# Check if SQLAlchemy installed
python -c "import sqlalchemy; print(sqlalchemy.__version__)"
# Output: 2.0.19 (or similar)
```

---

## PHASE 3: CREATE ADMIN USER

### Step 3.1: Start Admin User Creation

```bash
# Make sure virtual environment is activated
# (venv) should be in your terminal prompt

flask create-user
```

### Step 3.2: Follow Prompts

**Example Input:**
```
Name: Sabir Ahmed
Email: sabir.ahmed@abctraders.com
Password (min 6 chars): Abc123456!
Confirm Password: Abc123456!

✓ User 'sabir.ahmed@abctraders.com' created successfully!
```

**Output Confirmation:**
```
User Details:
- Email: sabir.ahmed@abctraders.com
- Name: Sabir Ahmed
- Role: admin
- Status: verified
```

### Step 3.3: Save These Credentials Securely

```
ADMIN CREDENTIALS (Save in Secure Location)
==========================================
Email: sabir.ahmed@abctraders.com
Password: Abc123456!
Role: admin

⚠️ KEEP SAFE - Do not share this password
```

---

## PHASE 4: START THE APP

### Step 4.1: Launch Development Server

**Windows/Mac/Linux:**
```bash
# Make sure (venv) is activated in terminal
# Then run:

python app.py
```

**Expected Output:**
```
[2026-07-31 14:30:45] WARNING in app: FISCAL_YEAR_START_MONTH is 7...
 * Running on http://127.0.0.1:5172
 * Debug mode: on
 * Press CTRL+C to quit
```

### Step 4.2: Open in Browser

```
Open Browser → Go to: http://127.0.0.1:5172
```

**What You Should See:**
- Login Page with TradeFlow logo
- Email field
- Password field
- Sign In button

### Step 4.3: Login Test

```
Email: sabir.ahmed@abctraders.com
Password: Abc123456!

Click: Sign In
```

**Expected Result:**
- Dashboard page loads
- Shows empty data (first time)
- No errors in terminal

---

## PHASE 5: ADD MASTER DATA

### Step 5.1: Open Database Manager (New Terminal Window)

**Keep first terminal with app running!**

**Open NEW terminal/command prompt:**
```bash
# Navigate to project folder
cd path\to\SalPurFlask1080

# Activate virtual environment
venv\Scripts\activate

# Run database manager
python database_manager.py
```

### Step 5.2: Select Database

```
======================================================================
DATABASE SELECTION
======================================================================
1. Local Database (SQLite)
2. Render Database (PostgreSQL)

Select database (1-2): 1

[OK] Using Local Database
[OK] Database connection successful
```

### Step 5.3: Add Suppliers - STEP BY STEP

```
Main Menu → Select: 2 (Manage Suppliers)
```

**First Supplier:**
```
----------------------------------------------------------------------
SUPPLIER MANAGEMENT
----------------------------------------------------------------------
1. Add New Supplier
2. Edit Supplier
3. Delete Supplier
4. View All Suppliers
0. Back

Select (0-4): 1

Supplier Name: Ahmed Textiles Pvt Ltd
Contact Number: 03001234567
Address: Karachi, Sindh
Opening Balance (0 if none): 50000

[OK] Supplier 'Ahmed Textiles Pvt Ltd' created
```

**Second Supplier:**
```
Select (0-4): 1

Supplier Name: Global Electronics
Contact Number: 03215678901
Address: Lahore, Punjab
Opening Balance (0 if none): 100000

[OK] Supplier 'Global Electronics' created
```

**View All Suppliers:**
```
Select (0-4): 4

Total Suppliers: 2

  Ahmed Textiles Pvt Ltd        | Contact: 03001234567  | Balance:       50,000.00
  Global Electronics            | Contact: 03215678901  | Balance:      100,000.00
```

### Step 5.4: Add Customers

```
Main Menu → Select: 3 (Manage Customers)
```

**First Customer:**
```
Select (0-3): 1

Customer Name: Karachi Retail Store
Contact Number: 02132456789
Address: Karachi
Opening Balance (0 if none): 0

[OK] Customer 'Karachi Retail Store' created
```

**Second Customer:**
```
Select (0-3): 1

Customer Name: Lahore Wholesale Mart
Contact Number: 04245678123
Address: Lahore
Opening Balance (0 if none): 25000

[OK] Customer 'Lahore Wholesale Mart' created
```

### Step 5.5: Add Items/Products

```
Main Menu → Select: 4 (Manage Items)
```

**Product 1 - Shirt:**
```
Select (0-4): 1

Item Name: Cotton Shirt (Large)
Categories:
  1. Electronics
  2. Textiles
  3. Food & Beverage
  
Select category (number): 2

Unit (pcs/kg/ltr/box): pcs
Barcode (optional): SKU-SHIRT-LG-001
Purchase Price: 800
Sale Price: 1200
Tax Percent (0 if none): 17
Is Taxable? (yes/no): yes

[OK] Item 'Cotton Shirt (Large)' created
```

**Product 2 - Jeans:**
```
Select (0-4): 1

Item Name: Denim Jeans (32)
Select category (number): 2
Unit (pcs/kg/ltr/box): pcs
Barcode (optional): SKU-JEANS-32-001
Purchase Price: 1500
Sale Price: 2500
Tax Percent (0 if none): 17
Is Taxable? (yes/no): yes

[OK] Item 'Denim Jeans (32)' created
```

**Product 3 - Electronics Item:**
```
Select (0-4): 1

Item Name: USB Mobile Charger
Select category (number): 1
Unit (pcs/kg/ltr/box): pcs
Barcode (optional): SKU-CHARGER-USB-001
Purchase Price: 500
Sale Price: 800
Tax Percent (0 if none): 17
Is Taxable? (yes/no): yes

[OK] Item 'USB Mobile Charger' created
```

**View All Items:**
```
Select (0-4): 4

Total Items: 3

  Cotton Shirt (Large)          | Purchase:       800.00 | Sale:     1,200.00
  Denim Jeans (32)              | Purchase:     1,500.00 | Sale:     2,500.00
  USB Mobile Charger            | Purchase:       500.00 | Sale:       800.00
```

### Step 5.6: Add Financial Accounts

```
Main Menu → Select: 7 (Manage Financial Accounts)
```

**Cash Account:**
```
Select (0-3): 1

Account Name: Main Cash Register
Account Type: 1. asset | 2. liability | 3. equity
Select (1-3): 1

Opening Balance: 100000

[OK] Account 'Main Cash Register' created
```

**Bank Account:**
```
Select (0-3): 1

Account Name: Habib Bank Limited (Current Account)
Select (1-3): 1
Opening Balance: 500000

[OK] Account 'Habib Bank Limited (Current Account)' created
```

### Step 5.7: View Database Summary

```
Main Menu → Select: 9 (View All Data Summary)
```

**Final Summary:**
```
======================================================================
DATABASE SUMMARY
======================================================================
Users:                1
Suppliers:            2
Customers:            2
Items:                3
Categories:           3
Business Categories:  21
Financial Accounts:   2
Tax Codes:            2
======================================================================
```

### Step 5.8: Exit Database Manager

```
Main Menu → Select: 0 (Exit)

Goodbye!
```

---

## PHASE 6: TEST THE APP

### Step 6.1: Go Back to Browser

```
URL: http://127.0.0.1:5172
Login: sabir.ahmed@abctraders.com
Password: Abc123456!
```

### Step 6.2: Dashboard Should Show Data

```
Dashboard will display:
✓ 2 Suppliers
✓ 2 Customers  
✓ 3 Products
✓ Financial balances
✓ Empty charts (first use)
```

### Step 6.3: Create Test Sale

```
Navigation → POS (or Sales)

1. Select Customer: "Karachi Retail Store"
2. Add Items:
   - Cotton Shirt (Large): 2 pcs @ 1,200
   - USB Charger: 5 pcs @ 800

3. Check totals:
   Qty: 7
   Gross: 6,400
   Tax (17%): 1,088
   Total: 7,488

4. Payment:
   Method: Cash
   Amount Paid: 7,500

5. Click: Complete Sale

Expected: Receipt generates, data saves
```

---

## PHASE 7: DAILY OPERATIONS CHECKLIST

### Every Day - Start of Day

```bash
# Terminal 1: Activate venv and start app
venv\Scripts\activate
python app.py

# Browser: Open http://127.0.0.1:5172
# Login with admin credentials
```

### Every Day - Managing Data

**Add new supplier:**
```bash
# Terminal 2: (new terminal)
venv\Scripts\activate
python database_manager.py
# Menu 2 → Option 1 → Enter supplier details
```

**Add new customer:**
```
Menu 3 → Option 1 → Enter customer details
```

**Add new products:**
```
Menu 4 → Option 1 → Enter product details
```

### Every Day - End of Day

```bash
# Leave app running
# Ctrl+C to stop (only if needed)

# Regular backups (weekly)
cp instance/database.db instance/database.db.backup-2026-07-31
```

---

## TROUBLESHOOTING WITH EXAMPLES

### Problem: "Port 5172 already in use"

```bash
# Find what's using port 5172
netstat -ano | findstr :5172

# Output might show: TCP 127.0.0.1:5172 LISTENING 1234

# Either:
# Option 1: Close the process (PID 1234)
taskkill /PID 1234 /F

# Option 2: Use different port
flask run --port 5173
```

### Problem: "Secret Key or Password errors"

```
Check .env file:
- SECRET_KEY should be 64 random characters
- SECURITY_PASSWORD_SALT should be 64 random characters
- MAIL_PASSWORD should be Google App Password (not regular password)
```

### Problem: "Database locked"

```bash
# Stop app (Ctrl+C in terminal)
# Then delete lock files:
rm instance/database.db-wal
rm instance/database.db-shm

# Restart app
python app.py
```

### Problem: "Email not working"

```
Check .env:
1. MAIL_USERNAME must be Gmail address
2. MAIL_PASSWORD must be App Password (from myaccount.google.com/apppasswords)
3. NOT regular Gmail password

Example:
MAIL_USERNAME=abc.traders.pk@gmail.com
MAIL_PASSWORD=hkjd kmlp qprs tuvw (16 chars with spaces)
```

---

## QUICK REFERENCE

### Command Cheatsheet

```bash
# Activate virtual environment
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux

# Start app
python app.py

# Open database manager
python database_manager.py

# Backup database
cp instance/database.db instance/database.db.backup

# Stop app
Ctrl+C

# Deactivate virtual environment
deactivate
```

### Default Ports
```
App: http://127.0.0.1:5172
Backup port: http://127.0.0.1:5173
```

### File Locations
```
Database: instance/database.db
Config: .env
Virtual Env: venv/
Backups: instance/database.db.backup-*
```

---

## SUPPORT CONTACTS

**For technical issues:**
- Check error messages in terminal (don't close terminal)
- Screenshot error and send to support
- Include terminal output (last 10 lines)

**Common Issues:**
1. Port in use → Use different port
2. Database locked → Delete .db-wal and .db-shm files
3. Email failed → Check Gmail App Password in .env
4. Dashboard empty → Add data via database_manager.py

---

## SECURITY REMINDERS

⚠️ **CRITICAL:**
1. Never share .env file
2. Never commit .env to git
3. Keep admin password secure
4. Regular backups (at least weekly)
5. Keep ALLOW_SIGNUP=false
6. Use Gmail App Password (not regular password)

✅ **BACKUP SCHEDULE:**
- Weekly: Automatic via backup script
- After major operations: Manual backup
- Keep last 4 weeks of backups

---

**Setup Time: 30-45 minutes**
**Support: Available 24/7**

