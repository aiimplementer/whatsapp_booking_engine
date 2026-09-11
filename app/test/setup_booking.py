#!/usr/bin/env python3
"""Setup WhatsApp Booking Engine - Idempotent with full debugging."""

import requests
import sys
import json

BASE_URL = "http://localhost:8008/api/v1"
TENANT_SLUG = "medicare"

# Credentials
EMAIL = "ai.implementer@gmail.com"
PASSWORD = "admin123"
TENANT_NAME = "Medicare"

def log(msg):
    print(f"✓ {msg}")

def error(msg):
    print(f"✗ {msg}")

def step(msg):
    print(f"\n>>> {msg}")

def check_tenant_exists():
    """Check if tenant exists."""
    try:
        r = requests.get(f"{BASE_URL}/public/{TENANT_SLUG}")
        return r.status_code == 200
    except:
        return False

def signup():
    """Create tenant if it doesn't exist."""
    step("1. Checking tenant")
    
    if check_tenant_exists():
        log(f"Tenant '{TENANT_SLUG}' already exists")
        return True
    
    step("1. Creating tenant")
    payload = {
        "business_name": TENANT_NAME,
        "tenant_slug": TENANT_SLUG,
        "business_email": "business@medicare.com",
        "business_phone": "+1234567890",
        "admin_email": EMAIL,
        "admin_password": PASSWORD,
        "timezone": "Asia/Kolkata"
    }
    try:
        r = requests.post(f"{BASE_URL}/auth/signup", json=payload)
        r.raise_for_status()
        log(f"Tenant created: {TENANT_SLUG}")
        return True
    except Exception as e:
        error(f"Signup failed: {e}")
        return False

def login():
    """Login and get JWT token."""
    step("2. Logging in")
    payload = {
        "tenant_slug": TENANT_SLUG,
        "email": EMAIL,
        "password": PASSWORD
    }
    try:
        r = requests.post(f"{BASE_URL}/auth/login", json=payload)
        r.raise_for_status()
        token = r.json().get("access_token")
        log(f"Login successful")
        return token
    except Exception as e:
        error(f"Login failed: {e}")
        return None

def get_headers(token):
    return {"Authorization": f"Bearer {token}"}

def get_services(token):
    """Fetch existing services."""
    try:
        r = requests.get(
            f"{BASE_URL}/scheduling/services",
            headers=get_headers(token)
        )
        r.raise_for_status()
        return r.json()
    except:
        return []

def create_service(token):
    """Create service if it doesn't exist."""
    step("3. Checking services")
    
    existing = get_services(token)
    if existing:
        svc = existing[0]
        log(f"Service exists: {svc.get('name')} (ID: {svc.get('id')})")
        return svc.get("id")
    
    step("3. Creating service")
    payload = {
        "name": "Routine Checkup",
        "duration_minutes": 15
    }
    try:
        r = requests.post(
            f"{BASE_URL}/scheduling/services",
            json=payload,
            headers=get_headers(token)
        )
        r.raise_for_status()
        service = r.json()
        log(f"Service created: {service.get('name')} (ID: {service.get('id')})")
        return service.get("id")
    except Exception as e:
        error(f"Service creation failed: {e}")
        return None

def get_scheduling_configs(token):
    """Fetch existing configs."""
    try:
        r = requests.get(
            f"{BASE_URL}/scheduling/configs",
            headers=get_headers(token)
        )
        r.raise_for_status()
        return r.json()
    except:
        return []

def create_scheduling_config(token, service_id):
    """Create scheduling config if it doesn't exist."""
    step("4. Checking scheduling config")
    
    existing = get_scheduling_configs(token)
    if existing:
        cfg = existing[0]
        log(f"Config exists: {cfg.get('start_time')}-{cfg.get('end_time')}")
        return True
    
    step("4. Creating scheduling config")
    payload = {
        "service_id": service_id,
        "start_time": "09:00",
        "end_time": "17:00",
        "slot_interval_minutes": 15,
        "available_days": [1, 2, 3, 4, 5]  # Mon-Fri
    }
    try:
        r = requests.put(
            f"{BASE_URL}/scheduling/configs",
            json=payload,
            headers=get_headers(token)
        )
        r.raise_for_status()
        log(f"Config created: 9am-5pm, 15min slots, Mon-Fri")
        return True
    except Exception as e:
        error(f"Config creation failed: {e}")
        return False

