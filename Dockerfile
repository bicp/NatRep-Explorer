FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx ./data/
RUN python scripts/build_db.py data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx data/natrep.db && rm data/*.xlsx
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
