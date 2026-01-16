import calendar
from datetime import datetime
from io import BytesIO
import re

import pandas as pd
import pdfplumber
import streamlit as st


st.set_page_config(page_title="Flight Schedule Matrix", layout="wide")


WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


DATE_REGEXES = [
    re.compile(r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})"),
    re.compile(r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})"),
]


ROW_REGEX = re.compile(
    r"^(?P<flight>\S+)\s+(?P<route>\S+)\s+(?P<ad>[APD])\s+(?P<type>\S+)\s+(?P<eta>\d{2}:\d{2})?\s*(?P<etd>\d{2}:\d{2})?"
)


st.title("Flight Schedule Matrix (PAX)")
st.markdown(
    """
    Carica un PDF con gli orari dei voli di febbraio 2026. L'app estrarrà solo i voli **PAX**, """
    """raggrupperà i dati per giorno della settimana e mostrerà una matrice con ETA/ETD."""
)


@st.cache_data(show_spinner=False)
def parse_pdf(file_bytes: bytes) -> pd.DataFrame:
    records = []
    current_date = None

    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            for line in lines:
                detected_date = extract_date(line)
                if detected_date:
                    current_date = detected_date
                    continue

                if "flight" in line.lower() and "route" in line.lower():
                    continue

                match = ROW_REGEX.match(line)
                if not match:
                    continue

                row = match.groupdict()
                if row["type"].upper() != "PAX":
                    continue

                if not current_date:
                    continue

                eta = row.get("eta")
                etd = row.get("etd")
                if not eta and not etd:
                    continue

                records.append(
                    {
                        "date": current_date,
                        "flight": row["flight"],
                        "route": row["route"],
                        "ad": row["ad"],
                        "eta": eta,
                        "etd": etd,
                    }
                )

    return pd.DataFrame(records)


def extract_date(line: str) -> datetime | None:
    lowered = line.lower()
    for regex in DATE_REGEXES:
        match = regex.search(lowered)
        if not match:
            continue

        day = int(match.group("day"))
        year = int(match.group("year"))
        month_raw = match.group("month")
        if month_raw.isdigit():
            month = int(month_raw)
        else:
            month = MONTH_MAP.get(month_raw)

        if not month:
            continue

        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    return None


def build_matrix(data: pd.DataFrame, weekday: str) -> pd.DataFrame:
    data = data.copy()
    data["weekday"] = data["date"].dt.strftime("%a")
    data = data[data["weekday"] == weekday]

    if data.empty:
        return pd.DataFrame()

    data["label"] = data.apply(
        lambda row: f"{row['flight']} | {row['route']} | {row['ad']}", axis=1
    )

    data["value"] = data.apply(
        lambda row: row["eta"] if row["ad"] == "A" else row["etd"], axis=1
    )

    matrix = (
        data.pivot_table(
            index="label",
            columns="date",
            values="value",
            aggfunc="first",
        )
        .sort_index()
    )

    matrix.columns = [col.strftime("%d %b") for col in matrix.columns]
    return matrix


def dates_for_weekday(year: int, month: int, weekday: str) -> list[str]:
    weekday_index = WEEKDAY_ORDER.index(weekday)
    dates = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        date_value = datetime(year, month, day)
        if date_value.weekday() == weekday_index:
            dates.append(date_value.strftime("%d %b"))
    return dates


uploaded_file = st.file_uploader("Carica il PDF dei voli", type=["pdf"])

if uploaded_file:
    data = parse_pdf(uploaded_file.getvalue())
    if data.empty:
        st.warning("Nessun dato PAX trovato nel PDF.")
    else:
        data["date"] = pd.to_datetime(data["date"])
        weekday = st.selectbox("Seleziona il giorno della settimana", WEEKDAY_ORDER)
        matrix = build_matrix(data, weekday)

        if matrix.empty:
            st.info("Nessun volo PAX disponibile per il giorno selezionato.")
        else:
            expected_dates = dates_for_weekday(2026, 2, weekday)
            matrix = matrix.reindex(columns=expected_dates)

            st.subheader(f"Matrice voli PAX - {weekday}")
            st.dataframe(matrix, use_container_width=True)

            csv_data = matrix.to_csv(index=True).encode("utf-8")
            st.download_button(
                "Scarica CSV",
                data=csv_data,
                file_name=f"pax_matrix_{weekday.lower()}.csv",
                mime="text/csv",
            )
else:
    st.info("Carica un PDF per iniziare.")
