FROM python:3.12-slim

WORKDIR /app

COPY requirements/runtime.txt requirements/runtime.txt
RUN pip install --no-cache-dir -r requirements/runtime.txt

COPY app/ app/
COPY alembic.ini .
COPY contracts/events/ contracts/events/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
