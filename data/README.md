# 로컬 오디오·정답

고정한 KSS 개발용 50개 발화의 준비·이용 조건·RP5 전달 방법은 [데이터 준비 안내](../docs/datasets.md)에 있습니다. `scripts/prepare_kss_subset.py`는 Host에서 원본·16kHz 변환본·정답 목록·출처·해시를 이 디렉터리 아래에 준비합니다.

WAV와 원시 데이터셋·개인 전사는 이 위치에 보관하거나 RP5의 기존 경로를 참조합니다. 안내 문서 외 전체가 Git 제외됩니다. 외부 경로의 오디오도 Git에 추가하지 마세요.

평가용 manifest 구조는 [명세](../benchmarks/manifest.md), 정답 정규화는 [벤치마크 설계](../docs/benchmark_design.md)를 따릅니다. 기존 실험의 WAV는 저장소에 포함하지 않았습니다.