def get_available_slots(service_id):
    """Fetch and display available slots."""
    step("5. Testing available slots")
    try:
        r = requests.get(
            f"{BASE_URL}/public/{TENANT_SLUG}/available-slots",
            params={"service_id": service_id}
        )
        r.raise_for_status()
        slots = r.json()
        if slots:
            log(f"Found {len(slots)} available slots")
            for i, slot in enumerate(slots[:5]):
                start = slot.get('start_time', '?')
                end = slot.get('end_time', '?')
                print(f"  - {start} to {end}")
            if len(slots) > 5:
                print(f"  ... and {len(slots) - 5} more")
            return True
        else:
            error("No slots found")
            return False
    except Exception as e:
        error(f"Slots fetch failed: {e}")
        return False

def get_business_info():
    """Verify public business info endpoint."""
    step("6. Verifying business info")
    try:
        r = requests.get(f"{BASE_URL}/public/{TENANT_SLUG}")
        r.raise_for_status()
        biz = r.json()
        log(f"Business: {biz.get('name')} (Slug: {biz.get('slug')}, TZ: {biz.get('timezone')})")
        return True
    except Exception as e:
        error(f"Business info fetch failed: {e}")
        return False

def debug_all_configs(token):
    """Debug: Show all configs."""
    step("DEBUG: Listing all configs")
    try:
        r = requests.get(
            f"{BASE_URL}/scheduling/configs",
            headers=get_headers(token)
        )
        r.raise_for_status()
        configs = r.json()
        if configs:
            for i, cfg in enumerate(configs):
                print(f"  Config {i+1}: {json.dumps(cfg, indent=2)}")
        else:
            log("No configs found")
        return configs
    except Exception as e:
        error(f"Config debug failed: {e}")
        return []

def debug_all_services(token):
    """Debug: Show all services."""
    step("DEBUG: Listing all services")
    try:
        r = requests.get(
            f"{BASE_URL}/scheduling/services",
            headers=get_headers(token)
        )
        r.raise_for_status()
        services = r.json()
        if services:
            for i, svc in enumerate(services):
                print(f"  Service {i+1}: {svc.get('name')} (ID: {svc.get('id')})")
        else:
            log("No services found")
        return services
    except Exception as e:
        error(f"Service debug failed: {e}")
        return []

def main():
    print("=" * 60)
    print("WhatsApp Booking Engine - Setup & Test")
    print("=" * 60)
    
    # Signup/Check tenant
    if not signup():
        error("Tenant check/creation failed")
        sys.exit(1)
    
    # Login
    token = login()
    if not token:
        error("Cannot proceed without token")
        sys.exit(1)
    
    # Create/Check service
    service_id = create_service(token)
    if not service_id:
        error("Cannot proceed without service")
        sys.exit(1)
    
    # Create/Check scheduling
    if not create_scheduling_config(token, service_id):
        error("Cannot proceed without scheduling")
        sys.exit(1)
    
    # Test public endpoints
    get_business_info()
    slots_ok = get_available_slots(service_id)
    
    # Debug info
    print("\n" + "=" * 60)
    print("DEBUG INFO")
    print("=" * 60)
    debug_all_services(token)
    debug_all_configs(token)
    
    print("\n" + "=" * 60)
    if slots_ok:
        print("✓ Setup complete!")
    else:
        print("⚠ Setup done but slots issue detected")
    print("=" * 60)
    print(f"\nBooking page: http://localhost:8008/")
    print(f"Admin panel: http://localhost:8008/admin")
    print(f"API docs: http://localhost:8008/docs\n")

if __name__ == "__main__":
    main()