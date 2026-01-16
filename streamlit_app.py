# streamlit_app.py

"""
App Streamlit per:
1. Caricare un PDF con orari voli (febbraio 2026).
2. Parsare le tabelle (solo PAX) e strutturare i dati.
3. Raggruppare per giorno della settimana.
4. Visualizzare una matrice (voli x date) filtrata per giorno della settimana.
5. Esportare la matrice in CSV.
"""

import io
import re
from datetime import datetime, date
from typing import Optional, List

import pandas as pd
import streamlit as st
import pdfplumber


# =========================
# Utilità parsing PDF
# =========================

MONTH_MAP_ENG = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_date_from_text(text: str) -> Optional[date]:
    """
    Cerca una data nel testo della pagina.
    Adatta ai formati più tipici delle planning di volo di un aeroporto.

    Esempi gestiti:
    - "01 FEB 2026"
    - "1 FEB 2026"
    - "01/02/2026"
    - "2026-02-01"
    """
    if not text:
        return None

    # 1) Formato tipo "01 FEB 2026"
    m = re.search(r"\b(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+2026\b", text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        month = MONTH_MAP_ENG[m.group(2).upper()]
        return date(2026, month, day)

    # 2) Formato numerico "01/02/2026" o "1/2/2026"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/2026\b", text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        return date(2026, month, day)

    # 3) Formato ISO "2026-02-01"
    m = re.search(r"\b2026-(\d{2})-(\d{2})\b", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        return date(2026, month, day)

    return None


def normalize_headers(raw_headers: List[str]) -> List[str]:
    """
    Normalizza le intestazioni delle tabelle in:
    Flight, Route, AD, Type, ETA, ETD

    Gestisce varianti tipo:
    - "FLIGHT", "FLIGHT NO", "FLT"
    - "A/D", "AD"
    """
    mapped = []
    for h in raw_headers:
        if h is None:
            mapped.append(None)
            continue
        key = re.sub(r"\W+", "", str(h)).upper()  # rimuove spazi, slash, punti, ecc.

        if key in ("FLIGHT", "FLIGHTNO", "FLT", "FLIGHTNUMBER"):
            mapped.append("Flight")
        elif key in ("ROUTE", "ROUTING"):
            mapped.append("Route")
        elif key in ("AD", "AD1", "ARRDEP", "ARRDEPT"):
            mapped.append("AD")
        elif key in ("TYPE", "TYP"):
            mapped.append("Type")
        elif key == "ETA":
            mapped.append("ETA")
        elif key == "ETD":
            mapped.append("ETD")
        else:
            mapped.append(h)  # lascio così com'è se non riconosciuto
    return mapped


def clean_table_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pulisce una singola tabella:
    - Rinomina colonne.
    - Tiene solo Flight, Route, AD, Type, ETA, ETD se presenti.
    - Elimina righe completamente vuote.
    """
    if df.empty:
        return df

    # Usa la prima riga come header se non ci sono header significativi
    # (dipende da come pdfplumber estrae la tabella)
    # Qui assumiamo che pdfplumber abbia già impostato le header con la prima riga,
    # ma se la prima riga è dati veri, si può adattare.
    df = df.copy()

    # Normalizza header
    df.columns = normalize_headers(list(df.columns))

    # Teniamo solo le colonne che ci interessano se esistono
    needed_cols = ["Flight", "Route", "AD", "Type", "ETA", "ETD"]
    cols_present = [c for c in needed_cols if c in df.columns]
    df = df[cols_present]

    # Rimuove righe completamente vuote
    df = df.dropna(how="all")

    # Strippa spazi
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()

    return df


def parse_pdf_to_flights_df(file_obj: io.BytesIO) -> pd.DataFrame:
    """
    Legge il PDF e restituisce un DataFrame con colonne:
    ['Date', 'Weekday', 'Flight', 'Route', 'AD', 'Type', 'ETA', 'ETD']
    Solo voli PAX (Type == 'PAX'), senza righe incomplete.
    """
    all_rows = []

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            page_date = parse_date_from_text(text)
            if page_date is None:
                # Se non troviamo la data, saltiamo la pagina
                continue

            weekday = page_date.strftime("%a")  # "Mon", "Tue", ...

            tables = page.extract_tables()
            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue

                # Prima riga = header
                header = normalize_headers(tbl[0])
                rows = tbl[1:]
                df = pd.DataFrame(rows, columns=header)
                df = clean_table_df(df)

                if df.empty:
                    continue

                # Aggiungi data e weekday
                df["Date"] = page_date
                df["Weekday"] = weekday

                all_rows.append(df)

    if not all_rows:
        return pd.DataFrame(columns=["Date", "Weekday", "Flight", "Route", "AD", "Type", "ETA", "ETD"])

    flights = pd.concat(all_rows, ignore_index=True)

    # Assicuriamoci che tutte le colonne siano presenti
    for col in ["Flight", "Route", "AD", "Type", "ETA", "ETD", "Date", "Weekday"]:
        if col not in flights.columns:
            flights[col] = None

    # Filtra solo voli passeggeri (Type == 'PAX') e non cargo
    flights["Type_norm"] = flights["Type"].str.upper()
    flights = flights[flights["Type_norm"] == "PAX"].copy()

    # Elimina righe con campi fondamentali mancanti
    flights = flights.replace({"": None, "None": None})
    flights = flights.dropna(subset=["Flight", "Route", "AD"])

    # Normalizza A/D (arrivo/partenza)
    flights["AD"] = flights["AD"].str.upper().str.strip()

    # Mantieni solo ciò che serve
    flights = flights[["Date", "Weekday", "Flight", "Route", "AD", "ETA", "ETD"]]

    return flights


# =========================
# Costruzione matrice
# =========================

def compute_time_value(row: pd.Series) -> Optional[str]:
    """
    Ritorna il valore da mettere nella matrice:
    - ETA se AD in ('A', 'ARR', ecc.)
    - ETD se AD in ('D', 'DEP', 'P' = partenza)
    """
    ad = str(row.get("AD", "")).upper()

    if ad in ("A", "ARR", "ARRIVAL"):
        return row.get("ETA") or None

    if ad in ("D", "DEP", "DEPT", "DEPARTURE", "P"):
        return row.get("ETD") or None

    return None


def build_matrix_for_weekday(flights: pd.DataFrame, weekday: str) -> pd.DataFrame:
    """
    Costruisce la matrice:
    - Righe = Flight + Route + AD (etichetta)
    - Colonne = Date (tutte le date che sono di quel weekday)
    - Valore = ETA (se arrivo) o ETD (se partenza)
    """
    if flights.empty:
        return pd.DataFrame()

    subset = flights[flights["Weekday"] == weekday].copy()
    if subset.empty:
        return pd.DataFrame()

    subset["TimeValue"] = subset.apply(compute_time_value, axis=1)

    # Rimuove righe senza orario utile
    subset = subset.dropna(subset=["TimeValue"])

    # Chiave riga: Flight | Route | AD
    subset["FlightKey"] = subset["Flight"] + " | " + subset["Route"] + " | " + subset["AD"]

    # Pivot: index = FlightKey, columns = Date, values = TimeValue
    matrix = subset.pivot_table(
        index="FlightKey",
        columns="Date",
        values="TimeValue",
        aggfunc="first"  # se ci fossero duplicati
    )

    # Ordina le colonne per data
    matrix = matrix.reindex(sorted(matrix.columns), axis=1)

    # Ordina le righe per codice volo (prima parte di FlightKey)
    matrix = matrix.sort_index()

    # Opzionale: format date columns come stringhe "dd-mm"
    matrix.columns = [d.strftime("%d-%m") if isinstance(d, (datetime, date)) else str(d) for d in matrix.columns]

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
        Carica un **PDF con orari voli** (uno o più blocchi per giorno di febbraio 2026).
        L'app:
        - considera **solo voli passeggeri (PAX)**,
        - ignora i voli **CARGO**,
        - raggruppa per **giorno della settimana**,
        - mostra una **matrice voli × date** per il giorno selezionato.
        """
    )

    uploaded_file = st.file_uploader("Carica il PDF con gli orari dei voli", type=["pdf"])

    if uploaded_file is None:
        st.info("Attendi il caricamento del PDF per vedere i risultati.")
        return

    # Parsing PDF
    with st.spinner("Parsing del PDF in corso..."):
        # pdfplumber accetta un file-like; Streamlit fornisce un UploadedFile che è già file-like.
        flights_df = parse_pdf_to_flights_df(uploaded_file)

    if flights_df.empty:
        st.error("Non sono stati trovati voli PAX validi nel PDF (o non è stata riconosciuta nessuna data).")
        return

    # Info sintetiche
    st.success(f"Parsing completato. Voli PAX trovati: **{len(flights_df)}**.")
    st.caption(
        "I dati grezzi non vengono mostrati in un'unica tabella per evitare una 'tabella gigante', "
        "come da specifiche."
    )

    # Giorni della settimana effettivamente presenti
    weekdays_present = sorted(flights_df["Weekday"].unique(), key=lambda x: WEEKDAY_ORDER.index(x))

    st.sidebar.header("Filtro")
    selected_weekday = st.sidebar.selectbox(
        "Seleziona giorno della settimana",
        options=weekdays_present,
        format_func=lambda x: {
            "Mon": "Lunedì",
            "Tue": "Martedì",
            "Wed": "Mercoledì",
            "Thu": "Giovedì",
            "Fri": "Venerdì",
            "Sat": "Sabato",
            "Sun": "Domenica",
        }.get(x, x),
    )

    matrix_df = build_matrix_for_weekday(flights_df, selected_weekday)

    if matrix_df.empty:
        st.warning("Per il giorno selezionato non sono stati trovati voli PAX con orari validi.")
        return

    st.subheader(
        f"Matrice voli PAX – {selected_weekday} "
        f"({{'Mon':'Lunedì','Tue':'Martedì','Wed':'Mercoledì','Thu':'Giovedì','Fri':'Venerdì','Sat':'Sabato','Sun':'Domenica'}[selected_weekday]})"
    )
    st.caption("Righe = Flight | Route | A/D – Colonne = Date di febbraio 2026 – Celle = ETA/ETD.")

    st.dataframe(
        matrix_df,
        use_container_width=True,
        height=600
    )

    # Esportazione CSV
    csv_buffer = matrix_df.to_csv(index=True).encode("utf-8")
    st.download_button(
        label="⬇️ Scarica matrice in CSV",
        data=csv_buffer,
        file_name=f"flight_matrix_{selected_weekday.lower()}.csv",
        mime="text/csv",
    )

    # (Opzionale) debug / info aggiuntive nel sidebar
    with st.sidebar.expander("Dettagli dataset", expanded=False):
        st.write("Date riconosciute:", sorted(flights_df["Date"].unique()))
        st.write("Numero voli per weekday:")
        st.dataframe(
            flights_df.groupby("Weekday")["Flight"].count().rename("N_voli").to_frame()
        )


if __name__ == "__main__":
    main()
