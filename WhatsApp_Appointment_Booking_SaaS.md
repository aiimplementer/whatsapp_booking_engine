# WhatsApp Appointment Scheduling SaaS - Architecture & Design

## System Overview

Multi-tenant SaaS platform enabling businesses to manage appointments via WhatsApp + Web portal. Each tenant (business) gets dedicated WhatsApp bot, custom branding, and scheduling rules.

---

## Core Architecture

```
┌─────────────────────────────────────────────┐
│           Tenant Portal (Web)               │
│  (Onboarding, Config, Dashboard, Analytics) │
└────────────────┬────────────────────────────┘
                 │
        ┌────────┴────────┐
        │                 │
   ┌────▼────┐       ┌───▼────┐
   │WhatsApp │       │REST API │
   │ Bot     │       │ Layer   │
   └────┬────┘       └───┬────┘
        │                 │
        └────────┬────────┘
                 │
        ┌────────▼─────────┐
        │  Core Services   │
        ├──────────────────┤
        │ • Auth & Multi-  │
        │   tenancy        │
        │ • Scheduling     │
        │   Engine         │
        │ • Slot Generator │
        │ • Notifications  │
        │ • Payment (opt)  │
        └────────┬─────────┘
                 │
        ┌────────▼──────────┐
        │   Neon Database   │
        │ (PostgreSQL)      │
        └───────────────────┘
                 │
        ┌────────▼──────────┐
        │ Background Jobs   │
        │ (n8n / Bull Queue)│
        │ • Reminders       │
        │ • Notifications   │
        │ • Cleanup         │
        └───────────────────┘
```

---

## Core Data Models

### Tenant (Business)
```
{
  id: UUID
  name: string
  email: string
  phone: string
  timezone: string
  status: 'active' | 'inactive' | 'trial'
  whatsapp_number: string
  web_booking_enabled: boolean
  branding: {
    logo_url: string
    business_color: string
    intro_message: string
  }
  created_at: timestamp
  updated_at: timestamp
}
```

### Scheduling Config (Per Tenant)
```
{
  id: UUID
  tenant_id: UUID
  service_id: UUID (optional)
  
  working_days: [MON, TUE, WED, THU, FRI] // bitmask or array
  
  time_slots: [
    {
      day: MON,
      start: "10:00",
      end: "13:00"
    },
    {
      day: MON,
      start: "16:00",
      end: "19:00"
    }
  ]
  
  appointment_duration: 20 // minutes
  buffer_time: 0 // minutes between appointments
  advance_booking_days: 30
  
  holidays: [
    { date: "2026-09-20", reason: "Diwali" }
  ]
  
  blocked_times: [
    {
      start_datetime: "2026-09-18T17:00:00",
      end_datetime: "2026-09-18T18:00:00",
      reason: "Lunch break"
    }
  ]
}
```

### Service (Optional - For Multi-Service Businesses)
```
{
  id: UUID
  tenant_id: UUID
  name: string // "Consultation", "Follow-up"
  duration: 20 // minutes
  description: string
  active: boolean
}
```

### Appointment
```
{
  id: UUID
  tenant_id: UUID
  service_id: UUID (optional)
  customer_name: string
  customer_phone: string
  customer_email: string (optional)
  
  scheduled_at: timestamp // appointment date/time
  duration: 20 // minutes
  
  status: 'PENDING' | 'CONFIRMED' | 'CHECKED_IN' | 'COMPLETED' | 'CANCELLED' | 'RESCHEDULED' | 'NO_SHOW'
  
  notes: string (optional)
  reminder_sent_at: timestamp
  created_at: timestamp
  updated_at: timestamp
}
```

### AvailableSlot (Cached/Computed)
```
{
  id: UUID
  tenant_id: UUID
  service_id: UUID (optional)
  slot_datetime: timestamp
  duration: 20
  booked: boolean
  blocked: boolean
  reason: string (optional)
  generated_at: timestamp
}
```

---

## Admin Setup & Onboarding

### Phase 1: Tenant Registration
1. Business signs up with email, phone, business name
2. Verify email/phone
3. Create tenant record
4. Default configuration (9 AM - 6 PM, 20 min slots, Mon-Fri)

### Phase 2: WhatsApp Connection
1. Tenant provides WhatsApp Business Account number (or gets allocated shared number)
2. Authenticate with WhatsApp API
3. Configure webhook for incoming messages
4. Test bot with welcome message

### Phase 3: Configuration
1. Set working hours (multiple time blocks per day)
2. Define services (optional)
3. Add holidays/blocked dates
4. Set appointment duration & buffer
5. Configure timezone
6. Enable web booking (optional)

