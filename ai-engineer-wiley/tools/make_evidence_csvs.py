#!/usr/bin/env python3
"""Generate the two evidence files Chapter 2's tools call.

  data/ip_reputation.csv  - a threat-intel feed snapshot, one row per IP (100 rows)
  data/login_history.csv  - an identity provider's sign-in log, one row per event (100 rows)

ALT-1001 is planted exactly as Lab 1's alert describes it: userid123, source IP
198.51.100.4, 12 failed sign-ins then 1 success within 6 minutes, unknown device.
All IPs are from the documentation ranges (RFC 5737), so none is a real address.
Seeded, so every run produces byte-identical files.
"""
import csv
import random
from datetime import datetime, timedelta, timezone

random.seed(1001)
UTC = timezone.utc
ALERT_IP, ALERT_USER = "198.51.100.4", "userid123"
ALERT_START = datetime(2026, 9, 17, 2, 7, 0, tzinfo=UTC)      # 12 failures, then success at 02:13
FEED_SNAPSHOT = datetime(2026, 9, 17, 1, 0, 0, tzinfo=UTC)    # intel snapshot predates the alert

def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------- IP reputation feed
# Fixed IPs the story needs, then filler. (ip, reputation, risk, conf, category, types,
#  country, city, asn, as_org, tor, proxy, vpn, hosting, abuse_reports, first_seen_days_ago)
STORY_IPS = [
    (ALERT_IP, "MALICIOUS", 92, 96, "CREDENTIAL_ATTACK", "brute_force;credential_stuffing;password_spray",
     "NL", "Amsterdam", 64505, "Synthetic Hosting BV", False, True, False, True, 47, 326),
    ("198.51.100.7", "TRUSTED", 3, 95, "BENIGN", "none",
     "US", "San Jose", 64500, "Aegis Corp VPN", False, False, True, False, 0, 900),
    ("198.51.100.23", "TRUSTED", 8, 80, "BENIGN", "none",
     "US", "Oakland", 64510, "Example Broadband", False, False, False, False, 0, 600),
    ("192.0.2.66", "SUSPICIOUS", 74, 88, "ANONYMIZATION", "tor_exit",
     "DE", "Frankfurt", 64520, "Synthetic Cloud GmbH", True, False, False, True, 19, 210),
    ("192.0.2.140", "MALICIOUS", 85, 90, "CREDENTIAL_ATTACK", "brute_force",
     "BR", "Sao Paulo", 64530, "Example Datacenter", False, False, False, True, 31, 120),
]

CATEGORIES = {   # reputation -> (category, types, risk range)
    "MALICIOUS": [("CREDENTIAL_ATTACK", "brute_force;credential_stuffing", (78, 98)),
                  ("BOTNET", "botnet_c2;scanner", (80, 99)),
                  ("MALWARE", "malware_distribution", (82, 97))],
    "SUSPICIOUS": [("ANONYMIZATION", "proxy;tor_exit", (55, 79)),
                   ("SCANNER", "port_scan", (50, 70)),
                   ("SPAM", "spam_source", (45, 65))],
    "TRUSTED": [("BENIGN", "none", (0, 10))],
    "UNKNOWN": [("UNCLASSIFIED", "none", (20, 45))],
}
PLACES = [("US", "New York"), ("US", "Seattle"), ("US", "Austin"), ("GB", "London"), ("DE", "Berlin"),
          ("FR", "Paris"), ("NL", "Amsterdam"), ("SG", "Singapore"), ("JP", "Tokyo"), ("BR", "Sao Paulo"),
          ("IN", "Mumbai"), ("CA", "Toronto"), ("AU", "Sydney"), ("RO", "Bucharest"), ("ZA", "Johannesburg")]
ORGS = ["Example Transit", "Synthetic Cloud", "Example Broadband", "Research Network",
        "Example Mobile", "Synthetic Hosting BV", "Example Datacenter", "Example Telecom"]

used = {row[0] for row in STORY_IPS}
pool = [f"{p}.{i}" for p in ("192.0.2", "198.51.100", "203.0.113") for i in range(2, 255)]
filler_ips = random.sample([ip for ip in pool if ip not in used], 100 - len(STORY_IPS))

