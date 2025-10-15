# How to Check Logs in Grafana Cloud Loki

## The Situation

- ✅ Handler attached at startup (logs show "Total handlers: 1")
- ❌ Handler missing now (check shows "Handlers: 0")
- ❌ Application logs NOT in pod stdout (means they're being captured)
- ❌ No OTLP activity in Grafana Alloy logs
- ❓ Logs might be in Grafana Cloud but with different labels

---

## Check in Grafana Cloud NOW

🔗 **URL**: https://yurisa2.grafana.net

### Try ALL These Queries

Navigate to: **Explore** → **Loki**

#### Query 1: By Namespace (What You've Been Trying)
```logql
{namespace="petrosa-apps", pod=~"petrosa-tradeengine.*"}
```

#### Query 2: By Service Name (OTLP Attribute)
```logql
{service_name="tradeengine"}
```

#### Query 3: By Job
```logql
{job=~".*tradeengine.*"}
```

#### Query 4: All Logs (Broadest)
```logql
{}
```

Then filter manually for "tradeengine" or "petrosa"

#### Query 5: Check All Available Labels
In Loki, click "Label browser" to see what labels actually exist.
Look for anything related to "trade" or "petrosa"

---

## Time Range

Try these time ranges:
- Last 1 hour
- Last 6 hours
- Last 24 hours
- Custom: From when pods started (check pod age)

---

## What to Look For

If you see logs with ANY of these:
- "Starting Petrosa Trading Engine"
- "MongoDB"
- "Binance"
- "NATS consumer"
- "watchdog"
- "LIVE TEST LOG"
- "WATCHDOG TEST LOG"

Then logs ARE being exported, just with different labels than expected!

---

## If Still No Logs

The issue is likely one of:
1. **OTLP log exporter not actually sending** (silent failure)
2. **Grafana Alloy receiving but not forwarding** (config issue)
3. **Grafana Cloud receiving but rejecting** (auth issue for logs specifically)

---

**Please check ALL the queries above and let me know what you find!**
