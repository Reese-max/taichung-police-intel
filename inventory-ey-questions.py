from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, quote, urlencode

import requests
from bs4 import BeautifulSoup


BASE = "https://query.ey.gov.tw/legisWeb/webQuery.aspx"
STATIC_BASE = "https://query.ey.gov.tw/legisweb/html"
TERMS_AND_SESSIONS = {9: range(1, 9), 10: range(1, 9), 11: range(1, 6)}
LOCAL_KEYWORDS = ("臺中", "台中")
CITY_UNIT_ID = "387000000"
CHECKPOINT_VERSION = 1
CHECKPOINT_MAX_AGE = timedelta(hours=24)

POLICE_DIRECT = (
    "警察", "警政", "警力", "警員", "員警", "警方", "警務", "警用", "警局", "派出所", "義警",
    "刑事警察", "交通警察", "航空警察", "保安警察", "國道警察", "鐵路警察",
)
POLICE_INDIRECT = (
    "治安", "刑事案件", "刑事政策", "刑事訴訟", "犯罪", "詐欺",
    "詐騙集團", "詐騙案件", "詐騙手法", "反詐", "毒品", "毒駕", "槍械", "槍枝",
    "槍砲", "幫派", "性侵", "性犯罪", "家庭暴力", "家暴", "婦幼", "兒少性剝削",
    "跟蹤騷擾", "人口販運", "交通執法", "科技執法", "酒駕", "肇事逃逸",
    "資通安全", "資訊安全", "資安事件", "網路犯罪", "駭客", "通訊監察", "集會遊行",
    "集會維安", "活動維安", "重要設施維安", "特勤維安", "元首維安", "民防", "反恐",
    "爆裂物", "爆炸物", "洗錢", "失蹤人口", "監視器",
)
LOCAL_AGENCY_RE = re.compile(
    r"(?:臺中|台中)市(?:政府(?:警察局)?|警察局|警局)|中市警局"
)
LOCAL_RE = re.compile(r"臺中|台中")
LOCAL_ACTION_RE = re.compile(
    r"應|須|需|將|已|請|辦理|執行|配合|建置|改善|規劃|補助|查復|函請|通報|要求|督導|協調|汰換|訓練"
)
UNIVERSAL_LOCAL_RE = re.compile(
    r"各直轄市|各縣市政府|各縣市警察局|地方政府|直轄市、?縣(?:（市）|\(市\)|市)"
)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def comparable_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", clean_text(value))


def local_search_text(value: str) -> str:
    value = re.sub(r"全[臺台]中(?:小企業|小型企業|低收入|低收戶)", "", value)
    value = re.sub(r"美[臺台]中(?=三|關|錯|貿|科技|網絡|供應鏈|對抗|競爭|之間|各方)", "", value)
    value = re.sub(r"平[臺台]中(?=[，、。內上的建使])", "", value)
    return re.sub(r"舞[臺台]中央", "", value)


def has_local(value: str) -> bool:
    return bool(LOCAL_RE.search(local_search_text(value)))


def title_matches_detail(title: str, detail: str) -> bool:
    title_text = comparable_text(title)
    detail_text = comparable_text(detail)
    if not title_text or title_text in detail_text:
        return True
    stop_pairs = {"問題", "臺中", "台中", "政府", "行政", "委員", "本院", "有關", "針對", "要求", "建請", "質詢"}
    title_pairs = {title_text[index:index + 2] for index in range(len(title_text) - 1)} - stop_pairs
    detail_pairs = {detail_text[index:index + 2] for index in range(len(detail_text) - 1)} - stop_pairs
    required = min(2, len(title_pairs))
    return len(title_pairs & detail_pairs) >= required


def fetch(session: requests.Session, url: str) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            if not response.content.strip():
                raise requests.RequestException("HTTP 200 但內容為空")
            response.encoding = response.apparent_encoding or "utf-8"
            return response
        except (requests.RequestException, UnicodeError) as exc:
            last_error = exc
            time.sleep(attempt + 1)
    raise RuntimeError(f"取得失敗：{url}：{last_error}")


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "TaichungPolicePublicInfoInventory/1.0 (research; low-rate)"
    return session