ip_rows = []
for ip, rep, risk, conf, cat, types, cc, city, asn, org, tor, proxy, vpn, hosting, abuse, age in STORY_IPS:
    ip_rows.append((ip, rep, risk, conf, cat, types, cc, city, asn, org, tor, proxy, vpn, hosting, abuse, age))
reps = (["MALICIOUS"] * 15 + ["SUSPICIOUS"] * 25 + ["TRUSTED"] * 25 + ["UNKNOWN"] * 30)
random.shuffle(reps)
for ip, rep in zip(filler_ips, reps):
    cat, types, (lo, hi) = random.choice(CATEGORIES[rep])
    cc, city = random.choice(PLACES)
    hosting = rep in ("MALICIOUS", "SUSPICIOUS") and random.random() < 0.7
    ip_rows.append((ip, rep, random.randint(lo, hi), random.randint(60, 98), cat, types, cc, city,
                    random.randint(64496, 64511) if rep != "TRUSTED" else 64500 + random.randint(0, 5),
                    random.choice(ORGS), "tor_exit" in types, "proxy" in types,
                    rep == "TRUSTED" and random.random() < 0.3, hosting,
                    random.randint(5, 60) if rep in ("MALICIOUS", "SUSPICIOUS") else random.randint(0, 2),
                    random.randint(15, 700)))

IP_HEADER = ["ip_address", "reputation", "risk_score", "confidence_score", "threat_category", "threat_types",
             "first_seen", "last_seen", "country_code", "city", "asn", "as_org",
             "is_tor", "is_proxy", "is_vpn", "is_hosting", "abuse_reports", "feed_source", "updated_at"]
