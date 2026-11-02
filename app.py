import streamlit as st
import pandas as pd
import io, re, time
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from curl_cffi import requests

st.set_page_config(page_title="CoachStats AI", page_icon="🏀", layout="wide")
st.title("🏀 Turnkey High School Stat & Coaching Importer")
st.write("Paste any MaxPreps team link below to build a verified dataset for Google AI Studio.")

ALL_SEASONS = [f"{str(y)[-2:]}-{str(y+1)[-2:]}" for y in range(2005, 2027)]

# Tabs to strip from user input to find the root team path
STOP_WORDS = {
    'stats', 'schedule', 'roster', 'standings', 'rankings', 
    'photos', 'videos', 'news', 'history', 'media', 'home'
}

def clean_maxpreps_url(url):
    parsed = urlparse(url.strip())
    if "maxpreps.com" not in parsed.netloc.lower():
        return None, None, None
        
    path = parsed.path.rstrip('/')
    parts = [p for p in path.split('/') if p]
    
    # Strip out season numbers and sub-tabs
    filtered = [
        p for p in parts 
        if not re.match(r'^\d{2}-\d{2}$', p) and p.lower() not in STOP_WORDS
    ]
    
    if len(filtered) < 4:
        return None, None, None
        
    base_path = '/'.join(filtered)
    stats_template = f"https://www.maxpreps.com/{base_path}/{{season}}/stats/"
    history_url = f"https://www.maxpreps.com/{base_path}/history/"
    school = filtered[2].replace('-', ' ').title()
    return stats_template, history_url, school

team_url = st.text_input(
    "Paste Any MaxPreps Team URL (Stats, Roster, or Schedule page):",
    placeholder="https://www.maxpreps.com/va/virginia-beach/bayside-marlins/football/"
)

col_opt1, col_opt2, col_opt3 = st.columns(3)
with col_opt1:
    fetch_all = st.checkbox("⚡ Scan entire 20+ year archive (05-06 to 26-27)", value=True)
with col_opt2:
    exclude_totals = st.checkbox("🧹 Remove 'Season Totals' summary rows", value=True)
with col_opt3:
    fetch_coaches = st.checkbox("📋 Also extract Head Coaching History (/history/)", value=True)

if not fetch_all:
    col1, col2 = st.columns(2)
    with col1:
        start_year = st.selectbox("Start Season", ALL_SEASONS, index=0)
    with col2:
        end_year = st.selectbox("End Season", ALL_SEASONS, index=len(ALL_SEASONS)-1)
else:
    start_year, end_year = "05-06", "26-27"

