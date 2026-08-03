# Developer Panel Security Documentation

## Overview

The Developer Panel is a secure administration interface available in local development and staging environments. It provides access to:
- Database management and schema synchronization
- Application diagnostics and health checks
- Environment configuration inspection
- Application logs and statistics

## Security Architecture

### 🔐 Authentication

**Password Hashing:** Argon2id (industry standard, resistant to GPU cracking)
- Password hash stored in environment variables (`.env` files)
- Never stored in code or version control
- Same hash used across all environments for consistency

**Login Flow:**
1. User submits password via `/developer/login`
2. Password verified against Argon2id hash from `DEVELOPER_PASSWORD_HASH` env variable
3. Session created with timestamp for timeout tracking
4. Verified credentials stored in Flask session (encrypted cookie)

### 📍 Access Control by Environment

#### Local Development (`.env`)
```
ENABLE_DEVELOPER_PANEL=true
DEVELOPER_LOCALHOST_ONLY=true         # Restricted to 127.0.0.1 and localhost
DEVELOPER_SESSION_TIMEOUT=30          # 30 minute auto-logout
DEVELOPER_READ_ONLY_MODE=false        # Full write access
```
**Use Case:** Developer machine only. Full access to modify schemas and data.

#### SalPurFlask Staging (`.env.render-salpurflask`)
```
ENABLE_DEVELOPER_PANEL=true
DEVELOPER_LOCALHOST_ONLY=false        # Accessible from anywhere (behind Render proxy)
DEVELOPER_SESSION_TIMEOUT=30
DEVELOPER_READ_ONLY_MODE=true         # Read-only: no schema/data modifications
```
**Use Case:** Staging environment for team access. Protected from accidental changes.

#### TradeFlow Production (`.env.render-tradeflow`)
```
ENABLE_DEVELOPER_PANEL=false          # Completely disabled
```
**Use Case:** Production data. No developer tools available.

### 🔒 Session Security

**Auto-Logout (Timeout):**
- Default: 30 minutes of inactivity
- Configurable via `DEVELOPER_SESSION_TIMEOUT`
- Session tracked with login timestamp
- Enforced on every request via `developer_login_required` decorator

**Session Storage:**
- Flask secure cookie (encrypted with `SECRET_KEY`)
- Session data: `developer_authenticated` (bool), `developer_login_time` (datetime)
- CSRF protection enabled via Flask-WTF (all forms use tokens)

### 🚫 Rate Limiting

**Failed Login Protection:**
- Max 5 failed attempts per IP address
- 15-minute lockout after threshold
- Automatic cleanup of old lockout records
- Logged for security audit

**Example Flow:**
```
Attempt 1-4: "Incorrect password" message
Attempt 5:   Lockout triggered, user locked out for 15 minutes
After 15min: Automatic unlock, user can retry
```

### 🌐 Network Restrictions

**Local Instance (port 5172):**
- `DEVELOPER_LOCALHOST_ONLY=true` enforces `request.remote_addr in ('127.0.0.1', 'localhost')`
- Non-localhost requests return 403 Forbidden
- Prevents accidental network exposure

**Render Instances:**
- Requests come through Render proxy, so `request.remote_addr` is internal
- `DEVELOPER_LOCALHOST_ONLY=false` allows all requests through proxy
- Still protected by password + HTTPS (Render enforces SSL)

### 📝 Audit Logging

All developer access is logged to `logs/app.log`:
```
INFO:salpurflask.routes.developer: Developer access granted to 127.0.0.1
WARNING:salpurflask.routes.developer: Developer login failed from 127.0.0.1 - 1 attempts
WARNING:salpurflask.routes.developer: Developer login rate-limited from 192.168.1.100
```

Access logs include:
- Timestamp
- Success/failure status
- Source IP address
- Failed attempt counts
- Rate limit triggers

## Configuration Guide

### Setting a New Password

1. Generate new Argon2id hash (run on any machine with Python):
```bash
python -c "from passlib.context import CryptContext; ctx = CryptContext(schemes=['argon2']); print(ctx.hash('YOUR_NEW_PASSWORD'))"
```

2. Update `.env` file:
```
DEVELOPER_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$...long_hash_here...
```

3. Restart Flask app for changes to take effect

### Changing Session Timeout

Edit `.env` (or respective Render `.env.render-*` file):
```
DEVELOPER_SESSION_TIMEOUT=60   # Change from 30 to 60 minutes
```

