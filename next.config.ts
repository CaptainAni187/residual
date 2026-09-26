import type { NextConfig } from "next";

const API = process.env.RESIDUAL_API ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  async rewrites() {
    return process.env.NODE_ENV === "development"
      ? [{ source: "/api/:path*", destination: `${API}/api/:path*` }]
      : [];
  },
};

export default config;
