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

# Helper function to flatten MaxPreps MultiIndex headers
def flatten_maxpreps_table(df):
    if isinstance(df.columns, pd.MultiIndex):
        new_cols = []
        for col in df.columns:
            # Pick the lowest level label (e.g., 'Name', 'GP', 'Pts')
            clean_parts = [str(x).strip() for x in col if str(x).strip() and not str(x).startswith('Unnamed')]
            new_cols.append(clean_parts[-1] if clean_parts else str(col[-1]))
        df.columns = new_cols
    return df

team_url = st.text_input(
    "Paste Any MaxPreps Team URL (Stats, Roster, or Schedule page):",
    placeholder="https://www.maxpreps.com/va/virginia-beach/bayside-marlins/basketball/"
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
        # 1. COACHING HISTORY PARSER
        # -------------------------------------------------------------
        coaches_df = pd.DataFrame()
        if fetch_coaches:
            st.write("⏳ Scanning MaxPreps `/history/` for Head Coaching Records...")
            try:
                h_res = requests.get(history_url, impersonate="chrome120")
                if h_res.status_code == 200:
                    soup = BeautifulSoup(h_res.text, 'html.parser')
                    coach_records = []
                    
                    for el in soup.find_all(['li', 'div', 'tr', 'p']):
                        text = el.get_text(separator=' ').strip()
                        if 'Head Coach:' in text or 'Overall Record:' in text or 'W-L' in text:
                            m_yr = re.search(r'(20\d{2}[–-]\d{2,4}|\d{2}[–-]\d{2})', text)
                            m_coach = re.search(r'Head Coach:\s*([A-Za-z\s\.\'-]+)', text)
                            m_record = re.search(r'Overall Record:\s*(\d+[-–]\d+)', text)
                            if m_yr:
                                coach_records.append({
                                    "School": school_name,
                                    "Season": m_yr.group(1),
                                    "Head Coach": m_coach.group(1).strip() if m_coach else "Not Listed",
                                    "Record": m_record.group(1).strip() if m_record else "—"
                                })
                    if coach_records:
                        coaches_df = pd.DataFrame(coach_records).drop_duplicates(subset=['Season'])
                        st.success(f"✅ Captured {len(coaches_df)} seasons of coaching history!")
                    else:
                        st.info("– No coaching roster published on `/history/` for this program.")
                else:
                    st.info(f"– History page returned status {h_res.status_code}.")
            except Exception as e:
                st.info(f"– Coaching scan note: {e}")

        # -------------------------------------------------------------
        # 2. PLAYER STATS SCANNER (With MultiIndex Flattener)
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
                    try:
                        raw_tables = pd.read_html(io.StringIO(res.text))
                        player_tables = []
                        for rt in raw_tables:
                            cleaned_t = flatten_maxpreps_table(rt)
                            # Match if any column contains 'Name' or 'GP' (case-insensitive)
                            col_str = " ".join([str(c).lower() for c in cleaned_t.columns])
                            if "name" in col_str or "gp" in col_str or "pts" in col_str:
                                player_tables.append(cleaned_t)
                        
                        if player_tables:
                            season_df = player_tables[0]
                            for next_t in player_tables[1:]:
                                keys = [c for c in ["#", "Name"] if c in season_df.columns and c in next_t.columns]
                                if not keys:
                                    keys = [c for c in season_df.columns if "name" in str(c).lower() and c in next_t.columns]
                                if keys:
                                    overlap = [c for c in next_t.columns if c in season_df.columns and c not in keys]
                                    next_t_clean = next_t.drop(columns=overlap)
                                    season_df = pd.merge(season_df, next_t_clean, on=keys, how="outer")

                            # Name & Class cleanup
                            name_col = None
                            for c in season_df.columns:
                                if "name" in str(c).lower():
                                    name_col = c
                                    break
                            
                            if name_col:
                                season_df[name_col] = season_df[name_col].astype(str)
                                if exclude_totals:
                                    season_df = season_df[~season_df[name_col].str.contains("Season Totals|Team Totals", case=False, na=False)]
                                
                                extracted_class = season_df[name_col].str.extract(r'\((Fr|So|Jr|Sr)\)', expand=False)
                                season_df[name_col] = season_df[name_col].str.replace(r'\s*\((Fr|So|Jr|Sr)\)', '', regex=True).str.strip()
                                season_df.insert(1, "Class", extracted_class.fillna("Unknown"))

                            season_df.insert(0, "Season", s)
                            season_df.insert(0, "School", school_name)
                            frames.append(season_df)
                            st.write(f"✅ **{s}**: Captured {len(season_df)} players ({len(player_tables)} stat tables merged)")
                        else:
                            st.write(f"– **{s}**: Page loaded, no player box scores entered by coach.")
                    except ValueError:
                        st.write(f"– **{s}**: No stats tables published.")
                elif res.status_code == 404:
                    st.write(f"– **{s}**: Season archive not found on MaxPreps (404).")
                else:
                    st.write(f"– **{s}**: Server responded with status {res.status_code}.")
            except Exception as e:
                st.write(f"⚠️ **{s}** Error: {e}")

            progress_bar.progress((i + 1) / len(selected_seasons))
            time.sleep(0.3)

        status_box.empty()

        # -------------------------------------------------------------
        # 3. DISPLAY RESULTS & DOWNLOAD BUTTONS
        # -------------------------------------------------------------
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
            st.success("🎉 Player Stats Extraction Complete!")
            st.subheader("🏃 Player Statistics")
            m1, m2, m3 = st.columns(3)
            m1.metric("Active Seasons Captured", f"{len(frames)} / {len(selected_seasons)}")
            m2.metric("Total Player-Season Rows", len(master_df))
            m3.metric("Stat Columns Merged", len(master_df.columns))
            st.dataframe(master_df)

            p_csv = master_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Verified Player Stats CSV for AI Studio",
                data=p_csv,
                file_name=f"{school_name.replace(' ', '_')}_Verified_Stats.csv",
                mime="text/csv"
            )
        elif coaches_df.empty:
            st.error("No player stats or coaching records found on MaxPreps for this team.")

