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
    """Turn PMU 'musique' like '1p3p0p2a' into list of finishing places."""
    places = []
    for ch in (music or ""):
        if ch.isdigit():
            places.append(int(ch))
    return places


def score_horse(p):
    """Higher score = more likely to finish in the top 3."""
    score = 0.0

    # 1. Market odds (lower odds = market favourite)
    odds = None
    try:
        odds = p["dernierRapportDirect"]["rapport"]
    except Exception:
        try:
            odds = p["dernierRapportReference"]["rapport"]
        except Exception:
            odds = None
    if odds and odds > 0:
        score += 50.0 / odds  # implied probability weight

    # 2. Recent form from 'musique' (last 6 runs)
    places = parse_music(p.get("musique"))[:6]
    if places:
        top3 = sum(1 for x in places if 1 <= x <= 3)
        score += 8.0 * (top3 / len(places))

    # 3. Career place rate
    runs = p.get("nombreCourses") or 0
    places_count = p.get("nombrePlaces") or 0
    wins = p.get("nombreVictoires") or 0
    if runs >= 3:
        score += 6.0 * ((wins + places_count) / runs)

    # 4. Penalise non-runners
    if p.get("statut") and p["statut"] != "PARTANT":
        score = -999

    return score, odds


def rank_race(date_str, r, c):
    rows = []
    for p in get_participants(date_str, r, c):
        score, odds = score_horse(p)
        rows.append({
            "Number": p.get("numPmu"),
            "Horse": p.get("nom"),
            "Jockey/Driver": p.get("driver") or p.get("jockey"),
            "Trainer": p.get("entraineur"),
            "Form": p.get("musique"),
            "Runs": p.get("nombreCourses"),
            "Wins": p.get("nombreVictoires"),
            "Places": p.get("nombrePlaces"),
            "Odds": odds,
            "Score": round(score, 2),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Score", ascending=False).reset_index(drop=True)
    return df


def get_results(date_str, r, c):
    """Return the first three finishers (numbers) of a finished race."""
    try:
        data = get_json(f"{BASE}/{date_str}/R{r}/C{c}")
        arrivee = data.get("ordreArrivee") or []
        flat = [x[0] if isinstance(x, list) else x for x in arrivee]
        return flat[:3]
    except Exception:
        return []


# ---------------- UI ----------------
st.set_page_config(page_title="PMU Place Helper", layout="wide")
st.title("PMU Place Helper")

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

    courses = reunion.get("courses", [])
    c_labels = {}
    for co in courses:
        c_labels[f"C{co['numOrdre']} - {co.get('libelle', '')} ({co.get('nombreDeclaresPartants', '?')} horses)"] = co

    c_choice = st.selectbox("Race (C)", list(c_labels.keys()))
    c_num = c_labels[c_choice]["numOrdre"]

    df = rank_race(date_str, r_num, c_num)
    if df.empty:
        st.warning("No runners found.")
    else:
        st.subheader("Top 3 suggested for PLACE")
        st.dataframe(df.head(3), use_container_width=True)
        st.subheader("All runners")
        st.dataframe(df, use_container_width=True)
        st.caption("A suggestion, not a guarantee.")

with tab2:
    st.write("Tests how often the top pick finished in the top 3 on past days.")
    days_back = st.slider("Days back", 3, 60, 14)
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
                        pick = df.iloc[0]["Number"]
                        total += 1
                        if pick in top3:
                            hits += 1
            except Exception:
                pass
            bar.progress(i / days_back)
        if total:
            st.success(f"Top pick placed {hits} of {total} races = {100*hits/total:.1f}%")
        else:
            st.warning("No finished races found.")
