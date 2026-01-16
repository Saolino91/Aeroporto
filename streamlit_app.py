# streamlit_app.py

"""
App Streamlit per:
1. Caricare un PDF con orari voli (febbraio 2026).
2. Parsare i voli PAX, anche quando un giorno è spezzato su più tabelle / pagine.
3. Raggruppare per giorno della settimana.
4. Visualizzare una matrice voli × date con interfaccia curata.
5. Esportare la matrice in CSV.
"""

import io
import re
from datetime import date
from typing import List, Optional

import pandas as pd
import pdfplumber
import streamlit as st


# =========================
# Costanti
# =========================

WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

WEEKDAY_LABELS_IT = {
    "Mon": "Lunedì",
    "Tue": "Martedì",
    "Wed": "Mercoledì",
    "Thu": "Giovedì",
    "Fri": "Venerdì",
    "Sat": "Sabato",
    "Sun": "Domenica",
}

DAY_PATTERN = re.compile(
    r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d{1,2})\s+Feb\s+2026$"
)


# =========================
# PARSING PDF
# =========================

def parse_pdf_to_flights_df(file_obj: io.BytesIO) -> pd.DataFrame:
    """
    Parser per il PDF "orario voli febbraio 2026".

    Logica:
    - divide orizzontalmente la pagina in 7 colonne di uguale larghezza;
    - per ogni tabella:
        * calcola la colonna dal centro orizzontale (xc);
        * se la prima cella è tipo "Sun 22 Feb 2026" → nuova data e weekday per quella colonna;
        * altrimenti la tabella è continuazione del giorno corrente in quella colonna;
    - per ogni tabella con data nota legge le righe voli:
        Flight, Route, A/D, Type, ETA, ETD.

    Restituisce un DataFrame con colonne:
        ['Date', 'Weekday', 'Flight', 'Route', 'AD', 'Type', 'ETA', 'ETD']
        (poi filtrato a PAX).
    """
    records: List[dict] = []

    with pdfplumber.open(file_obj) as pdf:
        # larghezza pagina e ampiezza colonne
        first_page = pdf.pages[0]
        page_width = first_page.width
        col_width = page_width / 7.0

        def col_index_from_xc(xc: float) -> int:
            idx = int(xc / col_width)
            if idx < 0:
                idx = 0
            if idx > 6:
                idx = 6
            return idx

        # stato corrente per ogni colonna: data e weekday
        current_date_by_col = {i: None for i in range(7)}
        current_weekday_by_col = {i: None for i in range(7)}

        # scorri tutte le pagine
        for page in pdf.pages:
            tables = page.find_tables()
            # ordina tabelle dall'alto verso il basso
            tables = sorted(tables, key=lambda t: t.bbox[1])

            for t in tables:
                rows = t.extract()
                if not rows:
                    continue

                x0, _, x1, _ = t.bbox
                xc = 0.5 * (x0 + x1)
                col = col_index_from_xc(xc)

                first_row = rows[0] if rows else []
                first_cell = (first_row[0] or "").strip() if first_row else ""
                m = DAY_PATTERN.match(first_cell)

                # caso 1: tabella con intestazione del giorno (es. "Sun 22 Feb 2026")
                if m:
                    weekday = m.group(1)
                    day_num = int(m.group(2))
                    cur_date = date(2026, 2, day_num)
                    current_date_by_col[col] = cur_date
                    current_weekday_by_col[col] = weekday
                    start_idx = 2  # riga 1 = header "Flight RouteA/D Type ETA ETD"

                # caso 2: continuazione del giorno corrente di quella colonna
                else:
                    cur_date = current_date_by_col[col]
                    cur_weekday = current_weekday_by_col[col]
                    if cur_date is None or cur_weekday is None:
                        # tabella fuori da una colonna "attiva": ignora
                        continue

                    # se la prima cella è "Flight", è un header ripetuto
                    if first_cell.lower() == "flight":
                        start_idx = 1
                    else:
                        start_idx = 0

                cur_date = current_date_by_col[col]
                cur_weekday = current_weekday_by_col[col]
                if cur_date is None or cur_weekday is None:
                    continue

                # estrai righe voli
                for row in rows[start_idx:]:
                    if not row or not row[0]:
                        continue

                    flight = (row[0] or "").strip()
                    route = (row[1] or "").strip() if len(row) > 1 else ""
                    ad = (row[2] or "").strip() if len(row) > 2 else ""
                    typ = (row[3] or "").strip() if len(row) > 3 else ""
                    eta = (row[4] or "").strip() if len(row) > 4 else ""
                    etd = (row[5] or "").strip() if len(row) > 5 else ""

                    if not flight:
                        continue

                    records.append(
                        {
                            "Date": cur_date,
                            "Weekday": cur_weekday,
                            "Flight": flight,
                            "Route": route,
                            "AD": ad,
                            "Type": typ,
                            "ETA": eta,
                            "ETD": etd,
                        }
                    )

    if not records:
        return pd.DataFrame(
            columns=["Date", "Weekday", "Flight", "Route", "AD", "Type", "ETA", "ETD"]
        )

    df = pd.DataFrame(records)

    # normalizzazione
    df["Type"] = df["Type"].str.upper().str.strip()
    df["AD"] = df["AD"].str.upper().str.strip()
    df["ETA"] = df["ETA"].str.strip()
    df["ETD"] = df["ETD"].str.strip()

    # solo PAX (CARGO esclusi automaticamente)
    df = df[df["Type"] == "PAX"].copy()
    df.replace({"": None}, inplace=True)

    return df


