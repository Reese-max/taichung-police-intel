import { createVerifier, readBoundedJson } from "./auth.js";
import { reserve } from "./budget.js";
import { deny, failure, GLOBAL_OBJECT_NAME, loadConfig, response, validateContract, validateRoute } from "./config.js";

// Dependency injection is code-only for offline tests, never a request/env hook.
export function createAdmissionHandler({ storage, env, fetchImpl = fetch, now = Date.now, cryptoImpl = crypto }) {
  const verify = createVerifier({ fetchImpl, now, cryptoImpl });
  return async request => {
    try {
      validateRoute(request);
      const config = loadConfig(env);
      if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") deny("INVALID_REQUEST", 400);
      const token = request.headers.get("CF-Access-Jwt-Assertion");
      if (!token || token.length > 16384) deny("UNAUTHENTICATED", 403);
      const input = await readBoundedJson(request, 1024);
      validateContract(input);
      const verified = await verify(token, config);
      if (request.signal.aborted) deny("CANCELLED");
      await reserve(storage, verified, config, { now, signal: request.signal });
      // A lost/aborted response can leave a spent reservation. There is no refund,
      // idempotent reuse, provider call, or retry path here.
      return response(true);
    } catch (error) { return failure(error); }
  };
}
export class ResearchBudget {
  constructor(ctx, env) { this.authorize = createAdmissionHandler({ storage: ctx.storage, env }); }
  fetch(request) { return this.authorize(request); }
}
export default {
  async fetch(request, env) {
    try {
      validateRoute(request);
      loadConfig(env);
      if (!env.RESEARCH_BUDGET || typeof env.RESEARCH_BUDGET.idFromName !== "function" || typeof env.RESEARCH_BUDGET.get !== "function") deny("UNAVAILABLE");
      // Never partition by subject, deployment version, request URL, or day.
      const id = env.RESEARCH_BUDGET.idFromName(GLOBAL_OBJECT_NAME);
      const stub = env.RESEARCH_BUDGET.get(id);
      const headers = new Headers();
      for (const name of ["content-type", "CF-Access-Jwt-Assertion"]) if (request.headers.has(name)) headers.set(name, request.headers.get(name));
      const forwarded = new Request("https://research-admission/authorize", { method: "POST", headers, body: request.body, signal: request.signal, redirect: "error", duplex: "half" });
      return await stub.fetch(forwarded);
    } catch (error) { return failure(error); }
  },
};
