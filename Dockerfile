# CCAR-F 모의시험 Agent 실행 이미지
# 빌드: docker build -t ccarf-mock-exam .
# 실행: docker run --rm -p 8000:8000 -v ccarf-state:/app/state --env-file .env ccarf-mock-exam
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

# 컨테이너를 다시 띄워도 남아야 하는 것(진도 DB, 검색 인덱스, 요청 트레이스)은 /app/state 볼륨에 둔다.
# -v ccarf-state:/app/state 로 이름 있는 볼륨을 붙이면 진도 기록이 유지되고, 검색 인덱스를 매번 다시 만들지 않는다.
ENV PROGRESS_DB_PATH=/app/state/progress.db \
    CHROMA_DIR=/app/state/chroma \
    TRACE_LOG_DIR=/app/state/traces
VOLUME ["/app/state"]

EXPOSE 8000
CMD ["uvicorn", "src.agent:app", "--host", "0.0.0.0", "--port", "8000"]
