import { fileURLToPath } from "node:url";

const basePath = process.env.PAGES_BASE_PATH || "";

export default {
  output: "export",
  basePath,
  trailingSlash: true,
  turbopack: { root: fileURLToPath(new URL(".", import.meta.url)) },
};
