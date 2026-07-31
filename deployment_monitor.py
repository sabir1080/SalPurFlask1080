#!/usr/bin/env python3
"""
Render Deployment Monitor
Checks deployment status every minute and updates comparison page
"""

import subprocess
import json
import time
from datetime import datetime
from pathlib import Path

def check_render_deployment():
    """Check if Render deployment is complete"""
    # Check recent commits
    result = subprocess.run(
        ['git', 'log', '--oneline', '-5'],
        capture_output=True,
        text=True
    )

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking deployment status...")
    print("Recent commits:")
    print(result.stdout)

    # Check if render URLs are accessible
    try:
        import urllib.request

        urls = {
            'salpurflask': 'https://salpurflask.onrender.com/pos',
            'tradeflow-demo': 'https://tradeflow-demo.onrender.com/pos'
        }

        for name, url in urls.items():
            try:
                response = urllib.request.urlopen(url, timeout=5)
                if response.status == 200:
                    print(f"✓ {name}: ACCESSIBLE (Status {response.status})")
                else:
                    print(f"? {name}: Status {response.status}")
            except Exception as e:
                print(f"✗ {name}: Not accessible yet - {type(e).__name__}")
    except Exception as e:
        print(f"Error checking URLs: {e}")

def update_comparison_page():
    """Update the comparison HTML with current status"""
    html_file = Path('database_comparison_3way.html')

    if html_file.exists():
        print("\n✓ Comparison page exists and ready for update")
        print(f"  Location: {html_file.absolute()}")
    else:
        print("\n✗ Comparison page not found")

# Main monitoring loop
print("=" * 60)
print("RENDER DEPLOYMENT MONITOR")
print("=" * 60)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("\nMonitoring deployment status...")
print("Checking every 30 seconds for 10 minutes")
print("=" * 60)

start_time = time.time()
timeout = 600  # 10 minutes

while time.time() - start_time < timeout:
    check_render_deployment()
    update_comparison_page()

    elapsed = time.time() - start_time
    remaining = timeout - elapsed

    print(f"\nElapsed: {int(elapsed)}s | Remaining: {int(remaining)}s")
    print("-" * 60)

    if remaining > 0:
        time.sleep(30)  # Check every 30 seconds

print("\n" + "=" * 60)
print("MONITORING COMPLETE")
print("=" * 60)
print(f"Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("\nNEXT STEPS:")
print("1. Visit https://salpurflask.onrender.com/pos")
print("2. Visit https://tradeflow-demo.onrender.com/pos")
print("3. If pages load, deployment is complete!")