### Phase 4: Go Live
1. Publish WhatsApp bot
2. Send tenant their booking link
3. Dashboard access granted

---

## WhatsApp Bot Flow

### Incoming Message Handler

```
Message received
    ↓
Tenant lookup (from bot number)
    ↓
User state tracking (session)
    ↓
Intent detection:
    ├─ "Book" → Slot selection flow
    ├─ "Cancel" → Find appointment → Confirm cancel
    ├─ "Reschedule" → Find appointment → New slot → Confirm
    ├─ "Info" → Send business info
    └─ "Help" → Send menu
    ↓
Response generated + sent
    ↓
Update session state
```

### Booking Flow (Detailed)

```
Step 1: Main Menu
"👋 Welcome to Business Name
📅 Book Appointment
🔄 Reschedule
❌ Cancel
📞 Contact"

Step 2: Service Selection (if multiple)
"Select service:
1️⃣ Consultation (20 min)
2️⃣ Follow-up (15 min)"

Step 3: Date Selection
"Select date:
📅 Today
📅 Tomorrow
📅 Friday, Sept 12
📅 Pick date"

Step 4: Available Slots
"Available times:
🕐 4:00 PM
🕐 4:20 PM
🕐 4:40 PM
🕐 5:00 PM"

Step 5: Customer Details
"Name:
Email: (optional)
Phone: (already have)"

Step 6: Confirmation
"Confirm Appointment?
📅 Friday, 12 Sept
⏰ 4:40 PM
🩺 Consultation (20 min)
👤 John Doe

✅ Confirm
❌ Cancel"

Step 7: Success
"✅ Appointment Confirmed!
📅 Friday, 12 Sept at 4:40 PM
Ref: #APT-12345678

📱 Reschedule | ❌ Cancel"
```

---

## Scheduling Engine Logic

### Available Slot Generation

```python
def generate_available_slots(tenant_id, service_id, start_date, end_date):
    config = get_scheduling_config(tenant_id, service_id)
    appointments = get_appointments_in_range(tenant_id, start_date, end_date)
    blocked_times = config.blocked_times
    holidays = config.holidays
    
    slots = []
    
    for date in date_range(start_date, end_date):
        if date in holidays:
            continue
        if date.day_of_week not in config.working_days:
            continue
        
        time_blocks = config.time_slots_for_day(date.day_of_week)
        
        for block in time_blocks:
            current_time = block.start
            
            while current_time + duration <= block.end:
                if not is_blocked(current_time, blocked_times):
                    if not is_booked(current_time, appointments):
                        slots.append({
                            tenant_id: tenant_id,
                            slot_datetime: datetime(date, current_time),
                            duration: config.appointment_duration,
                            booked: False
                        })
                
                current_time += timedelta(minutes=config.appointment_duration)
    
    return slots
```

### Slot Availability Check

```python
def is_slot_available(tenant_id, slot_datetime, duration):
    # Check if slot conflicts with any confirmed appointment
    conflicts = Appointment.query.filter(
        Appointment.tenant_id == tenant_id,
        Appointment.status.in_(['CONFIRMED', 'CHECKED_IN']),
        Appointment.scheduled_at <= slot_datetime,
        Appointment.scheduled_at + duration > slot_datetime
    ).count()
    
    return conflicts == 0
```

---

## API Endpoints

### Authentication & Tenant Management
```
POST   /api/v1/auth/register           - Tenant signup
POST   /api/v1/auth/login              - Login
POST   /api/v1/auth/verify-email       - Email verification
GET    /api/v1/tenant/profile          - Get tenant info
PATCH  /api/v1/tenant/profile          - Update tenant config
```

### WhatsApp Integration
```
POST   /api/v1/whatsapp/webhook        - Incoming message webhook
POST   /api/v1/whatsapp/send           - Send message
POST   /api/v1/whatsapp/connect        - Connect WhatsApp number
POST   /api/v1/whatsapp/disconnect     - Disconnect
```

### Scheduling Config
```
GET    /api/v1/scheduling/config       - Get config
PATCH  /api/v1/scheduling/config       - Update config
POST   /api/v1/scheduling/holidays     - Add holiday
DELETE /api/v1/scheduling/holidays/:id - Remove holiday
POST   /api/v1/scheduling/blocked      - Block time
DELETE /api/v1/scheduling/blocked/:id  - Unblock
```

### Services
```
GET    /api/v1/services                - List services
POST   /api/v1/services                - Create service
PATCH  /api/v1/services/:id            - Update service
DELETE /api/v1/services/:id            - Delete service
```