def parse_count(page: str) -> int:
    match = re.search(r"共\s*([\d,]+)\s*筆", page)
    return int(match.group(1).replace(",", "")) if match else 0


def parse_detail(page: str, dsrl: str, query_kind: str, query_value: str) -> dict[str, str]:
    # 官方頁面以 </td> 關閉 <th>；lxml 可修復，內建 parser 會把欄位錯誤巢狀化。
    soup = BeautifulSoup(page, "lxml")
    table = soup.find("table", id="headtitle")
    if table is None:
        raise ValueError(f"詳細頁缺少 headtitle 表格：{clean_text(soup.get_text(' '))[:240]}")

    fields: dict[str, str] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name == "th" and cells[index + 1].name == "td":
                fields[clean_text(cells[index].get_text(" "))] = clean_text(
                    cells[index + 1].get_text(" ")
                )
                index += 2
            else:
                index += 1

    required = ("種類", "屆別", "會期", "案號", "辦理機關")
    missing = [name for name in required if not fields.get(name)]
    if missing:
        raise ValueError(
            f"詳細頁缺少欄位：{','.join(missing)}：{clean_text(table.get_text(' '))[:240]}"
        )

    full_text = " ".join(fields.get(name, "") for name in ("案由", "說明", "內容"))
    return {
        "term": fields["屆別"],
        "session": fields["會期"],
        "case_type": fields["種類"],
        "case_no": fields["案號"],
        "dsrl": dsrl,
        "incoming_date": fields.get("來函日期", ""),
        "sent_date": fields.get("發文日期", ""),
        "document_no": clean_text(f"{fields.get('發文字', '')} {fields.get('發文號', '')}"),
        "legislator": fields.get("立法委員", ""),
        "handling_agency": fields["辦理機關"],
        "business_category": fields.get("業務別", ""),
        "summary": fields.get("案由", ""),
        "summary_source": "DETAIL" if fields.get("案由", "") else "LIST",
        "explanation": fields.get("說明", ""),
        "response": fields.get("內容", ""),
        "full_text": full_text,
        "query_kind": query_kind,
        "query_value": query_value,
    }


def police_relevance(text: str) -> tuple[str, list[str]]:
    text = text.replace("全民防衛", "")
    direct = [term for term in POLICE_DIRECT if term in text]
    if direct:
        return "DIRECT", direct
    indirect = [term for term in POLICE_INDIRECT if term in text]
    return ("INDIRECT", indirect) if indirect else ("NONE", [])


def first_matching_sentence(text: str, predicate) -> str:
    for sentence in re.split(r"(?<=[。！？；])|\n", text):
        sentence = clean_text(sentence)
        if sentence and predicate(sentence):
            return sentence[:300]
    return ""


def classify(record: dict[str, str]) -> dict[str, str]:
    text = record["full_text"]
    local_test_text = local_search_text(text)
    agency = record["handling_agency"]
    formal_city = bool(re.search(r"(?:臺中|台中)市政府", agency))
    local_explicit = has_local(local_test_text)
    implementation_evidence = first_matching_sentence(
        record["response"],
        lambda sentence: (
            bool(LOCAL_AGENCY_RE.search(sentence)) and bool(LOCAL_ACTION_RE.search(sentence))
        ) or (
            has_local(sentence)
            and bool(UNIVERSAL_LOCAL_RE.search(sentence))
            and bool(LOCAL_ACTION_RE.search(sentence))
        ),
    )

    local_sentences = " ".join(
        sentence for sentence in re.split(r"(?<=[。！？；])|\n", local_test_text)
        if has_local(sentence)
    )
    police_focus = clean_text(f"{implementation_evidence} {local_sentences}")
    relevance, police_terms = police_relevance(police_focus)
    police_evidence = first_matching_sentence(
        police_focus, lambda sentence: has_local(sentence) and police_relevance(sentence)[0] != "NONE"
    )

    if formal_city:
        local_class = "FORMAL_CITY_HANDLING"
    elif local_explicit and implementation_evidence:
        local_class = "LOCAL_IMPLEMENTATION"
    elif local_explicit:
        local_class = "LOCAL_MENTION"
    else:
        local_class = "QUERY_FALSE_POSITIVE"

    local_evidence = first_matching_sentence(
        local_test_text, has_local
    )
    body_status = "FULLTEXT" if record["response"] else "METADATA_ONLY"
    return {
        "local_class": local_class,
        "police_relevance": relevance,
        "police_terms": "｜".join(police_terms),
        "police_evidence": police_evidence,
        "local_evidence": local_evidence,
        "implementation_evidence": implementation_evidence,
        "source_body_status": body_status,
        "in_scope": "YES" if (formal_city or local_explicit) else "NO",
    }


