"""Length-band padding (TASK §3.3, §5.4 V-10 b): realistic, label-free Korean minutes/change logs.

Sentences come from slot templates so that padding differs between scenarios. The calibrator measures
the real prefix growth with the gpt-oss tokenizer and keeps L_M-L_S ≈ +600 and L_L-L_S ≈ +1,500.
"""
import random

TEAMS = {
    "FIN": ["자금팀", "결산팀", "세무팀", "재무기획팀", "채권관리팀", "내부회계팀"],
    "PRC": ["구매팀", "물류팀", "품질팀", "자재관리팀", "협력사관리팀", "생산계획팀"],
    "HRI": ["인사팀", "보상팀", "채용팀", "교육팀", "계정관리팀", "노무팀"],
    "PRV": ["정보보호팀", "데이터관리팀", "고객지원팀", "법무팀", "분석팀", "기록관리팀"],
    "OPS": ["운영팀", "배포관리팀", "인프라팀", "장애대응팀", "플랫폼팀", "서비스지원팀"],
    "PUB": ["연구지원팀", "편집팀", "홍보팀", "자료실", "연구윤리팀", "출판지원팀"],
}
TOPICS = {
    "FIN": ["월말 미지급금 정리", "법인카드 사용 내역 대사", "환율 변동분 재평가", "분기 예산 집행률", "선급금 회수 계획",
            "감가상각 기준 정비", "외화 예금 이자 처리", "거래처 잔액 확인서 회신", "부가세 신고 준비", "비용 배부 기준"],
    "PRC": ["포장재 단가 재협상", "납기 지연 사례 정리", "차기 분기 수요 예측", "입고 검수 기준 보완", "창고 재고 실사",
            "운송사 계약 갱신", "협력사 품질 점수 산정", "긴급 발주 절차 개선", "반품 처리 기준", "안전 재고 수준 조정"],
    "HRI": ["신규 입사자 교육 편성", "연차 사용 촉진 안내", "평가 면담 일정 조율", "복리후생 제도 설문", "조직 개편 후속 조치",
            "채용 공고 문구 검토", "근무 시간 기록 점검", "교육 이수율 집계", "사내 동호회 지원 기준", "직무 기술서 갱신"],
    "PRV": ["보존 기간 경과 자료 정리", "접근 기록 정기 점검", "고객 문의 응답 품질", "동의서 양식 개정", "가명 처리 절차 교육",
            "자료 요청 처리 기간", "외부 감사 대응 준비", "분석 환경 계정 정리", "문서 분류 기준 보완", "백업 주기 점검"],
    "OPS": ["야간 배치 지연 원인", "모니터링 경보 기준 조정", "용량 증설 계획", "점검 공지 템플릿", "장애 회고 후속 조치",
            "배포 체크리스트 개정", "로그 보관 주기", "대기 근무 순번", "인증서 갱신 계획", "테스트 환경 정리"],
    "PUB": ["학술지 투고 준비", "연구 노트 보관 방식", "공개 자료 인용 기준", "세미나 발표자 섭외", "데이터 공개 범위 검토",
            "번역 원고 교정", "도서관 구독 갱신", "연구비 정산 서류", "보도자료 검토 절차", "외부 자문 위원 섭외"],
}
TEMPLATES = [
    "{month}월 {day}일 회의에서 {topic} 건을 다뤘고, {team}이 세부 일정을 다음 회의까지 정리하기로 했다.",
    "{topic} 관련 지난달 실적은 계획 대비 {pct}% 수준이었으며 차이의 대부분은 일정 조정에서 생겼다.",
    "{team} 담당자는 {topic} 진행 상황을 공유했고, 남은 작업은 약 {num}건으로 추산했다.",
    "참석자들은 {topic}에 대해 기존 방식을 유지하되 점검 주기를 {num2}주 단위로 바꾸는 안을 검토했다.",
    "{month}월 중순까지 {topic} 초안을 마련하고, 의견 수렴은 사내 게시판으로 받기로 했다.",
    "{team}은 {topic}에 필요한 자료가 일부 늦게 도착해 확인 작업이 {num2}일가량 밀렸다고 설명했다.",
    "지난 분기 {topic} 과정에서 나온 개선 의견 {num}건 가운데 절반 정도가 반영되었다.",
    "{topic} 결과는 다음 정기 보고에 요약 형태로 포함하고, 상세 내용은 부록으로 첨부한다.",
    "회의 말미에 {team}은 {topic} 담당 인력이 {num2}명 늘어날 예정이라고 알렸다.",
    "{topic_wa} 관련해 외부 업체 견해를 들어 보자는 의견이 있었으나 이번 분기에는 보류했다.",
    "{month}월 {day}일 기준으로 {topic} 진척률은 {prog}%이며, 지연 항목은 별도 목록으로 관리한다.",
    "{team}이 준비한 {topic} 설명 자료는 공유 폴더에 올라가 있고 수정 사항은 주간 회의에서 반영한다.",
    "{topic} 건은 작년 같은 시기와 비교해 처리량이 {pct}% 늘었다는 집계가 보고되었다.",
    "참석자 일부는 {topic} 일정이 연말 업무와 겹친다는 점을 우려했고, 우선순위를 다시 정하기로 했다.",
    "{topic}에 대한 내부 교육은 {month}월 넷째 주에 두 차례 나누어 진행한다.",
    "{team}은 {topic} 관련 문의가 지난달 {num}건 접수되었고 대부분 당일 처리되었다고 보고했다.",
    "다음 회의 전까지 {topic} 진행 현황을 표로 정리해 미리 배포하기로 했다.",
    "{topic} 개선안 중 비용이 드는 항목은 차기 예산 편성 때 함께 논의한다.",
    "{team}과 협의한 결과 {topic} 작업은 기존 담당 체계를 유지하기로 정리되었다.",
    "{topic} 진행 중 발견된 문서 양식 불일치 {num2}건은 이번 달 안에 정리한다.",
]
HEADINGS_M = ["## 지난 회의 요약", "## 이전 회의 기록", "## 참고: 직전 회의 메모"]
HEADINGS_L = ["## 부록: 분기 업무 변경 이력", "## 부록: 진행 중인 개선 과제", "## 부록: 주간 회의 누적 기록"]


