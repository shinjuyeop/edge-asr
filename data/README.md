# 로컬 오디오·정답

[M3 공개 자료 검수 준비](../docs/research/m3_public_vad_data.md)는 Seoul Corpus 시간 라벨·원본 음성과 공개 잡음을 별도 `sources/`, `review/`에 보존합니다. 미검수 목록의 전사·음성 구간은 null이며 바로 평가기에 넣지 않습니다. 음원·전사·검수 페이지는 Git에서 제외됩니다.

고정한 KSS 개발용 50개 발화의 준비·이용 조건·RP5 전달 방법은 [데이터 준비 안내](../docs/guides/datasets.md)에 있습니다. `scripts/edge_asr.py tools prepare-kss`는 Host에서 원본·16kHz 변환본·정답 목록·출처·해시를 이 디렉터리 아래에 준비합니다.

WAV와 원시 데이터셋·개인 전사는 이 위치에 보관하거나 RP5의 기존 경로를 참조합니다. 안내 문서 외 전체가 Git 제외됩니다. 외부 경로의 오디오도 Git에 추가하지 마세요.

평가용 manifest 구조는 [명세](../benchmarks/manifest.md), 정답 정규화는 [벤치마크 설계](../docs/reference/benchmark_design.md)를 따릅니다. 기존 실험의 WAV는 저장소에 포함하지 않았습니다.
