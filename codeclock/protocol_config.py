"""Protocol constants — every timing and dose the engine uses lives HERE.

Clinicians: this is the file to correct. All intervals are in seconds, doses
in mg. VERIFY against current AHA guidelines and the team on the floor before
any demo.
"""

# --- Rhythm / pulse checks -------------------------------------------------
RHYTHM_CHECK_INTERVAL_S = 120     # check roughly every 2 minutes
RHYTHM_CHECK_WARN_S = 105         # heads-up shortly before due

# --- Epinephrine -----------------------------------------------------------
EPI_DUE_S = 180                   # 1 mg every 3-5 min: "due" at 3 min
EPI_OVERDUE_S = 300               # "overdue" past 5 min
EPI_MIN_GAP_S = 120               # doses closer than this = double-dose alert

# --- Amiodarone (refractory shockable rhythm) ------------------------------
AMIODARONE_FIRST_MG = 300.0
AMIODARONE_SECOND_MG = 150.0
AMIODARONE_MAX_TOTAL_MG = 450.0   # dose ceiling — alert if exceeded
REFRACTORY_SHOCK_COUNT = 2        # shocks on a shockable rhythm before amio is suggested

# --- Rhythm classification -------------------------------------------------
SHOCKABLE_RHYTHMS = {"vf", "pvt"}
NON_SHOCKABLE_RHYTHMS = {"asystole", "pea", "organized"}

RHYTHM_SYNONYMS = {
    "vf": "vf", "v-fib": "vf", "vfib": "vf", "ventricular fibrillation": "vf",
    "pvt": "pvt", "pulseless vt": "pvt", "pulseless v-tach": "pvt",
    "v-tach": "pvt", "vt": "pvt", "ventricular tachycardia": "pvt",
    "asystole": "asystole", "flatline": "asystole",
    "pea": "pea", "pulseless electrical activity": "pea",
    "organized rhythm": "organized", "organized": "organized",
    "sinus rhythm": "organized", "sinus": "organized",
}

MED_SYNONYMS = {
    "epi": "epinephrine", "epinephrine": "epinephrine", "adrenaline": "epinephrine",
    "amio": "amiodarone", "amiodarone": "amiodarone",
}
