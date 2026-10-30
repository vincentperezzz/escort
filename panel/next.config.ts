import type { NextConfig } from "next";

const listener = process.env.LISTENER_URL || "http://127.0.0.1:8747";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/listener/:path*",
        destination: `${listener}/panel/:path*`,
      },
    ];
  },
};

export default nextConfig;
