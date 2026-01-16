# streamlit_app.py

"""
App Streamlit per:
1. Caricare un PDF con orari voli (febbraio 2026).
2. Parsare le tabelle giornaliere (solo PAX).
3. Raggruppare per giorno della settimana.
4. Visualizzare una matrice voli × date.
5. Esportare la matrice in CSV.
"""

import io
import re
from datetime import date
from typing import Optional, List

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


# =========================
# Parsing PDF (tabella per tabella, su TUTTE le pagine)
# =========================

def parse_pdf_to_flights_df(file_obj: io.BytesIO) -> pd.DataFrame:
    """
    Parser per il PDF "orario voli febbraio 2026".

    Ogni tabella valida ha:
        riga 0: 'Mon 2 Feb 2026'
        riga 1: 'Flight','Route','A/D','Type','ETA','ETD'
        righe successive: dati voli

    Restituisce un DataFrame con colonne:
        ['Date', 'Weekday', 'Flight', 'Route', 'AD', 'Type', 'ETA', 'ETD']
    (poi filtreremo a PAX).
    """
    records: List[dict] = []

    # pattern per la prima cella: "Mon 2 Feb 2026"
    day_pattern = re.compile(
        r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\d{1,2})\s+Feb\s+2026$"
    )

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for tbl in tables:
                # scarta tabelle troppo corte (non possono essere giorni)
                if not tbl or len(tbl) < 3:
                    continue

                first_row = tbl[0]
                first_cell = (first_row[0] or "").strip() if first_row else ""
                m = day_pattern.match(first_cell)
                if not m:
                    # non è una tabella giornaliera → ignora
                    continue

                weekday = m.group(1)          # es. "Mon"
                day = int(m.group(2))         # es. 2
                current_date = date(2026, 2, day)

                # righe di dati: da index 2 in poi
                for row in tbl[2:]:
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
                            "Date": current_date,
                            "Weekday": weekday,
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

    # Normalizzazioni base
    df["Type"] = df["Type"].str.upper().str.strip()
    df["AD"] = df["AD"].str.upper().str.strip()
    df["ETA"] = df["ETA"].str.strip()
    df["ETD"] = df["ETD"].str.strip()

    # Solo PAX ⇒ CARGO automaticamente esclusi
    df = df[df["Type"] == "PAX"].copy()

    # Sostituisci stringhe vuote con None per ETA/ETD
    df.replace({"": None}, inplace=True)

    return df


# =========================
# Costruzione matrice
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
    - Colonne = date (es. "02-02", "09-02", ...)
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

    # Pivot con indice multiplo sulle 3 colonne richieste
    matrix = subset.pivot_table(
        index=["Flight", "Route", "AD"],
        columns="Date",
        values="TimeValue",
        aggfunc="first",
    )

    # Ordina le colonne per data
    matrix = matrix.reindex(sorted(matrix.columns), axis=1)

    # Porta Flight, Route, AD a colonne "normali"
    matrix = matrix.reset_index()

    # Rinomina colonne data in "dd-mm"
    new_cols = []
    for c in matrix.columns:
        if isinstance(c, date):
            new_cols.append(c.strftime("%d-%m"))
        else:
            new_cols.append(c)
    matrix.columns = new_cols

    # Ordina le righe per Flight, Route, AD
    matrix = matrix.sort_values(by=["Flight", "Route", "AD"]).reset_index(drop=True)

    return matrix


# =========================
# UI Streamlit
# =========================

def main():
    st.set_page_config(
        page_title="Flight Matrix (PAX only) - February 2026",
        layout="wide",
    )

    st.title("📅 Flight Matrix PAX – Febbraio 2026")

    st.markdown(
        """
        Carica il **PDF con gli orari voli** di febbraio 2026.

        L'app:
        - considera **solo voli passeggeri (PAX)**,
        - esclude i voli **CARGO**,
        - raggruppa per **giorno della settimana**,
        - mostra una **matrice** con:
            - righe = `Flight`, `Route`, `A/D`,
            - colonne = date del mese,
            - celle = ETA / ETD a seconda del tipo (arrivo/partenza).
        """
    )

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

    st.success(f"Parsing completato. Voli PAX trovati: **{len(flights_df)}**.")

    # Giorni effettivamente presenti
    weekdays_present = sorted(
        flights_df["Weekday"].unique(),
        key=lambda x: WEEKDAY_ORDER.index(x),
    )

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

    label_it = WEEKDAY_LABELS_IT.get(selected_weekday, selected_weekday)
    st.subheader(f"Matrice voli PAX – {selected_weekday} ({label_it})")
    st.caption(
        "Righe = Flight | Route | A/D – Colonne = date di febbraio 2026 – Celle = ETA/ETD."
    )

    st.dataframe(matrix_df, use_container_width=True, height=600)

    # Export CSV
    csv_buffer = matrix_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Scarica matrice in CSV",
        data=csv_buffer,
        file_name=f"flight_matrix_{selected_weekday.lower()}.csv",
        mime="text/csv",
    )

    # Info extra nel sidebar: qui puoi verificare che ci siano TUTTE le date 1–28
    with st.sidebar.expander("Dettagli dataset", expanded=False):
        st.write("Date riconosciute:")
        st.write(sorted(flights_df["Date"].unique()))
        st.write("Numero voli PAX per data:")
        st.dataframe(
            flights_df.groupby("Date")["Flight"].count().rename("N_voli").to_frame()
        )


if __name__ == "__main__":
    main()
