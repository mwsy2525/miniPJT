"""RAG 파이프라인 진입점(제출 규약). 구현은 src/retrieval/service.py에 있다.

이 모듈은 이름을 다시 내보내기만 한다. 테스트에서 함수를 바꿔 끼울 때는 src.retrieval.service를 대상으로 한다.
"""
from src.retrieval.service import *  # noqa: F401,F403
from src.retrieval.service import _index_cache  # noqa: F401
