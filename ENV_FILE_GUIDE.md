# .env File Management Guide

**Date:** 2026-08-06  
**Purpose:** Prevent .env file corruption and accidental deletion

---

## 📋 File Structure

```
Project Root/
├─ .env              ← MAIN config file (LOCAL development)
├─ .env.local        ← BACKUP of .env (Git-ignored)
├─ .env.example      ← Template for new setup
├─ .env.render       ← Production config (Render/Cloud)
├─ .env.render-salpurflask
└─ .env.render-tradeflow
```

### **Which file does Flask load?**
```python
# app.py line 35-43
load_dotenv(os.path.join(BASE_DIR, ".env"))
```

Flask loads **only `.env`** for local development.

---

## ⚠️ Common Mistakes & How to Avoid

### **Mistake 1: Opening .env in IDE creates backup**

**What happens:**
```
IDE open .env
  ↓
IDE creates .env.backup or .env11
  ↓
Original .env gets corrupted/deleted
  ↓
Flask can't find .env
  ↓
App breaks ❌
```

**Solution:**
- ✅ Always use **Text Editor** to open .env
- ✅ Use VSCode built-in editor (Ctrl+`)
- ✅ Never copy-paste from Word or other formatters
- ✅ Save with Ctrl+S immediately after edit

---

### **Mistake 2: Renaming .env file**

**DON'T DO THIS:**
```bash
❌ .env → .env11 (wrong!)
❌ .env → .env.backup (wrong!)
❌ .env → .env.local (depends)
```

**DO THIS:**
```bash
✅ .env (main file - keep this name!)
✅ .env.local (backup - Git ignored)
✅ .env.example (template - check in Git)
```

---

### **Mistake 3: Editing wrong file**

**Before editing, confirm:**
```bash
# Check if .env exists
ls -la .env

# Check file size (should be ~600-700 bytes)
wc -c .env

# Check CURRENCY value
grep CURRENCY .env
```

---

## ✅ Safe .env Editing Process

### **Step-by-step:**

1. **Stop Flask** (if running)
   ```bash
   Ctrl+C in terminal
   ```

2. **Backup current .env** (optional but safe)
   ```bash
   cp .env .env.backup
   ```

3. **Open .env in text editor**
   ```bash
   # VSCode
   code .env
   
   # Or right-click → Open With → Text Editor
   ```

4. **Make changes**
   ```
   Edit only the values you need
   Don't add/remove lines unnecessarily
   ```

5. **Save file**
   ```bash
   Ctrl+S
   ```

6. **Verify changes**
   ```bash
   grep CURRENCY .env
   # Should output: CURRENCY=Rs
   ```

7. **Restart Flask**
   ```bash
   python app.py
   ```

8. **Test in browser**
   ```
   http://127.0.0.1:5172/purchase/1
   Should show: Rs 7,087.50
   ```

---

## 🔍 Troubleshooting

### **Problem: .env file deleted**

**Check:**
```bash
# List .env files
ls -la | grep "env"

# Should show:
# .env (MUST exist)
# .env.local (backup)
# .env.example (template)
```

**Fix:**
```bash
# Restore from backup
cp .env.local .env

# OR recreate from example
cp .env.example .env

# Then edit CURRENCY=Rs and other values
```

---

### **Problem: CURRENCY still shows Rs11**

**Diagnose:**
```bash
# 1. Check .env has correct value
grep CURRENCY .env
# Output should be: CURRENCY=Rs

# 2. Check no .env11 or backup files exist
ls -la | grep "env"

# 3. Verify Flask loads it
python -c "from app import app; print(app.config.get('CURRENCY'))"
# Output should be: Rs

# 4. Test money filter
python -c "from app import money_filter; print(money_filter(5000))"
# Output should be: Rs 5,000.00
```

**Common causes:**
- ❌ Wrong .env file being edited
- ❌ Flask process still running old version
- ❌ Browser cache (clear with Ctrl+Shift+R)
- ❌ .env file corrupted (restore from backup)

**Fix:**
```bash
# Kill Flask
pkill -f "python app.py"

# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +

# Verify .env correct
cat .env | grep CURRENCY

# Restart Flask
python app.py
```

---

## 📝 Required .env Variables

| Variable | Value | Notes |
|----------|-------|-------|
| `SECRET_KEY` | Random hex string | Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SECURITY_PASSWORD_SALT` | Random hex string | Same as above |
| `COMPANY_NAME` | Multi Computer Forms | Your business name |
| `APP_NAME` | TradeFlow | Application name |
| `COMPANY_TAGLINE` | Inventory & Accounts Mgmt | Tagline |
| `APP_TIMEZONE` | Asia/Karachi | Your timezone |
| `FISCAL_YEAR_START_MONTH` | 7 | Pakistan: 7, USA: 1, UK: 4 |
| `CURRENCY` | Rs | Your currency symbol |
| `MAIL_USERNAME` | your_email@gmail.com | Gmail for password reset |
| `MAIL_PASSWORD` | app_password | Gmail app password (NOT normal password) |
| `ALLOW_SIGNUP` | false | Enable/disable registration |
| `ENABLE_DEVELOPER_PANEL` | true | Developer tools access |

---

## 🛡️ Best Practices

### **DO:**
- ✅ Keep .env in project root
- ✅ Add .env to .gitignore (already done)
- ✅ Make regular backups (.env.local)
- ✅ Use different .env for each environment (dev/prod)
- ✅ Never commit .env to Git
- ✅ Document all variables in .env.example
- ✅ Test after each .env change

### **DON'T:**
- ❌ Rename .env file
- ❌ Store .env in Git
- ❌ Edit .env in Word/Google Docs
- ❌ Keep multiple .env*.backup files
- ❌ Share .env file publicly
- ❌ Commit sensitive data
- ❌ Edit .env while Flask is running

---

## 🚀 Environment-Specific Files

### **Local Development (.env)**
```
CURRENCY=Rs
ALLOW_SIGNUP=false
DEVELOPER_PANEL=true
DATABASE_URL=(empty - uses SQLite)
```

### **Render/Production (.env.render)**
```
CURRENCY=Rs
ALLOW_SIGNUP=false
DEVELOPER_PANEL=false
DATABASE_URL=postgresql://...
```

---

## ⚙️ Safety Checks in app.py

Flask now checks if .env exists:

```python
# app.py lines 34-43
if not os.path.exists(env_path):
    print("⚠️  WARNING: .env file not found!")
    print("   Create .env from .env.example or .env.local")
    print("   Running with defaults — this may cause issues!")
```

If you see this warning:
1. ✅ Recreate .env from .env.local
2. ✅ Verify CURRENCY=Rs
3. ✅ Restart Flask

---

## 📞 Quick Reference

**Flask won't start?**
```bash
# Fix missing .env
cp .env.local .env

# Or from example
cp .env.example .env
grep CURRENCY .env  # Edit if needed
```

**Currency showing wrong?**
```bash
# Verify .env
grep CURRENCY .env
# Should be: CURRENCY=Rs

# Verify app reads it
python -c "from app import app; print(app.config['CURRENCY'])"

# Clear cache and restart
pkill -f python
python app.py
```

**Lost your .env?**
```bash
# Restore from backup
cp .env.local .env

# Or from Git history
git checkout .env

# Or create new
cp .env.example .env
# Then manually edit values
```

---

**Status:** ✅ PROTECTED  
**Last Updated:** 2026-08-06  
**Protection Level:** High