### Disabling Developer Panel on a Server

Set in the `.env` file:
```
ENABLE_DEVELOPER_PANEL=false
```

All requests to `/developer/*` will return 403 Forbidden.

## Security Best Practices

### ✅ DO:
- Keep `DEVELOPER_PASSWORD_HASH` secret (don't commit to public repos)
- Use unique password for each environment (generate separate hashes)
- Regularly review audit logs in `logs/app.log`
- Set `DEVELOPER_LOCALHOST_ONLY=true` for sensitive local development
- Disable (`ENABLE_DEVELOPER_PANEL=false`) on production servers
- Use strong passwords (8+ chars, mix of upper/lower/numbers/symbols)

### ❌ DON'T:
- Store passwords in code (they go in `.env` only)
- Disable rate limiting by editing code
- Share password via unencrypted channels (email, Slack, etc.)
- Enable developer panel on production without explicit need
- Ignore 403 errors (they indicate security blocks)
- Access developer panel over non-HTTPS connections (use Render/HTTPS)

## Troubleshooting

### Problem: "Access Denied" (403 Forbidden)

**Check:**
1. Are you on localhost? (For local dev, `DEVELOPER_LOCALHOST_ONLY=true` required)
2. Is developer panel enabled? (Check `ENABLE_DEVELOPER_PANEL=true`)
3. What's your IP? (Check Flask startup logs for `request.remote_addr`)

**Fix:**
```
# For local dev, ensure:
DEVELOPER_LOCALHOST_ONLY=true
ENABLE_DEVELOPER_PANEL=true

# Then access via http://127.0.0.1:5172/developer/login
```

### Problem: "Too many failed attempts"

**Solution:**
- Wait 15 minutes for automatic unlock
- Or clear `failed_attempts` dict in developer.py (restart app)
- Password is case-sensitive, check Caps Lock

### Problem: Session expires too quickly

**Solution:**
1. Increase timeout in `.env`:
```
DEVELOPER_SESSION_TIMEOUT=60   # Increase from 30 to 60 minutes
```

2. Restart Flask app
3. Log back in

### Problem: Password doesn't work

**Check:**
1. Is password hash set? `echo $DEVELOPER_PASSWORD_HASH` (should show hash, not empty)
2. Did you restart app after changing `.env`? (Load_dotenv only runs on startup)
3. Is the hash corrupted? (Copy-paste errors, truncated text)

**Fix:**
```bash
# Generate new hash with correct password
python -c "from passlib.context import CryptContext; ctx = CryptContext(schemes=['argon2']); print(ctx.hash('_@sabir@_'))"

# Update .env with new hash
# Restart Flask app
```

## Deployment Checklist

- [ ] Local `.env`: `ENABLE_DEVELOPER_PANEL=true`, `DEVELOPER_LOCALHOST_ONLY=true`
- [ ] SalPurFlask `.env.render-salpurflask`: `ENABLE_DEVELOPER_PANEL=true`, `DEVELOPER_READ_ONLY_MODE=true`
- [ ] TradeFlow `.env.render-tradeflow`: `ENABLE_DEVELOPER_PANEL=false`
- [ ] All `.env` files have `DEVELOPER_PASSWORD_HASH` set
- [ ] `.env` files are in `.gitignore` (don't commit passwords)
- [ ] Session timeout is reasonable (30-60 minutes)
- [ ] Rate limiting is enabled (MAX_ATTEMPTS=5, LOCKOUT_DURATION=15min)
- [ ] Audit logs configured and monitored
- [ ] Team trained on password security

## Files & Components

### Code
- `salpurflask/routes/developer.py` - Main developer routes with security
- `templates/developer/login.html` - Login page with Argon2 verification
- `templates/developer/dashboard.html` - Main admin panel
- `templates/errors/403.html` - Access denied error page

### Configuration
- `.env` - Local development (localhost restricted, full access)
- `.env.render-salpurflask` - Staging (accessible, read-only)
- `.env.render-tradeflow` - Production (disabled)

### Logging
- `logs/app.log` - All developer access attempts (login/logout/failures/rate-limits)

## Support & Questions

For security concerns or questions, review:
1. CLAUDE.md - Architecture and guidelines
2. app.py - Config loading and session setup
3. salpurflask/routes/developer.py - Security implementation
4. logs/app.log - Access audit trail
