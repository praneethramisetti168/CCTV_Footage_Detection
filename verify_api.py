import urllib.request, json, time

base = 'http://localhost:8000'
endpoints = [
    '/api/v1/analytics/summary',
    '/api/v1/analytics/heatmap',
    '/api/v1/analytics/footfall?period=hourly',
    '/api/v1/analytics/dwell-time',
    '/api/v1/events?limit=5',
    '/api/v1/anomalies?limit=5',
    '/api/v1/persons?limit=5',
    '/api/v1/zones',
    '/api/v1/cameras',
    '/api/v1/anomalies/stats/summary',
]

time.sleep(4)
print('--- API Verification ---')
for ep in endpoints:
    try:
        r = urllib.request.urlopen(base + ep, timeout=5)
        data = json.loads(r.read())
        extra = ''
        if 'active_persons' in data:
            extra = f"  active={data['active_persons']}  total={data['total_persons_today']}  fps={data['fps']}"
        elif 'total' in data and 'events' in data:
            extra = f"  total_events={data['total']}"
        elif 'total' in data and 'anomalies' in data:
            extra = f"  total_anomalies={data['total']}"
        elif 'zones' in data:
            extra = f"  zones={len(data['zones'])}"
        elif 'cameras' in data:
            extra = f"  status={data['cameras'][0]['status']}"
        elif 'by_severity' in data:
            extra = f"  unresolved={data['total_unresolved']}"
        print(f"  OK   {ep}{extra}")
    except Exception as e:
        print(f"  FAIL {ep}  -> {e}")

print('\n--- Server Stats ---')
r = urllib.request.urlopen(base + '/health', timeout=5)
h = json.loads(r.read())
print(json.dumps(h, indent=2))
