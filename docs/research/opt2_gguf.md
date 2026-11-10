# OPT-2 첫 GGUF Q8_0 비교 프로토콜

2026-11-09, 실제 GGUF 전사 결과를 보기 전에 고정합니다. 실행 ID는 `opt2-gguf-20261109-01`이며 기존 OPT-0/1의 환경·모델·입력·결과를 변경하지 않습니다.

## 배포 경로

공식 [multimodal 지원 목록](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/docs/multimodal.md)에 Qwen3-ASR가 있고, [libmtmd 구현](https://github.com/ggml-org/llama.cpp/tree/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd)에서 CPU 오디오 인코더와 llama 디코더를 연결할 수 있습니다. 범용 텍스트 llama CLI만으로 오디오를 전사하지 않습니다.

- llama.cpp: `7d7f0287530bc8981690f361d8c1627dbae1c99a`, 별도 소스/빌드 `runtimes/qwen3-opt/llama-q8-20261109-01/`.
- [공식 GGUF 배포본](https://huggingface.co/ggml-org/Qwen3-ASR-0.6B-GGUF/tree/928ab958557df9aa2ef1c93e0e83c7ad0933fae2): revision `928ab958557df9aa2ef1c93e0e83c7ad0933fae2`.
- `Qwen3-ASR-0.6B-Q8_0.gguf`: 804,749,248 bytes, SHA-256 `bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971`.
- `mmproj-Qwen3-ASR-0.6B-Q8_0.gguf`: 214,392,480 bytes, SHA-256 `41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d`.
- 모델 경로 `models/qwen3-opt/gguf-q8-20261109-01/`. Python 패키지를 추가하지 않고 기존 표준 라이브러리 `ctypes`로 별도 C ABI를 호출합니다.

RP5 native Release CPU 빌드, GPU와 KleidiAI OFF, inference/batch 각 2스레드로 시작합니다. context/batch/ubatch 512, 출력 최대 128토큰, greedy, assistant `language Korean<asr_text>`를 고정합니다. 실제 cache dtype·flash attention 설정·CPU 기능·공유 라이브러리·컴파일 명령/해시를 기록합니다. 모델은 프로세스당 한 번 로드하고 파일마다 KV 상태를 비웁니다. 청크 간 KV 재사용 최적화는 이번 범위가 아닙니다.

## 동일 조건과 차이

같은 16kHz mono PCM16 WAV를 공통 로더가 float32로 바꾸어 두 어댑터에 전달합니다. GGUF 입력은 `mtmd_bitmap_init_from_audio`로 직접 전달하므로 별도 codec/resampling은 없습니다. 전체 입력+출력 예약이 context를 넘으면 실패로 남기고 자르지 않습니다. EOS/EOG 이전 토큰 한도·마지막 64개 ID 중 고유 ID 3개 이하 반복·불완전 UTF-8은 성공으로 채택하지 않습니다. 실패 원문·토큰·시간도 보존합니다.

ONNX INT8과 GGUF text/mmproj Q8_0은 가중치 양자화·프런트엔드·출력 검사 구현이 다릅니다. mtmd는 reflect padding과 10ms 프레임 계산을 자체 수행하며 sherpa와 경계 프레임 처리도 다릅니다. 따라서 **엔진+자산+전처리 구성 비교**입니다. 속도 차이를 순수 엔진 효과로 단정하거나 같은 양자화라고 표현하지 않습니다. 수동 전사 보정·접두/접미 문자열 삭제는 하지 않습니다.

## 순서와 판정

1. 고정 KSS 첫 입력 1개로 GGUF 첫 추론 1회·추가 워밍업 3회·steady 1회. 로딩·원문·토큰·메모리·시간을 확인합니다. Host 검사는 native 모의/문법 검사이며 RP5 실제 전사와 구분합니다.
2. 성공하면 기존 OPT-0 KSS 6개 manifest를 그대로 읽어 ONNX→GGUF 순서로 각 5라운드(각 30호출)를 실행합니다. 첫 추론/워밍업은 별도입니다. 라운드마다 순서를 회전합니다. 별도 프로세스에서 기존 진단 8개도 ONNX→GGUF 순서로 한 번씩 실행합니다. 진단 정답은 없고 CER=null입니다.
3. 파일럿 실행이 정상적으로 완료되면 공통 `evaluate`로 기존 KSS 개발 50개를 ONNX→GGUF 순서로 각각 1회, 워밍업 3회 실행합니다. 오류가 있더라도 완료한 호출을 제외하지 않습니다. native 충돌·시간 제한은 중단/미실행으로 표시합니다.
4. 후보 채택의 최소 기준은 같은 50개에서 **추가 실패 0개, 정답 문자 커버리지 100%, corpus 편집 오류 증가 0개**입니다. CER는 같은 `ko_cer_v1`이며 발화별 개선/동률/악화, 실패 포함 평가도 보고합니다. 소규모 반복 30개를 서로 다른 발화 30개로 세지 않습니다. 속도만 좋아져도 이 기준을 통과하지 못하면 채택을 보류합니다. 새 독립 한국어 평가는 후속 단계입니다.

로드/첫 추론/steady 처리시간·RTF·CPU·100ms PID RSS·시작/최고 온도·클럭/제한 플래그를 기록합니다. 자산 해시 검사 후 로딩이므로 cold-storage 성능이 아닙니다. 엔진 순서·열 상태를 통제하지 않은 운영 비교이고 통계적 우월성이나 장기 안정성을 주장하지 않습니다. GGUF 내부 첫 non-EOG 토큰 선택 시각은 별도로 기록하지만 외부에 텍스트를 스트리밍하지 않으므로 사용자 TTFT/부분 자막 지연을 대신하지 않습니다.

사용자 지정 `thermal-run --temperature-policy operational --skip-temperature-admission`, 작업별 600초·4GiB 주소 공간·저전압/센서 검사를 사용합니다. 온도로 기다리거나 중단하지 않고 관측치를 보존합니다. 모든 실패 실행은 유지하고 수정 후 새 실행 경로로만 재시도합니다. 공통 재채점·Host/RP5 해시 대조·기존 자산 보존 감사 후 요약을 작성합니다.

2026-11-10 경로 정리: 위 당시 실행 명령·조건은 기록으로 유지합니다. 현재 소스·설정·준비 CLI는 [구조 이행표](../reference/structure_migration.md)와 [실행 안내](../guides/qwen3_optimization.md)를 따릅니다.