if st.button("🚀 Fetch & Validate Stats & Coaches", type="primary"):
    stats_template, history_url, school_name = clean_maxpreps_url(team_url)
    
    if not stats_template:
        st.error("❌ Invalid URL. Please make sure you pasted a full team link from MaxPreps.com.")
    else:
        st.info(f"Targeting: **{school_name}**")

        # -------------------------------------------------------------
        # STEP 1: SCRAPE COACHING HISTORY (/history/)
        # -------------------------------------------------------------
        coaches_df = pd.DataFrame()
        if fetch_coaches:
            st.write("⏳ Extracting Head Coaching History from MaxPreps `/history/`...")
            try:
                h_res = requests.get(history_url, impersonate="chrome120")
                if h_res.status_code == 200:
                    h_tables = pd.read_html(io.StringIO(h_res.text))
                    # MaxPreps history table typically has 'Season' and 'Head Coach'
                    for ht in h_tables:
                        cols = [str(c).lower() for c in ht.columns]
                        if any("coach" in c for c in cols):
                            coaches_df = ht
                            break
                    
                    # Fallback parser if HTML table isn't standard <table>
                    if coaches_df.empty:
                        soup = BeautifulSoup(h_res.text, 'html.parser')
                        rows = []
                        for li in soup.find_all(['li', 'tr', 'div'], text=re.compile(r'Head Coach:', re.I)):
                            text = li.get_text(separator=' ')
                            m_yr = re.search(r'(\d{4}[–-]\d{2,4})', text)
                            m_coach = re.search(r'Head Coach:\s*([A-Za-z\s\.\'-]+)', text)
                            if m_yr and m_coach:
                                rows.append({'Season': m_yr.group(1), 'Head Coach': m_coach.group(1).strip()})
                        if rows:
                            coaches_df = pd.DataFrame(rows)

                    if not coaches_df.empty:
                        coaches_df.insert(0, "School", school_name)
                        st.success(f"✅ Captured {len(coaches_df)} seasons of Head Coaching history!")
                    else:
                        st.warning("– Could not locate structured coaching table on `/history/`.")
                else:
                    st.warning(f"– Could not reach history page (Status {h_res.status_code}).")
            except Exception as e:
                st.warning(f"⚠️ Coaching history extraction error: {e}")

        # -------------------------------------------------------------
        # STEP 2: SCRAPE PLAYER BOX SCORES (/stats/)
        # -------------------------------------------------------------
        s_idx = ALL_SEASONS.index(start_year)
        e_idx = ALL_SEASONS.index(end_year)
        selected_seasons = ALL_SEASONS[s_idx:e_idx+1]

        frames = []
        progress_bar = st.progress(0)
        status_box = st.empty()

        for i, s in enumerate(selected_seasons):
            target = stats_template.format(season=s)
            status_box.write(f"⏳ Scanning player stats for season **{s}**...")

            try:
                res = requests.get(target, impersonate="chrome120")
                if res.status_code == 200:
                    tables = pd.read_html(io.StringIO(res.text))
                    player_tables = [t for t in tables if "Name" in t.columns or "GP" in t.columns]
                    
                    if player_tables:
                        season_df = player_tables[0]
                        for next_t in player_tables[1:]:
                            keys = [c for c in ["#", "Name"] if c in season_df.columns and c in next_t.columns]
                            if keys:
                                overlap = [c for c in next_t.columns if c in season_df.columns and c not in keys]
                                next_t_clean = next_t.drop(columns=overlap)
                                season_df = pd.merge(season_df, next_t_clean, on=keys, how="outer")

                        if "Name" in season_df.columns:
                            season_df["Name"] = season_df["Name"].astype(str)
                            if exclude_totals:
                                season_df = season_df[~season_df["Name"].str.contains("Season Totals|Team Totals", case=False, na=False)]
                            
                            extracted_class = season_df["Name"].str.extract(r'\((Fr|So|Jr|Sr)\)', expand=False)
                            season_df["Name"] = season_df["Name"].str.replace(r'\s*\((Fr|So|Jr|Sr)\)', '', regex=True).str.strip()
                            season_df.insert(1, "Class", extracted_class.fillna("Unknown"))

                        season_df.insert(0, "Season", s)
                        season_df.insert(0, "School", school_name)
                        frames.append(season_df)
            except Exception as e:
                pass

            progress_bar.progress((i + 1) / len(selected_seasons))
            time.sleep(0.3)

        status_box.empty()

        # -------------------------------------------------------------
        # STEP 3: DISPLAY AND DOWNLOAD BUTTONS
        # -------------------------------------------------------------
        if frames or not coaches_df.empty:
            st.success("🎉 Extraction & Validation Complete!")

            if not coaches_df.empty:
                st.subheader("📋 Head Coaching Lineage")
                st.dataframe(coaches_df)
                c_csv = coaches_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Coaching Ledger CSV",
                    data=c_csv,
                    file_name=f"{school_name.replace(' ', '_')}_Coaches_History.csv",
                    mime="text/csv"
                )

            if frames:
                master_df = pd.concat(frames, ignore_index=True)
                st.subheader("🏃 Player Statistics")
                m1, m2, m3 = st.columns(3)
                m1.metric("Active Seasons Captured", f"{len(frames)} / {len(selected_seasons)}")
                m2.metric("Total Player-Season Rows", len(master_df))
                m3.metric("Stat Columns Merged", len(master_df.columns))
                st.dataframe(master_df)

                p_csv = master_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Verified Player Stats CSV",
                    data=p_csv,
                    file_name=f"{school_name.replace(' ', '_')}_Verified_Stats.csv",
                    mime="text/csv"
                )
        else:
            st.error("No player stats or coaching records found.")

