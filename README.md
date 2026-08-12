# Achievement Age

Ever wonder what it would feel like to be exactly as old, in days, as Napoleon was when he became
emperor — or as Ada Lovelace was when she published her notes on the Analytical Engine? Achievement
Age tracks your age in days and matches it against the ages of historical figures at the moments
they did something notable, so you can browse (or just get quietly notified) every day you happen
to share an age with someone from history.

**Live app:** [_add your deployed Streamlit URL here_](#)

## What it does

- **Calendar view** — browse any month, past or future, and see which days line up with a historical
  event at your exact age in days.
- **Event detail** — click a matching day to see who it was, what happened, a longer description,
  and a link to Wikipedia when one's available.
- **Tag filtering** — every event is tagged (science, military, arts, …); filter the calendar down
  to the categories you care about. If you're subscribed, your filter is saved to your private link
  and also decides which matches are worth a notification.
- **Mathematical anniversaries** — optionally also mark the days when your age in days is itself an
  interesting number: a power of two, a Fibonacci number, the 100th triangular number. Off by
  default, and each sequence can be switched on or off individually — primes are left off to begin
  with, since roughly one day in nine is one.
- **Passive notifications** — sign up once with your birthday and get a free push notification (via
  [ntfy](https://ntfy.sh)) on the actual day a match happens, without needing to check the app
  yourself. This is the main point of the project: something that runs quietly in the background
  and only interrupts you when there's something worth seeing.
- **No account needed** — enter your birthday once, get a private bookmarkable link that remembers
  it for next time. No password, no login.

## Using the app

1. Open the app and enter your birthday. You'll immediately see your current age and a calendar
   with a red circle marking any day that matches a historical event, and a triangle marking a
   mathematical anniversary if you've turned those on. Today's date is filled in black.
2. Click a ⭐ day to see the details of the match.
3. Use the ◀ ▶ arrows or the month/year dropdowns to browse other months — including ones in your
   future, so you can see what's coming up.
4. To get notified automatically instead of checking back: open "Get notified when your age matches
   an event," click the button, and you'll get a link plus a notification topic.
   - **Bookmark the link** (or add it to your phone's home screen) — it remembers your birthday, so
     you won't have to re-enter it.
   - **Install the [ntfy app](https://ntfy.sh)** (iOS/Android, free, no account) and subscribe to the
     topic name you were given.
   - From then on, you'll get a push notification whenever your age matches an event, with a link
     back to the details.

## How it works, briefly

- **Streamlit** app backed by **Supabase** (Postgres) for events, people, and subscriptions.
- A **GitHub Actions** workflow runs daily, checks every subscriber's age-in-days against the event
  database, and pushes matches via **ntfy.sh** — no server to keep running for the notification side.
- Event descriptions were reworded for display using Claude Haiku subagents run from within Claude
  Code, not a metered LLM API.

Full technical setup (Supabase schema, secrets, local dev) is in [SUPABASE_SETUP.md](SUPABASE_SETUP.md).

## Running locally

```bash
venv\Scripts\activate
pip install -e .
pip install -r requirements.txt
streamlit run src/app/ui.py
```

You'll need Supabase credentials in a local `.env` file first — see
[SUPABASE_SETUP.md](SUPABASE_SETUP.md) for the one-time setup.

Run the tests with:

```bash
pytest
```

## License

MIT — see [LICENSE](LICENSE).