### Appointments
```
GET    /api/v1/appointments            - List (with filters)
POST   /api/v1/appointments            - Create appointment
GET    /api/v1/appointments/:id        - Get appointment
PATCH  /api/v1/appointments/:id        - Update status
DELETE /api/v1/appointments/:id        - Cancel

GET    /api/v1/appointments/available-slots  - Get available slots
```

### Public Web Booking
```
GET    /api/v1/public/:tenant_slug/available-slots
POST   /api/v1/public/:tenant_slug/book
```

---

## Database Schema

### Tables

```sql
-- Multi-tenancy
tenants
  ├─ id (UUID, PK)
  ├─ name (varchar)
  ├─ email (varchar, unique)
  ├─ phone (varchar)
  ├─ timezone (varchar)
  ├─ status (enum)
  ├─ whatsapp_number (varchar)
  ├─ branding (jsonb)
  ├─ subscription_tier (enum)
  ├─ created_at
  ├─ updated_at
  └─ deleted_at (soft delete)

tenant_users (team members)
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ email (varchar)
  ├─ role (enum: admin, staff, viewer)
  ├─ created_at

-- Scheduling
scheduling_configs
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ service_id (UUID, FK, nullable)
  ├─ working_days (smallint bitmask)
  ├─ time_slots (jsonb array)
  ├─ appointment_duration (int)
  ├─ buffer_time (int)
  ├─ advance_booking_days (int)
  ├─ updated_at

services
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ name (varchar)
  ├─ duration (int)
  ├─ description (text)
  ├─ active (boolean)
  └─ updated_at

holidays
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ date (date)
  ├─ reason (varchar)

blocked_times
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ start_datetime (timestamp)
  ├─ end_datetime (timestamp)
  ├─ reason (varchar)

-- Appointments
appointments
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ service_id (UUID, FK, nullable)
  ├─ customer_name (varchar)
  ├─ customer_phone (varchar, indexed)
  ├─ customer_email (varchar, nullable)
  ├─ scheduled_at (timestamp, indexed)
  ├─ duration (int)
  ├─ status (enum, indexed)
  ├─ notes (text)
  ├─ reminder_sent_at (timestamp, nullable)
  ├─ created_at
  └─ updated_at

-- WhatsApp Session Management (optional, for flow state)
whatsapp_sessions
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK)
  ├─ customer_phone (varchar, indexed)
  ├─ current_step (varchar)
  ├─ temp_data (jsonb)
  ├─ last_activity (timestamp)
  └─ updated_at

-- Audit
audit_logs
  ├─ id (UUID, PK)
  ├─ tenant_id (UUID, FK, indexed)
  ├─ action (varchar)
  ├─ resource_type (varchar)
  ├─ resource_id (UUID)
  ├─ changes (jsonb)
  ├─ created_at (indexed)

-- Indexes
CREATE INDEX idx_appointments_tenant_scheduled ON appointments(tenant_id, scheduled_at);
CREATE INDEX idx_appointments_customer_phone ON appointments(tenant_id, customer_phone);
CREATE INDEX idx_appointments_status ON appointments(tenant_id, status);
CREATE INDEX idx_blocked_times_tenant_datetime ON blocked_times(tenant_id, start_datetime);
CREATE INDEX idx_whatsapp_sessions_tenant_phone ON whatsapp_sessions(tenant_id, customer_phone);
```

---

## Business Dashboard (Tenant Portal)

### Overview Dashboard
- Total appointments (this week/month)
- Booking rate graph
- Cancellation rate
- Popular time slots
- Revenue (if paid features)

### Appointment Management
- Calendar view (day/week/month)
- List view with filters (status, date, customer)
- Quick actions (reschedule, cancel, send reminder)
- Bulk operations

### Configuration Pages
1. **Working Hours** - Set time blocks per day
2. **Services** - Create/edit services with durations
3. **Holidays** - Mark closed days
4. **Blocked Times** - Block specific hours
5. **Integrations** - WhatsApp settings, web booking link
6. **Branding** - Logo, colors, welcome message
7. **Team** - Manage staff access

### Analytics
- Appointment trends
- Customer demographics
- Revenue breakdown (by service, time)
- No-show rate
- Average booking lead time

### Settings
- Timezone
- Notification preferences
- Payment setup (optional)
- API keys for web booking

---

## Appointment Status Flow

### Ideal Flow
```
PENDING (immediately after booking)
    ↓
CONFIRMED (customer confirms via WhatsApp/email)
    ↓
CHECKED_IN (at appointment time)
    ↓
COMPLETED (after session ends)
```

