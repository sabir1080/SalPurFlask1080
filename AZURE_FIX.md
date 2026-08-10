# Azure Deployment Fix - Step by Step

## Problem
Azure default page dikha raha hai - app deploy nahi hua

## Solution

### Option 1: GitHub Continuous Deployment (BEST)

**Azure Portal mein:**

1. **Deployment Center kholo**
   - Left sidebar: Deployment → Deployment Center
   
2. **Source select karo**
   - Select: GitHub
   
3. **Authorize**
   - Click "Authorize"
   - Sign in with GitHub
   - Allow Azure access
   
4. **Repository select karo**
   - Organization: sabir1080
   - Repository: SalPurFlask1080
   - Branch: main
   
5. **App Stack**
   - Runtime stack: Python
   - Python version: 3.12
   
6. **Save**
   - Click "Save" button
   - Azure automatically deploys latest code
   - Wait 2-3 minutes

7. **Verify**
   - Go to: https://tradeflow-fvbwbnbhe3axc7h8.azurewebsites.net/
   - Should see TradeFlow dashboard

---

### Option 2: Manual Git Push

**Terminal mein:**

```bash
cd g:\Sbr\App\A6Flask\SalPurFlask1

# Add Azure remote
git remote add azure https://<username>@tradeflow-fvbwbnbhe3axc7h8.scm.azurewebsites.net:443/tradeflow-fvbwbnbhe3axc7h8.git

# Push to Azure
git push azure main

# Wait 2-3 minutes for deployment
```

---

## After Deployment

### Test URL
```
https://tradeflow-fvbwbnbhe3axc7h8.azurewebsites.net/purchase_orders
```

### Expected Result
- Discount column visible
- Tax column visible
- Can add discount/tax values
- Fields save correctly

### If Still Not Working

**Check Azure Logs:**
1. App Service → Log Stream
2. Check for Python errors
3. Look for migration errors

**Common Issues:**
- Database connection error
- Python version mismatch
- Missing dependencies

---

## What to Expect

**First deployment takes 2-3 minutes:**
1. GitHub code pulled
2. Dependencies installed (pip install)
3. Database migrations run
4. App starts

**Subsequent deployments are faster** (30 seconds - 1 minute)

---

## Status Check

**In Azure Portal:**
- Go to Deployment Center
- Check "Active deployment"
- Should show: "Succeeded" with timestamp

**If showing "Failed":**
- Click on it to see error details
- Common: Python version, requirements.txt

---

Ready to connect GitHub? 👍
