# 호스트 검사

Python 3.11 가상환경에서 기본 패키지 설치 후 저장소 루트에서 실행합니다.

```bash
python -m unittest discover -s tests -v
sha256sum -c experiments/m1_zipformer/SHA256SUMS
bash -n scripts/deploy_rp5.sh
```

설정 경계값과 CLI의 JSON 출력·실패 종료 코드, 합성 Summary 파싱·시간 반올림, 분위수·교차 순서, 모의 프로브의 성공·실패·시간 초과·자원 수집·워밍업 제외·기존 파일 보존을 검사합니다. 초기 원시 로그는 테스트 의존성이 아닙니다. 실제 모델 런타임·가중치·평가 WAV·RP5 연결은 요구하지 않습니다. WAV 포맷 검사는 임시 합성 WAV를 생성해 사용합니다. Linux 프로세스 검사는 다른 OS에서 건너뜁니다. 이 검사의 통과는 실제 추론·ARM64·실시간 서비스 성능 검증이 아닙니다.

`test_consolidate.py`는 임시 Git 저장소에서 한 폴더 통합 미리보기·실제 적용, 가상환경/모델·과거 결과 보존, 충돌 파일/Git 메타데이터 백업, 실패·중단 복원, 실행 중 실험·수정 소스·다른 Git 이력·symlink 거부와 hard link 보존을 검사합니다. Linux/Git CLI가 필요하며 실제 RP5 폴더를 사용하지 않습니다.
