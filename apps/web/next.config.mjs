import { fileURLToPath } from "node:url";

const basePath = process.env.PAGES_BASE_PATH || "";
const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));

export default {
  output: "export",
  basePath,
  trailingSlash: true,
  turbopack: {
    root: repositoryRoot,
  },
};
