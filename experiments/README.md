# 실험 자료

- [Qwen 최적화](qwen3_optimization/README.md): 새 OPT 연구의 실행·보존 규칙. 재사용 가능한 구현은 `src/edge_asr/tools/optimization/`에 둡니다.
- [M1 Zipformer 원본](m1_zipformer/README.md): 이전 탐색 프로브·참조 코드·작은 요약·체크섬을 원래 경로에 보존합니다. 초기 원시 로그 3개는 `archive-initial-zipformer-20261028` 태그에 있습니다.

이전 M2/M3의 구현은 `src/edge_asr/legacy/`, 계획은 [보관 문서](../docs/archive/model-comparison/README.md)입니다. 단순히 폴더를 채우려고 미구현 profiler/backend/quantization 스크립트를 만들지 않습니다. 실제 검증한 일회성 코드만 목적과 출처를 적어 추가합니다.
