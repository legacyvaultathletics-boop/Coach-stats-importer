import streamlit as st
import pandas as pd
import io, re, time
from urllib.parse import urlparse
from curl_cffi import requests

st.set_page_config(page_title="CoachStats AI", page_icon="🏀", layout="wide")
st.title("🏀 Turnkey High School Stat Importer")
st.write("Paste any MaxPreps team link below to build a verified, multi-season dataset for Google AI Studio.")

ALL_SEASONS = [f"{str(y)[-2:]}-{str(y+1)[-2:]}" for y in range(2005, 2027)]

# Tabs that employees might accidentally copy instead of /stats/
STOP_WORDS = {
    'stats', 'schedule', 'roster', 'standings', 'rankings', 
    'photos', 'videos', 'news', 'history', 'media', 'home'
}

def clean_maxpreps_url(url):
    parsed = urlparse(url.strip())
    if "maxpreps.com" not in parsed.netloc.lower():
        return None, None
        
    path = parsed.path.rstrip('/')
    parts = [p for p in path.split('/') if p]
    
    # Remove season numbers (e.g., 25-26) and any page tab names (schedule, roster, stats, etc.)
    filtered = [
        p for p in parts 
        if not re.match(r'^\d{2}-\d{2}$', p) and p.lower() not in STOP_WORDS
    ]
    
    if len(filtered) < 4:
        return None, None
        
    base_path = '/'.join(filtered)
    template = f"https://www.maxpreps.com/{base_path}/{{season}}/stats/"
    school = filtered[2].replace('-', ' ').title()
    return template, school

team_url = st.text_input(
    "Paste Any MaxPreps Team URL (Stats, Roster, or Schedule page):",
    placeholder="https://www.maxpreps.com/nc/high-point/southwest-guilford-cowboys/basketball/girls/stats/"
)

col_opt1, col_opt2 = st.columns(2)
with col_opt1:
    fetch_all = st.checkbox("⚡ Scan entire 20+ year archive (05-06 to 26-27)", value=True)
with col_opt2:
    exclude_totals = st.checkbox("🧹 Remove 'Season Totals' summary rows (Recommended for AI)", value=True)

if not fetch_all:
    col1, col2 = st.columns(2)
    with col1:
        start_year = st.selectbox("Start Season", ALL_SEASONS, index=0)
    with col2:
        end_year = st.selectbox("End Season", ALL_SEASONS, index=len(ALL_SEASONS)-1)
else:
    start_year, end_year = "05-06", "26-27"

if st.button("🚀 Fetch & Validate Stats", type="primary"):
    template_url, school_name = clean_maxpreps_url(team_url)
    
    if not template_url:
        st.error("❌ Invalid URL. Please make sure you pasted a full team link from MaxPreps.com.")
    else:
        st.info(f"Targeting: **{school_name}** | Standardized Path: `{template_url.format(season='[SEASON]')}`")

        s_idx = ALL_SEASONS.index(start_year)
        e_idx = ALL_SEASONS.index(end_year)
        selected_seasons = ALL_SEASONS[s_idx:e_idx+1]

        frames = []
        progress_bar = st.progress(0)
        status_box = st.empty()

        for i, s in enumerate(selected_seasons):
            target = template_url.format(season=s)
            status_box.write(f"⏳ Scanning season **{s}**...")

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

                        # Error Check: Separate Player Name and Class Year (e.g., "T. Freeman (Jr)" -> "T. Freeman", "Jr")
                        if "Name" in season_df.columns:
                            season_df["Name"] = season_df["Name"].astype(str)
                            if exclude_totals:
                                season_df = season_df[~season_df["Name"].str.contains("Season Totals|Team Totals", case=False, na=False)]
                            
                            # Extract (Fr), (So), (Jr), (Sr) into a dedicated Class column
                            extracted_class = season_df["Name"].str.extract(r'\((Fr|So|Jr|Sr)\)', expand=False)
                            season_df["Name"] = season_df["Name"].str.replace(r'\s*\((Fr|So|Jr|Sr)\)', '', regex=True).str.strip()
                            season_df.insert(1, "Class", extracted_class.fillna("Unknown"))

                        season_df.insert(0, "Season", s)
                        season_df.insert(0, "School", school_name)
                        frames.append(season_df)
                        st.write(f"✅ **{s}**: Verified {len(season_df)} athletes ({len(player_tables)} stat tables merged)")
                    else:
                        st.write(f"– **{s}**: Page loaded, no player stat tables found")
                elif res.status_code == 404:
                    st.write(f"– **{s}**: No page for this season (404)")
                else:
                    st.write(f"– **{s}**: Server status {res.status_code}")
            except ValueError:
                st.write(f"– **{s}**: No stats entered by coach")
            except Exception as e:
                st.write(f"⚠️ **{s}** Error: {e}")

            progress_bar.progress((i + 1) / len(selected_seasons))
            time.sleep(0.3)

        status_box.empty()

        if frames:
            master_df = pd.concat(frames, ignore_index=True)
            
            # Pre-Flight Audit Summary for the Employee
            st.success("🎉 Extraction & Validation Complete!")
            m1, m2, m3 = st.columns(3)
            m1.metric("Active Seasons Captured", f"{len(frames)} / {len(selected_seasons)}")
            m2.metric("Total Player-Season Rows", len(master_df))
            m3.metric("Stat Columns Merged", len(master_df.columns))

            st.dataframe(master_df)

            csv_data = master_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Verified Master CSV for Google AI Studio",
                data=csv_data,
                file_name=f"{school_name.replace(' ', '_')}_Verified_Stats.csv",
                mime="text/csv"
            )
        else:
            st.error("No player stats found across the selected seasons.")