# =========================
# COSTRUZIONE MATRICE
# =========================

def compute_time_value(row: pd.Series) -> Optional[str]:
    """
    Valore da mettere nella matrice:
    - ETA se AD = A (arrivo)
    - ETD se AD in {P, D, DEP, DEPT} (partenza)
    """
    ad = str(row.get("AD", "")).upper()

    if ad in ("A", "ARR", "ARRIVAL"):
        return row.get("ETA") or None

    if ad in ("P", "D", "DEP", "DEPT", "DEPARTURE"):
        return row.get("ETD") or None

    return None


def build_matrix_for_weekday(flights: pd.DataFrame, weekday: str) -> pd.DataFrame:
    """
    Matrice per un dato weekday:

    - Righe = 3 campi:
        Flight, Route, A/D
    - Colonne = date (es. "02-02", "09-02", "16-02", "23-02")
    - Celle = ETA (se arrivo) o ETD (se partenza)
    """
    if flights.empty:
        return pd.DataFrame()

    subset = flights[flights["Weekday"] == weekday].copy()
    if subset.empty:
        return pd.DataFrame()

    subset["TimeValue"] = subset.apply(compute_time_value, axis=1)
    subset = subset.dropna(subset=["TimeValue"])

    if subset.empty:
        return pd.DataFrame()

    # Pivot con indice multiplo: Flight, Route, AD
    matrix = subset.pivot_table(
        index=["Flight", "Route", "AD"],
        columns="Date",
        values="TimeValue",
        aggfunc="first",
    )

    # Colonne in ordine di data
    matrix = matrix.reindex(sorted(matrix.columns), axis=1)

    # Flight, Route, AD tornano colonne normali
    matrix = matrix.reset_index()

    # Rinomina colonne data in "dd-mm"
    new_cols = []
    for c in matrix.columns:
        if isinstance(c, date):
            new_cols.append(c.strftime("%d-%m"))
        else:
            new_cols.append(c)
    matrix.columns = new_cols

    # Ordina le righe
    matrix = matrix.sort_values(by=["Flight", "Route", "AD"]).reset_index(drop=True)

    return matrix


# =========================
# STYLING PER LA VIEW
# =========================

def style_ad(val: str) -> str:
    """
    Colore per la colonna AD:
    - P → rosso
    - A → verde
    """
    if val == "P":
        return "color: #f97373;"  # rosso soft
    if val == "A":
        return "color: #4ade80;"  # verde soft
    return ""


def style_time(row: pd.Series):
    """
    Colora gli orari (colonne data) in base al valore di AD nella riga:
    - se AD = P → orari rossi
    - se AD = A → orari verdi
    """
    ad = row.get("AD", None)
    color = None
    if ad == "P":
        color = "#f97373"
    elif ad == "A":
        color = "#4ade80"

    styles = []
    for col in row.index:
        # non coloriamo le colonne descrittive
        if col in ("Codice Volo", "Aeroporto", "AD"):
            styles.append("")
            continue

        # colonne data: se c'è un orario e abbiamo un colore, applicalo
        if pd.notna(row[col]) and row[col] != "" and color is not None:
            styles.append(f"color: {color};")
        else:
            styles.append("")
    return styles


# =========================
# UI STREAMLIT
# =========================

