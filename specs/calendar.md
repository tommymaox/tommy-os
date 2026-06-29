# Calendar Tab — Spec

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/events?start=YYYY-MM-DD&end=YYYY-MM-DD` | List events in date range |
| POST | `/api/events` | Create new event |
| PUT | `/api/events/{id}` | Update event (partial) |
| DELETE | `/api/events/{id}` | Delete event |

---

## Data Model

```sql
CREATE TABLE events (
  id TEXT PRIMARY KEY,          -- uuid4
  title TEXT NOT NULL,
  date TEXT NOT NULL,           -- YYYY-MM-DD
  type TEXT DEFAULT 'event',    -- see event types below
  start_time TEXT,              -- HH:MM (24hr, optional)
  end_time TEXT,                -- HH:MM (24hr, optional)
  notes TEXT,
  color TEXT,                   -- hex override (optional)
  created_at TEXT,
  updated_at TEXT
);
```

---

## Event Types

| ID | Label | Color | Icon |
|---|---|---|---|
| `run` | Run | `#00c98d` | 🏃 |
| `gym` | Gym | `#635bff` | 💪 |
| `tempo` | Tempo | `#ff8c42` | ⚡ |
| `long` | Long Run | `#3b9eff` | 🛣️ |
| `event` | Event | `#f5c842` | 📌 |
| `task` | Task | `#7878a0` | ✓ |

To add a new type: edit `EVENT_TYPES` object in `static/index.html` `<script>` block.

---

## Training Schedule (Client-side)

Defined in `static/index.html` as `TRAINING_SCHEDULE` — a map of `dayOfWeek (0=Sun)` → sessions.
These render as dashed/semi-transparent pills and are **not stored in the database**.

```js
const TRAINING_SCHEDULE = {
  1: [ // Monday
    { title: 'Push — Chest & Shoulders', type: 'gym', start_time: '17:15', end_time: '18:45' }
  ],
  2: [ // Tuesday
    { title: 'Easy Run 6km', type: 'run', start_time: '07:00', end_time: '07:35' },
    { title: 'Pull — Back & Biceps', type: 'gym', start_time: '17:15', end_time: '18:30' }
  ],
  3: [ // Wednesday
    { title: 'Easy Run 6km', type: 'run', start_time: '07:00', end_time: '07:35' }
  ],
  4: [ // Thursday
    { title: 'Tempo Run (3+5+3km)', type: 'tempo', start_time: '17:15', end_time: '18:15' },
    { title: 'Legs', type: 'gym', start_time: '19:30', end_time: '20:45' }
  ],
  5: [ // Friday
    { title: 'Push — Chest & Shoulders', type: 'gym', start_time: '17:15', end_time: '18:45' }
  ],
  6: [ // Saturday
    { title: 'Easy Run 6km', type: 'run', start_time: '07:00', end_time: '07:35' },
    { title: 'Pull — Back & Biceps', type: 'gym', start_time: '09:30', end_time: '11:00' }
  ],
  0: [ // Sunday
    { title: 'Long Run 15km', type: 'long', start_time: '07:00', end_time: '08:30' }
  ]
};
```

To modify: edit this object directly in the `<script>` block of `static/index.html`.

---

## Views

### Month View (default)
- 7-column grid, Monday–Sunday
- Each cell shows: training schedule pills (dashed) + custom events (solid)
- Clicking a day opens the Day Panel

### Week View
- Time axis 6am–11pm, 60px per hour
- Training sessions shown as positioned blocks (dashed)
- Custom events shown as solid blocks
- Clicking a time slot opens Add Event modal with date/time prefilled

---

## Day Panel

Slides in from the right (desktop) or bottom (mobile) when a day is clicked.

- **Training Schedule** section — read-only, from `TRAINING_SCHEDULE`
- **Events** section — custom events with edit ✏️ and delete 🗑 buttons
- **Add Event** button — opens modal pre-filled with selected date

---

## Add/Edit Modal

Fields:
- Title (required)
- Type (pill selector)
- Date (pre-filled)
- Start time, End time (optional)
- Notes (optional)

On save → POST or PUT to API → updates local state → re-renders calendar.
