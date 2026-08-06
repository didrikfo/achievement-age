# Achievement Age App

## To run ##
Data now lives in Supabase, not local JSON files — see [SUPABASE_SETUP.md](SUPABASE_SETUP.md) for one-time
setup (create the tables, run the migration, configure secrets) before the app will start.

Windows command prompt:
    venv\Scripts\activate
    pip install -e .
    pip install -r requirements.txt
    streamlit run src/app/ui.py