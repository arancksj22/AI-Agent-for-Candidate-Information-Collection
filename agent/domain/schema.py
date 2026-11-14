"""Field rules, enums and alias tables, taken from GET /v1/schema."""
from __future__ import annotations

MANDATORY_FIELDS = [
    "full_name", "phone", "email", "college", "degree", "branch", "graduation_year",
    "cgpa", "preferred_location", "available_from", "internship_duration_months",
]
OPTIONAL_FIELDS = ["linkedin_url", "github_url", "skills"]

# Fields the agent may write with candidate data. full_name is always present and phone is read-only.
COLLECTABLE_FIELDS = [f for f in MANDATORY_FIELDS if f not in ("full_name", "phone")] + OPTIONAL_FIELDS

# Suggested asking order: easy and impersonal first, logistics next, sensitive last.
ASK_PRIORITY = [
    "email", "college", "degree", "branch", "graduation_year",
    "preferred_location", "available_from", "internship_duration_months", "cgpa",
]

DEGREES = ["B.Tech", "B.E.", "B.Sc", "BCA", "MBA Tech", "MCA", "M.Tech", "Other"]
LOCATIONS = ["Mumbai", "Bengaluru", "Remote", "No preference"]
DURATIONS = [2, 3, 6]
STATUSES = ["pending", "in_progress", "completed", "no_answer", "refused", "needs_human"]

GRAD_YEAR_MIN, GRAD_YEAR_MAX = 2026, 2028
AVAILABLE_MIN, AVAILABLE_MAX = "2026-01-01", "2027-12-31"
NOTES_MAX = 4000

# Keys are compared after lowercasing and stripping everything except letters/digits
# ("B. Tech." → "btech", "work from home" → "workfromhome"). Only unambiguous spellings are listed:
# anything that could mean two options (e.g. "ME", "MSc", "BBA") is left to the LLM, which asks or uses
# "Other" with a note.
DEGREE_ALIASES = {
    # B.Tech
    "btech": "B.Tech", "btec": "B.Tech", "btek": "B.Tech", "bteck": "B.Tech", "btch": "B.Tech",
    "btechh": "B.Tech", "bachtech": "B.Tech", "bachelortech": "B.Tech", "bacheloroftech": "B.Tech",
    "bacheloroftechnology": "B.Tech", "bachelorsoftechnology": "B.Tech", "bachelorintechnology": "B.Tech",
    "bachelorstechnology": "B.Tech", "btechhons": "B.Tech", "btechhonours": "B.Tech",
    "btechhonors": "B.Tech", "btechcse": "B.Tech", "btechit": "B.Tech", "btechcs": "B.Tech",
    "btechece": "B.Tech", "btechmech": "B.Tech", "btechdualdegree": "B.Tech", "btechlateral": "B.Tech",
    "lateralentrybtech": "B.Tech", "bteh": "B.Tech", "vtech": "B.Tech",
    # B.E.
    "be": "B.E.", "beng": "B.E.", "bengg": "B.E.", "bachelorofengineering": "B.E.",
    "bachelorsofengineering": "B.E.", "bachelorinengineering": "B.E.", "bachelorsinengineering": "B.E.",
    "bachelorengineering": "B.E.", "bachelorsengineering": "B.E.", "behons": "B.E.", "becse": "B.E.",
    "becomps": "B.E.", "beit": "B.E.", "beextc": "B.E.", "bemech": "B.E.", "becivil": "B.E.",
    "becomputer": "B.E.", "becomputers": "B.E.", "beengineering": "B.E.", "engineeringdegree": "B.E.",
    # B.Sc
    "bsc": "B.Sc", "bsci": "B.Sc", "bs": "B.Sc", "bachelorofscience": "B.Sc",
    "bachelorsofscience": "B.Sc", "bachelorinscience": "B.Sc", "bachelorsinscience": "B.Sc",
    "bscit": "B.Sc", "bsccs": "B.Sc", "bsccomputerscience": "B.Sc", "bschons": "B.Sc",
    "bscdatascience": "B.Sc", "bscmaths": "B.Sc", "bscmath": "B.Sc", "bscphysics": "B.Sc",
    "bscstats": "B.Sc", "bscinformationtechnology": "B.Sc",
    # BCA
    "bca": "BCA", "bachelorofcomputerapplications": "BCA", "bachelorofcomputerapplication": "BCA",
    "bachelorsofcomputerapplications": "BCA", "bachelorincomputerapplications": "BCA",
    "bachelorcomputerapplications": "BCA", "bcahons": "BCA",
    # MBA Tech
    "mbatech": "MBA Tech", "mbatec": "MBA Tech", "mbatek": "MBA Tech", "mbatechintegrated": "MBA Tech",
    "integratedmbatech": "MBA Tech", "btechmba": "MBA Tech", "btechmbaintegrated": "MBA Tech",
    "mbatechnology": "MBA Tech", "mbatechnologymanagement": "MBA Tech", "mbatechdual": "MBA Tech",
    "techmba": "MBA Tech", "btechplusmba": "MBA Tech", "mbatechnmims": "MBA Tech",
    # MCA
    "mca": "MCA", "masterofcomputerapplications": "MCA", "masterofcomputerapplication": "MCA",
    "mastersofcomputerapplications": "MCA", "mastersincomputerapplications": "MCA",
    "mastercomputerapplications": "MCA", "integratedmca": "MCA",
    # M.Tech
    "mtech": "M.Tech", "mtec": "M.Tech", "mtek": "M.Tech", "mteck": "M.Tech", "mtch": "M.Tech",
    "masteroftechnology": "M.Tech", "mastersoftechnology": "M.Tech", "masterintechnology": "M.Tech",
    "mastersintechnology": "M.Tech", "mastertech": "M.Tech", "masterstech": "M.Tech",
    "mtechcse": "M.Tech", "mtechcs": "M.Tech", "mtechai": "M.Tech", "integratedmtech": "M.Tech",
    "btechmtech": "M.Tech", "btechmtechdual": "M.Tech",
    # Other
    "other": "Other", "others": "Other", "somethingelse": "Other", "none": "Other",
}

