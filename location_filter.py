"""
location_filter.py
------------------
One shared US-location filter for every scraper in the monitor.

Why this exists: each scraper used to carry its own hand-written whitelist of
US keywords, so any state not on that particular list was silently dropped
(this is what hid the Winchester, Virginia posting). Worse, the international
blacklists used plain substring matching, so "uk" matched Milwaukee and
"india" matched Indianapolis.

Match order matters, and it is the whole design:

    0. US overrides        -> True   (New Mexico, before the Mexico rule)
    1. foreign COUNTRY     -> False  (absolute veto: "Remote - Germany")
    2. US state signal     -> True   (Melbourne FL, Warsaw IN, Vienna VA)
    3. foreign CITY alone  -> False  ("Melbourne", "Warsaw" with no state)
    4. remote / flexible   -> True
    5. unrecognized        -> False, and printed so it isn't silent

Step 2 sitting above step 3 is what stops a US city from being thrown out
because a foreign city shares its name. There are a lot of these and they
land on real employer sites: Melbourne FL (Northrop), Warsaw IN (J&J DePuy),
Vienna VA, Rome NY, Dublin OH, Lima OH, Bristol CT, Naples FL.

Usage in a scraper:
    from location_filter import is_us_location
    ...
    if re.search(r'\\bintern', title.lower()) and is_us_location(location):
        ...
"""

import re

DEBUG_UNKNOWN = True

# --- 0. US places that collide with a country name -------------------------
US_OVERRIDES = [
    "new mexico", "albuquerque", "las cruces", "los alamos", "santa fe",
    "roswell", "white sands", "kirtland",
]

# --- 1. Foreign countries: absolute veto -----------------------------------
INTERNATIONAL_COUNTRIES = [
    "uk", "u.k.", "united kingdom", "england", "scotland", "wales",
    "northern ireland", "ireland", "canada", "mexico", "india", "china",
    "japan", "korea", "south korea", "singapore", "taiwan", "malaysia",
    "thailand", "vietnam", "philippines", "indonesia", "australia",
    "new zealand", "germany", "deutschland", "france", "spain", "italy",
    "italia", "netherlands", "holland", "belgium", "switzerland", "austria",
    "sweden", "norway", "denmark", "finland", "poland", "polska",
    "czech republic", "czechia", "slovakia", "hungary", "romania",
    "bulgaria", "portugal", "greece", "turkey", "israel",
    "united arab emirates", "uae", "saudi arabia", "qatar", "egypt",
    "south africa", "nigeria", "kenya", "brazil", "brasil", "argentina",
    "chile", "colombia", "peru", "costa rica", "panama", "russia", "ukraine",
]

# Country / province codes, matched uppercase against the raw string.
# Codes that collide with US state abbreviations (AR CO DE IL ID LA MS IN OR)
# are deliberately absent -- those countries are caught by name above.
INTERNATIONAL_CODES = [
    "GB", "IE", "FR", "ES", "IT", "NL", "BE", "CH", "AT", "SE", "NO",
    "DK", "FI", "PL", "CZ", "HU", "RO", "BG", "PT", "GR", "TR",
    "AE", "QA", "EG", "ZA", "NG", "KE", "BR", "CL", "PE",
    "MX", "JP", "KR", "SG", "TW", "TH", "VN", "PH", "MY", "NZ", "AU",
    "CN", "HK", "ON", "QC", "BC", "MB", "NS", "NB",
]

# --- 2. US signals: all 50 states, DC, territories -------------------------
US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "district of columbia", "washington dc",
    "washington d.c.", "puerto rico", "guam", "virgin islands",
    "american samoa",
]

US_STATE_CODES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "GU", "VI",
]

US_COUNTRY_SIGNALS = [
    "united states", "usa", "u.s.a.", "u.s.", "us -", "- us", "america",
]

