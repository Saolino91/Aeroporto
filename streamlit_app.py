from datetime import datetime
from io import BytesIO
import re
import pandas as pd
import pdfplumber
import streamlit as st
import calendar

# Configurazione della pagina
st.set_page_config(page_title="Flight Schedule Matrix", layout="wide")

# Dizionario per convertire i nomi (in inglese) dei mesi
MONTH_MAP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9,
    "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Espressioni regolari per trovare date e righe dei voli
DATE_REGEXES = [
    re.compile(r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})"),
    re.compile(r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})"),
]
ROW_REGEX = re.compile(
    r"^(?P<flight>\S+)\s+(?P<route>\S+)\s+(?P<ad>[APD])\s+(?P<type>\S+)\s+(?P<eta>\d{2}:\d{2})?\s*(?P<etd>\d{2}:\d{2})?"
)

# Titolo dell'app
st.title("Flight Schedule Matrix (PAX)")
st.markdown("""
    Carica un PDF con gli orari dei voli di tutti i giorni. 
    L'app estrarrà i voli **PAX**, li raggrupperà per giorno della settimana e mostrerà una matrice degli orari (ETA/ETD).
""")

@st.cache_data(show_spinner=False)
def parse_pdf(file_bytes: bytes) -> pd.DataFrame:
    """
    Funzione per leggere il contenuto di un PDF e estrarre i dati di voli PAX
    """
    records = []
    current_date = None

    # Apriamo il file PDF con pdfplumber
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]

            for line in lines:
                # Identifica la data relativa alla colonna
                detected_date = extract_date(line)
                if detected_date:
                    current_date = detected_date
                    continue

                # Salta i titoli
                if "flight" in line.lower() and "route" in line.lower():
                    continue

                # Estrarre dati validi usando ROW_REGEX
                match = ROW_REGEX.match(line)
                if match:
                    row = match.groupdict()
                    
                    # Filtra i voli di tipo PAX
                    if row["type"].upper() != "PAX" or not current_date:
                        continue

                    eta = row.get("eta")
                    etd = row.get("etd")

                    # Verifica che ci sia un orario (ETA o ETD)
                    if not eta and not etd:
                        continue

                    records.append({
                        "date": current_date,
                        "flight": row["flight"],
                        "route": row["route"],
                        "ad": row["ad"],
                        "eta": eta,
                        "etd": etd,
                    })
    return pd.DataFrame(records)

def extract_date(line: str) -> datetime | None:
    """
    Estrae una data da una riga di testo utilizzando le espressioni regolari
    """
    lowered = line.lower()
    for regex in DATE_REGEXES:
        match = regex.search(lowered)
        
        if match:
            day = int(match.group("day"))
            year = int(match.group("year"))
            month_raw = match.group("month")

            # Converti il mese in numero
            if month_raw.isdigit():
                month = int(month_raw)
            else:
                month = MONTH_MAP.get(month_raw.lower())

            if month:
                try:
                    return datetime(year, month, day)
                except ValueError:
                    pass  # Salta date non valide
    return None

WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_full_matrix(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Costruisce una matrice per ogni giorno della settimana.
    """
    data["weekday"] = data["date"].dt.strftime("%a")  # Aggiungi il giorno della settimana
    matrices = {}

    for weekday in WEEKDAY_ORDER:  # WEEKDAY_ORDER è già ordinato da Lun a Dom
        filtered_data = data[data["weekday"] == weekday]

        if filtered_data.empty:
            matrices[weekday] = pd.DataFrame()
            continue

        filtered_data["label"] = filtered_data.apply(
            lambda row: f"{row['flight']} | {row['route']} | {row['ad']}", axis=1
        )
        filtered_data["value"] = filtered_data.apply(
            lambda row: row["eta"] if row["ad"] == "A" else row["etd"], axis=1
        )

        matrix = filtered_data.pivot_table(
            index="label",
            columns="date",
            values="value",
            aggfunc="first"
        ).sort_index()

        # Trasforma le date in una rappresentazione leggibile
        matrix.columns = [col.strftime("%d %b %Y") for col in matrix.columns]
        matrices[weekday] = matrix

    return matrices

# Caricamento del file PDF
uploaded_file = st.file_uploader("Carica il PDF dei voli", type=["pdf"])

if uploaded_file:
    # Parsing del file PDF
    data = parse_pdf(uploaded_file.getvalue())
    if data.empty:
        st.warning("Nessun dato PAX trovato nel PDF.")
    else:
        data["date"] = pd.to_datetime(data["date"])  # Converti la colonna "date"
        
        # Genera una matrice per ogni giorno della settimana
        matrices = build_full_matrix(data)

        # Selezione del giorno da visualizzare
        selected_weekday = st.selectbox("Seleziona il giorno della settimana", WEEKDAY_ORDER)

        selected_matrix = matrices[selected_weekday]

        if selected_matrix.empty:
            st.info(f"Nessun volo PAX disponibile per il giorno {selected_weekday}.")
        else:
            st.subheader(f"Matrice voli PAX - {selected_weekday}")
            st.dataframe(selected_matrix, use_container_width=True)

            # Download del CSV
            csv_data = selected_matrix.to_csv(index=True).encode("utf-8")
            st.download_button(
                "Scarica CSV",
                data=csv_data,
                file_name=f"pax_matrix_{selected_weekday.lower()}.csv",
                mime="text/csv",
            )
else:
    st.info("Carica un PDF per iniziare.")