# Common short forms of branches. Values are stored as given unless the whole answer is one of these
# keys ("comp eng" → "Computer Engineering"); real names like "Mechatronics" pass through unchanged.
BRANCH_ALIASES = {
    # computer
    "cs": "Computer Science", "cse": "Computer Science and Engineering",
    "csengg": "Computer Science and Engineering", "cseng": "Computer Science and Engineering",
    "computerscience": "Computer Science", "compsci": "Computer Science", "compscience": "Computer Science",
    "computersci": "Computer Science", "comsci": "Computer Science",
    "computerscienceengineering": "Computer Science and Engineering",
    "computerscienceandengineering": "Computer Science and Engineering",
    "ce": "Computer Engineering", "comp": "Computer Engineering", "comps": "Computer Engineering",
    "compeng": "Computer Engineering", "compengg": "Computer Engineering", "compengineering": "Computer Engineering",
    "computereng": "Computer Engineering", "computerengg": "Computer Engineering",
    "computer": "Computer Engineering", "computers": "Computer Engineering",
    "computerengineering": "Computer Engineering", "coe": "Computer Engineering",
    "it": "Information Technology", "infotech": "Information Technology",
    "informationtechnology": "Information Technology", "infotechnology": "Information Technology",
    "aiml": "Artificial Intelligence and Machine Learning", "aiandml": "Artificial Intelligence and Machine Learning",
    "csaiml": "Computer Science (AI and ML)", "cseaiml": "Computer Science (AI and ML)",
    "ai": "Artificial Intelligence", "artificialintelligence": "Artificial Intelligence",
    "aids": "Artificial Intelligence and Data Science", "aianddatascience": "Artificial Intelligence and Data Science",
    "ml": "Machine Learning", "machinelearning": "Machine Learning",
    "ds": "Data Science", "datascience": "Data Science", "csds": "Computer Science (Data Science)",
    "csedatascience": "Computer Science (Data Science)",
    "cybersecurity": "Cyber Security", "cybersec": "Cyber Security", "cyber": "Cyber Security",
    "csecybersecurity": "Computer Science (Cyber Security)", "iot": "Internet of Things",
    "internetofthings": "Internet of Things", "softwareengineering": "Software Engineering",
    "se": "Software Engineering", "ca": "Computer Applications", "computerapplications": "Computer Applications",
    "csbs": "Computer Science and Business Systems", "computerscienceandbusinesssystems":
        "Computer Science and Business Systems", "cloudcomputing": "Cloud Computing",
    "blockchain": "Blockchain Technology", "dataanalytics": "Data Analytics",
    # electronics / electrical
    "ece": "Electronics and Communication Engineering", "ec": "Electronics and Communication Engineering",
    "electronicsandcommunication": "Electronics and Communication Engineering",
    "electronicsandcommunicationengineering": "Electronics and Communication Engineering",
    "entc": "Electronics and Telecommunication Engineering", "extc": "Electronics and Telecommunication Engineering",
    "etc": "Electronics and Telecommunication Engineering", "electronicsandtelecommunication":
        "Electronics and Telecommunication Engineering", "electronicsandtelecom": "Electronics and Telecommunication Engineering",
    "electronicstelecommunication": "Electronics and Telecommunication Engineering",
    "eee": "Electrical and Electronics Engineering", "electricalandelectronics": "Electrical and Electronics Engineering",
    "electricalandelectronicsengineering": "Electrical and Electronics Engineering",
    "ee": "Electrical Engineering", "electrical": "Electrical Engineering", "elec": "Electrical Engineering",
    "electricalengineering": "Electrical Engineering", "electricalengg": "Electrical Engineering",
    "electronics": "Electronics Engineering", "electronicsengineering": "Electronics Engineering",
    "etrx": "Electronics Engineering", "elex": "Electronics Engineering", "eie": "Electronics and Instrumentation Engineering",
    "instrumentation": "Instrumentation Engineering", "ic": "Instrumentation and Control Engineering",
    "vlsi": "VLSI Design", "mechatronics": "Mechatronics", "mechtronics": "Mechatronics",
    "robotics": "Robotics", "roboticsandautomation": "Robotics and Automation",
    # core
    "mech": "Mechanical Engineering", "mechanical": "Mechanical Engineering", "me": "Mechanical Engineering",
    "mechanicalengineering": "Mechanical Engineering", "mechengg": "Mechanical Engineering",
    "mecheng": "Mechanical Engineering", "civil": "Civil Engineering", "civilengineering": "Civil Engineering",
    "civilengg": "Civil Engineering", "chem": "Chemical Engineering", "chemical": "Chemical Engineering",
    "chemicalengineering": "Chemical Engineering", "chemengg": "Chemical Engineering",
    "biotech": "Biotechnology", "biotechnology": "Biotechnology", "bt": "Biotechnology",
    "biomed": "Biomedical Engineering", "biomedical": "Biomedical Engineering",
    "aero": "Aerospace Engineering", "aerospace": "Aerospace Engineering", "aeronautical": "Aeronautical Engineering",
    "aeronauticalengineering": "Aeronautical Engineering", "auto": "Automobile Engineering",
    "automobile": "Automobile Engineering", "automotive": "Automobile Engineering",
    "production": "Production Engineering", "industrial": "Industrial Engineering",
    "metallurgy": "Metallurgical Engineering", "metallurgical": "Metallurgical Engineering",
    "textile": "Textile Engineering", "mining": "Mining Engineering", "petroleum": "Petroleum Engineering",
    "marine": "Marine Engineering", "agri": "Agricultural Engineering", "agricultural": "Agricultural Engineering",
    "environmental": "Environmental Engineering", "foodtech": "Food Technology", "foodtechnology": "Food Technology",
    # science / other
    "maths": "Mathematics", "math": "Mathematics", "mathematics": "Mathematics", "mnc": "Mathematics and Computing",
    "mathsandcomputing": "Mathematics and Computing", "mathematicsandcomputing": "Mathematics and Computing",
    "physics": "Physics", "chemistry": "Chemistry", "stats": "Statistics", "statistics": "Statistics",
    "engineeringphysics": "Engineering Physics", "ep": "Engineering Physics",
}

