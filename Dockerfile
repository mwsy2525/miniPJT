# CCAR-F 모의시험 Agent 실행 이미지
# 빌드: docker build -t ccarf-mock-exam .
# 실행: docker run --rm -p 8000:8000 --env-file .env ccarf-mock-exam
# 비밀 값(.env)은 이미지에 넣지 않고 실행할 때 --env-file로 넘긴다(.dockerignore에서 제외).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY data ./data
COPY web ./web
COPY evaluation ./evaluation

EXPOSE 8000
CMD ["uvicorn", "src.agent:app", "--host", "0.0.0.0", "--port", "8000"]
