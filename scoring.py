"""
scoring.py -- two different questions about a posting, kept apart on purpose.

  internship_score(title)        Is this a real internship at all?
  fit_score(title, location)     Is it a good fit for THIS student?

The first exists because the scrapers match on the word "intern", which also
catches "Internal Audit Manager" and "International Trade Compliance" -- about
37% of the database was that kind of row at one point. The dashboard uses it
to sort the junk to the bottom.

The second is new and answers a question the first one can't: of the ~2,000
real internships in the database, which ones are worth a mechanical
engineering undergrad's attention this cycle? Used by the Discord digest to
pick the few worth pushing to a phone, and available to the dashboard for
ranking.

Both live here rather than in dashboard.py so the notifier and the dashboard
cannot drift apart.
"""
import re

# --- is it an internship? -------------------------------------------------
STRONG = re.compile(r"\b(intern|interns|internship|internships|co-op|coop|co op)\b")
FALSE_FRIEND = re.compile(r"\b(internal|international|internally|internationally)\b")
SENIORITY = re.compile(
    r"\b(manager|mgr|director|senior|sr\.?|principal|staff|supervisor|"
    r"chief|vp|president|executive|head of|trainer|recruiter|recruiting)\b")
SEASON = re.compile(r"\b(summer|fall|autumn|spring|winter)\b|\b20\d\d\b")
ADVANCED = re.compile(r"\b(phd|ph\.d|doctoral|postdoc|mba|jd|law|md)\b")
GRADUATE = re.compile(r"\b(graduate|masters|master's)\b")
RELEVANT = re.compile(
    r"\b(mechanical|manufacturing|aerospace|aeronautic|astronautic|propulsion|"
    r"structures|structural|thermal|design|test|testing|systems|materials|"
    r"robotics|avionics|mechatronic|industrial|hardware|cad|engineer|"
    r"engineering|production|quality|integration|flight|vehicle)\b")

# --- is it a fit? ---------------------------------------------------------
# Mechanical engineering and its close neighbours. A hit here is the whole
# point of the fit score.
ME_CORE = re.compile(
    r"\b(mechanical|aerospace|aeronautic|astronautic|propulsion|thermal|"
    r"fluid|fluids|aero|structures|structural|mechatronic|robotics|"
    r"manufacturing|materials|machining|cad|solidworks|gd&t|hvac|turbine|"
    r"engine|airframe|vehicle|spacecraft|launch)\b")
# Engineering-adjacent: good, but not as on-target as the list above.
ENG_WIDE = re.compile(
    r"\b(engineer|engineering|design|test|testing|integration|quality|"
    r"production|industrial|systems|hardware|r&d|research|flight|controls)\b")
# Real internships that are not this student's field. Finance and HR
# internships are still internships -- they just shouldn't reach his phone.
NON_TECH = re.compile(
    r"\b(finance|financial|accounting|audit|tax|payroll|human resources|hr|"
    r"talent|recruit|recruiting|marketing|brand|communications|social media|"
    r"sales|legal|paralegal|counsel|procurement|purchasing|merchandis|"
    r"customer service|public relations|graphic|content|policy|insurance|"
    r"real estate|nursing|clinical|pharmac|medical|veterinar|culinary)\b")
SOFTWARE = re.compile(
    r"\b(software|developer|full.?stack|front.?end|back.?end|web|cyber|"
    r"data scien|machine learning|ml|ai|cloud|devops|it\b)\b")

TARGET_SEASON = re.compile(r"\bsummer\b[^a-z0-9]{0,6}2027\b|\b2027\b[^a-z0-9]{0,6}summer\b")
TARGET_YEAR = re.compile(r"\b2027\b")
OLD_CYCLE = re.compile(r"\b(2024|2025|2026)\b")

HOME_STATE = re.compile(r"\b(fl|florida)\b|orlando|melbourne|cape canaveral|"
                        r"titusville|tampa|daytona|kennedy space|lake mary|"
                        r"palm bay|jacksonville|miami|sarasota|clearwater")
REMOTE = re.compile(r"\b(remote|virtual|work from home|anywhere)\b")


def _normalize(text):
    """Separators are word characters to regex, so "Program_Internship"
    defeats \\binternship\\b. Normalize them to spaces first."""
    return re.sub(r"[_/\\|+&–—-]+", " ", (text or "").lower())


def internship_score(title):
    """0-100ish. Higher = more likely a real internship worth your time."""
    t = _normalize(title)
    score = 0

    if STRONG.search(t):
        score += 100
    elif FALSE_FRIEND.search(t):
        return 0          # "International Trade Compliance Manager 3"

    if SENIORITY.search(t):
        score -= 70       # "Recruiting Coordinator, Intern Program"
    if ADVANCED.search(t):
        score -= 35       # PhD/MBA tracks, not a sophomore ME
    if GRADUATE.search(t):
        score -= 15
    if SEASON.search(t):
        score += 15       # "Summer 2027 ..." reads like a real cycle posting
    if RELEVANT.search(t):
        score += 12

    return max(score, 0)


def score_tier(score):
    if score >= 100:
        return "core"       # confident internship
    if score >= 40:
        return "maybe"      # internship-ish, or senior-flavoured
    return "unlikely"       # almost certainly a false positive


def fit_score(title, location=""):
    """0-100: how well this posting fits a mechanical engineering undergrad
    looking for Summer 2027 in the USA.

    Anything that isn't a confident internship scores 0 -- the fit question
    only makes sense once the internship question is settled.
    """
    if score_tier(internship_score(title)) != "core":
        return 0

    t = _normalize(title)
    loc = _normalize(location)
    score = 30                      # a real internship starts with a floor

    if ME_CORE.search(t):
        score += 40                 # mechanical/aero/manufacturing: the target
    elif ENG_WIDE.search(t):
        score += 22                 # engineering, just not his discipline
    elif SOFTWARE.search(t):
        score += 8                  # adjacent; he can do some of these
    if NON_TECH.search(t) and not ME_CORE.search(t):
        score -= 35                 # finance/HR/marketing internships

    if TARGET_SEASON.search(t):
        score += 20                 # "Summer 2027" exactly
    elif TARGET_YEAR.search(t):
        score += 12
    elif OLD_CYCLE.search(t):
        score -= 10                 # a 2026 cycle posting is late for him

    if HOME_STATE.search(loc):
        score += 12                 # UCF: Florida is commutable
    elif REMOTE.search(loc):
        score += 4

    return max(0, min(score, 100))


def fit_label(score):
    if score >= 75:
        return "strong"
    if score >= 55:
        return "good"
    if score > 0:
        return "weak"
    return "none"
