# Client Setup Guide - SalPurFlask/TradeFlow

Complete setup instructions for client before they start using the app.

---

## Phase 1: Initial Setup (First Time Only)

### Step 1: Environment Configuration
```bash
# Create .env file from template
cp .env.example .env
```

**Edit `.env` and set these values:**

```
# Secret Keys (MUST be random - never use defaults!)
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
SECURITY_PASSWORD_SALT=<generate: python -c "import secrets; print(secrets.token_hex(32))">

# Email Configuration (Gmail App Password)
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password

# Company Information
COMPANY_NAME=Your Business Name
APP_NAME=TradeFlow (or custom name)
COMPANY_TAGLINE=Your tagline
DESIGNED_DEVELOPED=Your Name

# Database (leave empty for local SQLite)
# DATABASE_URL= (leave blank for local testing)

# Optional
ANTHROPIC_API_KEY=your-api-key (if using AI features)
ALLOW_SIGNUP=false (security: disable unless needed)
```

**Generate Random Keys:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Step 2: Install Dependencies
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install packages
pip install -r requirements.txt
```

### Step 3: Create Admin User
```bash
flask create-user
# Follow prompts to create first admin user
# Email: admin@yourcompany.com
# Password: (strong password - at least 6 chars)
```

### Step 4: Verify Installation
```bash
python app.py
# Should print: Running on http://127.0.0.1:5172
```

Visit: http://127.0.0.1:5172
- Login with admin credentials
- If dashboard loads → Installation successful ✓

---

## Phase 2: Initial Data Setup (First Day of Operations)

### Option A: Manual Data Entry via Database Manager
```bash
python database_manager.py
```

**Add these master data:**

1. **Suppliers** (Menu option 2)
   - Add all your regular suppliers
   - Include: Name, Contact, Address, Opening Balance

2. **Customers** (Menu option 3)
   - Add regular customers
   - Include: Name, Contact, Address, Opening Balance

3. **Items/Products** (Menu option 4)
   - Add all products you sell
   - Include: Name, Category, Unit, Purchase Price, Sale Price, Tax Status

4. **Categories** (Menu option 5)
   - Add item categories if not already present

5. **Business Categories** (Menu option 6)
   - Already pre-loaded with 21 categories
   - Can add more if needed

6. **Financial Accounts** (Menu option 7)
   - Add: Cash Account (opening balance)
   - Add: Bank Account (opening balance)

7. **Users** (Menu option 1)
   - Add staff/manager users with appropriate roles

### Option B: Bulk Import (If you have CSV data)
```
Contact support for CSV import script
```

---

## Phase 3: Pre-Launch Checklist

Before client starts live operations, verify:

- [ ] Admin user created and tested
- [ ] All suppliers added
- [ ] All customers added
- [ ] All products added with correct pricing
- [ ] Financial accounts created with opening balances
- [ ] Test sale created and verified
- [ ] Test purchase created and verified
- [ ] Receipt prints correctly
- [ ] Dashboard loads without errors
- [ ] Email configuration tested (if using verification)

---

## Phase 4: Daily Operations

### Start the App
```bash
# Activate virtual environment first
venv\Scripts\activate

# Start development server
python app.py

# OR use Flask CLI
flask run --port 5172 --debug
```

### Access the App
```
http://127.0.0.1:5172
```

### Manage Data During Operations
```bash
# If needed, run database manager in separate terminal
python database_manager.py

# Select: 1 (Local Database)
# Add/Edit/Delete as needed
```

---

## Important Security Notes

### 🔐 Critical
1. **Never share SECRET_KEY or SECURITY_PASSWORD_SALT**
2. **Use strong passwords** (minimum 6 characters, preferably 12+)
3. **Disable ALLOW_SIGNUP** in production (set to false)
4. **Backup database regularly:**
   ```bash
   cp instance/database.db instance/database.db.backup
   ```
5. **Keep .env file secure** - never commit to git

### Database Backup
```bash
# Weekly backup
cp instance/database.db instance/database.db.$(date +%Y%m%d).backup
```

---

## Troubleshooting

### App Won't Start
```bash
# Check if port 5172 is in use
netstat -ano | findstr :5172

# Try different port
flask run --port 5173
```

### Database Locked Error
```bash
# Stop all running instances
# Delete lock file if exists
rm instance/database.db-wal
rm instance/database.db-shm
```

### Forgotten Admin Password
```bash
# Delete database and restart
rm instance/database.db

# Restart app and create new admin
python app.py
```

### Email Not Working
- Verify MAIL_USERNAME and MAIL_PASSWORD in .env
- Use Gmail App Password (not regular password)
- Generate at: https://myaccount.google.com/apppasswords

---

## Support Contact

For issues during setup:
- Check error messages in terminal
- Run database_manager.py for data issues
- Contact support with error details and logs

---

## Next Steps After Setup

1. Train staff on POS system
2. Test with sample sales
3. Review reports and dashboards
4. Set up regular backups
5. Monitor error logs

---

**Setup Time: ~30-45 minutes**
**Go-Live Ready: After Phase 2 & 3 complete**

