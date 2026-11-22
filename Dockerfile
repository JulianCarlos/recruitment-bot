FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py core.py activity.py voice.py presentation.py settings.py setup_ui.py configure.py config.example.toml ./
CMD ["python", "-u", "bot.py"]