GEO = {}
with open("data/ip_reputation.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(IP_HEADER)
    for (ip, rep, risk, conf, cat, types, cc, city, asn, org, tor, proxy, vpn, hosting, abuse, age) in \
            sorted(ip_rows, key=lambda r: tuple(int(x) for x in r[0].split("."))):
        last = FEED_SNAPSHOT - timedelta(hours=random.randint(2, 72))
        if ip == ALERT_IP:
            last = FEED_SNAPSHOT - timedelta(hours=9)       # seen spraying other tenants the day before
        first = last - timedelta(days=age)
        w.writerow([ip, rep, risk, conf, cat, types, iso(first), iso(last), cc, city, asn, org,
                    str(tor).lower(), str(proxy).lower(), str(vpn).lower(), str(hosting).lower(),
                    abuse, "aegis-intel-feed", iso(FEED_SNAPSHOT)])
        GEO[ip] = (cc, city, asn)

# ---------------------------------------------------------------- Sign-in log
LOGIN_HEADER = ["event_id", "timestamp", "ingested_at", "user_id", "username", "source_ip",
                "outcome", "failure_reason", "auth_method", "mfa_result", "device_id", "device_managed",
                "new_device", "operating_system", "browser", "country_code", "city", "asn",
                "application", "risk_level", "correlation_id", "event_source"]
events = []

def event(t, user, ip, outcome, reason, device, managed, new, os_, browser, mfa, risk, corr,
          app="Corporate Portal", method="password"):
    cc, city, asn = GEO[ip]
    events.append(dict(timestamp=t, ingested_at=t + timedelta(seconds=random.randint(4, 15)),
                       user_id=user, username=f"{user}@aegis-lab.example", source_ip=ip,
                       outcome=outcome, failure_reason=reason, auth_method=method, mfa_result=mfa,
                       device_id=device, device_managed=str(managed).lower(), new_device=str(new).lower(),
                       operating_system=os_, browser=browser, country_code=cc, city=city, asn=asn,
                       application=app, risk_level=risk, correlation_id=corr, event_source="aegis-idp"))

# 1. ALT-1001: 12 failures, then 1 success, every 30 seconds from 02:07:00 to 02:13:00
for i in range(13):
    ok = i == 12
    event(ALERT_START + timedelta(seconds=30 * i), ALERT_USER, ALERT_IP,
          "SUCCESS" if ok else "FAILURE", "none" if ok else "INVALID_CREDENTIALS",
          "unknown-77", False, True, "Linux", "Chrome", "NOT_CHALLENGED", "HIGH", "corr-alt-1001")

# 2. userid123's normal pattern: managed laptop, corporate VPN or home ISP, MFA passed
for days, hour, ip in [(13, 16, "198.51.100.7"), (11, 17, "198.51.100.7"), (9, 15, "198.51.100.23"),
                       (6, 16, "198.51.100.7"), (4, 18, "198.51.100.7"), (2, 16, "198.51.100.23")]:
    t = ALERT_START.replace(hour=hour, minute=random.randint(0, 59)) - timedelta(days=days)
    event(t, ALERT_USER, ip, "SUCCESS", "none", "laptop-0123", True, False, "macOS", "Safari",
          "SUCCESS", "LOW", f"corr-{random.randint(10000, 99999)}", method="password+mfa")

# 3. The same attacker IP spraying two other accounts just before ALT-1001 (the Chapter 5 campaign)
for user, minute in [("userid118", 48), ("userid131", 52), ("userid118", 55)]:
    event(ALERT_START.replace(hour=1, minute=minute), user, ALERT_IP, "FAILURE", "INVALID_CREDENTIALS",
          "unknown-77", False, True, "Linux", "Chrome", "NOT_CHALLENGED", "HIGH", "corr-spray-0917")

# 4. Background traffic from 25 other users: mostly normal, with realistic noise
OTHERS = [f"userid{n}" for n in range(101, 141) if n not in (123,)][:25]
TRUSTED = [r[0] for r in ip_rows if r[1] == "TRUSTED"]
RISKY = [r[0] for r in ip_rows if r[1] in ("SUSPICIOUS", "MALICIOUS") and r[0] != ALERT_IP]
UNKNOWN = [r[0] for r in ip_rows if r[1] == "UNKNOWN"]
DEVICES = [("Windows", "Edge"), ("macOS", "Chrome"), ("Windows", "Chrome"), ("iOS", "Safari"), ("Android", "Chrome")]
APPS = ["Corporate Portal", "Email", "HR System", "VPN Gateway", "Code Repository"]
home_ip = {u: random.choice(TRUSTED) for u in OTHERS}
remaining = 100 - len(events)
for _ in range(remaining):
    u = random.choice(OTHERS)
    t = ALERT_START - timedelta(days=random.randint(0, 13), hours=random.randint(-10, 14),
                                minutes=random.randint(0, 59))
    os_, br = random.choice(DEVICES)
    roll = random.random()
    if roll < 0.72:                       # normal sign-in
        event(t, u, home_ip[u], "SUCCESS", "none", f"device-{OTHERS.index(u):03d}", True, False,
              os_, br, "SUCCESS", "LOW", f"corr-{random.randint(10000, 99999)}",
              app=random.choice(APPS), method="password+mfa")
    elif roll < 0.84:                     # fat-fingered password, same device
        event(t, u, home_ip[u], "FAILURE", "INVALID_CREDENTIALS", f"device-{OTHERS.index(u):03d}", True,
              False, os_, br, "NOT_CHALLENGED", "LOW", f"corr-{random.randint(10000, 99999)}",
              app=random.choice(APPS))
    elif roll < 0.92:                     # travel or new phone: unknown IP, MFA passed
        event(t, u, random.choice(UNKNOWN), "SUCCESS", "none", f"mobile-{random.randint(100, 999)}",
              False, True, "iOS", "Safari", "SUCCESS", "MEDIUM", f"corr-{random.randint(10000, 99999)}",
              app="Email", method="password+mfa")
    else:                                 # opportunistic attack from a risky IP, stopped by MFA
        event(t, u, random.choice(RISKY), "FAILURE", "MFA_DENIED", f"unknown-{random.randint(10, 99)}",
              False, True, "Linux", "Firefox", "DENIED", "HIGH", f"corr-{random.randint(10000, 99999)}")

events.sort(key=lambda e: e["timestamp"])
with open("data/login_history.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=LOGIN_HEADER)
    w.writeheader()
    for i, e in enumerate(events, 1):
        w.writerow({"event_id": f"evt-{i:05d}", **e, "timestamp": iso(e["timestamp"]),
                    "ingested_at": iso(e["ingested_at"])})
print("wrote data/ip_reputation.csv and data/login_history.csv")
