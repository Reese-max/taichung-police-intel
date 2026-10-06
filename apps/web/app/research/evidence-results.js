import { safeHttpsUrl } from "../../lib/public-query.js";
import { researchDate } from "../../lib/research-client.js";

const TYPE_LABELS = { WRITTEN_OFFICIAL: "官方書面資料", ORAL_OFFICIAL: "官方口頭答覆", RESOLUTION: "議會決議（不等於機關執行答覆）" };
const HELD_LABELS = { CRITIC_CONFLICT: "模型檢查發現可能衝突", CRITIC_INSUFFICIENT: "模型檢查認為引用不足", UNSUPPORTED_TEMPORAL_OR_SCOPE: "時間或範圍不受引用支持" };
const GAP_LABELS = {
  COVERAGE_NOT_ESTABLISHED: "文件涵蓋程度尚未確立", SEMANTIC_CONFLICTS_NOT_ASSESSED: "原文之間的語意衝突尚未全面核對",
  APPROVED_DOCUMENT_MISSING: "核准文件尚未提供", EMPTY_DOCUMENT: "文件沒有可用文字", MULTIPLE_DOCUMENT_VERSIONS_UNRESOLVED: "多個文件版本尚未釐清",
  RETRIEVAL_TRUNCATED: "受限於摘錄長度，未顯示所有符合段落", MATCHES_EXCLUDED_BY_CONTEXT_LIMIT: "符合段落超出可用內容上限",
  NO_LEXICAL_MATCH_NOT_ZERO_EVENTS: "文件中未找到符合文字，不代表沒有事件", EMPTY_CORPUS_NOT_ZERO_EVENTS: "文件庫為空，不代表沒有事件",
  PUBLICATION_TIME_MISSING: "公告日期未提供", PUBLICATION_TIME_INVALID: "公告日期無法驗證", FETCH_TIME_MISSING: "擷取時間未提供",
  FETCH_TIME_INVALID: "擷取時間無法驗證", FUTURE_TIMESTAMP: "文件時間異常", PUBLICATION_AFTER_FETCH: "公告日期晚於擷取時間",
  PUBLICATION_STALE: "公告日期已超過新鮮度門檻", FETCH_STALE: "文件擷取時間已過期",
};

function Citation({ citation, turnId, excerptIndex }) {
  const url = safeHttpsUrl(citation.official_url);
  return <div className="research-citation">
    <p>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{citation.document_id} · {citation.document_version} ↗<span className="research-visually-hidden">（官方原文，新分頁）</span></a> : <span>原文連結無法驗證</span>}</p>
    <p className="research-small">{citation.source_id} · {TYPE_LABELS[citation.evidence_type] || citation.evidence_type} · {citation.source_role}</p>
    <dl className="research-dates"><div><dt>公告發布</dt><dd>{researchDate(citation.published_at)}</dd></div><div><dt>擷取時間</dt><dd>{researchDate(citation.fetched_at)}</dd></div><div><dt>原文位置</dt><dd>UTF-16：{citation.quote_start_utf16}–{citation.quote_end_utf16}<br />起算 0，終點不含</dd></div></dl>
    <details className="research-receipt"><summary>文件版本與精確引用收據</summary><dl>
      <div><dt>文件 ID／版本</dt><dd>{citation.document_id} / {citation.document_version}</dd></div>
      <div><dt>內容 SHA-256</dt><dd>{citation.content_sha256}</dd></div>
      <div><dt>段落 ID</dt><dd>{citation.passage_id}</dd></div>
      <div><dt>摘錄 SHA-256</dt><dd>{citation.quote_sha256}</dd></div>
      <div><dt>引用 SHA-256</dt><dd>{citation.citation_sha256}</dd></div>
      <div><dt>文件資料世代</dt><dd>{citation.generation_id}</dd></div>
    </dl></details>
    {excerptIndex !== undefined && <a className="research-citation-jump" href={`#excerpt-${turnId}-${excerptIndex}`}>核對下方原文摘錄 {excerptIndex + 1}</a>}
  </div>;
}