LOCATION_ALIASES = {
    # Mumbai (city names, MMR suburbs, common misspellings)
    "mumbai": "Mumbai", "bombay": "Mumbai", "mumbay": "Mumbai", "mumabi": "Mumbai", "mumbaii": "Mumbai",
    "mubai": "Mumbai", "mumba": "Mumbai", "bom": "Mumbai", "mum": "Mumbai", "bby": "Mumbai",
    "navimumbai": "Mumbai", "newmumbai": "Mumbai", "thane": "Mumbai", "andheri": "Mumbai", "bandra": "Mumbai",
    "powai": "Mumbai", "borivali": "Mumbai", "malad": "Mumbai", "goregaon": "Mumbai", "dadar": "Mumbai",
    "kurla": "Mumbai", "vashi": "Mumbai", "kharghar": "Mumbai", "panvel": "Mumbai", "ghatkopar": "Mumbai",
    "vileparle": "Mumbai", "juhu": "Mumbai", "colaba": "Mumbai", "churchgate": "Mumbai", "worli": "Mumbai",
    "lowerparel": "Mumbai", "bkc": "Mumbai", "kandivali": "Mumbai", "mulund": "Mumbai", "chembur": "Mumbai",
    "dombivli": "Mumbai", "kalyan": "Mumbai", "mirabhayandar": "Mumbai", "vasai": "Mumbai", "virar": "Mumbai",
    "santacruz": "Mumbai", "mumbaicity": "Mumbai", "mumbaioffice": "Mumbai", "mmr": "Mumbai",
    "mumbaimetropolitanregion": "Mumbai", "mumbaimaharashtra": "Mumbai",
    # Bengaluru
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "blr": "Bengaluru", "bengalore": "Bengaluru",
    "banglore": "Bengaluru", "bangaluru": "Bengaluru", "banglaore": "Bengaluru", "bangalor": "Bengaluru",
    "bengalooru": "Bengaluru", "bengluru": "Bengaluru", "benguluru": "Bengaluru", "bangaloru": "Bengaluru",
    "bnglr": "Bengaluru", "blore": "Bengaluru", "bang": "Bengaluru", "bengaluruoffice": "Bengaluru",
    "bangaloreoffice": "Bengaluru", "bengalurucity": "Bengaluru", "bangalorecity": "Bengaluru",
    "koramangala": "Bengaluru", "whitefield": "Bengaluru", "indiranagar": "Bengaluru",
    "electroniccity": "Bengaluru", "hsrlayout": "Bengaluru", "hsr": "Bengaluru", "marathahalli": "Bengaluru",
    "jayanagar": "Bengaluru", "hebbal": "Bengaluru", "yelahanka": "Bengaluru", "btm": "Bengaluru",
    "btmlayout": "Bengaluru", "bellandur": "Bengaluru", "sarjapur": "Bengaluru", "jpnagar": "Bengaluru",
    "mgroad": "Bengaluru", "bangalorekarnataka": "Bengaluru", "bengalurukarnataka": "Bengaluru",
    "siliconvalleyofindia": "Bengaluru",
    # Remote
    "remote": "Remote", "remotely": "Remote", "remoteonly": "Remote", "fullyremote": "Remote",
    "remotework": "Remote", "remotejob": "Remote", "remoteinternship": "Remote", "wfh": "Remote",
    "workfromhome": "Remote", "fromhome": "Remote", "home": "Remote", "athome": "Remote", "online": "Remote",
    "virtual": "Remote", "virtually": "Remote", "wfa": "Remote", "workfromanywhere": "Remote",
    "gharse": "Remote", "gharsekaam": "Remote", "gharsehi": "Remote", "gharpe": "Remote",
    "gharbaithe": "Remote", "remoteplease": "Remote", "remotepreferred": "Remote",
    # No preference
    "nopreference": "No preference", "noprefernce": "No preference", "nopref": "No preference",
    "nopreferences": "No preference", "nopreferance": "No preference", "nopreferrence": "No preference",
    "anywhere": "No preference", "anyplace": "No preference", "anylocation": "No preference",
    "any": "No preference", "anyone": "No preference", "anyofthem": "No preference",
    "anyofthese": "No preference", "anyoption": "No preference", "anyisfine": "No preference",
    "anyworks": "No preference", "anythingworks": "No preference", "anythingisfine": "No preference",
    "either": "No preference", "eitherisfine": "No preference", "eitherworks": "No preference",
    "both": "No preference", "bothareok": "No preference", "bothfine": "No preference",
    "dontmind": "No preference", "idontmind": "No preference", "doesntmatter": "No preference",
    "doesnotmatter": "No preference", "notparticular": "No preference", "flexible": "No preference",
    "imflexible": "No preference", "open": "No preference", "openforall": "No preference",
    "opentoall": "No preference", "opentoanything": "No preference", "whatever": "No preference",
    "whicheverisfine": "No preference", "whereveryouwant": "No preference", "wherever": "No preference",
    "koibhi": "No preference", "kahinbhi": "No preference", "kuchbhi": "No preference",
    "kahibhi": "No preference", "jahabhi": "No preference", "chalega": "No preference",
    "sabchalega": "No preference", "koifaraknahi": "No preference", "farknahipadta": "No preference",
}

