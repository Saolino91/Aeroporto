# streamlit_app.py

"""
App Streamlit per:
1. Caricare un PDF con orari voli (febbraio 2026, layout tipo "Flight RouteA/D Type ETA ETD").
2. Parsare le righe testuali (solo PAX) anche quando ci sono più voli sulla stessa riga.
3. Raggruppare per giorno della settimana.
4. Visualizzare una matrice voli × date.
5. Esportare la matrice in CSV.
"""

import io
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
# Parsing PDF specifico per il tuo file
# =========================

def parse_pdf_to_flights_df(file_obj: io.BytesIO) -> pd.DataFrame:
    """
    Parser ad hoc per il PDF dell'aeroporto (Feb 2026).

    Layout:
        Mon 2 Feb 2026
        Flight RouteA/D Type ETA ETD
        DX1702 FCO P PAX 06:05
        DJ6402 BGY A PAX 07:20 DX1701 FCO A PAX 08:55 ...
        ...

    Ogni riga (dopo l'header) contiene N blocchi da 5 token:
        Flight, Route, A/D, Type, Time

    Restituisce un DataFrame con colonne:
        ['Date', 'Weekday', 'Flight', 'Route', 'AD', 'Type', 'ETA', 'ETD']
    (solo Type == 'PAX')
    """
    records: List[dict] = []

    current_date: Optional[date] = None
    current_weekday: Optional[str] = None

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            # splitlines() preserva l'ordine; strip per togliere spazi laterali
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

            for line in lines:
                # 1) Salta la riga iniziale "from Sun 1 Feb 2026 to Sat 28 Feb 2026"
                if line.startswith("from "):
                    continue

                # 2) Riconosci intestazioni di giorno: "Mon 2 Feb 2026"
                #    (sempre Feb 2026 in questo file)
                parts = line.split()
                if (
                    len(parts) == 4
                    and parts[0] in WEEKDAY_ORDER
                    and parts[2] == "Feb"
                    and parts[3] == "2026"
                ):
                    # es: ["Mon", "2", "Feb", "2026"]
                    weekday_str = parts[0]
                    day = int(parts[1])
                    current_date = date(2026, 2, day)
                    current_weekday = weekday_str
                    continue

                # 3) Salta la riga di intestazione tabellare
                #    "Flight RouteA/D Type ETA ETD" (può essere con o senza spazi tra Route e A/D)
                line_no_space = line.replace(" ", "")
                if line_no_space.startswith("FlightRouteA/DTypeETAETD"):
                    continue

                # 4) Se non abbiamo ancora una data corrente, la riga non è utilizzabile
                if current_date is None or current_weekday is None:
                    continue

                # 5) Righe voli: token in gruppi da 5
                tokens = line.split()
                while len(tokens) >= 5:
                    flight, route, ad, typ, time_str = tokens[:5]
                    tokens = tokens[5:]

                    records.append(
                        {
                            "Date": current_date,
                            "Weekday": current_weekday,
                            "Flight": flight,
                            "Route": route,
                            "AD": ad,
                            "Type": typ,
                            "Time": time_str,
                        }
                    )

    if not records:
        return pd.DataFrame(
            columns=["Date", "Weekday", "Flight", "Route", "AD", "Type", "ETA", "ETD"]
        )

    df = pd.DataFrame(records)

    # Normalizzazioni
    df["Type"] = df["Type"].str.upper().str.strip()
    df["AD"] = df["AD"].str.upper().str.strip()

    # Solo voli passeggeri (PAX), escludiamo CARGO automaticamente
    df = df[df["Type"] == "PAX"].copy()

    # Assegna ETA/ETD sulla base di A/D
    df["ETA"] = df.apply(
        lambda r: r["Time"] if r["AD"] == "A" else None,
        axis=1,
    )
    df["ETD"] = df.apply(
        lambda r: r["Time"] if r["AD"] in ("P", "D", "DEP", "DEPT") else None,
        axis=1,
    )

    # Mantieni solo le colonne richieste
    df = df[["Date", "Weekday", "Flight", "Route", "AD", "ETA", "ETD"]]

    return df


# =========================
# Costruzione matrice
# =========================

def compute_time_value(row: pd.Series) -> Optional[str]:
    """
    Valore da mettere nella matrice:
    - ETA se AD indica arrivo
    - ETD se AD indica partenza
    """
    ad = str(row.get("AD", "")).upper()

    if ad in ("A", "ARR", "ARRIVAL"):
        return row.get("ETA") or None

    if ad in ("P", "D", "DEP", "DEPT", "DEPARTURE"):
        return row.get("ETD") or None

    return None


def build_matrix_for_weekday(flights: pd.DataFrame, weekday: str) -> pd.DataFrame:
    """
    Matrice:
    - Righe = "Flight | Route | A/D"
    - Colonne = Date (tutte le date con quel weekday)
    - Celle = orario (ETA se arrivo, ETD se partenza)
    """
    if flights.empty:
        return pd.DataFrame()

    subset = flights[flights["Weekday"] == weekday].copy()
    if subset.empty:
        return pd.DataFrame()

    subset["TimeValue"] = subset.apply(compute_time_value, axis=1)
    subset = subset.dropna(subset=["TimeValue"])

    # Etichetta riga
    subset["FlightKey"] = (
        subset["Flight"].astype(str)
        + " | "
        + subset["Route"].astype(str)
        + " | "
        + subset["AD"].astype(str)
    )

    # Pivot
    matrix = subset.pivot_table(
        index="FlightKey",
        columns="Date",
        values="TimeValue",
        aggfunc="first",
    )

    # Ordina colonne per data
    matrix = matrix.reindex(sorted(matrix.columns), axis=1)

    # Ordina righe per chiave
    matrix = matrix.sort_index()

    # Colonne in formato "dd-mm"
    matrix.columns = [
        d.strftime("%d-%m") if isinstance(d, date) else str(d)
        for d in matrix.columns
    ]

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
        - ignora i voli **CARGO**,
        - raggruppa i dati per **giorno della settimana**,
        - mostra una **matrice**: righe = *Flight | Route | A/D*, colonne = *date*.
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
        st.error(
            "Non sono stati trovati voli PAX o non è stato possibile riconoscere la struttura del PDF."
        )
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

    # Sottotitolo senza f-string problematica
    label_it = WEEKDAY_LABELS_IT.get(selected_weekday, selected_weekday)
    st.subheader(f"Matrice voli PAX – {selected_weekday} ({label_it})")
    st.caption("Righe = Flight | Route | A/D – Colonne = date di febbraio 2026 – Celle = ETA/ETD.")

    st.dataframe(matrix_df, use_container_width=True, height=600)

    # Export CSV
    csv_buffer = matrix_df.to_csv(index=True).encode("utf-8")
    st.download_button(
        label="⬇️ Scarica matrice in CSV",
        data=csv_buffer,
        file_name=f"flight_matrix_{selected_weekday.lower()}.csv",
        mime="text/csv",
    )

    # Info extra nel sidebar
    with st.sidebar.expander("Dettagli dataset", expanded=False):
        st.write("Date riconosciute:")
        st.write(sorted(flights_df["Date"].unique()))
        st.write("Numero voli per weekday:")
        st.dataframe(
            flights_df.groupby("Weekday")["Flight"].count().rename("N_voli").to_frame()
        )


if __name__ == "__main__":
    main()
