# Qwen 중심 파일·폴더 정리 — 2026-11-10

실행 ID `layout-cleanup-20261110-01`, 정리 전 main `9957094`, 실제 회귀 소스 `89e4a8b`입니다. 흩어진 Qwen 실행·C++·입력 준비를 역할별로 모았습니다. [이전→현재 경로](../../../docs/reference/structure_migration.md).

| 역할 | 현재 위치 |
| --- | --- |
| Qwen Python | `src/edge_asr/qwen/` — `onnx.py`, `gguf.py`, `benchmark.py`, `profiling.py` |
| C++·빌드 | `native/gguf/`, `native/ort_trace/` |
| 데이터 준비 | `tools prepare-fixed100`, `prepare-baseline100`, `prepare-opt0` |
| 현재 기준 설정 | `configs/onnx.toml`, `configs/gguf.toml` |
| Qwen 테스트 | `tests/qwen/` |

중복 OPT-0/OPT-2 설정2개와 중복 안내 README3개를 제거하고 빌드 안내를 [native/README](../../../native/README.md)로 합쳤습니다. README는102줄에서73줄로 줄였습니다. 현재 두 설정·C++ 소스와 주 어댑터/기준선의 내용은 그대로이며 import·설정 조회 경로만 갱신했습니다. Python 내부 import와 옛 준비 스크립트 경로는 새 위치/CLI를 사용합니다. 기존 benchmark/profile 명령 인자는 유지합니다.

Host/RP5 각347개 테스트(native mock·문법 검사 포함), 기존 공통 집계50개가 통과했습니다. 과거 집계의 사용자 정의 확장 필드는 재계산 범위 밖입니다. 새 준비 CLI의100개·순서·음원 해시·설정 SHA도 이전과 같습니다. 기존 영수증·원시/요약 JSON의 경로·명령·해시를 소급 수정하지 않았습니다.

RP5 실제 회귀9건의 상태·전사·토큰·출력 정책이 이전 원시와 일치했습니다. 정상7건, 기존23.04초 ONNX context 거부1건, 기존56ms 반복 경고 실패1건입니다. 첫 추론/워밍업8회는 별도로 보존했습니다. 운영 열 정책·외부600초·주소 공간4GiB 아래 순차 실행했고 관측 현재 제한은 모두0입니다. 전체100개 정확도나 속도 개선을 새로 측정한 작업은 아닙니다.

원시/검증43파일 해시, 양쪽 분석 JSON·50개 공통 재채점 결과가 일치했습니다. 환경·음원/가중치·기존 원시/JSON·M1 원본 감사는 Host 17,525파일·RP5 14,466파일에서 변경·누락0입니다. 프로젝트 안내 Markdown과 이번에 이동한 프로젝트 소스는 자산 감사 대상과 구분하며 Git 이력으로 보존합니다. 외부 엔진/빌드9파일과 모델/연결10파일의 해시도 유지했습니다. 재설치·모델 재빌드는 하지 않았습니다.

원시는 `results/optimization/opt2/layout-cleanup-20261110-01/`, 상세 로그·이행표·보존/전송 검증은 `results/maintenance/layout-cleanup-20261110-01/`에 있습니다. [집계 JSON](layout-cleanup-20261110.json).
