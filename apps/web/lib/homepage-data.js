// apps/web/lib/homepage-data.js
// Evidence manifest and bilingual copy contract for the council-prep homepage.
// Both language paths reference the same evidence identifiers and official URLs.
// Formal citation always returns to the official source; GROQ_ASR text is navigation only.

import { COUNCIL_FIXTURE, getFixtureEvidenceChain } from "./council-prep.js";

const COUNCIL_EVIDENCE_CHAIN = getFixtureEvidenceChain();
const HISTORICAL_EVIDENCE = COUNCIL_FIXTURE.historical_question;
const TRANSCRIPT_EVIDENCE = COUNCIL_FIXTURE.derived_transcript;

export const EVIDENCE_SOURCE_ID = HISTORICAL_EVIDENCE.source_id;

// This is the homepage candidate projection of the council fixture. It keeps
// evidence IDs/URLs/clip bounds in one contract instead of restating them in
// the production page.
export const PRIORITY_ITEM = Object.freeze({
  item_id: `COUNCIL-${HISTORICAL_EVIDENCE.evidence_id}`,
  evidence_source_id: EVIDENCE_SOURCE_ID,
  evidence_ids: Object.freeze(COUNCIL_EVIDENCE_CHAIN.map(({ evidence_id }) => evidence_id)),
  session_date: COUNCIL_FIXTURE.agenda_item.session_date,
  official_page_url: HISTORICAL_EVIDENCE.official_url,
  official_url: HISTORICAL_EVIDENCE.official_url,
  meeting_records_url: COUNCIL_FIXTURE.meeting_record.official_url,
  clip_start_seconds: HISTORICAL_EVIDENCE.clip_start_seconds,
  clip_duration_seconds: HISTORICAL_EVIDENCE.clip_duration_seconds,
  derivation_type: TRANSCRIPT_EVIDENCE.evidence_type,
  verification_status: COUNCIL_FIXTURE.validation.verification_status,
  content_disposition: "HOME_CANDIDATE",
  reason_codes: Object.freeze(["COUNCIL_ATTENTION"]),
  item_value_score: 100,
  content_label: HISTORICAL_EVIDENCE.content_label,
  post_meeting_label: COUNCIL_FIXTURE.validation.post_meeting_label,
});

export const SOURCE_NAMES_EN = {
  "S-004": "Taichung City Council meeting agenda",
  "S-006": "Taichung City Council interpellation order",
  "S-007": "Taichung City Council proceedings records",
  "S-009": "Taichung City Council proposals",
  "S-029": "Taichung City Government council project reports",
};

// ── Central-policy cap ────────────────────────────────────────────────────────
// R4: at most two central-policy items may appear; official stage and any
// inferred local impact must be kept separate.
export const CENTRAL_POLICY_LIMIT = 2;

export const CENTRAL_POLICY_CARDS = [
  // No central-policy items currently qualify.
  // When added each entry must include: evidence_source_id, official_stage,
  // and optionally inferred_local_impact.
];

/**
 * Pure function — returns at most CENTRAL_POLICY_LIMIT cards from the input.
 * The caller must always use this instead of slicing raw CENTRAL_POLICY_CARDS.
 * @param {Array} cards
 * @returns {Array}
 */
export function limitCentralPolicyCards(cards) {
  return cards.slice(0, CENTRAL_POLICY_LIMIT);
}

export function formatPlaybackStatus(status, copy) {
  if (status.kind === "seeked") {
    return `${copy.playback_seeked_prefix}${status.timestamp}${copy.playback_seeked_suffix}`;
  }
  if (status.kind === "autoplay_blocked") {
    return `${copy.playback_autoplay_blocked_prefix}${status.timestamp}${copy.playback_autoplay_blocked_suffix}`;
  }
  return copy[`playback_${status.kind}`] || copy.playback_loading;
}

export function requiresOfficialFallback(status) {
  return status?.kind === "hls_error" || status?.kind === "hls_unsupported";
}