def crawl_job(term: int, session_no: int, query_kind: str, query_value: str) -> list[dict[str, str]]:
    session = new_session()
    query = {
        "sys": "620", "funid": "lglistnew", "term": term, "ses": session_no,
        "srltype": 0, "leg": "", "unit_id": query_value if query_kind == "unit" else "",
        "senddatef": "", "senddatet": "", "keyword": query_value if query_kind == "keyword" else "",
        "max": 300, "outmode": 0,
    }
    first_url = f"{BASE}?{urlencode(query)}"
    first_page = fetch(session, first_url).text
    total = parse_count(first_page)
    if total == 0:
        return []
    if total > 300:
        raise ValueError(f"查詢超過 300 筆：{term}-{session_no}-{query_kind}-{query_value}")

    records: list[dict[str, str]] = []
    for page_no in range(1, math.ceil(total / 15) + 1):
        page = first_page if page_no == 1 else fetch(session, f"{first_url}&page={page_no}").text
        soup = BeautifulSoup(page, "lxml")
        anchors = [
            anchor for anchor in soup.find_all("a", href=True)
            if "getData(630,'" in anchor["href"]
        ]
        for anchor in anchors:
            match = re.search(r"getData\(630,'([^']+)'", anchor["href"])
            if not match:
                continue
            fragment = match.group(1)
            params = parse_qs(fragment.lstrip("&"))
            dsrl = params.get("dsrl", [""])[0]
            detail_query = {
                "sys": "630", "funid": "lgresultnew", "term": term, "ses": session_no,
                "keyword": query_value if query_kind == "keyword" else "",
                "unit_id": query_value if query_kind == "unit" else "",
            }
            detail_url = f"{BASE}?{urlencode(detail_query)}{fragment}"
            response = fetch(session, detail_url)
            try:
                record = parse_detail(response.text, dsrl, query_kind, query_value)
            except ValueError as exc:
                raise ValueError(
                    f"{term}-{session_no}-{query_kind}-{query_value}-page{page_no}：{detail_url}：{exc}"
                ) from exc
            if not record["summary"]:
                record["summary"] = clean_text(anchor.get("alt") or anchor.get_text(" "))
                record["full_text"] = clean_text(f"{record['summary']} {record['full_text']}")
            list_title = clean_text(anchor.get("alt") or anchor.get_text(" "))
            record["list_title"] = list_title
            record["title_match"] = "YES" if title_matches_detail(list_title, record["full_text"]) else "TITLE_REVIEW"
            if record["term"] != str(term) or record["session"] != str(session_no):
                raise ValueError(f"清單與詳細頁錯置：{term}-{session_no} -> {record['term']}-{record['session']}")
            record.update(classify(record))
            canonical_content = "\n".join(record.get(field, "") for field in (
                "term", "session", "case_type", "case_no", "incoming_date", "sent_date",
                "document_no", "legislator", "handling_agency", "summary", "explanation", "response",
            ))
            record["content_sha256"] = hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()
            records.append(record)
    if len(records) != total:
        raise ValueError(
            f"清單筆數不一致：{term}-{session_no}-{query_kind}-{query_value}：宣告 {total}，取得 {len(records)}"
        )
    record_ids = {f"{item['term']}-{item['session']}-{item['dsrl']}-{item['case_no']}" for item in records}
    if len(record_ids) != total:
        raise ValueError(
            f"同一查詢出現重複案件 ID：{term}-{session_no}-{query_kind}-{query_value}：宣告 {total}，唯一 {len(record_ids)}"
        )
    return records