### Alternate Flows
```
CONFIRMED → CANCELLED (customer cancels)
CONFIRMED → RESCHEDULED (customer reschedules)
CONFIRMED → NO_SHOW (customer doesn't show up)
PENDING → EXPIRED (no confirmation after X hours)
```

### Status Transitions & Rules
```
PENDING:
  - Auto-expire after 24 hours (optional)
  - Can transition to: CONFIRMED, CANCELLED

CONFIRMED:
  - Send reminder 24h, 1h, 15min before
  - Can transition to: CHECKED_IN, CANCELLED, RESCHEDULED, NO_SHOW

CHECKED_IN:
  - Recorded when customer arrives
  - Can transition to: COMPLETED, NO_SHOW

COMPLETED:
  - Final state
  - Can request follow-up

CANCELLED / RESCHEDULED / NO_SHOW:
  - Final states (or allow re-open)
  - Slot becomes available again
```

---

## Background Jobs & Notifications

### Jobs Queue (Bull/n8n)

| Job | Frequency | Purpose |
|-----|-----------|---------|
| SendReminder | 24h, 1h, 15min before | WhatsApp message |
| ExpirePendingAppointments | Every 1 hour | Clear unconfirmed bookings |
| GenerateAvailableSlots | Every day (3 AM) | Cache next 30 days of slots |
| SendConfirmationEmail | Immediate | Email confirmation (if opted) |
| SendNoShowAlert | Appointment end time | Notify if customer no-show |
| CleanupSessions | Every 6 hours | Remove expired sessions |
| DailyReport | 8 AM (tenant's tz) | Email summary to business |

### Notification Channels
- **WhatsApp** - Primary (confirmations, reminders, cancellations)
- **Email** (optional) - Backup + confirmations for customers
- **SMS** (optional) - Last-minute reminders
- **In-app** - Dashboard notifications for staff

---

## Tech Stack Recommendation

### Backend
- **Framework**: FastAPI (Python) or Node.js + Express
- **Database**: PostgreSQL (Neon)
- **Queue**: Bull (Node) / Celery (Python)
- **WhatsApp API**: Twilio or Meta WhatsApp Business API
- **Cache**: Redis (optional, for slot caching)
- **Auth**: JWT + refresh tokens

### Frontend
- **Web Portal**: React + TypeScript + Tailwind
- **Calendar**: React Big Calendar or FullCalendar
- **Forms**: React Hook Form + Zod
- **State**: TanStack Query + Zustand

### Infrastructure
- **Hosting**: Vercel (frontend), Railway/Render (backend)
- **Database**: Neon (PostgreSQL)
- **File Storage**: AWS S3 / Cloudflare R2
- **Monitoring**: Sentry, LogRocket

---

## Implementation Phases

### Phase 1: MVP (2-3 weeks)
- Tenant registration & auth
- WhatsApp bot (basic flow: book → slot → confirm)
- Scheduling config (working hours, duration)
- Appointment storage
- Simple dashboard (list appointments)

### Phase 2: Enhanced Booking (1-2 weeks)
- Web booking page
- Service selection
- Cancellation/rescheduling
- Holiday & blocked time management
- Email confirmations

### Phase 3: Advanced Features (2-3 weeks)
- Reminders (WhatsApp + email)
- Multi-team support
- Customer notes & history
- Bulk operations
- Analytics dashboard

### Phase 4: Scale & Polish (ongoing)
- Payment integration
- SMS notifications
- Appointment notes/meeting links
- Customer portal (reschedule, cancel without WhatsApp)
- Multi-language support

---

## Security Considerations

1. **Multi-tenancy isolation**: Row-level security policies in PostgreSQL
2. **Rate limiting**: Prevent bot abuse (max slots/day per phone)
3. **Data encryption**: Encrypt customer phone/email at rest
4. **Audit logs**: Track all changes for compliance
5. **API authentication**: JWT with short expiry + refresh tokens
6. **Webhook verification**: Validate WhatsApp webhook signatures
7. **GDPR compliance**: Deletion, export, consent management
8. **PCI compliance** (if payments): Use third-party payment processors

---

## Deployment Checklist

- [ ] Neon database provisioned & migrated
- [ ] FastAPI/Express server running
- [ ] WhatsApp webhook configured
- [ ] Redis cache (if using)
- [ ] Bull queue worker running
- [ ] Frontend deployed
- [ ] SSL certificates
- [ ] Environment variables configured
- [ ] Monitoring & logging setup
- [ ] Backup strategy
- [ ] Rate limiting configured
- [ ] Error handling & alerting