# Cities and metro phrasings that carry no state, e.g. "Greater Seattle Area",
# "Bay Area", "Cape Canaveral". Word-boundary matching means a bare city name
# here also catches the "Greater X Area" wrapper for free.
#
# Deliberately EXCLUDED because they're ambiguous with foreign cities of the
# same name: Birmingham, Cambridge, Manchester, London, Ontario, Athens,
# Naples. Those need a state to be counted, which is the safe default.
US_CITIES = [
    # Pacific NW
    "seattle", "bellevue", "redmond", "everett", "renton", "kent wa",
    "tukwila", "spokane", "portland", "beaverton", "hillsboro",
    # California
    "los angeles", "hawthorne", "el segundo", "long beach", "torrance",
    "redondo beach", "pasadena", "burbank", "glendale", "santa monica",
    "culver city", "irvine", "anaheim", "san diego", "san francisco",
    "san jose", "sunnyvale", "palo alto", "mountain view", "silicon valley",
    "bay area", "sacramento", "fresno", "mojave", "vandenberg", "palmdale",
    "lancaster", "san luis obispo", "goleta", "santa barbara",
    # Florida
    "cape canaveral", "merritt island", "titusville", "kennedy space center",
    "cocoa", "orlando", "tampa", "jacksonville", "miami", "palm bay",
    "fort lauderdale", "st. petersburg", "clearwater", "daytona",
    # Texas
    "houston", "dallas", "fort worth", "ft. worth", "austin", "san antonio",
    "mcgregor", "boca chica", "brownsville", "van horn", "el paso", "waco",
    "midland", "odessa", "corpus christi", "richardson", "plano",
    # Mountain / Southwest
    "denver", "colorado springs", "littleton", "boulder", "aurora co",
    "phoenix", "mesa", "chandler", "tempe", "tucson", "las vegas", "reno",
    "salt lake city", "ogden", "provo", "boise",
    # Midwest
    "chicago", "detroit", "dearborn", "ann arbor", "grand rapids",
    "minneapolis", "st. paul", "saint paul", "milwaukee", "madison",
    "indianapolis", "columbus", "cleveland", "cincinnati", "dayton",
    "st. louis", "saint louis", "kansas city", "wichita", "omaha",
    "des moines", "tulsa", "oklahoma city",
    # South
    "huntsville", "birmingham al", "atlanta", "savannah", "charlotte",
    "raleigh", "durham", "greensboro", "nashville", "memphis", "knoxville",
    "louisville", "lexington ky", "new orleans", "baton rouge", "jackson ms",
    "charleston", "columbia sc", "greenville",
    # Northeast / Mid-Atlantic
    "new york city", "nyc", "brooklyn", "queens ny", "long island",
    "philadelphia", "pittsburgh", "harrisburg", "baltimore", "annapolis",
    "boston", "worcester", "springfield ma", "hartford", "new haven",
    "providence", "portsmouth nh", "arlington va", "alexandria va",
    "reston", "herndon", "chantilly", "mclean", "tysons", "fairfax",
    "quantico", "norfolk", "hampton roads", "newport news", "richmond va",
    "dmv area", "national capital region",
]

# --- 3. Foreign cities: only consulted if no US state was found ------------
INTERNATIONAL_CITIES = [
    "london", "bristol", "manchester", "birmingham", "glasgow", "edinburgh",
    "dublin", "belfast", "toronto", "montreal", "ottawa", "vancouver",
    "calgary", "winnipeg", "mississauga", "brampton", "guadalajara",
    "monterrey", "tijuana", "juarez", "queretaro", "bangalore", "bengaluru",
    "hyderabad", "pune", "chennai", "mumbai", "gurgaon", "gurugram", "noida",
    "shanghai", "beijing", "shenzhen", "suzhou", "hong kong", "tokyo",
    "osaka", "seoul", "taipei", "kuala lumpur", "penang", "bangkok",
    "manila", "jakarta", "ho chi minh", "hanoi", "sydney", "melbourne",
    "brisbane", "adelaide", "perth", "canberra", "auckland", "wellington",
    "christchurch", "mahia", "warkworth", "munich", "münchen", "berlin",
    "frankfurt", "hamburg", "stuttgart", "erlangen", "nuremberg",
    "nürnberg", "düsseldorf", "cologne", "köln", "paris", "toulouse",
    "lyon", "marseille", "madrid", "barcelona", "milan", "milano", "rome",
    "roma", "torino", "amsterdam", "eindhoven", "rotterdam", "brussels",
    "antwerp", "zurich", "zürich", "geneva", "basel", "vienna", "wien",
    "stockholm", "gothenburg", "oslo", "copenhagen", "helsinki", "warsaw",
    "warszawa", "krakow", "kraków", "wroclaw", "gdansk", "prague", "brno",
    "bratislava", "budapest", "bucharest", "cluj", "sofia", "lisbon",
    "porto", "athens", "istanbul", "ankara", "tel aviv", "haifa", "dubai",
    "abu dhabi", "riyadh", "doha", "cairo", "johannesburg", "cape town",
    "lagos", "nairobi", "sao paulo", "são paulo", "rio de janeiro",
    "buenos aires", "santiago", "bogota", "bogotá", "moscow", "kyiv", "kiev",
]

