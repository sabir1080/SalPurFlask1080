#!/usr/bin/env python3
"""Phase 3 Security Audit - Comprehensive vulnerability scanning."""

import re
from collections import defaultdict

def audit_routes():
    """Analyze all routes for proper authentication/authorization."""
    with open('app.py', 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Find all routes
    routes = re.finditer(r'@app\.route\(["\']([^"\']+)["\'][^)]*\)\s*(?:\n\s*@(\w+)\s*)*\s*def\s+(\w+)', content)

    unprotected = []
    protected = defaultdict(list)

    for route_match in routes:
        path = route_match.group(1)
        # Check if there's a decorator before the route
        decorator_section = content[max(0, route_match.start()-500):route_match.start()]

        # Public routes that should be unprotected
        public_paths = ['/', '/login', '/signup', '/about', '/manual', '/health', '/forgot_password', '/reset_password']

        has_auth = any(dec in decorator_section for dec in ['@login_required', '@verified_required', '@admin_required', '@manager_required'])

        if path not in public_paths and not has_auth:
            unprotected.append((path, route_match.group(3)))

        if has_auth:
            for dec in ['login_required', 'verified_required', 'admin_required', 'manager_required']:
                if dec in decorator_section:
                    protected[dec].append(path)

    return unprotected, protected

def audit_file_uploads():
    """Check for file upload security issues."""
    with open('app.py', 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    issues = []

    # Check for file.save() without validation
    if 'file.save(' in content:
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'file.save(' in line:
                # Check if validation happens before
                context = '\n'.join(lines[max(0, i-10):i])
                if 'ALLOWED_EXTENSIONS' not in context and 'MIME_TYPES' not in context:
                    issues.append(('Potential unsafe file upload', i+1))

    return issues

def audit_session_security():
    """Check session and cookie security configuration."""
    with open('app.py', 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    findings = {
        'SESSION_COOKIE_SECURE': 'SESSION_COOKIE_SECURE' in content,
        'SESSION_COOKIE_HTTPONLY': 'SESSION_COOKIE_HTTPONLY' in content,
        'SESSION_COOKIE_SAMESITE': 'SESSION_COOKIE_SAMESITE' in content,
        'PERMANENT_SESSION_LIFETIME': 'PERMANENT_SESSION_LIFETIME' in content,
    }

    return findings

def audit_authentication():
    """Check password hashing and authentication security."""
    with open('app.py', 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    findings = {
        'uses_passlib': 'passlib' in content and 'CryptContext' in content,
        'hashes_passwords': 'hash_password' in content or 'pwd_context' in content,
        'verifies_passwords': 'verify' in content,
        'rate_limiting': 'check_rate_limit' in content,
    }

    return findings

if __name__ == '__main__':
    print("\n" + "="*70)
    print("PHASE 3: SECURITY AUDIT")
    print("="*70)

    print("\n[1] ROUTE AUTHENTICATION AUDIT")
    print("-" * 70)
    unprotected, protected = audit_routes()

    if unprotected:
        print(f"WARNING: {len(unprotected)} routes may lack proper protection:")
        for path, func in unprotected[:10]:
            print(f"  - {path} ({func})")
    else:
        print("OK: All data routes appear properly protected")

    print(f"\nProtected routes summary:")
    for dec, routes in protected.items():
        print(f"  @{dec}: {len(routes)} routes")

    print("\n[2] FILE UPLOAD AUDIT")
    print("-" * 70)
    upload_issues = audit_file_uploads()
    if upload_issues:
        print(f"Found {len(upload_issues)} potential issues:")
        for issue, line in upload_issues[:5]:
            print(f"  - Line {line}: {issue}")
    else:
        print("OK: File uploads appear to be protected")

    print("\n[3] SESSION & COOKIE SECURITY")
    print("-" * 70)
    session_config = audit_session_security()
    for setting, enabled in session_config.items():
        status = "OK" if enabled else "MISSING"
        print(f"  {setting}: {status}")

    print("\n[4] AUTHENTICATION SECURITY")
    print("-" * 70)
    auth_config = audit_authentication()
    for setting, enabled in auth_config.items():
        status = "OK" if enabled else "MISSING"
        print(f"  {setting}: {status}")

    print("\n" + "="*70)
    print("Audit complete. Review findings above.")
    print("="*70 + "\n")