# Month counts in words (English and Hinglish), used by the duration normaliser.
DURATION_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a couple of": 2, "couple of": 2, "couple": 2, "a couple": 2,
    "a quarter": 3, "one quarter": 3, "quarter": 3, "two quarters": 6,
    "half a year": 6, "half year": 6, "half-year": 6, "six-month": 6, "semester": 6, "a semester": 6,
    "a year": 12, "full year": 12, "one year": 12, "1 year": 12,
    # Hinglish
    "ek mahina": 1, "do mahine": 2, "do mahina": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "chhah": 6, "chhey": 6, "chah": 6, "chheh": 6, "chha": 6,
    "saat": 7, "saath": 7, "aath": 8, "nau": 9, "das": 10, "baarah": 12, "barah": 12,
    "aadha saal": 6, "adha saal": 6, "aadhe saal": 6, "ek saal": 12,
}

COMMON_EMAIL_DOMAINS = [
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com",
    "rocketmail.com", "outlook.com", "outlook.in", "hotmail.com", "hotmail.co.in", "live.com",
    "live.in", "msn.com", "icloud.com", "me.com", "mac.com", "rediffmail.com", "rediff.com",
    "protonmail.com", "proton.me", "pm.me", "zoho.com", "zohomail.in", "aol.com", "gmx.com",
    "mail.com", "yandex.com", "tutanota.com", "fastmail.com", "hey.com",
    # Indian college domains seen often in applications
    "nmims.edu.in", "nmims.in", "svkmmumbai.onmicrosoft.com", "iitb.ac.in", "iitd.ac.in",
    "vit.edu", "vitstudent.ac.in", "srmist.edu.in", "manipal.edu", "learner.manipal.edu",
    "bits-pilani.ac.in", "pilani.bits-pilani.ac.in", "christuniversity.in", "spit.ac.in",
    "vjti.ac.in", "somaiya.edu", "djsce.edu.in", "mitwpu.edu.in", "amity.edu", "student.amity.edu",
]

FIELD_LABELS = {
    "email": "email address",
    "college": "college name",
    "degree": "degree",
    "branch": "branch / specialisation",
    "graduation_year": "graduation year",
    "cgpa": "CGPA (10-point scale)",
    "preferred_location": "preferred location",
    "available_from": "date they can start",
    "internship_duration_months": "internship duration",
    "linkedin_url": "LinkedIn URL",
    "github_url": "GitHub URL",
    "skills": "skills",
}
