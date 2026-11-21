import datetime as dt
import requests
import pandas as pd
import streamlit as st

BASE = "https://offline.turfinfo.api.pmu.fr/rest/client/7/programme"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def get_programme(date_str):
    return get_json(f"{BASE}/{date_str}")


@st.cache_data(ttl=300)
def get_participants(date_str, r, c):
    data = get_json(f"{BASE}/{date_str}/R{r}/C{c}/participants")
    return data.get("participants", [])


def parse_music(music):
    """Turn PMU 'musique' like '1p3p0p2a' into finishing places."""
    places = []
    for ch in (music or ""):
        if ch.isdigit():
            places.append(int(ch))
    return places


def get_odds(p):
    for key in ("dernierRapportDirect", "dernierRapportReference"):
        try:
            v = p[key]["rapport"]
            if v and v > 0:
                return float(v)
        except Exception:
            pass
    return None


def safety_score(p, race_distance=None):
    """0-100. Higher = safer bet to finish in the top 3."""
    if p.get("statut") and p["statut"] != "PARTANT":
        return -1.0

    score = 0.0

    # 1. Market odds: implied chance (max 45 points)
    odds = get_odds(p)
    if odds:
        score += min(45.0, 45.0 * (3.0 / odds))

    # 2. Recent form: weighted top-3 rate, recent runs count more (max 30)
    places = parse_music(p.get("musique"))[:6]
    if places:
        weights = [6, 5, 4, 3, 2, 1][: len(places)]
        got = sum(w for w, x in zip(weights, places) if 1 <= x <= 3)
        score += 30.0 * got / sum(weights)

    # 3. Career top-3 rate (max 15)
    runs = p.get("nombreCourses") or 0
    if runs >= 3:
        top3 = (p.get("nombreVictoires") or 0) + (p.get("nombrePlaces") or 0)
        score += 15.0 * min(1.0, top3 / runs)

    # 4. Consistency: no disqualification (D) in last 6 runs (max 10)
    music = (p.get("musique") or "")[:12].upper()
    if "D" not in music:
        score += 10.0

    return round(score, 1)


def rank_race(date_str, r, c):
    rows = []
    for p in get_participants(date_str, r, c):
        s = safety_score(p)
        rows.append({
            "Number": p.get("numPmu"),
            "Horse": p.get("nom"),
            "Jockey/Driver": p.get("driver") or p.get("jockey"),
            "Form": p.get("musique"),
            "Odds": get_odds(p),
            "Safety": s,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df[df["Safety"] >= 0]
        df = df.sort_values("Safety", ascending=False).reset_index(drop=True)
    return df


def get_results(date_str, r, c):
    try:
        data = get_json(f"{BASE}/{date_str}/R{r}/C{c}")
        arrivee = data.get("ordreArrivee") or []
        flat = [x[0] if isinstance(x, list) else x for x in arrivee]
        return flat[:3]
    except Exception:
        return []


def confidence_label(df):
    """Is the anchor clearly safer than the rest?"""
    if len(df) < 2:
        return "n/a"
    gap = df.iloc[0]["Safety"] - df.iloc[1]["Safety"]
    top = df.iloc[0]["Safety"]
    if top >= 60 and gap >= 8:
        return "STRONG anchor"
    if top >= 50:
        return "OK anchor"
    return "WEAK - skip this race"


# ---------------- UI ----------------
st.set_page_config(page_title="PMU Anchor Helper", layout="wide")
st.title("PMU Anchor Helper")
st.caption("The anchor is the SAFEST horse of the race, not a sure thing.")

tab1, tab2 = st.tabs(["Race picker", "Back-test"])

with tab1:
    day = st.date_input("Date", dt.date.today())
    date_str = day.strftime("%d%m%Y")

    try:
        prog = get_programme(date_str)
        reunions = prog["programme"]["reunions"]
    except Exception as e:
        st.error(f"Could not load programme: {e}")
        st.stop()

    labels = {}
    for ru in reunions:
        num = ru["numOfficiel"]
        hip = ru.get("hippodrome", {}).get("libelleCourt", "")
        labels[f"R{num} - {hip}"] = ru

    choice = st.selectbox("Meeting (R)", list(labels.keys()))
    reunion = labels[choice]
    r_num = reunion["numOfficiel"]

    c_labels = {}
    for co in reunion.get("courses", []):
        name = f"C{co['numOrdre']} - {co.get('libelle', '')} ({co.get('nombreDeclaresPartants', '?')} horses)"
        c_labels[name] = co

    c_choice = st.selectbox("Race (C)", list(c_labels.keys()))
    c_num = c_labels[c_choice]["numOrdre"]

    df = rank_race(date_str, r_num, c_num)
    if df.empty:
        st.warning("No runners found.")
    else:
        anchor = df.iloc[0]
        label = confidence_label(df)
        st.success(f"ANCHOR (safest): number {anchor['Number']} - {anchor['Horse']}")
        st.write(f"Safety score {anchor['Safety']} / 100 - {label}")

        st.subheader("Partners for a Couple Place (pick one)")
        st.dataframe(df.iloc[1:3], use_container_width=True)

        st.subheader("All runners")
        st.dataframe(df, use_container_width=True)
        st.caption("Safest, not certain. Skip races marked WEAK.")

with tab2:
    st.write("How often did the safest horse finish in the top 3 on past days?")
    days_back = st.slider("Days back", 3, 60, 14)
    only_strong = st.checkbox("Count only STRONG and OK anchors", value=True)
    if st.button("Run back-test"):
        hits, total = 0, 0
        bar = st.progress(0.0)
        for i in range(1, days_back + 1):
            d = (dt.date.today() - dt.timedelta(days=i)).strftime("%d%m%Y")
            try:
                prog = get_programme(d)
                for ru in prog["programme"]["reunions"]:
                    rn = ru["numOfficiel"]
                    for co in ru.get("courses", []):
                        cn = co["numOrdre"]
                        top3 = get_results(d, rn, cn)
                        if len(top3) < 3:
                            continue
                        df = rank_race(d, rn, cn)
                        if df.empty:
                            continue
                        if only_strong and confidence_label(df).startswith("WEAK"):
                            continue
                        total += 1
                        if df.iloc[0]["Number"] in top3:
                            hits += 1
            except Exception:
                pass
            bar.progress(i / days_back)
        if total:
            st.success(f"Anchor placed {hits} of {total} races = {100*hits/total:.1f}%")
        else:
            st.warning("No finished races found.")
