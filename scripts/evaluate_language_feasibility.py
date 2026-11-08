"""GH-only language feasibility benchmark. No production adapter or paid API."""
import importlib.metadata,json,re,resource,time
from pathlib import Path
from lingua import Language,LanguageDetectorBuilder
cases=json.loads(Path("tests/fixtures/language_feasibility_ru_am_en.json").read_text())
started=time.perf_counter()
detector=LanguageDetectorBuilder.from_languages(Language.RUSSIAN,Language.ARMENIAN,Language.ENGLISH).with_minimum_relative_distance(0.2).build()
def predict(text):
    cleaned=re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+"," ",text)
    if sum(c.isalpha() for c in cleaned)<5:return None
    scripts=[len(re.findall(pattern,cleaned)) for pattern in (r"[А-Яа-яЁё]",r"[\u0531-\u058f]",r"[A-Za-z]")]
    if sum(n>=3 for n in scripts)>=2:return None
    language=detector.detect_language_of(cleaned)
    return language.iso_code_639_1.name.lower() if language else None
rows=[]
for case in cases:
    predicted=predict(case["text"])
    rows.append({"id":case["id"],"expected":case["expected"],"predicted":predicted,
                 "correct":predicted==case["expected"]})
print("LANGUAGE_FEASIBILITY="+json.dumps({"version":importlib.metadata.version("lingua-language-detector"),
 "synthetic":True,"cases":len(rows),"correct":sum(r["correct"] for r in rows),"rows":rows,
 "seconds_including_model_load":round(time.perf_counter()-started,3),
 "max_rss_mib":round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),
 "policy":"three languages only; short/strong mixed-script inputs may be unknown",
 "production_installed":False},sort_keys=True))