def main():
    st.set_page_config(
        page_title="Flight Matrix",
        page_icon="✈️",
        layout="wide",
    )

    # ---- CSS custom per look più moderno ----
    st.markdown(
        """
        <style>
        /* Riduci un po' il padding globale */
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }

        /* Titolo centrale */
        h1 {
            text-align: center;
        }

        /* Card container */
        .info-card {
            background: rgba(15,23,42,0.9);
            padding: 1rem 1.2rem;
            border-radius: 0.9rem;
            border: 1px solid rgba(148,163,184,0.35);
        }

        .info-card p {
            margin-bottom: 0.2rem;
        }

        /* Badge giorno settimana */
        .day-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.75rem;
            border-radius: 999px;
            border: 1px solid rgba(148,163,184,0.8);
            background: rgba(15,23,42,0.9);
            font-size: 0.9rem;
            gap: 0.4rem;
        }

        .day-dot {
            width: 0.6rem;
            height: 0.6rem;
            border-radius: 999px;
            background: #38bdf8;
        }

        /* Legend pill */
        .legend-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.25rem 0.6rem;
            border-radius: 999px;
            border: 1px solid rgba(148,163,184,0.4);
            font-size: 0.8rem;
            margin-right: 0.4rem;
        }

        .legend-color-arr {
            width: 0.9rem;
            height: 0.35rem;
            border-radius: 999px;
            background: #4ade80;
        }
        .legend-color-dep {
            width: 0.9rem;
            height: 0.35rem;
            border-radius: 999px;
            background: #f97373;
        }

        /* Upload label più compatta */
        .uploadedFile { font-size: 0.9rem !important; }

        </style>
        """,
        unsafe_allow_html=True,
    )

    # Titolo con icone aereo
    st.title("✈️ Flight Matrix")

    # Intro card
    with st.container():
        st.markdown(
            """
            <div class="info-card">
                <p>🛫🛬 <strong>Carica il PDF con gli orari dei voli</strong>.</p>
                <p style="margin-top:0.35rem;">L'app:</p>
                <ul style="margin-top:0.15rem;">
                    <li>considera <strong>solo voli passeggeri (PAX)</strong></li>
                    <li>esclude i voli <strong>CARGO</strong></li>
                    <li>raggruppa per <strong>giorno della settimana</strong></li>
                    <li>mostra una <strong>matrice</strong> con i voli per tipologia giorno</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")  # piccolo spazio

    uploaded_file = st.file_uploader("Carica il PDF con gli orari dei voli", type=["pdf"])

    if uploaded_file is None:
        st.info("Carica il PDF per procedere.")
        return

    # Parsing
    with st.spinner("Parsing del PDF in corso..."):
        flights_df = parse_pdf_to_flights_df(uploaded_file)

    if flights_df.empty:
        st.error("Non sono stati trovati voli PAX o la struttura del PDF non è riconosciuta.")
        return

    # Piccole metriche di riepilogo
    unique_days = sorted(flights_df["Date"].unique())
    num_days = len(unique_days)
    num_flights = len(flights_df)

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        st.metric("Voli PAX estratti", num_flights)
    with col2:
        st.metric("Giorni coperti", num_days)
    with col3:
        if num_days > 0:
            start = unique_days[0]
            end = unique_days[-1]
            st.write(
                f"📆 Periodo: **{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}**"
            )

    st.success("Parsing completato.")

    # Giorni effettivamente presenti
    weekdays_present = sorted(
        flights_df["Weekday"].unique(),
        key=lambda x: WEEKDAY_ORDER.index(x),
    )

    # Sidebar
    st.sidebar.header("Filtro giorno")
    selected_weekday = st.sidebar.selectbox(
        "Seleziona giorno della settimana",
        options=weekdays_present,
        format_func=lambda x: WEEKDAY_LABELS_IT.get(x, x),
    )

    matrix_df = build_matrix_for_weekday(flights_df, selected_weekday)

    if matrix_df.empty:
        st.warning("Per il giorno selezionato non sono stati trovati voli PAX con orari validi.")
        return

    # Badge con giorno della settimana
    label_it = WEEKDAY_LABELS_IT.get(selected_weekday, selected_weekday)
    st.markdown(
        f"""
        <div style="margin-top: 1.2rem; margin-bottom: 0.4rem;">
            <span class="day-badge">
                <span class="day-dot"></span>
                <span>{label_it}</span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Legend arrivi/partenze
    st.markdown(
        """
        <div style="margin-bottom: 0.6rem;">
            <span class="legend-pill">
                <span class="legend-color-arr"></span>
                <span>Arrivi (A)</span>
            </span>
            <span class="legend-pill">
                <span class="legend-color-dep"></span>
                <span>Partenze (P)</span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Rinomina colonne per la visualizzazione
    display_df = matrix_df.rename(columns={"Flight": "Codice Volo", "Route": "Aeroporto"})

    # Applica stile AD + orari
    if "AD" in display_df.columns:
        styled_df = (
            display_df
            .style
            .apply(style_time, axis=1)
            .applymap(style_ad, subset=["AD"])
        )
    else:
        styled_df = display_df.style  # fallback

    st.dataframe(styled_df, use_container_width=True, height=650)

    # Export CSV (con intestazioni italiane)
    csv_buffer = display_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Scarica matrice in CSV",
        data=csv_buffer,
        file_name=f"flight_matrix_{label_it.lower()}.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
