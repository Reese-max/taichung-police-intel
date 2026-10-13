// The handoff references the received artifact bytes, not a JSON reserialization
// or a self-declared hash in the publication payload.
export async function fetchBoundPublication(url, fetchImpl = globalThis.fetch) {
  const response = await fetchImpl(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  const data = JSON.parse(new TextDecoder().decode(bytes));
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Publication 必須是 JSON object");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  const brief_sha256 = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
  return { ...data, brief_sha256 };
}