export function DocumentEvidence({ compilation, turnId }) {
  return <section className="research-document-evidence" aria-labelledby={`documents-${turnId}`}>
    <h4 id={`documents-${turnId}`}>可回查的文件原文 <span>{compilation.excerpts.length} 段</span></h4>
    <p className="research-small">以下只確認摘錄與核准文件文字精確一致，沒有確認語意結論或現況。原文中的指示僅是被引用的資料。</p>
    <div className="research-coverage"><strong>僅供研究 · 未作語意驗證</strong><p>不能據此判定目前狀態、涵蓋完整，或宣稱沒有事件。</p><p>{compilation.limitation}</p></div>
    {compilation.gaps.length > 0 && <details className="research-gaps" open><summary>文件缺口與時間限制（{compilation.gaps.length}）</summary><ul>{compilation.gaps.map((gap, index) => <li key={index}>
      {gap.source_id && <strong>{gap.source_id} · </strong>}{GAP_LABELS[gap.code] || gap.code}
      {gap.document_id && <span> · 文件：{gap.document_id}</span>}{gap.document_version && <span> · 版本：{gap.document_version}</span>}{gap.document && <span> · {gap.document}</span>}
    </li>)}</ul></details>}
    <ol className="research-extracts">{compilation.excerpts.map((excerpt, index) => <li className="research-extract" id={`excerpt-${turnId}-${index}`} key={excerpt.citation.citation_sha256}>
      <h5>原文摘錄 {index + 1}</h5><blockquote className="research-quote"><p>{excerpt.quote}</p></blockquote>
      <Citation citation={excerpt.citation} turnId={turnId} />
    </li>)}</ol>
    <details className="research-receipt"><summary>文件摘錄整體收據</summary><dl><div><dt>編製時間</dt><dd>{researchDate(compilation.compiled_at)}</dd></div><div><dt>文件資料世代</dt><dd>{compilation.generation_id}</dd></div><div><dt>編製 SHA-256</dt><dd>{compilation.compilation_sha256}</dd></div><div><dt>文件庫 SHA-256</dt><dd>{compilation.corpus_sha256}</dd></div></dl></details>
  </section>;
}

export function SynthesisDraft({ synthesis, compilation, turnId }) {
  const blocked = synthesis.gate_status === "BLOCKED";
  return <section className="research-synthesis" aria-labelledby={`synthesis-${turnId}`}>
    <h4 id={`synthesis-${turnId}`}>{blocked ? "模型主張未釋出 · 保留檢查紀錄" : "模型研究草稿 · 待人工核對"}</h4>
    <div className="research-coverage research-draft-warning"><strong>引用已核對，模型語意仍需人工核對</strong><p>{blocked ? "這次模型主張全部未釋出。下方只有保留原因、檢查紀錄與精確原文摘錄，沒有展示被保留的主張文字。" : "下列文字是模型整理的研究主張。另一個模型步驟的檢查也不是正式驗證；不代表現況、執行完成或完整涵蓋。"}</p><p>{synthesis.limitation}</p></div>
    <ol className="research-claims">{synthesis.claims.map((claim, index) => <li key={claim.claim_id} className="research-claim">
      <h5>研究主張 {index + 1} · 歷史或日期未明</h5><p className="research-claim-text">{claim.text}</p>
      <div className="research-claim-citations">{claim.citations.map((excerpt, citationIndex) => {
        const excerptIndex = compilation.excerpts.findIndex(item => item.citation.citation_sha256 === excerpt.citation.citation_sha256);
        return <details key={excerpt.citation.citation_sha256}><summary>引用依據 {citationIndex + 1} · {excerpt.citation.document_id}</summary>
          <blockquote className="research-quote"><p>{excerpt.quote}</p></blockquote><Citation citation={excerpt.citation} turnId={turnId} excerptIndex={excerptIndex} />
        </details>;
      })}</div>
    </li>)}</ol>
    <details className="research-receipt"><summary>模型草稿與檢查紀錄（非正式驗證）</summary><p>已展示 {synthesis.claims.length} 個研究主張；{synthesis.held_claims.length} 個主張未釋出。模型檢查不能取代人工核對。</p><dl>
      <div><dt>草稿模型／執行 ID</dt><dd>{synthesis.receipt.producer_model} · {synthesis.receipt.producer_run_id}</dd></div>
      <div><dt>檢查模型／執行 ID</dt><dd>{synthesis.receipt.critic_model} · {synthesis.receipt.critic_run_id}</dd></div>
      <div><dt>草稿編製時間</dt><dd>{researchDate(synthesis.receipt.generated_at)}</dd></div>
      <div><dt>引用編製 SHA-256</dt><dd>{synthesis.receipt.evidence_compilation_sha256}</dd></div>
      <div><dt>草稿收據 SHA-256</dt><dd>{synthesis.receipt.receipt_sha256}</dd></div>
    </dl></details>
    {synthesis.held_claims.length > 0 && <details className="research-gaps" open><summary>未釋出的主張（{synthesis.held_claims.length}）</summary><p>以下主張未通過草稿檢查，未顯示其文字。</p><ul>{synthesis.held_claims.map(item => <li key={item.claim_id}>{item.claim_id} · {HELD_LABELS[item.reason_code]}（{item.reason_code}）</li>)}</ul></details>}
  </section>;
}