// ── Bilingual copy contract ───────────────────────────────────────────────────
// Every key must exist in both "en" and "zh". Values must be non-empty strings.
// The Chinese transcript text itself is NOT translated here — it is preserved
// verbatim in the component as an official-source Chinese navigation aid.
export const COPY = {
  en: {
    // ── Language toggle ───────────────────────────────────────────────────────
    lang_toggle_label: "切換中文",
    lang_toggle_current: "English",

    // ── Site header ───────────────────────────────────────────────────────────
    site_kicker: "Taichung police intelligence",
    site_title: "Tonight's council preparation",
    freshness_live: "Live check: ",
    freshness_static: "Static evidence: 2026-08-14",

    // ── Judge guide ───────────────────────────────────────────────────────────
    guide_label: "Judge quick guide · English",
    guide_heading: "Evidence-backed council preparation in five minutes",
    guide_body:
      "This public demo helps police-policy staff spot a council issue, inspect official-source health and intelligence gaps, then jump to the exact official video timestamp.",
    guide_step1: "Review the priority brief.",
    guide_step2: "Check source health, freshness, gaps, and last-known-good.",
    guide_step3: "Open the evidence drawer and select a transcript timestamp.",

    // ── Priority brief ────────────────────────────────────────────────────────
    brief_label: "Priority item · 01",
    brief_heading:
      "Incentives for apprehending unaccounted-for migrant workers — a police-bureau council Q&A issue",
    brief_body:
      "A councillor compared the incentive gap between frontline officers and civilian tip-off reporters, and pressed on application procedures, disbursement timing, and whether the bureau has escalated the issue centrally. Recommend including in policy review and Q&A preparation.",

    // ── Priority card ─────────────────────────────────────────────────────────
    card_tag: "SYNTHESIS",
    card_session: "Fire-police-sanitation business Q&A",
    card_prep_heading: "Preparation points",
    card_point1:
      "Current conditions and application process for the officer apprehension incentive.",
    card_point2:
      "Whether the police bureau has formally raised the outdated standard with the NPA.",
    card_point3:
      "The chief answered on-site: 'Will handle after reviewing the relevant regulations.'",
    card_action_drawer: "View video evidence",
    card_action_records: "Open meeting records",

    // ── Workflow / limitation note ────────────────────────────────────────────
    workflow_heading: "Interpretation principle",
    workflow_body:
      "The Groq transcript is for navigation only. Formal citation returns to the Taichung City Council official video and meeting records.",
    limitation_note:
      "ORAL_OFFICIAL · UNVERIFIED_AFTER_MEETING · Navigation text is GROQ_ASR; formal citation returns to official source.",

    // ── Source monitor ────────────────────────────────────────────────────────
    source_monitor_label: "Public source monitor · Observability",
    source_monitor_heading: "Source health and intelligence gaps",
    source_next_update: "Next update",
    source_snapshot_loading: "Competition snapshot loading",
    source_data_as_of: "Data as of: ",
    source_no_date: "Date not provided by source",
    source_lkg: "LKG: ",
    source_no_lkg: "None yet",
    source_no_gaps: "No known gaps",
    source_official_name: "Official Chinese name:",
    source_back_to_official: "Back to official source",

    // ── Evidence drawer header ────────────────────────────────────────────────
    drawer_kicker: "Evidence drawer · S-010",
    drawer_heading: "Video evidence",
    drawer_close: "Close",
    drawer_close_aria: "Close video evidence",

    // ── Drawer states ─────────────────────────────────────────────────────────
    drawer_loading: "Building traceable timeline…",
    drawer_error_prefix: "Load failed: ",

    // ── Player panel ──────────────────────────────────────────────────────────
    player_aria: "Official video player",
    video_aria: "Taichung City Council official video",
    transport_back: "Back to clip start",
    provenance_source: "Source",
    provenance_source_value: "Taichung City Council",
    provenance_derived: "Derived text",
    provenance_timestamps: "Timestamps",
    provenance_cer: "CER gate",
    source_action_official: "Official video",
    source_action_reference: "Reference transcript",
    signoff_done: "Human-signed off",
    signoff_pending: "Pending business spot-check sign-off",

    // ── Playback status messages ──────────────────────────────────────────────
    playback_loading: "Loading evidence…",
    playback_ready: "Official video ready — click a segment or word timestamp.",
    playback_seeked_prefix: "Seeked to official video ",
    playback_seeked_suffix: ".",
    playback_hls_error: "HLS playback failed — verify via official source.",
    playback_hls_unsupported:
      "This browser does not support HLS — verify via official source.",
    playback_open_official: "Open official council video",
    playback_autoplay_blocked_prefix: "Seeked to ",
    playback_autoplay_blocked_suffix: " — press play to watch.",

    // ── Transcript panel ──────────────────────────────────────────────────────
    transcript_label: "Jumpable transcript",
    transcript_heading: "Click a word to return to official video",
    transcript_note:
      "Chinese official-source transcript — for timestamp navigation only, not authoritative English evidence.",
    search_aria: "Search transcript",
    search_placeholder: "Search: 警察局, 移工, 獎勵…",
    follow_on: "Following playback",
    follow_off: "Playback follow off",
    terms_aria: "Detected police terms",
    segment_jump_prefix: "Jump to ",
    word_blank: "space",
    empty_state: "No matching segments — clear the search.",

    // ── Intelligence summary ──────────────────────────────────────────────────
    summary_heading: "Intelligence Summary",
    summary_topics: "Key Topics",
    download_csv: "Download CSV",
    download_json: "Download JSON",
    download_heading: "Export Data",
  },

  zh: {
    // ── Language toggle ───────────────────────────────────────────────────────
    lang_toggle_label: "Switch to English",
    lang_toggle_current: "中文",

    // ── Site header ───────────────────────────────────────────────────────────
    site_kicker: "Taichung police intelligence",
    site_title: "今晚議會準備",
    freshness_live: "線上核對：",
    freshness_static: "靜態證據：2026-08-14",

    // ── Judge guide ───────────────────────────────────────────────────────────
    guide_label: "Judge quick guide · English",
    guide_heading: "Evidence-backed council preparation in five minutes",
    guide_body:
      "This public demo helps police policy staff spot a council issue, inspect official-source health and intelligence gaps, then jump to the exact official video timestamp. Chinese is the end-user language.",
    guide_step1: "Review the priority brief.",
    guide_step2: "Check source health, freshness, gaps, and last-known-good.",
    guide_step3: "Open the evidence drawer and select a transcript timestamp.",

    // ── Priority brief ────────────────────────────────────────────────────────
    brief_label: "今日需注意 · 01",
    brief_heading: "失聯移工查緝獎勵制度，已成為警察局答詢議題",
    brief_body:
      "議員比較第一線員警與民眾檢舉獎勵差距，並追問申請流程、撥款時間及是否向中央反映。建議列入後續制度盤點與答詢準備。",

    // ── Priority card ─────────────────────────────────────────────────────────
    card_tag: "SYNTHESIS",
    card_session: "警消環衛業務質詢",
    card_prep_heading: "準備重點",
    card_point1: "目前員警查獲失聯移工的獎勵與申請條件。",
    card_point2: "警察局是否已向警政署反映過時標準。",
    card_point3: "局長現場答覆為「了解規定後再做處理」。",
    card_action_drawer: "查看影音證據",
    card_action_records: "開啟議事錄",

    // ── Workflow / limitation note ────────────────────────────────────────────
    workflow_heading: "判讀原則",
    workflow_body:
      "Groq 逐字稿只負責定位。正式引用仍回到臺中市議會官方影音與議事錄。",
    limitation_note:
      "ORAL_OFFICIAL · UNVERIFIED_AFTER_MEETING · 導航文字為 GROQ_ASR；正式引用回到官方來源。",

    // ── Source monitor ────────────────────────────────────────────────────────
    source_monitor_label: "Public source monitor · 可觀測性",
    source_monitor_heading: "來源健康與情報缺口",
    source_next_update: "下次更新",
    source_snapshot_loading: "競賽快照載入中",
    source_data_as_of: "資料截至：",
    source_no_date: "來源未提供日期",
    source_lkg: "LKG：",
    source_no_lkg: "尚無",
    source_no_gaps: "目前無已知缺口",
    source_official_name: "官方中文名稱：",
    source_back_to_official: "回到官方來源",

    // ── Evidence drawer header ────────────────────────────────────────────────
    drawer_kicker: "Evidence drawer · S-010",
    drawer_heading: "影音證據",
    drawer_close: "關閉",
    drawer_close_aria: "關閉影音證據",

    // ── Drawer states ─────────────────────────────────────────────────────────
    drawer_loading: "正在建立可追溯時間軸…",
    drawer_error_prefix: "載入失敗：",

    // ── Player panel ──────────────────────────────────────────────────────────
    player_aria: "官方影音播放器",
    video_aria: "臺中市議會官方影音",
    transport_back: "回到片段起點",
    provenance_source: "來源",
    provenance_source_value: "臺中市議會",
    provenance_derived: "衍生文字",
    provenance_timestamps: "時間戳",
    provenance_cer: "CER Gate",
    source_action_official: "官方影音",
    source_action_reference: "校對參考稿",
    signoff_done: "人工已簽核",
    signoff_pending: "待業務抽聽簽核",

    // ── Playback status messages ──────────────────────────────────────────────
    playback_loading: "正在讀取證據…",
    playback_ready: "官方影音已就緒，可點擊段落或逐字時間點。",
    playback_seeked_prefix: "已定位至官方影音 ",
    playback_seeked_suffix: "。",
    playback_hls_error: "HLS 播放失敗，請改由官方來源核對。",
    playback_hls_unsupported: "此瀏覽器不支援 HLS，請改由官方來源核對。",
    playback_open_official: "開啟臺中市議會官方影音",
    playback_autoplay_blocked_prefix: "已定位至 ",
    playback_autoplay_blocked_suffix: "，按播放鍵即可觀看。",

    // ── Transcript panel ──────────────────────────────────────────────────────
    transcript_label: "可跳轉逐字稿",
    transcript_heading: "點字詞回到官方影音",
    transcript_note:
      "官方影音中文逐字稿——僅供時間點導航，非英文證據。",
    search_aria: "搜尋逐字稿",
    search_placeholder: "搜尋警察局、移工、獎勵…",
    follow_on: "跟隨播放中",
    follow_off: "已停止跟隨",
    terms_aria: "辨識到的警政詞",
    segment_jump_prefix: "跳到 ",
    word_blank: "空白",
    empty_state: "找不到符合的逐字稿，請清除搜尋條件。",

    // ── Intelligence summary ──────────────────────────────────────────────────
    summary_heading: "情報摘要",
    summary_topics: "關鍵議題",
    download_csv: "下載 CSV",
    download_json: "下載 JSON",
    download_heading: "匯出資料",
  },
};
