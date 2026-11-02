import os
import re
import json
import time
from urllib.parse import urlparse, urljoin
import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ==========================================
# PAGE SETUP & CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="LVA CoachStats AI - Data Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("⚡ Legacy Vault Athletics: MaxPreps Ingestion Engine")
st.caption("Turnkey Multi-Season Data Harvester for Google Sheets Master Engine & Fan Portals")

# ==========================================
# SIDEBAR CONFIGURATION
# ==========================================
with st.sidebar:
    st.header("⚙️ Ingestion Settings")
    
    # ScraperAPI / ZenRows Key for Cloud Deployment (Streamlit Cloud bypass)
    default_api_key = os.getenv("SCRAPER_API_KEY", "")
    scraper_api_key = st.text_input(
        "ScraperAPI Key (Optional / Cloud Bypass)",
        value=default_api_key,
        type="password",
        help="Required when running on Streamlit Community Cloud to bypass Cloudflare IP blocks. Free tier grants 5,000 requests/mo.",
    )
    
    sport_type = st.selectbox(
        "Sport Target",
        options=["Auto-Detect", "Boys Basketball", "Girls Basketball", "Football"],
        index=0,
    )
    
    request_delay = st.slider("Throttle Delay (Seconds)", min_value=0.5, max_value=3.0, value=1.0, step=0.5)