REMOTE_SIGNALS = [
    "remote", "flexible", "virtual", "work from home", "telework",
    "anywhere", "multiple locations", "nationwide", "various locations",
]


def _word_match(needles, haystack):
    """True if any needle appears in haystack on word boundaries."""
    for needle in needles:
        if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack):
            return True
    return False


def _code_match(codes, raw):
    """Match uppercase 2-letter codes against the original-case string."""
    for code in codes:
        if re.search(r"(?<![A-Za-z])" + code + r"(?![A-Za-z])", raw):
            return True
    return False


def _has_us_signal(raw, loc):
    return (
        _word_match(US_COUNTRY_SIGNALS, loc)
        or _word_match(US_STATE_NAMES, loc)
        or _code_match(US_STATE_CODES, raw)
        or _word_match(US_CITIES, loc)
    )


def is_us_location(location_name, company=""):
    """Return True if the location looks like it is in the United States."""
    if not location_name:
        return False

    raw = str(location_name).strip()
    loc = raw.lower()

    # 0. US places whose names collide with a country
    if _word_match(US_OVERRIDES, loc):
        return True

    # 1. Foreign country named outright -- absolute veto
    if _word_match(INTERNATIONAL_COUNTRIES, loc):
        return False
    if _code_match(INTERNATIONAL_CODES, raw) and not _has_us_signal(raw, loc):
        return False

    # 2. Any US state / country signal
    if _has_us_signal(raw, loc):
        return True

    # 3. Foreign city with no US state attached
    if _word_match(INTERNATIONAL_CITIES, loc):
        return False

    # 4. Remote / flexible
    if _word_match(REMOTE_SIGNALS, loc):
        return True

    # 5. Nothing matched -- say so instead of dropping it silently
    if DEBUG_UNKNOWN:
        tag = f"[{company}] " if company else ""
        print(f"{tag}location not recognized, skipping: {raw!r}")
    return False


# ===========================================================================
# TITLE FILTER
# ===========================================================================
# Lives here rather than in 15 separate copies, for the same reason the
# location filter does: one definition means one place to fix.
#
# The old test was re.search(r'\bintern', title.lower()). \b marks the START
# of a word but nothing marked the END, so it accepted every title with a word
# beginning i-n-t-e-r-n:
#     "IT Internal Audit Manager"        -> Internal
#     "International Project Manager 4"  -> International
# That was 23 of the 63 rows in the live database. The closing \b fixes it.

INTERNSHIP_TITLE = re.compile(
    r"\b(intern|interns|internship|internships|coop|coops|co op|co ops)\b"
)


def is_internship_title(title):
    """True if the title is really an internship, not merely 'International'."""
    if not title:
        return False
    # Separators are word characters to regex, so "Program_Internship" and
    # "Internship/Co-op" defeat \b. Normalize them to spaces first.
    t = re.sub(r"[_/\\|+&–—-]+", " ", title.lower())
    return bool(INTERNSHIP_TITLE.search(t))


# ===========================================================================
# SENIORITY FILTER (for facet-sourced postings)
# ===========================================================================
# When a Workday facet says "Intern/Co-op", we skip the title check so that
# untitled-but-real internships (e.g. "Student Trainee") get through. But some
# employers file advanced roles under the same subtype: J&J tags Postdoctoral
# Scholars and a Principal Data Engineer as Intern/Co-op, because a postdoc is
# technically a fixed-term training position.
#
# So facet-sourced postings still get a negative check: keep anything that
# isn't obviously senior or post-graduate.

SENIOR_TITLE = re.compile(
    r"\b(post ?doc|post ?doctoral|postdoctorate|principal|senior|sr|staff|"
    r"director|manager|supervisor|head|chief|vp|president|executive|"
    r"distinguished|professor|attending|resident physician)\b"
)

EARLY_CAREER = re.compile(
    r"\b(intern|interns|internship|internships|coop|coops|co op|co ops|"
    r"student|students|trainee|apprentice|apprenticeship|undergraduate|"
    r"sophomore|freshman|junior year|rotational)\b"
)


def is_plausible_internship(title):
    """
    For postings the employer already tagged as Intern/Co-op.

    Explicit early-career wording wins outright; otherwise anything that reads
    as senior or post-doctoral is rejected. Deliberately more permissive than
    is_internship_title() -- the employer's tag is evidence, just not proof.
    """
    if not title:
        return False
    t = re.sub(r"[_/\\|+&–—-]+", " ", title.lower())

    if EARLY_CAREER.search(t) and not re.search(r"\bpost ?doc", t):
        return True
    if SENIOR_TITLE.search(t):
        return False
    return True