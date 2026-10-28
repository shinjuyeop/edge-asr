"""버전이 명시된 문자 정규화와 문자 오류율. 외부 런타임 의존성 없음."""

import unicodedata

NORMALIZATIONS = ("ko_cer_v1", "nfc_v1")


def normalize(text: str, policy: str = "ko_cer_v1") -> str:
    if policy not in NORMALIZATIONS:
        raise ValueError(f"지원하지 않는 정규화 정책: {policy}")
    text = unicodedata.normalize("NFC", text)
    if policy == "ko_cer_v1":
        text = "".join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P"))
    return text


def character_errors(reference: str, hypothesis: str, policy: str = "ko_cer_v1") -> dict:
    ref, hyp = normalize(reference, policy), normalize(hypothesis, policy)
    # (거리, 치환, 삭제, 삽입). 같은 거리이면 대각선→삭제→삽입을 우선한다.
    previous = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i, ref_char in enumerate(ref, 1):
        current = [(i, 0, i, 0)]
        for j, hyp_char in enumerate(hyp, 1):
            distance, sub, delete, insert = previous[j - 1]
            changed = int(ref_char != hyp_char)
            diagonal = (distance + changed, sub + changed, delete, insert)
            distance, sub, delete, insert = previous[j]
            deletion = (distance + 1, sub, delete + 1, insert)
            distance, sub, delete, insert = current[j - 1]
            insertion = (distance + 1, sub, delete, insert + 1)
            current.append(min((diagonal, deletion, insertion), key=lambda value: value[0]))
        previous = current
    errors, sub, delete, insert = previous[-1]
    return {"normalization": policy, "normalized_reference": ref, "normalized_hypothesis": hyp,
            "reference_characters": len(ref), "substitutions": sub, "deletions": delete,
            "insertions": insert, "errors": errors, "cer": errors / len(ref) if ref else None}
