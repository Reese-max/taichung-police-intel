export function formatTime(seconds, precise = false) {
  const safe = Math.max(0, Number(seconds) || 0);
  const whole = Math.floor(safe);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const body = `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  const clock = hours ? `${String(hours).padStart(2, "0")}:${body}` : body;
  return precise ? `${clock}.${String(Math.floor((safe - whole) * 100 + 1e-6)).padStart(2, "0")}` : clock;
}

export function findActiveIndex(items, seconds, tolerance = 0.05) {
  let active = -1;
  let latestStart = -Infinity;
  items.forEach((item, index) => {
    const start = Number(item.start);
    if (start <= seconds + tolerance && start >= latestStart) {
      latestStart = start;
      active = index;
    }
  });
  return active;
}

export function groupWordsBySegment(segments, words) {
  const groups = segments.map(() => []);
  words.forEach((word, wordIndex) => {
    const midpoint = (Number(word.start) + Number(word.end)) / 2;
    let segmentIndex = 0;
    let bestDistance = Infinity;
    segments.forEach((segment, index) => {
      const start = Number(segment.start);
      const end = Number(segment.end);
      const distance = midpoint < start ? start - midpoint : midpoint > end ? midpoint - end : 0;
      if (distance < bestDistance) {
        bestDistance = distance;
        segmentIndex = index;
      }
    });
    groups[segmentIndex].push({ ...word, wordIndex });
  });
  return groups;
}

export function validateEvidence(payload) {
  if (payload?.status !== "PASS") throw new Error("ASR 證據尚未通過 Gate。");
  if (!payload?.source?.media_url || !payload?.source?.official_page_url) throw new Error("缺少官方影音來源。");
  if (!payload?.asr?.segments?.length || !payload?.asr?.words?.length) throw new Error("缺少段落或逐字時間戳。");
  return payload;
}