def checkpoint_job_key(job) -> str:
    return json.dumps(job, ensure_ascii=False, separators=(",", ":"))


def new_checkpoint(jobs) -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "version": CHECKPOINT_VERSION,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "jobs_sha256": hashlib.sha256(
            json.dumps(jobs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "started_at": now,
        "updated_at": now,
        "completed": {},
    }


def load_checkpoint(path: Path, jobs) -> dict:
    expected = new_checkpoint(jobs)
    if not path.exists():
        return expected
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(state["updated_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"checkpoint 損壞：{path}；請用 --restart 重新開始") from exc
    for field in ("version", "script_sha256", "jobs_sha256"):
        if state.get(field) != expected[field]:
            raise ValueError(f"checkpoint 與目前程式或工作清單不相容：{path}；請用 --restart 重新開始")
    if updated_at.tzinfo is None:
        raise ValueError(f"checkpoint 時間格式無效：{path}；請用 --restart 重新開始")
    if datetime.now().astimezone() - updated_at > CHECKPOINT_MAX_AGE:
        raise ValueError(f"checkpoint 已超過 24 小時：{path}；請用 --restart 重新開始")
    completed = state.get("completed")
    expected_keys = {checkpoint_job_key(job) for job in jobs}
    if not isinstance(completed, dict) or not set(completed) <= expected_keys:
        raise ValueError(f"checkpoint 工作內容無效：{path}；請用 --restart 重新開始")
    if any(not isinstance(records, list) or any(not isinstance(record, dict) for record in records)
           for records in completed.values()):
        raise ValueError(f"checkpoint 資料格式無效：{path}；請用 --restart 重新開始")
    print(f"CHECKPOINT_RESUME={len(completed)}/{len(jobs)} PATH={path.resolve()}")
    return state


def save_checkpoint(path: Path, state: dict) -> None:
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def crawl_jobs(jobs, crawler=crawl_job, checkpoint: Path | None = None) -> list[dict[str, str]]:
    # 行政院舊站的詳細頁在重疊請求時會截斷或交錯正文，必須逐工作依序抓取。
    # ponytail: 每個查詢工作存一次；只有單一工作過大時才升級成逐頁 checkpoint。
    state = load_checkpoint(checkpoint, jobs) if checkpoint else {"completed": {}}
    if checkpoint and not checkpoint.exists():
        save_checkpoint(checkpoint, state)
    completed = state["completed"]
    for job in jobs:
        key = checkpoint_job_key(job)
        if key in completed:
            print(f"RESUMED {job}: {len(completed[key])}")
            continue
        try:
            records = crawler(*job)
        except Exception:
            if checkpoint:
                print(f"CHECKPOINT_SAVED={checkpoint.resolve()} COMPLETED={len(completed)}/{len(jobs)}")
            raise
        job_fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for record in records:
            record.setdefault("_fetched_at", job_fetched_at)
        completed[key] = records
        if checkpoint:
            save_checkpoint(checkpoint, state)
        print(f"FETCHED {job}: {len(records)}")
    return [record for job in jobs for record in completed[checkpoint_job_key(job)]]


def compare_inventories(current: Path, baseline: Path) -> tuple[int, int]:
    def load(path: Path) -> dict[str, dict[str, str]]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {row["record_id"]: row for row in csv.DictReader(handle)}

    current_rows, baseline_rows = load(current), load(baseline)
    if current_rows.keys() != baseline_rows.keys():
        raise ValueError(
            f"一致性閘門失敗：案件鍵不同：current={len(current_rows)} baseline={len(baseline_rows)}"
        )
    fields = (set(next(iter(current_rows.values()))) | set(next(iter(baseline_rows.values())))) - {"fetched_at"}
    changed_rows = sum(
        any(current_rows[key].get(field, "") != baseline_rows[key].get(field, "") for field in fields)
        for key in current_rows
    )
    changed_hashes = sum(
        current_rows[key].get("content_sha256", "") != baseline_rows[key].get("content_sha256", "")
        for key in current_rows
    )
    return changed_rows, changed_hashes


def self_check() -> None:
    malformed = """<table id='headtitle'><tr><th>種類</td><td>專案</td><th>屆別</td><td>9</td>
    <th>會期</td><td>1</td><th>案號</td><td>1</td></tr><tr><th>辦理機關</td><td>內政部</td></tr>
    <tr><th>案由</td><td>臺中市警政測試</td></tr><tr><th>內容</td><td>正式答覆</td></tr></table>"""
    parsed = parse_detail(malformed, "22", "keyword", "臺中")
    assert parsed["case_no"] == "1" and parsed["handling_agency"] == "內政部"
    assert title_matches_detail("就加強溪谷戲水安全宣導問題", "應加強宣導夏季於溪谷戲水應注意事項")
    assert not title_matches_detail("臺中校園警力部署", "臺中港郵輪觀光規劃")
    base = {
        "handling_agency": "內政部", "full_text": "臺中市發生警察裝備問題。",
        "response": "內政部已函請臺中市政府警察局辦理裝備汰換。",
    }
    assert classify(base)["local_class"] == "LOCAL_IMPLEMENTATION"
    assert classify(base)["police_relevance"] == "DIRECT"
    assert classify({**base, "handling_agency": "臺中市政府"})["local_class"] == "FORMAL_CITY_HANDLING"
    mention = {**base, "response": "中央已完成統計。"}
    assert classify(mention)["local_class"] == "LOCAL_MENTION"
    unrelated = {
        "handling_agency": "交通部", "summary": "改善臺中觀光品質",
        "full_text": "改善臺中觀光品質。另請警察加強全國勤務。", "response": "中央已完成統計。",
    }
    assert classify(unrelated)["police_relevance"] == "NONE"
    composite = {
        "handling_agency": "交通部", "summary_source": "LIST", "summary": "研議酒駕規範。推動台中捷運。",
        "full_text": "研議酒駕規範。推動台中捷運。", "response": "中央已完成統計。",
    }
    assert classify(composite)["police_relevance"] == "NONE"
    for false_local in ("美台中錯綜複雜的關係", "公共政策平台中，請警察說明", "全台中低收戶統計"):
        assert not has_local(false_local)
    assert police_relevance("臺中市結合全民防衛動員署")[0] == "NONE"
    false_positive = {**base, "full_text": "協助全臺中小企業。", "response": ""}
    assert classify(false_positive)["local_class"] == "QUERY_FALSE_POSITIVE"
    jobs = [(9, 1, "keyword", "臺中"), (9, 1, "keyword", "台中"), (9, 1, "unit", CITY_UNIT_ID)]
    with TemporaryDirectory() as directory:
        checkpoint = Path(directory) / "resume.json"
        first_calls = []

        def fail_second(*job):
            first_calls.append(job)
            if job == jobs[1]:
                raise RuntimeError("simulated interruption")
            return [{"query": job[3]}]

        try:
            crawl_jobs(jobs, fail_second, checkpoint)
            raise AssertionError("simulated interruption was not raised")
        except RuntimeError as exc:
            assert str(exc) == "simulated interruption"
        assert first_calls == jobs[:2]
        second_calls = []
        resumed = crawl_jobs(
            jobs,
            lambda *job: second_calls.append(job) or [{"query": job[3]}],
            checkpoint,
        )
        assert second_calls == jobs[1:]
        assert [record["query"] for record in resumed] == ["臺中", "台中", CITY_UNIT_ID]
        assert not checkpoint.with_name(checkpoint.name + ".tmp").exists()
    print("SELF_CHECK_OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="盤點行政院第 9～11 屆臺中相關質詢案件")
    parser.add_argument("--output", type=Path, default=Path("executive-yuan-taichung-question-inventory.csv"))
    parser.add_argument("--checkpoint", type=Path, help="預設為 <output>.checkpoint.json")
    parser.add_argument("--restart", action="store_true", help="捨棄既有 checkpoint 並從頭抓取")
    parser.add_argument("--compare-with", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return

    jobs = [
        (term, session_no, "keyword", keyword)
        for term, sessions in TERMS_AND_SESSIONS.items()
        for session_no in sessions
        for keyword in LOCAL_KEYWORDS
    ] + [
        (term, session_no, "unit", CITY_UNIT_ID)
        for term, sessions in TERMS_AND_SESSIONS.items()
        for session_no in sessions
    ]

    checkpoint = args.checkpoint or args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    if not checkpoint.name.endswith(".checkpoint.json"):
        parser.error("--checkpoint 檔名必須以 .checkpoint.json 結尾")
    if checkpoint.resolve() == args.output.resolve():
        parser.error("--checkpoint 不得與 --output 指向同一檔案")
    if args.restart and checkpoint.exists():
        checkpoint.unlink()
        print(f"CHECKPOINT_RESTARTED={checkpoint.resolve()}")
    all_records = crawl_jobs(jobs, checkpoint=checkpoint)

    deduped: dict[str, dict[str, str]] = {}
    for record in all_records:
        record_id = f"{record['term']}-{record['session']}-{record['dsrl']}-{record['case_no']}"
        record["record_id"] = record_id
        if record_id in deduped:
            previous = deduped[record_id]
            previous["query_value"] = "｜".join(sorted(set((previous["query_value"] + "｜" + record["query_value"]).split("｜"))))
            if record["query_kind"] == "unit":
                previous["query_kind"] = "keyword｜unit"
                previous["local_class"] = "FORMAL_CITY_HANDLING"
        else:
            deduped[record_id] = record

    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = []
    for record_id in sorted(deduped, key=lambda key: tuple(int(part) for part in key.split("-"))):
        record = deduped[record_id]
        source_query = {
            "sys": 620, "funid": "lglistnew", "term": record["term"], "ses": record["session"],
            "srltype": 1 if record["case_type"] == "專案" else 2, "leg": "", "unit_id": "",
            "senddatef": "", "senddatet": "", "seqf": record["case_no"], "seqt": record["case_no"],
            "keyword": "", "max": 300, "outmode": 0,
        }
        record["source_url"] = f"{BASE}?{urlencode(source_query)}"
        record["static_url"] = f"{STATIC_BASE}/{record['term']}_{record['session']}_{record['dsrl']}_{record['case_no']}.htm"
        record["static_status"] = "UNVERIFIED"
        record["fetched_at"] = record.pop("_fetched_at", fetched_at)
        record["historical_home_status"] = "SEARCH_ONLY"
        rows.append(record)

    fields = (
        "record_id", "term", "session", "case_type", "case_no", "dsrl", "incoming_date", "sent_date",
        "document_no", "legislator", "handling_agency", "business_category", "summary", "summary_source", "local_class",
        "police_relevance", "police_terms", "police_evidence", "source_body_status", "local_evidence", "implementation_evidence",
        "in_scope", "historical_home_status", "query_kind", "query_value", "list_title", "title_match", "source_url",
        "static_url", "static_status", "content_sha256", "fetched_at",
    )
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    in_scope = [row for row in rows if row["in_scope"] == "YES"]
    police = [row for row in in_scope if row["police_relevance"] != "NONE"]
    counts = {name: sum(row["local_class"] == name for row in rows) for name in (
        "FORMAL_CITY_HANDLING", "LOCAL_IMPLEMENTATION", "LOCAL_MENTION", "QUERY_FALSE_POSITIVE"
    )}
    print(f"OUTPUT={args.output.resolve()}")
    print(f"ROWS={len(rows)} IN_SCOPE={len(in_scope)} POLICE={len(police)} COUNTS={counts}")
    if args.compare_with:
        changed_rows, changed_hashes = compare_inventories(args.output, args.compare_with)
        print(f"CONSISTENCY_CHANGED_ROWS={changed_rows} CONSISTENCY_CHANGED_CONTENT_HASHES={changed_hashes}")
        if changed_rows or changed_hashes:
            raise ValueError("一致性閘門失敗")
        print("CONSISTENCY_GATE=PASS")
    checkpoint.unlink(missing_ok=True)
    print(f"CHECKPOINT_CLEARED={checkpoint.resolve()}")


if __name__ == "__main__":
    main()