def _wa(word):
    """'과' after a final consonant, '와' after a vowel (Hangul syllable arithmetic)."""
    last = word[-1]
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + ("과" if has_final else "와")


def sentence_stream(domain, seed):
    rng = random.Random(f"{domain}:{seed}")
    topics, teams = list(TOPICS[domain]), list(TEAMS[domain])
    seen = set()
    while True:
        template = rng.choice(TEMPLATES)
        topic = rng.choice(topics)
        sentence = template.format(month=rng.randint(1, 12), day=rng.randint(1, 28), topic=topic, topic_wa=_wa(topic),
                                   team=rng.choice(teams), pct=rng.randint(55, 130), num=rng.randint(4, 60),
                                   num2=rng.randint(2, 6), prog=rng.randint(35, 95))
        key = (template, topic)  # never repeat the same template on the same topic
        if key in seen and len(seen) < len(TEMPLATES) * len(topics):
            continue
        seen.add(key)
        yield sentence


def build(domain, seed, m_count, l_extra):
    stream = sentence_stream(domain, seed)
    rng = random.Random(f"h:{domain}:{seed}")
    first = [next(stream) for _ in range(m_count)]
    second = [next(stream) for _ in range(l_extra)]
    head_m, head_l = rng.choice(HEADINGS_M), rng.choice(HEADINGS_L)
    middle = "\n" + head_m + "\n" + "\n".join(f"- {line}" for line in first) + "\n"
    large = middle + "\n" + head_l + "\n" + "\n".join(f"- {line}" for line in second) + "\n"
    return {"S": "", "M": middle, "L": large}


def calibrate(spec, file_path, seed=None, measure=None):
    """Choose sentence counts so that L_M-L_S and L_L-L_S sit near 600 and 1,500 tokens."""
    from scenario_suite.v2.replay import _v10_lengths  # noqa: F401 (import check)
    from scenario_suite.v2 import replay

    domain = spec["domain"]
    seed = seed if seed is not None else spec["id"]
    item = next(entry for entry in spec["public"]["files"] if entry["path"] == file_path)

    def deltas(m_count, l_extra):
        item["length_band_padding"] = build(domain, seed, m_count, l_extra)
        base_p, _ = spec["decision_point"]["placement"], None
        k = replay.first_pd_k(spec)
        lengths = {}
        for width in ("S", "M", "L"):
            text = replay.prefix_text(spec, {"O": "D", "L": "L2", "P": base_p, "W": width, "cell": "cal"}, k)
            lengths[width] = replay.token_count(text)
        return lengths["M"] - lengths["S"], lengths["L"] - lengths["S"]

    m_count = 14
    for _ in range(12):
        d_m, _ = deltas(m_count, 0)
        if d_m < 560:
            m_count += max(1, round((600 - d_m) / 40))
        elif d_m > 650:
            m_count -= max(1, round((d_m - 600) / 40))
        else:
            break
    l_extra = 24
    for _ in range(12):
        d_m, d_l = deltas(m_count, l_extra)
        if d_l < 1400:
            l_extra += max(1, round((1500 - d_l) / 40))
        elif d_l > 1620:
            l_extra -= max(1, round((d_l - 1500) / 40))
        else:
            break
    d_m, d_l = deltas(m_count, l_extra)
    return {"M": d_m, "L": d_l, "m_count": m_count, "l_extra": l_extra}
