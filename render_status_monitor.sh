#!/bin/bash
# Render Deployment Status Checker
# Run this to monitor when Render deployments complete

echo "=================================================="
echo "RENDER DEPLOYMENT MONITOR"
echo "=================================================="
echo "Checking deployment status every 30 seconds..."
echo "This will run for 10 minutes maximum"
echo ""
echo "Expected URLs:"
echo "  - https://salpurflask.onrender.com/pos"
echo "  - https://tradeflow-demo.onrender.com/pos"
echo "=================================================="
echo ""

TIMEOUT=600  # 10 minutes
INTERVAL=30  # 30 seconds
START_TIME=$(date +%s)

while true; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    REMAINING=$((TIMEOUT - ELAPSED))

    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "[$(date +'%H:%M:%S')] Monitoring timeout - deployment should be complete now"
        break
    fi

    echo "[$(date +'%H:%M:%S')] Elapsed: ${ELAPSED}s | Remaining: ${REMAINING}s"

    # Check salpurflask
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://salpurflask.onrender.com/pos --max-time 5 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  ✓ salpurflask: ACCESSIBLE (HTTP $HTTP_CODE)"
    else
        echo "  ✗ salpurflask: Status $HTTP_CODE"
    fi

    # Check tradeflow-demo
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://tradeflow-demo.onrender.com/pos --max-time 5 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  ✓ tradeflow-demo: ACCESSIBLE (HTTP $HTTP_CODE)"
    else
        echo "  ✗ tradeflow-demo: Status $HTTP_CODE"
    fi

    echo ""
    sleep $INTERVAL
done

echo ""
echo "=================================================="
echo "DONE - Visit the URLs to verify deployments"
echo "=================================================="
