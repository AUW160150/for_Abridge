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

# --- Code stroke (AHA/ASA targets) -----------------------------------------
STROKE_CT_DUE_S = 25 * 60          # door-to-CT target <= 25 min
STROKE_CT_WARN_S = 15 * 60
STROKE_NEEDLE_DUE_S = 60 * 60      # door-to-needle target <= 60 min
STROKE_NEEDLE_WARN_S = 30 * 60
STROKE_NEEDLE_LATE_S = 45 * 60     # escalate as the 60-min mark approaches
STROKE_LKW_PROMPT_S = 10 * 60      # prompt to establish LKW if undocumented
STROKE_TPA_WINDOW_S = int(4.5 * 3600)  # thrombolytic window from last known well

THROMBOLYTICS = {"tpa", "alteplase", "tenecteplase", "tnk", "thrombolytic"}
CT_KEYWORDS = {"ct", "cat scan"}
LKW_KEYWORDS = {"last known well", "lkw"}

# --- Rubric activation evidence (matched against entity + utterance text) ---
RUBRIC_ACTIVATION_KEYWORDS = {
    "acls_cardiac_arrest": (
        "code blue", "cardiac arrest", "pulseless", "no pulse",
        "starting compressions", "start compressions", "cpr",
        "v-fib", "asystole", "pea",
    ),
    "stroke_code": (
        "code stroke", "stroke alert", "stroke code", "facial droop",
        "last known well", "nihss", "thrombolysis", "tpa", "alteplase",
        "tenecteplase", "thrombectomy",
    ),
}
