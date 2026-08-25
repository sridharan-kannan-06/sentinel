import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run runs the container directly, so the standalone output keeps the
  // image to the server and its actual dependencies rather than the whole
  // node_modules tree.
  output: "standalone",
  sassOptions: {
    // Turbopack resolves Sass through the modern Dart Sass API, which reads
    // loadPaths. includePaths alone is silently ignored there and Carbon's
    // internal relative imports then fail to resolve.
    loadPaths: ["./node_modules"],
    includePaths: ["./node_modules"],
    // Carbon still uses Sass features that Dart Sass warns about. The warnings
    // are about Carbon's own internals and are not actionable from here; left
    // unsilenced they bury real errors under thousands of lines.
    silenceDeprecations: [
      "mixed-decls",
      "global-builtin",
      "import",
      "legacy-js-api",
      "slash-div",
      "color-functions",
    ],
  },
};

export default nextConfig;