# ==========================================
# CORE EXTRACTION UTILITIES
# ==========================================
class MaxPrepsScraper:
    def __init__(self, api_key: str = "", delay: float = 1.0):
        self.api_key = api_key.strip()
        self.delay = delay
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def fetch_html(self, target_url: str) -> str:
        """Fetches target URL via ScraperAPI proxy (if configured) or local curl_cffi."""
        time.sleep(self.delay)
        
        # Route 1: ScraperAPI Proxy (Cloud Safe)
        if self.api_key:
            proxy_url = f"https://api.scraperapi.com?api_key={self.api_key}&url={target_url}&render=false"
            resp = cffi_requests.get(proxy_url, impersonate="chrome120", timeout=30)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 403:
                raise PermissionError("Cloudflare blocked proxy request. Verify API quota or enable render=true.")
            else:
                raise RuntimeError(f"Proxy request failed with status code: {resp.status_code}")

        # Route 2: Direct Residential curl_cffi (Local Machine Safe)
        resp = cffi_requests.get(
            target_url,
            headers=self.headers,
            impersonate="chrome124",
            timeout=20,
        )
        if resp.status_code == 403:
            raise PermissionError(
                "Cloudflare 403 Forbidden. Your hosting IP is flagged. "
                "Provide a free ScraperAPI key in the sidebar or run this script locally."
            )
        elif resp.status_code != 200:
            raise RuntimeError(f"HTTP fetch failed with status code {resp.status_code}")
        
        return resp.text

    @staticmethod
    def flatten_multiindex_table(df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalizes MultiIndex headers from Basketball/Football tables.
        Converts ('Totals', 'Name') -> 'Name', ('Per Game', 'PTS') -> 'Per_Game_PTS'.
        """
        if not isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c).strip() for c in df.columns]
            return df

        new_cols = []
        player_anchor_labels = {"#", "jersey", "name", "athlete", "player", "yr", "year", "pos", "position", "class"}

        for col_tuple in df.columns.values:
            # Filter empty or 'Unnamed' sub-headers
            clean_levels = [
                str(level).strip()
                for level in col_tuple
                if str(level).strip() and not str(level).startswith("Unnamed")
            ]

            if not clean_levels:
                new_cols.append("Unknown")
            elif len(clean_levels) == 1:
                new_cols.append(clean_levels[0])
            else:
                top_level = clean_levels[0]
                bottom_level = clean_levels[-1]

                # If the column represents player identity, use the base field name
                if bottom_level.lower() in player_anchor_labels:
                    new_cols.append(bottom_level.capitalize())
                else:
                    clean_top = re.sub(r"\W+", "_", top_level)
                    clean_bot = re.sub(r"\W+", "_", bottom_level)
                    new_cols.append(f"{clean_top}_{clean_bot}")

        df.columns = new_cols
        # Deduplicate identical column names if any exist
        seen = {}
        deduped = []
        for c in df.columns:
            if c not in seen:
                seen[c] = 0
                deduped.append(c)
            else:
                seen[c] += 1
                deduped.append(f"{c}_{seen[c]}")
        df.columns = deduped
        return df

    @classmethod
    def extract_stats_tables(cls, html_content: str, season_label: str) -> list[pd.DataFrame]:
        """Extracts and sanitizes all player statistical tables from season stats HTML."""
        extracted_tables = []
        try:
            raw_tables = pd.read_html(html_content)
        except ValueError:
            return extracted_tables

        for raw_df in raw_tables:
            if raw_df.empty or len(raw_df) < 2:
                continue

            cleaned_df = cls.flatten_multiindex_table(raw_df)

            # Validate presence of player identification columns
            has_name = any(
                str(col).lower() in ["name", "athlete", "player", "athlete name"]
                for col in cleaned_df.columns
            )
            if not has_name:
                continue

            # Identify and normalize player name column
            for col in cleaned_df.columns:
                if str(col).lower() in ["name", "athlete", "player", "athlete name"]:
                    cleaned_df.rename(columns={col: "Athlete"}, inplace=True)
                    break

            # Filter out subheader rows repeating within tables
            cleaned_df = cleaned_df[
                cleaned_df["Athlete"].astype(str).str.lower() != "athlete"
            ]
            cleaned_df = cleaned_df[
                cleaned_df["Athlete"].astype(str).str.lower() != "name"
            ]
            cleaned_df = cleaned_df.dropna(subset=["Athlete"])

            cleaned_df.insert(0, "Season", season_label)
            extracted_tables.append(cleaned_df)

        return extracted_tables

    @classmethod
    def extract_coaching_history(cls, html_content: str, base_url: str) -> pd.DataFrame:
        """
        Resilient coaching history parser:
        1. Checks Next.js JSON state (__NEXT_DATA__).
        2. Falls back to standard HTML tables.
        3. Falls back to parsing BeautifulSoup card/list containers.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        records = []

        # Strategy A: Next.js Embedded JSON (__NEXT_DATA__)
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if next_data_tag and next_data_tag.string:
            try:
                data = json.loads(next_data_tag.string)
                # Traverse standard MaxPreps page props for history payload
                page_props = data.get("props", {}).get("pageProps", {})
                seasons = page_props.get("history", {}).get("seasons", [])
                for s in seasons:
                    records.append({
                        "Season": s.get("seasonYear", ""),
                        "Head Coach": s.get("headCoach", "Unknown"),
                        "Overall W": s.get("wins", 0),
                        "Overall L": s.get("losses", 0),
                        "Overall T": s.get("ties", 0),
                        "League Record": s.get("leagueRecord", ""),
                        "Playoff Result": s.get("playoffResult", ""),
                    })
                if records:
                    return pd.DataFrame(records)
            except Exception:
                pass  # Fall through to HTML parsing

        # Strategy B: Standard Table Elements
        try:
            tables = pd.read_html(html_content)
            for t in tables:
                t = cls.flatten_multiindex_table(t)
                cols_lower = [str(c).lower() for c in t.columns]
                if any("coach" in c for c in cols_lower) or any("record" in c for c in cols_lower):
                    return t
        except Exception:
            pass

        # Strategy C: BeautifulSoup Card / List Item Parser
        # MaxPreps cards commonly use lists or grid containers with season headers
        card_items = soup.select("ul.team-history li, div[class*='history-row'], div[class*='season-card']")
        if not card_items:
            card_items = soup.find_all(["li", "tr", "div"], class_=re.compile(r"history|season", re.I))

        for item in card_items:
            text = " ".join(item.stripped_strings)
            season_match = re.search(r"\b(19\d{2}|20\d{2})(-\d{2})?\b", text)
            record_match = re.search(r"\b(\d{1,2})-(\d{1,2})(?:-(\d{1,2}))?\b", text)

            if season_match and record_match:
                season_val = season_match.group(0)
                w, l = record_match.group(1), record_match.group(2)
                t = record_match.group(3) if record_match.group(3) else "0"

                # Extract Coach Name
                coach_name = "Not Listed"
                coach_element = item.find(string=re.compile(r"coach", re.I))
                if coach_element:
                    parent_text = coach_element.parent.get_text(strip=True)
                    coach_clean = re.sub(r"(?i)head coach[:\s]*|coach[:\s]*", "", parent_text)
                    if coach_clean:
                        coach_name = coach_clean
                else:
                    # Heuristic: Find prominent text tokens that aren't the season or record
                    cleaned_tokens = re.sub(r"\b\d{1,2}-\d{1,2}(-\d{1,2})?\b", "", text)
                    cleaned_tokens = re.sub(r"\b(19|20)\d{2}(-\d{2})?\b", "", cleaned_tokens)
                    remaining = cleaned_tokens.strip().split(" - ")
                    if len(remaining) > 1:
                        coach_name = remaining[0].strip()

                records.append({
                    "Season": season_val,
                    "Head Coach": coach_name,
                    "Wins": w,
                    "Losses": l,
                    "Ties": t,
                    "Raw Summary": text,
                })

        if records:
            df_history = pd.DataFrame(records).drop_duplicates(subset=["Season"])
            return df_history

        # Default fallback dataframe if no records extracted
        return pd.DataFrame(columns=["Season", "Head Coach", "Wins", "Losses", "Ties", "Notes"])

# ==========================================
# USER INTERFACE & PIPELINE RUNNER
# ==========================================
st.markdown("### 1. Source School Specification")
target_url_input = st.text_input(
    "MaxPreps Team Varsity Home URL",
    placeholder="https://www.maxpreps.com/va/virginia-beach/bayside-marlins/basketball/...",
    help="Paste the root varsity page, stats page, or history page for any high school program.",
)

col_a, col_b = st.columns([1, 1])
with col_a:
    season_range = st.slider("Season Ingestion Depth (Past Years)", min_value=1, max_value=25, value=15)
with col_b:
    extract_history_toggle = st.checkbox("Extract Coaching History Tab", value=True)

if st.button("🚀 Harvest School Records", type="primary", use_container_width=True):
    if not target_url_input.strip():
        st.error("Please enter a valid MaxPreps URL to proceed.")
        st.stop()

    scraper = MaxPrepsScraper(api_key=scraper_api_key, delay=request_delay)
    parsed = urlparse(target_url_input.strip())
    path_segments = [p for p in parsed.path.split("/") if p]

    if len(path_segments) < 3:
        st.error("URL structure invalid. Expected: maxpreps.com/{state}/{city}/{school-slug}/{sport}/")
        st.stop()

    # Determine Base Team Path
    # Structure: /{state}/{city}/{school}/{sport}/
    base_team_path = "/" + "/".join(path_segments[:4]) + "/"
    school_slug = path_segments[2]
    sport_slug = path_segments[3] if len(path_segments) > 3 else "athletics"
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    progress_bar = st.progress(0)
    status_box = st.empty()

    all_stat_tables = []
    coaches_df = pd.DataFrame()

    # STEP 1: Scrape History Page
    if extract_history_toggle:
        history_url = f"{base_domain}{base_team_path}history/"
        status_box.info(f"Retrieving head coaching ledger from: {history_url}")
        try:
            history_html = scraper.fetch_html(history_url)
            coaches_df = scraper.extract_coaching_history(history_html, history_url)
            st.success(f"Extracted {len(coaches_df)} coaching history records.")
        except Exception as e:
            st.warning(f"Coaching history notice: {str(e)}")

    # STEP 2: Loop Seasons for Stat Tables
    current_year = 2024
    seasons_to_check = [f"{y}-{str(y+1)[-2:]}" for y in range(current_year, current_year - season_range, -1)]
    
    total_steps = len(seasons_to_check)
    for idx, s_label in enumerate(seasons_to_check):
        progress_val = int(((idx + 1) / total_steps) * 100)
        progress_bar.progress(progress_val)
        
        # MaxPreps seasonal stat path: /{season}/stats/
        season_stats_url = f"{base_domain}{base_team_path}{s_label}/stats/"
        status_box.text(f"Fetching Season [{idx+1}/{total_steps}]: {s_label}...")

        try:
            stats_html = scraper.fetch_html(season_stats_url)
            season_tables = scraper.extract_stats_tables(stats_html, season_label=s_label)
            if season_tables:
                all_stat_tables.extend(season_tables)
        except PermissionError as pe:
            st.error(str(pe))
            st.stop()
        except Exception:
            # 404 or no stats logged for that specific season; continue pipeline
            continue

    status_box.empty()
    progress_bar.empty()

    # ==========================================
    # AGGREGATION & DATASET PRESENTATION
    # ==========================================
    if not all_stat_tables and coaches_df.empty:
        st.error("No statistical tables or coaching records found. Check URL format or API proxy status.")
        st.stop()

    st.markdown("---")
    st.header("📊 Verification & Deliverable Generation")

    # Consolidated Master Stats DataFrame
    if all_stat_tables:
        master_stats_df = pd.concat(all_stat_tables, axis=0, ignore_index=True)
        # Reorder so Athlete and Season come first
        front_cols = ["Season", "Athlete"]
        remaining_cols = [c for c in master_stats_df.columns if c not in front_cols]
        master_stats_df = master_stats_df[front_cols + remaining_cols]

        tab1, tab2 = st.tabs(["Player Master Statistics", "Coaching Ledger"])

        with tab1:
            st.subheader(f"Player Season Statistics ({len(master_stats_df)} Total Entries)")
            st.dataframe(master_stats_df.head(100), use_container_width=True)
            
            stats_csv = master_stats_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download [School]_Verified_Stats.csv",
                data=stats_csv,
                file_name=f"{school_slug}_{sport_slug}_Verified_Stats.csv",
                mime="text/csv",
                type="primary",
            )

        with tab2:
            st.subheader(f"Head Coaching & Win-Loss History ({len(coaches_df)} Records)")
            if not coaches_df.empty:
                st.dataframe(coaches_df, use_container_width=True)
                coaches_csv = coaches_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="📥 Download [School]_Coaches_History.csv",
                    data=coaches_csv,
                    file_name=f"{school_slug}_{sport_slug}_Coaches_History.csv",
                    mime="text/csv",
                )
            else:
                st.info("No standalone coaching history records were discovered.")
    else:
        st.warning("Player statistics were empty; showing coaching ledger only.")
        if not coaches_df.empty:
            st.dataframe(coaches_df, use_container_width=True)

